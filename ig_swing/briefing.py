"""Morning briefing: upcoming events and fresh headlines. Data only, no signals.

Run from the repo root:  python3 -m ig_swing.briefing
Read-only. Makes plain GET requests to the free sources and prints a summary.
Times are shown in UK time.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from .config import optional_key
from .events import HIGH, MEDIUM, Event, load_events
from .news import Headline, load_headlines

UK = ZoneInfo("Europe/London")
UTC = timezone.utc
DAYS_AHEAD = 7
LOOKBACK_HOURS = 12


def _when(e: Event) -> str:
    if e.time_utc is not None:
        return f"{e.time_utc.astimezone(UK):%a %d %b %H:%M}"
    return f"{e.day:%a %d %b} (time n/a)"


def format_briefing(now_utc: datetime, events: list[Event], headlines: list[Headline], notes: list[str], days: int = DAYS_AHEAD) -> str:
    today = now_utc.astimezone(UK).date()
    horizon = today + timedelta(days=days)
    lines = [f"IG-Swing briefing, {now_utc.astimezone(UK):%a %d %b %Y %H:%M} UK", ""]

    upcoming = [e for e in events if today <= e.day <= horizon]
    for tier, title in ((HIGH, "High impact"), (MEDIUM, "Medium impact")):
        group = [e for e in upcoming if e.tier == tier]
        lines.append(f"{title}, next {days} days ({len(group)})")
        lines += [f"  {_when(e)}  {e.label}  [{e.source}]" for e in group] or ["  none found"]
        lines.append("")

    lines.append(f"Headlines, last {LOOKBACK_HOURS}h ({len(headlines)} shown)")
    for h in headlines:
        lines.append(f"  {h.published.astimezone(UK):%H:%M}  {h.title}  ({h.source})")
    if not headlines:
        lines.append("  none found")
    lines.append("")

    if notes:
        lines.append("Notes")
        lines += [f"  {n}" for n in notes]
        lines.append("")
    lines.append("Data only. Tiers are a judgement call. No impact ratings or forecasts in the free sources.")
    return "\n".join(lines)


def main() -> int:
    now = datetime.now(UTC)
    session = requests.Session()
    events, event_notes = load_events(session, now.date(), now.date() + timedelta(days=DAYS_AHEAD), optional_key("FRED_API_KEY"))
    headlines, news_notes = load_headlines(session, optional_key("FINNHUB_API_KEY"), now, LOOKBACK_HOURS)
    print(format_briefing(now, events, headlines, event_notes + news_notes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
