"""Forward test of post-earnings drift, in real time, with no hindsight and no trading.

The historical test (ig_swing.pead_test, see CLAUDE.md) used today's large caps, which flatters it.
This watches the same fixed 97 stocks going forward. Each day it finds new earnings releases (SEC
EDGAR), works out the reacting session with the SAME code as the historical test, and records every
reaction of 5% or more as a would-have-traded event in the journal. Nothing is ever ordered.

Run after the US close (22:00 UTC or later, so the day's bar is complete):
  python3 -m ig_swing.earnings_watch scan      find and record new triggered events (read-only)
  python3 -m ig_swing.earnings_watch report    measure finished events with the pre-registered measures
  python3 -m ig_swing.earnings_watch read --ticker NVDA --direction BULLISH --confidence HIGH --reason "..."
                                               record the assistant's read of that release (BEFORE the outcome)

A read is recorded against the latest event for that ticker that has none. Reading the release text
before the outcome is known is the only way to test whether it adds anything beyond the price reaction.
The report splits finished events by whether the read backed the price move. Under 100 finished events
is no conclusion, whatever the average says. About 90 events a year are expected, so this takes a year.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import pandas as pd

from . import pead_test as pt
from .news_view import CONFIDENCES, DIRECTIONS

UTC = timezone.utc
TRIGGER_KIND = "shadow_earnings"
READ_KIND = "earnings_read"
LOOKBACK_DAYS = 10
BAR_COMPLETE_HOURS = 22     # a US session dated D is complete by 22:00 UTC on D (the close is 20:00 or 21:00 UTC)
MIN_BARS = 20


def complete_bars(px: pd.DataFrame, now: datetime) -> pd.DataFrame:
    """Drop any bar whose session may still be running."""
    ready = pd.to_datetime(px["timestamp"]) + pd.Timedelta(hours=BAR_COMPLETE_HOURS) <= pd.Timestamp(now).tz_convert("UTC").tz_localize(None)
    return px[ready].reset_index(drop=True)


@dataclass(frozen=True)
class Detection:
    status: str                    # trigger, pending, skip
    reason: str = ""
    event: dict | None = None


def detect(ticker: str, px: pd.DataFrame, accepted: datetime, now: datetime) -> Detection:
    """Is this filing a triggered, finished reaction? Uses only bars complete at ``now``."""
    px = complete_bars(px, now)
    dates = pd.DatetimeIndex(px["timestamp"])
    if len(px) < MIN_BARS:
        return Detection("skip", "not enough price history")
    if pt.classify(accepted, dates) is None:
        return Detection("skip", "filed during the session")
    r = pt.reaction_index(dates, accepted)
    if r is None:
        return Detection("pending", "reaction session not complete yet")
    if r < 15:
        return Detection("skip", "not enough price history")
    react = float(px["close"].iloc[r] / px["close"].iloc[r - 1] - 1)
    if abs(react) < pt.REACTION_THRESHOLD:
        return Detection("skip", f"reaction {react:+.1%} is below the threshold")
    from .indicators import atr
    a = float(atr(px.iloc[: r + 1], pt.ATR_PERIOD).iloc[-1])
    return Detection("trigger", "", {
        "ticker": ticker, "accepted": accepted.isoformat(), "react_date": str(dates[r].date()), "reaction": react,
        "direction": "BUY" if react > 0 else "SELL", "atr": a, "reaction_close": float(px["close"].iloc[r]),
    })


def recorded_keys(events: list[dict]) -> set[tuple[str, str]]:
    keys = set()
    for e in events:
        if e["kind"] == TRIGGER_KIND:
            try:
                d = json.loads(e["detail"])
                keys.add((d["ticker"], d["accepted"]))
            except (ValueError, KeyError, TypeError):
                continue
    return keys


def scan(journal: Any, now: datetime, filings: dict[str, list[dict]], get_prices: Callable[[str], pd.DataFrame]) -> tuple[list[dict], dict[str, int]]:
    """Record newly triggered events. ``filings`` maps ticker to its recent earnings filings."""
    seen = recorded_keys(journal.events())
    new: list[dict] = []
    stats: dict[str, int] = {}
    for ticker, items in filings.items():
        for f in items:
            key = (ticker, f["accepted"].isoformat())
            if key in seen:
                continue
            try:
                d = detect(ticker, get_prices(ticker), f["accepted"], now)
            except Exception as exc:  # type name only
                stats[f"error {type(exc).__name__}"] = stats.get(f"error {type(exc).__name__}", 0) + 1
                continue
            stats[d.status if d.status != "skip" else "skipped"] = stats.get(d.status if d.status != "skip" else "skipped", 0) + 1
            if d.status == "trigger":
                event = {**d.event, "url": f.get("url")}
                journal.record_event(now.isoformat(), TRIGGER_KIND, json.dumps(event))
                new.append(event)
                seen.add(key)
    return new, stats


def is_backlog(recorded_ts: str, react_date: str) -> bool:
    """True if the event was recorded after its entry open (13:30 UTC on the next business day).

    Such an event could not have been acted on in real time and any read of it would be written with the
    price move already known, so it is kept on record but left out of the forward measure and cannot be read.
    """
    entry_open = (pd.Timestamp(react_date) + pd.offsets.BDay(1)).tz_localize("UTC") + pd.Timedelta(hours=13, minutes=30)
    return pd.Timestamp(recorded_ts) >= entry_open


# ---- the assistant's read -------------------------------------------------


def latest_unread(events: list[dict], ticker: str) -> dict | None:
    read = set()
    for e in events:
        if e["kind"] == READ_KIND:
            try:
                d = json.loads(e["detail"])
                read.add((d["ticker"], d["accepted"]))
            except (ValueError, KeyError, TypeError):
                continue
    best = None
    for e in events:
        if e["kind"] != TRIGGER_KIND:
            continue
        try:
            d = json.loads(e["detail"])
        except ValueError:
            continue
        if d["ticker"] == ticker.upper() and (d["ticker"], d["accepted"]) not in read:
            best = {**d, "backlog": is_backlog(e["ts"], d["react_date"])}
    return best


def record_read(journal: Any, now: datetime, ticker: str, direction: str, confidence: str, reasons: list[str]) -> str:
    if direction not in DIRECTIONS or confidence not in CONFIDENCES or not reasons:
        return "REFUSED: direction, confidence and at least one reason are required"
    ev = latest_unread(journal.events(), ticker)
    if ev is None:
        return f"REFUSED: no unread triggered event for {ticker.upper()}"
    if ev["backlog"]:
        return f"REFUSED: the {ticker.upper()} event was detected after its entry open, so a read now would not be clean"
    journal.record_event(now.isoformat(), READ_KIND, json.dumps({
        "ticker": ev["ticker"], "accepted": ev["accepted"], "direction": direction, "confidence": confidence, "reasons": reasons[:5]}))
    return f"Recorded: {ev['ticker']} release of {ev['accepted'][:10]} read as {direction} ({confidence})"


# ---- measuring ------------------------------------------------------------


def measure(events: list[dict], get_prices: Callable[[str], pd.DataFrame], spy: pd.DataFrame) -> dict[str, Any]:
    triggers, reads = [], {}
    backlog = 0
    for e in events:
        try:
            d = json.loads(e["detail"])
        except (ValueError, TypeError):
            continue
        if e["kind"] == TRIGGER_KIND:
            if is_backlog(e["ts"], d["react_date"]):
                backlog += 1
                continue
            triggers.append(d)
        elif e["kind"] == READ_KIND:
            reads[(d["ticker"], d["accepted"])] = d
    finished: list[tuple[pt.EventResult, dict | None]] = []
    pending = 0
    for d in triggers:
        out = pt.evaluate_event(d["ticker"], get_prices(d["ticker"]), spy, datetime.fromisoformat(d["accepted"]))
        if isinstance(out, str):
            pending += 1
            continue
        finished.append((out, reads.get((d["ticker"], d["accepted"]))))
    return {"finished": finished, "pending": pending, "triggered": len(triggers), "backlog": backlog}


def format_measure(m: dict[str, Any]) -> str:
    pct = lambda v: "n/a" if v is None or v != v else f"{v * 100:+.2f}%"
    fin = m["finished"]
    results = [r for r, _ in fin]
    lines = [f"Forward earnings watch: {m['triggered']} triggered events recorded, {len(fin)} finished (n={len(fin)}), {m['pending']} still running"]
    if results:
        s = pt.summarise(results)
        lines.append(f"  PRIMARY market-adjusted gross per trade: mean {pct(s['mean'])}, SE {pct(s['se_cluster'])}, z {s['z']:+.2f}  -> {pt.verdict(s)}")
        for name, sc in pt.SCENARIOS.items():
            mean, se = pt.mean_se([pt.net_pct(r, sc) for r in results])
            lines.append(f"  Traded, {name}: mean {pct(mean)}, SE {pct(se)}")
        backed, other = [], []
        for r, rd in fin:
            agrees = rd is not None and rd["confidence"] in ("MEDIUM", "HIGH") and (
                (r.direction == 1 and rd["direction"] == "BULLISH") or (r.direction == -1 and rd["direction"] == "BEARISH"))
            (backed if agrees else other).append(r)
        for name, rs in (("read backed the move", backed), ("read did not back it, or no read", other)):
            s2 = pt.summarise(rs) if rs else None
            lines.append(f"  {name}: n={len(rs)}" + (f", mean {pct(s2['mean'])}" if s2 else ""))
    if len(fin) < pt.MIN_EVENTS:
        lines.append(f"  Fewer than {pt.MIN_EVENTS} finished events: no conclusion, whatever the averages say.")
    if m.get("backlog"):
        lines.append(f"  {m['backlog']} event(s) were detected after their entry open: on record, left out of this measure.")
    lines.append("Not traded. Costs are the pre-registered scenarios, not IG's live prices.")
    return "\n".join(lines)


# ---- command line ---------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ig_swing.earnings_watch")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scan")
    sub.add_parser("report")
    rd = sub.add_parser("read")
    rd.add_argument("--ticker", required=True)
    rd.add_argument("--direction", required=True, choices=DIRECTIONS)
    rd.add_argument("--confidence", required=True, choices=CONFIDENCES)
    rd.add_argument("--reason", action="append", required=True)
    args = ap.parse_args(argv)

    from .journal import Journal
    journal = Journal()
    now = datetime.now(UTC)

    if args.cmd == "read":
        msg = record_read(journal, now, args.ticker.upper(), args.direction, args.confidence, args.reason)
        print(msg)
        return 1 if msg.startswith("REFUSED") else 0

    import requests

    from .edgar import SourceError, load_ticker_map, recent_filings
    from .stock_prices import fetch_prices

    session = requests.Session()
    cache: dict[str, pd.DataFrame] = {}

    def prices(sym: str, start_days: int = 150) -> pd.DataFrame:
        if sym not in cache:
            cache[sym] = fetch_prices(session, sym, start=(now - timedelta(days=start_days)).date())
        return cache[sym]

    try:
        if args.cmd == "scan":
            mapping = load_ticker_map(session)
            filings: dict[str, list[dict]] = {}
            notes = []
            for sym in pt.UNIVERSE:
                if sym not in mapping:
                    continue
                try:
                    items = recent_filings(session, mapping[sym], now - timedelta(days=LOOKBACK_DAYS))
                except SourceError as exc:
                    notes.append(f"{sym}: unavailable ({exc})")
                    continue
                if items:
                    filings[sym] = items
            new, stats = scan(journal, now, filings, prices)
            print(f"{sum(len(v) for v in filings.values())} recent earnings filings in {len(filings)} stocks. " + ", ".join(f"{k} {v}" for k, v in sorted(stats.items())))
            for e in new:
                late = " (BACKLOG: entry open already passed, not part of the forward test)" if is_backlog(now.isoformat(), e["react_date"]) else ""
                print(f"  NEW {e['direction']} {e['ticker']}: reacted {e['reaction']:+.1%} on {e['react_date']}{late}. Release: {e['url']}")
            for n in notes:
                print(f"  note: {n}")
            print("Nothing was traded. Read the release, then record it with the read command.")
        else:
            events = journal.events()
            spy = fetch_prices(session, pt.BENCHMARK, start=(now - timedelta(days=900)).date())
            print(format_measure(measure(events, lambda s: prices(s, 900), spy)))
    except Exception as exc:  # type name only: messages can carry sensitive detail
        print(f"FAILED: {type(exc).__name__}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
