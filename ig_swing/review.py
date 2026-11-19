"""Position reviews: the exit line stored as a number, and a written re-decision of every open trade at every check.

Added 2026-11-19 after the crude short sat back above its own exit line (about 8316) while the checks compared it with the
current 20-day range, which had reset, and an Iran headline went unweighed. Two parts:

1. Exit line: the price at which the reason for the trade is broken (for a breakout, the old edge of the range). Stored as an
   `exit_line` event per deal, so a check compares the price with a number and not with my memory.
2. Review: at every health or market check each open position gets a `position_review` event: exit line status, news since entry,
   whether the reason for the trade still holds, what is due before the next check, "would I open this trade now, at this price?",
   and a verdict. Inconsistent answers are refused (a hold when I would not open it now, or when the thesis is broken).

Journal only: nothing here places, closes or changes an order.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

THESIS = ("intact", "weakened", "broken")
WOULD_OPEN = ("yes", "smaller", "no")
VERDICTS = ("hold", "tighten", "close")
NEAR_ATR = 0.25          # within this many daily ranges of the exit line counts as near
REVIEW_MAX_AGE = timedelta(hours=3)


def _parse(e: dict) -> dict | None:
    try:
        return json.loads(e["detail"])
    except (ValueError, TypeError, KeyError):
        return None


def exit_lines(events: list[dict]) -> dict[str, dict]:
    """The latest exit line per deal id."""
    out: dict[str, dict] = {}
    for e in events:
        if e.get("kind") == "exit_line":
            d = _parse(e)
            if d and d.get("deal_id") and d.get("level") is not None:
                out[d["deal_id"]] = {**d, "ts": e.get("ts")}
    return out


def latest_reviews(events: list[dict]) -> dict[str, dict]:
    """The latest review per deal id."""
    out: dict[str, dict] = {}
    for e in events:
        if e.get("kind") == "position_review":
            d = _parse(e)
            if d and d.get("deal_id"):
                out[d["deal_id"]] = {**d, "ts": e.get("ts")}
    return out


def exit_line_record(deal_id: str, direction: str, level: float, why: str) -> dict:
    """A SELL is broken when price goes back ABOVE the line, a BUY when it goes back BELOW it."""
    if level <= 0:
        raise ValueError("the exit line must be a price above zero")
    if not why.strip():
        raise ValueError("say what the exit line is (for example: the old 20-day low the breakdown went through)")
    return {"deal_id": deal_id, "direction": direction.upper(), "level": float(level), "side": "above" if direction.upper() == "SELL" else "below",
            "why": why.strip()[:200]}


def exit_status(direction: str, price: float | None, level: float | None, atr: float | None) -> tuple[str, str]:
    """(status, text). Status is 'none', 'unknown', 'crossed', 'near' or 'clear'. Distance is positive while the trade is on the right side."""
    if level is None:
        return "none", "NO EXIT LINE RECORDED: set one with `manage exitline`"
    if price is None:
        return "unknown", f"exit line {level:g}, no live price"
    room = (level - price) if direction.upper() == "SELL" else (price - level)
    side = "above" if direction.upper() == "SELL" else "below"
    if room <= 0:
        return "crossed", f"EXIT LINE CROSSED: price {price:g} is {abs(room):.1f} points {side} the exit line {level:g} (review now; the rule closes it on a daily close there)"
    if atr and room <= NEAR_ATR * atr:
        return "near", f"NEAR THE EXIT LINE: {room:.1f} points from {level:g} ({room / atr:.2f} daily ranges)"
    return "clear", f"exit line {level:g}, {room:.1f} points away" + (f" ({room / atr:.1f} daily ranges)" if atr else "")


def validate_review(thesis: str, would_open: str, verdict: str, exit_state: str, news: str, events_due: str, why: str,
                    sources: list[str], override: str = "") -> list[str]:
    """Reasons to refuse a review. Empty means it is consistent and can be recorded."""
    bad = []
    if thesis not in THESIS:
        bad.append(f"thesis must be one of {THESIS}")
    if would_open not in WOULD_OPEN:
        bad.append(f"would-open must be one of {WOULD_OPEN}")
    if verdict not in VERDICTS:
        bad.append(f"verdict must be one of {VERDICTS}")
    for name, val in (("news", news), ("events", events_due), ("why", why)):
        if not val.strip():
            bad.append(f"--{name} is empty: say what you found, even if it is 'nothing new'")
    if not [s for s in sources if s.strip()]:
        bad.append("at least one --source is needed (a headline feed, a web page, the IG calendar)")
    if (would_open == "no" or thesis == "broken") and verdict != "close":
        bad.append("you would not open this trade now, or its reason is broken: the verdict must be close")
    if (would_open == "smaller" or thesis == "weakened") and verdict == "hold":
        bad.append("a weakened trade, or one you would only open smaller, must be tightened or closed, not held as it is")
    if exit_state == "crossed" and verdict == "hold" and not override.strip():
        bad.append("the price is through the exit line: close, tighten, or give --override with the reason to wait (for example the daily close)")
    if exit_state == "none" and verdict == "hold":
        bad.append("no exit line is recorded for this position: set one with `manage exitline` first")
    return bad


def review_record(deal_id: str, market: str, r_now: float | None, exit_text: str, thesis: str, would_open: str, verdict: str,
                  news: str, events_due: str, why: str, sources: list[str], override: str = "") -> dict:
    return {"deal_id": deal_id, "market": market, "r": None if r_now is None else round(r_now, 3), "exit_line": exit_text[:200],
            "news": news.strip()[:500], "thesis": thesis, "events": events_due.strip()[:300], "would_open": would_open, "verdict": verdict,
            "why": why.strip()[:400], "sources": [s.strip()[:300] for s in sources if s.strip()][:8], "override": override.strip()[:200]}


def flags(direction: str, deal_id: str, price: float | None, lines: dict, reviews: dict, atr: float | None, now: datetime) -> list[str]:
    """What a check must deal with for one position, before anything else."""
    out = []
    line = lines.get(deal_id)
    state, text = exit_status(direction, price, line["level"] if line else None, atr)
    if state in ("none", "crossed", "near"):
        out.append(text)
    rv = reviews.get(deal_id)
    if rv is None:
        out.append("NOT REVIEWED YET: run `manage review` (news since entry, thesis, events due, would I open it now)")
    else:
        try:
            age = now - datetime.fromisoformat(rv["ts"])
        except (TypeError, ValueError):
            age = None
        if age is not None and age > REVIEW_MAX_AGE:
            out.append(f"REVIEW DUE: last review {age.total_seconds() / 3600:.1f} hours ago")
        if rv.get("verdict") == "close":
            out.append("LAST REVIEW SAID CLOSE but the position is still open")
    return out


def atr_by_epic(snapshots: dict[str, dict]) -> dict[str, float]:
    """ATR in points from the newest archived snapshot of each market (no price allowance used)."""
    return {s["epic"]: float(s["atr"]) for s in snapshots.values() if s.get("epic") and s.get("atr")}


def review_line(rv: dict | None, now: datetime) -> str:
    if not rv:
        return "not reviewed yet"
    try:
        age = f"{(now - datetime.fromisoformat(rv['ts'])).total_seconds() / 60:.0f} min ago"
    except (TypeError, ValueError, KeyError):
        age = "time unknown"
    return f"last review {age}: {rv.get('verdict', '?').upper()}, would open now: {rv.get('would_open', '?')}, reason for the trade: {rv.get('thesis', '?')}. {rv.get('why', '')}"


def summary_rows(positions: list, lines: dict, reviews: dict, atrs: dict, now: datetime) -> list[dict[str, Any]]:
    """One row per open position for the report pages."""
    rows = []
    for p in positions:
        price = p.offer if p.direction == "SELL" else p.bid
        line = lines.get(p.deal_id)
        state, text = exit_status(p.direction, price, line["level"] if line else None, atrs.get(p.epic))
        rows.append({"market": p.instrument_name, "direction": p.direction, "exit_state": state, "exit_text": text,
                     "review": reviews.get(p.deal_id), "review_text": review_line(reviews.get(p.deal_id), now),
                     "flags": flags(p.direction, p.deal_id, price, lines, reviews, atrs.get(p.epic), now)})
    return rows


def summary_html(rows: list[dict], now: datetime) -> str:
    import html
    if not rows:
        return '<div class=sub>No open positions.</div>'
    out = ['<table><tr><th>Position</th><th>Exit line</th><th>Review</th></tr>']
    for r in rows:
        col = "#ef6b6b" if r["flags"] else "#4ec98a"
        rv = r["review"] or {}
        detail = "" if not rv else (f'<div class=sub>News: {html.escape(rv.get("news", ""))}</div><div class=sub>Due: {html.escape(rv.get("events", ""))}</div>'
                                    + (f'<div class=sub>Override: {html.escape(rv["override"])}</div>' if rv.get("override") else ""))
        out.append(f'<tr><td><b>{r["direction"]} {html.escape(r["market"])}</b></td><td style="color:{col if r["exit_state"] in ("crossed", "near", "none") else "inherit"}">{html.escape(r["exit_text"])}</td>'
                   f'<td>{html.escape(r["review_text"])}{detail}' + "".join(f'<div style="color:{col}">{html.escape(f)}</div>' for f in r["flags"]) + '</td></tr>')
    return "".join(out) + "</table>"
