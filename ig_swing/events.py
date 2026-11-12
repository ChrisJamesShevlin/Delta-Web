"""Upcoming US macro events from free sources.

Sources (all tested read-only on 2026-11-11):
- BLS release schedule (ICS file, no key): jobs, inflation, productivity.
- FRED release dates (free API key): retail sales, GDP, PCE and more. The
  include_release_dates_with_no_data flag is what makes future dates appear.
  FRED gives dates only, with no time of day.
- FOMC decision dates: a hand-kept list taken from the Fed's own calendar
  page (federalreserve.gov/monetarypolicy/fomccalendars.htm), checked
  2026-11-11. Re-check it each year. The Fed says dates are tentative until
  confirmed at the meeting before.

No impact ratings or consensus forecasts exist in the free sources. The tiers
below are a judgement call and can be edited.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .sources import TIMEOUT_SECONDS, USER_AGENT, SourceError

ET = ZoneInfo("America/New_York")
UTC = timezone.utc

HIGH = "high"
MEDIUM = "medium"

BLS_ICS_URL = "https://www.bls.gov/schedule/news_release/bls.ics"
FRED_DATES_URL = "https://api.stlouisfed.org/fred/releases/dates"
FRED_PAGE_SIZE = 1000
FRED_MAX_PAGES = 6

# Second day of each two-day meeting: the decision and statement day (14:00 ET).
FOMC_DECISION_DATES: tuple[date, ...] = (
    date(2026, 1, 28), date(2026, 3, 18), date(2026, 4, 29), date(2026, 6, 17),
    date(2026, 7, 29), date(2026, 9, 16), date(2026, 10, 28), date(2026, 12, 9),
    date(2027, 1, 27), date(2027, 3, 17), date(2027, 4, 28), date(2027, 6, 9),
    date(2027, 7, 28), date(2027, 9, 15), date(2027, 10, 27), date(2027, 12, 8),
)

# Central bank decision days for the currencies we trade (added 2026-11-12). Taken on 2026-11-12 from each bank's own schedule page (ECB, Bank
# of England, Bank of Japan, RBA, Bank of Canada; the SNB's December 2026 date and 2027 list from a search readout of its schedule) and, for the
# RBNZ, from its own event listings for 9 Dec 2026 to 5 May 2027 plus a calendar site that cites it for the rest (16 Jun to 27 Oct 2027).
# Only the decision day is kept; announcement times are not (date-only events count for their whole day). Banks can add meetings, and the
# ECB page showed only its December meeting for 2026. Re-check every year.
CENTRAL_BANKS: dict[str, tuple[str, tuple[date, ...]]] = {
    "ECB": ("ECB rate decision (EUR)", (date(2026, 12, 17), date(2027, 2, 4), date(2027, 3, 18), date(2027, 4, 29), date(2027, 6, 10), date(2027, 7, 22),
                                        date(2027, 9, 9), date(2027, 10, 28), date(2027, 12, 16))),
    "BoE": ("Bank of England rate decision (GBP)", (date(2026, 12, 17), date(2027, 2, 4), date(2027, 3, 18), date(2027, 4, 29), date(2027, 6, 17),
                                                   date(2027, 7, 29), date(2027, 9, 16), date(2027, 11, 4), date(2027, 12, 16))),
    "BoJ": ("Bank of Japan rate decision (JPY)", (date(2026, 12, 18), date(2027, 1, 22), date(2027, 3, 18), date(2027, 4, 28), date(2027, 6, 11),
                                                 date(2027, 7, 22), date(2027, 9, 22), date(2027, 10, 29), date(2027, 12, 17))),
    "RBA": ("RBA rate decision (AUD)", (date(2026, 12, 8), date(2027, 2, 9), date(2027, 3, 23), date(2027, 5, 4), date(2027, 6, 22), date(2027, 8, 10),
                                       date(2027, 9, 28), date(2027, 11, 2), date(2027, 12, 14))),
    "RBNZ": ("RBNZ rate decision (NZD)", (date(2026, 12, 9), date(2027, 2, 10), date(2027, 3, 17), date(2027, 5, 5), date(2027, 6, 16), date(2027, 8, 4),
                                         date(2027, 9, 15), date(2027, 10, 27))),
    "BoC": ("Bank of Canada rate decision (CAD)", (date(2026, 12, 9), date(2027, 1, 27), date(2027, 3, 3), date(2027, 4, 28), date(2027, 6, 2),
                                                  date(2027, 7, 21), date(2027, 9, 8), date(2027, 10, 27), date(2027, 12, 8))),
    "SNB": ("SNB rate decision (CHF)", (date(2026, 12, 10), date(2027, 3, 18), date(2027, 6, 24), date(2027, 9, 23), date(2027, 12, 16))),
}
CENTRAL_BANK_LAST = date(2027, 10, 27)   # the earliest "last known date" among the banks except the RBNZ's gap: beyond it coverage is partial

# (full-match regex on the normalised release name, label, tier)
_RULES = [
    (r"employment situation", "Employment Situation (jobs report)", HIGH),
    (r"consumer price index", "Consumer Price Index (CPI)", HIGH),
    (r"producer price index", "Producer Price Index (PPI)", HIGH),
    (r"gross domestic product", "GDP", HIGH),
    (r"personal income and outlays", "Personal Income and Outlays (PCE inflation)", HIGH),
    (r"advance monthly sales for retail and food services", "Retail sales (advance)", HIGH),
    (r"job openings and labor turnover survey", "JOLTS job openings", HIGH),
    (r"summary of economic projections", "FOMC projections (SEP)", HIGH),
    (r"unemployment insurance weekly claims report", "Weekly jobless claims", MEDIUM),
    (r"adp national employment report", "ADP employment", MEDIUM),
    (r"surveys of consumers", "Consumer sentiment (Michigan)", MEDIUM),
    (r"g\.17 industrial production and capacity utilization", "Industrial production", MEDIUM),
    (r"manufacturing business outlook survey", "Philly Fed manufacturing", MEDIUM),
    (r"empire state manufacturing survey", "Empire State manufacturing", MEDIUM),
    (r"manufacturer's shipments, inventories, and orders \(m3\) survey", "Factory orders (M3)", MEDIUM),
    (r"u\.s\. international trade in goods and services", "Trade balance", MEDIUM),
    (r"productivity and costs", "Productivity and costs", MEDIUM),
    (r"employment cost index", "Employment Cost Index", MEDIUM),
    (r"u\.s\. import and export price indexes", "Import and export prices", MEDIUM),
    (r"advance economic indicators", "Advance economic indicators", MEDIUM),
    (r"new residential construction", "Housing starts", MEDIUM),
]
RULES = [(re.compile(p), label, tier) for p, label, tier in _RULES]


@dataclass(frozen=True)
class Event:
    day: date
    label: str
    tier: str
    source: str
    time_utc: datetime | None = None  # None means the source gives a date only


def _normalise(name: str) -> str:
    name = re.sub(r"\s+", " ", name.strip().lower())
    return re.sub(r"^the ", "", name)


def classify(name: str) -> tuple[str, str] | None:
    """Return (label, tier) for a release we care about, else None."""
    norm = _normalise(name)
    for pattern, label, tier in RULES:
        if pattern.fullmatch(norm):
            return label, tier
    return None


def _sort_key(e: Event):
    return (e.day, e.time_utc.timestamp() if e.time_utc else -1.0, e.label)


# ---- BLS ----------------------------------------------------------------


def parse_ics(text: str, start: date, end: date) -> list[Event]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n[ \t]", "", text)  # unfold continued lines
    out: list[Event] = []
    for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", text, re.S):
        m = re.search(r"^DTSTART([^:\n]*):(\d{8})(?:T(\d{6})(Z)?)?", block, re.M)
        s = re.search(r"^SUMMARY:(.*)$", block, re.M)
        if not m or not s:
            continue
        day = datetime.strptime(m.group(2), "%Y%m%d").date()
        if not (start <= day <= end):
            continue
        cls = classify(s.group(1).replace("\\,", ",").strip())
        if not cls:
            continue
        when = None
        if m.group(3):
            local = datetime.strptime(m.group(2) + m.group(3), "%Y%m%d%H%M%S")
            if m.group(4):
                when = local.replace(tzinfo=UTC)
            elif "Eastern" in m.group(1):
                when = local.replace(tzinfo=ET).astimezone(UTC)
        out.append(Event(day, cls[0], cls[1], "BLS", when))
    return out


def fetch_bls(session: Any, start: date, end: date) -> list[Event]:
    resp = session.get(BLS_ICS_URL, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_SECONDS)
    if resp.status_code != 200:
        raise SourceError(f"HTTP {resp.status_code}")
    return parse_ics(resp.text, start, end)


# ---- FRED ---------------------------------------------------------------


def parse_fred_rows(rows: list[dict], start: date, end: date) -> list[Event]:
    out: list[Event] = []
    for row in rows:
        try:
            day = datetime.strptime(row["date"], "%Y-%m-%d").date()
            name = row["release_name"]
        except (KeyError, ValueError, TypeError):
            continue
        if not (start <= day <= end):
            continue
        cls = classify(name)
        if cls:
            out.append(Event(day, cls[0], cls[1], "FRED", None))
    return out


def fetch_fred(session: Any, api_key: str, start: date, end: date) -> list[Event]:
    events: list[Event] = []
    offset = 0
    for _ in range(FRED_MAX_PAGES):
        resp = session.get(
            FRED_DATES_URL,
            params={
                "api_key": api_key,
                "file_type": "json",
                "include_release_dates_with_no_data": "true",
                "realtime_start": start.isoformat(),
                "realtime_end": "9999-12-31",
                "order_by": "release_date",
                "sort_order": "asc",
                "limit": FRED_PAGE_SIZE,
                "offset": offset,
            },
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT_SECONDS,
        )
        if resp.status_code != 200:
            raise SourceError(f"HTTP {resp.status_code}")
        data = resp.json()
        rows = data.get("release_dates", [])
        events += parse_fred_rows(rows, start, end)
        if not rows or rows[-1].get("date", "") > end.isoformat():
            break
        offset += len(rows)
        if offset >= int(data.get("count", 0)):
            break
    return events


# ---- FOMC ---------------------------------------------------------------


def fomc_events(start: date, end: date) -> list[Event]:
    out = []
    for d in FOMC_DECISION_DATES:
        if start <= d <= end:
            when = datetime.combine(d, time(14, 0), ET).astimezone(UTC)
            out.append(Event(d, "FOMC rate decision", HIGH, "Fed schedule", when))
    return out


def central_bank_events(start: date, end: date) -> list[Event]:
    out = []
    for _bank, (label, days) in CENTRAL_BANKS.items():
        for d in days:
            if start <= d <= end:
                out.append(Event(d, label, HIGH, "central bank schedule", None))
    return out


# ---- combine ------------------------------------------------------------


def merge_events(*lists: list[Event]) -> list[Event]:
    """Merge sources. A duplicate (same day and label) keeps the timed one."""
    best: dict[tuple[date, str], Event] = {}
    for lst in lists:
        for e in lst:
            key = (e.day, e.label)
            if key not in best or (best[key].time_utc is None and e.time_utc is not None):
                best[key] = e
    return sorted(best.values(), key=_sort_key)


def events_within(events: list[Event], now_utc: datetime, hours: float, tiers: tuple[str, ...] = (HIGH,)) -> list[Event]:
    """Events inside the next ``hours``. Date-only events count for their whole day."""
    stop = now_utc + timedelta(hours=hours)
    out = []
    for e in events:
        if e.tier not in tiers:
            continue
        if e.time_utc is not None:
            if now_utc <= e.time_utc <= stop:
                out.append(e)
        elif now_utc.date() <= e.day <= stop.date():
            out.append(e)
    return out


def load_events(session: Any, start: date, end: date, fred_key: str | None) -> tuple[list[Event], list[str]]:
    """Fetch every source. One failing source never stops the others."""
    notes: list[str] = []
    lists = [fomc_events(start, end), central_bank_events(start, end)]
    if end > max(FOMC_DECISION_DATES):
        notes.append(f"FOMC: hand-kept dates only run to {max(FOMC_DECISION_DATES)}")

    jobs = [("BLS", lambda: fetch_bls(session, start, end))]
    if fred_key:
        jobs.append(("FRED", lambda: fetch_fred(session, fred_key, start, end)))
    else:
        notes.append("FRED: no FRED_API_KEY set, skipped")

    for name, fn in jobs:
        try:
            lists.append(fn())
        except SourceError as exc:
            notes.append(f"{name}: unavailable ({exc})")
        except Exception as exc:  # type name only: messages can contain URLs with keys
            notes.append(f"{name}: unavailable ({type(exc).__name__})")
    return merge_events(*lists), notes
