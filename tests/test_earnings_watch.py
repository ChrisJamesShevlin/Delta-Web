"""Offline tests for ig_swing.earnings_watch and the EDGAR recent-filings helper."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from ig_swing import earnings_watch as ew
from ig_swing import edgar
from ig_swing.journal import Journal

UTC = timezone.utc
R = 30


def frames(n=90, jump=0.10):
    dates = pd.bdate_range("2026-01-05", periods=n)
    close = np.full(n, 100.0)
    close[R:] = 100 * (1 + jump)
    open_ = np.r_[close[0], close[:-1]]
    open_[R + 1] = close[R]
    px = pd.DataFrame({"timestamp": dates, "open": open_, "high": close + 1, "low": close - 1, "close": close, "volume": 1.0})
    spy = pd.DataFrame({"timestamp": dates, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1.0})
    return px, spy, dates


def accepted(dates, r=R):
    d = dates[r - 1]
    return datetime(d.year, d.month, d.day, 22, 0, tzinfo=UTC)          # after the close of the session before r


def at(dates, r, hour=23):
    d = dates[r]
    return datetime(d.year, d.month, d.day, hour, 0, tzinfo=UTC)


def test_a_bar_is_complete_only_after_22_utc_on_its_date():
    px, _, dates = frames()
    day = dates[40]
    assert ew.complete_bars(px, datetime(day.year, day.month, day.day, 21, 59, tzinfo=UTC))["timestamp"].iloc[-1] == dates[39]
    assert ew.complete_bars(px, datetime(day.year, day.month, day.day, 22, 0, tzinfo=UTC))["timestamp"].iloc[-1] == dates[40]


def test_detects_a_triggered_reaction_once_its_session_is_complete():
    px, _, dates = frames()
    d = ew.detect("AAA", px, accepted(dates), at(dates, R))
    assert d.status == "trigger" and d.event["direction"] == "BUY" and d.event["reaction"] == pytest.approx(0.10)
    assert d.event["react_date"] == str(dates[R].date()) and d.event["atr"] > 0
    early = ew.detect("AAA", px, accepted(dates), datetime(dates[R].year, dates[R].month, dates[R].day, 15, 0, tzinfo=UTC))
    assert early.status == "pending"                                    # the reacting session has not finished


def test_skips_are_explained():
    px, _, dates = frames(jump=0.03)
    assert "below the threshold" in ew.detect("AAA", px, accepted(dates), at(dates, R)).reason
    px, _, dates = frames()
    d = dates[R]
    assert ew.detect("AAA", px, datetime(d.year, d.month, d.day, 17, 0, tzinfo=UTC), at(dates, R)).reason == "filed during the session"
    assert ew.detect("AAA", px.iloc[:10], accepted(dates), at(dates, R)).reason == "not enough price history"


def test_a_short_event():
    px, _, dates = frames(jump=-0.10)
    assert ew.detect("AAA", px, accepted(dates), at(dates, R)).event["direction"] == "SELL"


def test_scan_records_new_triggers_once_and_never_orders(tmp_path):
    px, _, dates = frames()
    j = Journal(tmp_path / "j.db")
    filings = {"AAA": [{"accepted": accepted(dates), "url": "https://example.test/f/"}]}
    new, stats = ew.scan(j, at(dates, R), filings, lambda s: px)
    assert len(new) == 1 and stats == {"trigger": 1} and new[0]["url"] == "https://example.test/f/"
    again, stats2 = ew.scan(j, at(dates, R + 1), filings, lambda s: px)
    assert again == [] and [e["kind"] for e in j.events()].count("shadow_earnings") == 1       # not recorded twice


def test_scan_survives_a_failing_price_source(tmp_path):
    j = Journal(tmp_path / "j.db")
    _, _, dates = frames()

    def boom(s):
        raise RuntimeError("secret detail")

    new, stats = ew.scan(j, at(dates, R), {"AAA": [{"accepted": accepted(dates), "url": None}]}, boom)
    assert new == [] and stats == {"error RuntimeError": 1}


def test_backlog_means_recorded_after_the_entry_open():
    px, _, dates = frames()
    react = str(dates[R].date())                                        # entry is the next business day at 13:30 UTC
    entry_day = dates[R + 1]
    assert not ew.is_backlog(at(dates, R).isoformat(), react)           # recorded on the evening of the reaction day: clean
    assert not ew.is_backlog(datetime(entry_day.year, entry_day.month, entry_day.day, 13, 29, tzinfo=UTC).isoformat(), react)
    assert ew.is_backlog(datetime(entry_day.year, entry_day.month, entry_day.day, 13, 30, tzinfo=UTC).isoformat(), react)
    assert ew.is_backlog(at(dates, R + 5).isoformat(), react)


def test_backlog_events_are_excluded_from_the_measure_and_cannot_be_read(tmp_path):
    px, spy, dates = frames(n=100)
    j = Journal(tmp_path / "j.db")
    ew.scan(j, at(dates, R + 5), {"AAA": [{"accepted": accepted(dates), "url": None}]}, lambda s: px)   # found 5 sessions late
    assert [e["kind"] for e in j.events()] == ["shadow_earnings"]                                          # still on record
    assert "not be clean" in ew.record_read(j, at(dates, R + 5), "AAA", "BULLISH", "HIGH", ["x"])
    m = ew.measure(j.events(), lambda s: px, spy)
    assert m["triggered"] == 0 and m["backlog"] == 1 and "left out of this measure" in ew.format_measure(m)


def test_a_read_attaches_to_the_latest_unread_event_and_validates(tmp_path):
    px, _, dates = frames()
    j = Journal(tmp_path / "j.db")
    ew.scan(j, at(dates, R), {"AAA": [{"accepted": accepted(dates), "url": None}]}, lambda s: px)
    assert "REFUSED" in ew.record_read(j, at(dates, R), "ZZZ", "BULLISH", "HIGH", ["x"])        # no such event
    assert "REFUSED" in ew.record_read(j, at(dates, R), "AAA", "SIDEWAYS", "HIGH", ["x"])       # bad direction
    assert "Recorded" in ew.record_read(j, at(dates, R), "AAA", "BULLISH", "HIGH", ["Guidance raised"])
    assert "REFUSED" in ew.record_read(j, at(dates, R), "AAA", "BULLISH", "HIGH", ["x"])        # already read


def test_measure_splits_finished_events_by_whether_the_read_backed_the_move(tmp_path):
    px, spy, dates = frames(n=100)
    px.loc[R + 20, "close"] = 121.0                                     # exit session closes 10% above the entry open
    j = Journal(tmp_path / "j.db")
    ew.scan(j, at(dates, R), {"AAA": [{"accepted": accepted(dates), "url": None}]}, lambda s: px)
    ew.record_read(j, at(dates, R), "AAA", "BULLISH", "HIGH", ["Guidance raised"])
    m = ew.measure(j.events(), lambda s: px, spy)
    assert m["triggered"] == 1 and len(m["finished"]) == 1 and m["pending"] == 0
    res, read = m["finished"][0]
    assert res.gross_adj == pytest.approx(0.10) and read["direction"] == "BULLISH"
    text = ew.format_measure(m)
    assert "n=1" in text and "read backed the move: n=1" in text and "no conclusion" in text and "Not traded" in text


def test_unfinished_events_are_counted_as_still_running(tmp_path):
    px, spy, dates = frames(n=40)                                       # not enough bars after the event
    j = Journal(tmp_path / "j.db")
    j.record_event(at(dates, R).isoformat(), "shadow_earnings",
                   json.dumps({"ticker": "AAA", "accepted": accepted(dates).isoformat(), "react_date": str(dates[R].date())}))
    m = ew.measure(j.events(), lambda s: px, spy)
    assert len(m["finished"]) == 0 and m["pending"] == 1


def test_recent_filings_keeps_only_new_earnings_filings_with_a_link():
    class Resp:
        status_code = 200

        def json(self):
            return {"filings": {"recent": {
                "form": ["8-K", "8-K", "10-Q"], "items": ["2.02,9.01", "5.02", "2.02"],
                "acceptanceDateTime": ["2026-11-02T21:30:23.000Z", "2026-11-03T12:00:00.000Z", "2026-11-04T12:00:00.000Z"],
                "accessionNumber": ["0000320193-26-000123", "x", "y"]}}}

    class S:
        def get(self, *a, **k):
            return Resp()

    out = edgar.recent_filings(S(), 320193, datetime(2026, 10, 25, tzinfo=UTC))
    assert len(out) == 1 and out[0]["url"] == "https://www.sec.gov/Archives/edgar/data/320193/000032019326000123/"
    assert edgar.recent_filings(S(), 320193, datetime(2026, 11, 3, tzinfo=UTC)) == []        # older than the cut-off
