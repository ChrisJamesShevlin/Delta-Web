"""Offline tests for ig_swing.events."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from ig_swing import events
from ig_swing.events import (
    HIGH,
    MEDIUM,
    Event,
    classify,
    events_within,
    fomc_events,
    load_events,
    merge_events,
    parse_fred_rows,
    parse_ics,
)

UTC = timezone.utc
KEY = "SENTINEL-FRED-KEY-12345"

ICS = """BEGIN:VCALENDAR
X-WR-TIMEZONE:US-Eastern
BEGIN:VEVENT
DTSTART;TZID=US-Eastern:20261113T083000
SUMMARY:Producer Price Index
END:VEVENT
BEGIN:VEVENT
DTSTART;TZID=US-Eastern:20261118T083000
SUMMARY:Employer-Reported Workplace Injuries and Illnesses (Annual)
END:VEVENT
BEGIN:VEVENT
DTSTART;TZID=US-Eastern:20261204T083000
SUMMARY:The Employment Situation
END:VEVENT
BEGIN:VEVENT
DTSTART;TZID=US-Eastern:20270105T083000
SUMMARY:Consumer Price Index
END:VEVENT
END:VCALENDAR
"""


class Resp:
    def __init__(self, status=200, body=None, text=""):
        self.status_code, self._body, self.text = status, body, text

    def json(self):
        return self._body


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, params))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def test_classify_keeps_market_movers_and_drops_noise():
    assert classify("Consumer Price Index") == ("Consumer Price Index (CPI)", HIGH)
    assert classify("The Employment Situation")[1] == HIGH
    assert classify("Unemployment Insurance Weekly Claims Report")[1] == MEDIUM
    assert classify("Consumer Price Index Excluding Energy Commodities") is None
    assert classify("Gross Domestic Product by County") is None
    assert classify("Nasdaq Daily Index Data") is None


def test_ics_converts_eastern_to_utc_and_filters():
    evs = parse_ics(ICS, date(2026, 11, 11), date(2026, 12, 31))
    assert [e.label for e in evs] == ["Producer Price Index (PPI)", "Employment Situation (jobs report)"]
    assert evs[0].time_utc == datetime(2026, 11, 13, 13, 30, tzinfo=UTC)  # 08:30 EST = 13:30Z
    assert all(e.source == "BLS" for e in evs)


def test_ics_handles_folded_lines_and_date_only():
    text = "BEGIN:VEVENT\r\nDTSTART;VALUE=DATE:20261113\r\nSUMMARY:Producer Price\r\n  Index\r\nEND:VEVENT\r\n"
    evs = parse_ics(text, date(2026, 11, 1), date(2026, 11, 30))
    assert len(evs) == 1 and evs[0].time_utc is None


def test_fomc_decision_time_is_2pm_eastern():
    evs = fomc_events(date(2026, 12, 1), date(2026, 12, 31))
    assert len(evs) == 1 and evs[0].day == date(2026, 12, 9)
    assert evs[0].time_utc == datetime(2026, 12, 9, 19, 0, tzinfo=UTC)  # 14:00 EST


def test_fomc_list_has_eight_meetings_a_year():
    for yr in (2026, 2027):
        assert sum(d.year == yr for d in events.FOMC_DECISION_DATES) == 8


def test_fred_rows_filtered_by_whitelist_and_window():
    rows = [
        {"date": "2026-11-17", "release_name": "Advance Monthly Sales for Retail and Food Services"},
        {"date": "2026-11-17", "release_name": "Nasdaq Daily Index Data"},
        {"date": "2026-12-30", "release_name": "Gross Domestic Product"},
        {"date": "bad", "release_name": "Gross Domestic Product"},
    ]
    evs = parse_fred_rows(rows, date(2026, 11, 11), date(2026, 11, 30))
    assert [(e.day, e.label) for e in evs] == [(date(2026, 11, 17), "Retail sales (advance)")]


def test_fred_paging_follows_count_then_stops():
    page1 = Resp(200, {"count": 3, "release_dates": [
        {"date": "2026-11-12", "release_name": "Producer Price Index"},
        {"date": "2026-11-13", "release_name": "Nasdaq Daily Index Data"}]})
    page2 = Resp(200, {"count": 3, "release_dates": [
        {"date": "2026-11-17", "release_name": "Gross Domestic Product"}]})
    s = Session([page1, page2])
    evs = events.fetch_fred(s, KEY, date(2026, 11, 11), date(2026, 11, 30))
    assert len(s.calls) == 2 and s.calls[1][1]["offset"] == 2
    assert s.calls[0][1]["include_release_dates_with_no_data"] == "true"
    assert [e.label for e in evs] == ["Producer Price Index (PPI)", "GDP"]


def test_fred_stops_once_past_window():
    page = Resp(200, {"count": 5000, "release_dates": [{"date": "2027-01-01", "release_name": "Gross Domestic Product"}]})
    s = Session([page])
    assert events.fetch_fred(s, KEY, date(2026, 11, 11), date(2026, 11, 30)) == []
    assert len(s.calls) == 1


def test_merge_dedupes_and_prefers_timed_event():
    d = date(2026, 11, 13)
    untimed = Event(d, "Producer Price Index (PPI)", HIGH, "FRED", None)
    timed = Event(d, "Producer Price Index (PPI)", HIGH, "BLS", datetime(2026, 11, 13, 13, 30, tzinfo=UTC))
    merged = merge_events([untimed], [timed])
    assert len(merged) == 1 and merged[0].source == "BLS"


def test_events_within_handles_timed_and_date_only():
    now = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
    timed = Event(date(2026, 11, 13), "PPI", HIGH, "BLS", datetime(2026, 11, 13, 13, 30, tzinfo=UTC))
    later = Event(date(2026, 11, 13), "Late", HIGH, "BLS", datetime(2026, 11, 13, 20, 0, tzinfo=UTC))
    dateonly = Event(date(2026, 11, 13), "Retail", HIGH, "FRED", None)
    medium = Event(date(2026, 11, 13), "Claims", MEDIUM, "FRED", None)
    got = events_within([timed, later, dateonly, medium], now, hours=3)
    assert {e.label for e in got} == {"PPI", "Retail"}
    assert {e.label for e in events_within([medium], now, 3, tiers=(HIGH, MEDIUM))} == {"Claims"}


def test_load_events_survives_a_failing_source_and_never_leaks_the_key():
    s = Session([Resp(200, text=ICS), RuntimeError(f"connection failed for ...api_key={KEY}")])
    evs, notes = load_events(s, date(2026, 11, 11), date(2026, 11, 30), KEY)
    assert any(e.source == "BLS" for e in evs)
    assert any(n == "FRED: unavailable (RuntimeError)" for n in notes)
    assert KEY not in " ".join(notes)


def test_load_events_reports_http_status_and_missing_key():
    s = Session([Resp(503)])
    _, notes = load_events(s, date(2026, 11, 11), date(2026, 11, 30), None)
    assert "BLS: unavailable (HTTP 503)" in notes
    assert any("FRED_API_KEY" in n for n in notes)


def test_load_events_warns_when_fomc_list_runs_out():
    s = Session([Resp(200, text="")])
    _, notes = load_events(s, date(2028, 1, 1), date(2028, 1, 10), None)
    assert any("hand-kept dates only run to" in n for n in notes)
