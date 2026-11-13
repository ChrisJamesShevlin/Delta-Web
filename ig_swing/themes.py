"""Basket awareness: which markets are really the same bet, and whether correlated markets back an idea.

Written 2026-11-13 (user request). Nothing here blocks an order. Overlap is a warning, and confirmation is recorded as a feature on every idea so
`challenge review` can later show whether confirmed setups do better than lone ones. The groups are fixed judgements, not measured correlations.

A market's exposure to a theme is signed: +1 means it gains when the theme rises. A currency pair is exposed to both of its currencies (a long
EUR/USD is long EUR and short USD). Indices and shares are exposed to equities, their region, and for tech names the tech theme.
"""
from __future__ import annotations

import re
from typing import Any

REGION = {
    "US Tech 100": ("US equities", "tech"), "US 500": ("US equities",), "Wall Street": ("US equities",), "US Russell 2000": ("US equities",),
    "Germany 40": ("Europe equities",), "France 40": ("Europe equities",), "FTSE 100": ("Europe equities",),
    "Japan 225": ("Japan equities",),
    "Apple": ("US equities", "tech"), "Microsoft": ("US equities", "tech"), "NVIDIA": ("US equities", "tech"), "Amazon": ("US equities", "tech"),
    "Alphabet": ("US equities", "tech"), "Meta": ("US equities", "tech"), "Tesla": ("US equities",), "Netflix": ("US equities", "tech"),
    "Intel": ("US equities", "tech"),
}
OTHER = {"Gold": ("precious metals",), "Silver": ("precious metals",), "Crude oil": ("oil",), "Brent crude": ("oil",), "Natural gas": ("natural gas",)}
SIGNAL_DIRECTIONS = ("BUY", "SELL")


def canonical(market: str) -> str:
    """Map IG's own name for a market ("Spot Gold", "NVIDIA Corp (24 Hours)") to the name used in the briefing list, when it contains one."""
    if market in REGION or market in OTHER:
        return market
    for key in sorted((*REGION, *OTHER), key=len, reverse=True):
        if re.search(rf"\b{re.escape(key)}\b", market, flags=re.I):
            return key
    return market


def exposure(market: str, direction: str) -> dict[str, int]:
    """Signed theme exposure of a position. Unknown markets return {} (no overlap can be claimed for them)."""
    sign = 1 if direction.upper() == "BUY" else -1
    market = canonical(market)
    if "/" in market and len(market.split("/")) == 2:
        base, quote = (c.strip().upper() for c in market.split("/"))
        if len(base) == 3 and len(quote) == 3:
            return {base: sign, quote: -sign}
    if market in REGION:
        return {"equities": sign, **{t: sign for t in REGION[market]}}
    if market in OTHER:
        return {t: sign for t in OTHER[market]}
    return {}


def _relation(a: dict[str, int], b: dict[str, int]) -> int:
    """+1 if the two share at least one theme with the same sign and none with the opposite sign, -1 for the reverse, 0 otherwise."""
    shared = [(a[t], b[t]) for t in a if t in b]
    same = any(x == y for x, y in shared)
    opposite = any(x != y for x, y in shared)
    if same and not opposite:
        return 1
    if opposite and not same:
        return -1
    return 0


def shared_themes(a: dict[str, int], b: dict[str, int]) -> list[str]:
    return sorted(t for t in a if t in b)


def overlaps(market: str, direction: str, open_positions: list[tuple[str, str]]) -> list[str]:
    """Warnings for a new order against open positions: the same bet again, or an offsetting one. Empty when independent."""
    mine = exposure(market, direction)
    out: list[str] = []
    for m2, d2 in open_positions:
        rel = _relation(mine, exposure(m2, d2))
        if rel == 0:
            continue
        themes = ", ".join(f"{'long' if mine[t] > 0 else 'short'} {t}" for t in shared_themes(mine, exposure(m2, d2)))
        out.append(f"{'same bet as' if rel > 0 else 'offsets'} the open {d2.upper()} {m2} ({themes})")
    return out


def _signal(row: dict[str, Any]) -> int | None:
    """Mechanical trend signal from a briefing snapshot: price above its 20-day average is +1, below is -1."""
    price, sma = row.get("price"), row.get("sma20")
    if price is None or sma is None or price == sma:
        return None
    return 1 if price > sma else -1


CONFIRM_MIN_MARKETS = 2
CONFIRMED_SHARE, CONTRADICTED_SHARE = 0.6, 0.4


def confirmation(market: str, direction: str, snapshots: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """Do correlated markets point the same way? Uses the latest briefing snapshot of every other market that shares a theme and has a 20-day
    average (shares have none). Labels, fixed in advance: confirmed (at least 60% agree), contradicted (40% or fewer), mixed, too_few (under 2 markets)."""
    if not snapshots:
        return None
    mine = exposure(market, direction)
    if not mine:
        return None
    agree: list[str] = []
    disagree: list[str] = []
    for name, row in snapshots.items():
        if canonical(name) == canonical(market):
            continue
        sig = _signal(row)
        if sig is None:
            continue
        for d2 in SIGNAL_DIRECTIONS:
            if _relation(mine, exposure(name, d2)) == 1:
                (agree if (1 if d2 == "BUY" else -1) == sig else disagree).append(name)
                break
    n = len(agree) + len(disagree)
    share = len(agree) / n if n else None
    if n < CONFIRM_MIN_MARKETS:
        label = "too_few"
    elif share >= CONFIRMED_SHARE:
        label = "confirmed"
    elif share <= CONTRADICTED_SHARE:
        label = "contradicted"
    else:
        label = "mixed"
    return {"label": label, "n": n, "agree": sorted(agree), "disagree": sorted(disagree), "share": share}


def describe_confirmation(c: dict[str, Any] | None) -> str:
    if c is None:
        return "no briefing snapshot to compare with (run the briefing first)"
    if c["n"] == 0:
        return "no correlated markets with a trend signal in the latest briefing"
    text = f"{c['label']}: {len(c['agree'])} of {c['n']} correlated markets point the same way"
    if c["agree"]:
        text += f" ({', '.join(c['agree'])})"
    if c["disagree"]:
        text += f"; against: {', '.join(c['disagree'])}"
    return text
