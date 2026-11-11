"""Earnings-release dates from SEC EDGAR (free, official). Read-only.

A company files an 8-K with Item 2.02 ("Results of Operations") when it releases earnings.
The filing carries an exact acceptance timestamp, which tells us whether the news came out
before the open, after the close, or during the session, and so which trading day first
reacted to it. Verified reachable on 2026-11-11 with the generic client name in
ig_swing.sources (SEC asks for a contact in the User-Agent: none of the user's details are sent).

Limits: the acceptance time can lag the press release, and the ticker list covers current
companies only, so delisted companies are missing (survivorship bias).
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .sources import TIMEOUT_SECONDS, USER_AGENT, SourceError

UTC = timezone.utc
NY = ZoneInfo("America/New_York")
SUBMISSIONS_URL = "https://data.sec.gov/submissions/"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
EARNINGS_ITEM = "2.02"
DEDUPE_DAYS = 10
CACHE_PATH = Path("data/history/edgar_events.json")


def _get(session: Any, url: str) -> Any:
    resp = session.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_SECONDS)
    if resp.status_code != 200:
        raise SourceError(f"HTTP {resp.status_code}")
    return resp.json()


def load_ticker_map(session: Any) -> dict[str, int]:
    data = _get(session, TICKERS_URL)
    return {v["ticker"].upper(): int(v["cik_str"]) for v in data.values()}


def earnings_times(block: dict) -> list[datetime]:
    """Acceptance times (UTC) of 8-K filings with item 2.02 in one block of submission arrays."""
    out = []
    for form, items, accepted in zip(block.get("form", []), block.get("items", []), block.get("acceptanceDateTime", [])):
        if form != "8-K" or EARNINGS_ITEM not in str(items).split(","):
            continue
        try:
            out.append(datetime.fromisoformat(accepted.replace("Z", "+00:00")).astimezone(UTC))
        except (ValueError, AttributeError):
            continue
    return out


def dedupe(times: list[datetime], days: int = DEDUPE_DAYS) -> list[datetime]:
    """Keep the first filing in any cluster: a company often files a second 2.02 within days."""
    out: list[datetime] = []
    for t in sorted(times):
        if not out or (t - out[-1]).days >= days:
            out.append(t)
    return out


def fetch_events(session: Any, cik: int, pause: float = 0.2) -> list[datetime]:
    data = _get(session, f"{SUBMISSIONS_URL}CIK{cik:010d}.json")
    times = earnings_times(data["filings"]["recent"])
    for page in data["filings"].get("files", []):
        time.sleep(pause)
        times += earnings_times(_get(session, SUBMISSIONS_URL + page["name"]))
    return dedupe(times)


def fetch_all(session: Any, tickers: list[str], pause: float = 0.2) -> tuple[dict[str, list[datetime]], list[str]]:
    notes: list[str] = []
    mapping = load_ticker_map(session)
    out: dict[str, list[datetime]] = {}
    for sym in tickers:
        cik = mapping.get(sym.upper())
        if cik is None:
            notes.append(f"{sym}: not in the EDGAR ticker list")
            continue
        try:
            time.sleep(pause)
            out[sym] = fetch_events(session, cik, pause)
        except SourceError as exc:
            notes.append(f"{sym}: unavailable ({exc})")
        except Exception as exc:  # type name only
            notes.append(f"{sym}: unavailable ({type(exc).__name__})")
    return out, notes


def save_events(events: dict[str, list[datetime]], path: str | Path = CACHE_PATH) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({k: [t.isoformat() for t in v] for k, v in events.items()}))


def load_events(path: str | Path = CACHE_PATH) -> dict[str, list[datetime]]:
    raw = json.loads(Path(path).read_text())
    return {k: [datetime.fromisoformat(t) for t in v] for k, v in raw.items()}
