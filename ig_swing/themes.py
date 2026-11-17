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
OTHER = {"Gold": ("precious metals",), "Silver": ("precious metals",), "Crude oil": ("oil",), "Brent crude": ("oil",), "Natural gas": ("natural gas",),
         "UK Gilts ETF": ("bonds",), "US Treasury ETF": ("bonds",)}
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


# ---- the book as a whole (2026-11-17): information only, nothing here blocks an order ----

METAL_THEMES = ("precious metals",)


def stop_risk_gbp(pos: Any) -> float:
    """What the position would lose if its stop is hit now. Zero when the stop is at breakeven or locks in a profit, or there is no stop."""
    if getattr(pos, "stop_level", None) is None:
        return 0.0
    lost = (pos.open_level - pos.stop_level) if pos.direction == "BUY" else (pos.stop_level - pos.open_level)
    return max(0.0, lost * pos.size)


def rows_from_positions(positions: list[Any]) -> list[dict[str, Any]]:
    return [{"market": canonical(p.instrument_name), "direction": p.direction, "risk_gbp": stop_risk_gbp(p)} for p in positions]


def _dollar_link(exp: dict[str, int]) -> int:
    """Assumed, not measured: the dollar-and-yields group. Long USD counts +1; precious metals move against the dollar, so a long metal counts -1."""
    return exp.get("USD", 0) + sum(-exp[t] for t in METAL_THEMES if t in exp)


def _word(theme: str, sign: int) -> str:
    return f"{'long' if sign > 0 else 'short'} {theme}"


def _same_sign_groups(rows: list[dict[str, Any]]) -> dict[tuple[str, int], list[dict[str, Any]]]:
    groups: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for r in rows:
        for t, sg in exposure(r["market"], r["direction"]).items():
            if t in ("equities",):
                continue
            groups.setdefault((t, sg), []).append(r)
    link: dict[int, list[dict[str, Any]]] = {}
    for r in rows:
        v = _dollar_link(exposure(r["market"], r["direction"]))
        if v:
            link.setdefault(1 if v > 0 else -1, []).append(r)
    for sg, members in link.items():
        groups[("dollar-and-yields group (assumed link)", sg)] = members
    return groups


def book_view(rows: list[dict[str, Any]], equity: float) -> list[str]:
    """The open book grouped by driver: for each driver held by two or more positions the same way, the positions, the combined stop risk, and what
    happens if they all move against us together. Single-position drivers are not listed."""
    groups = {k: v for k, v in _same_sign_groups(rows).items() if len(v) >= 2}
    total = sum(r["risk_gbp"] for r in rows)
    lines = [f"Book view: {len(rows)} positions, stop risk £{total:.2f} ({(100 * total / equity) if equity else 0:.0f}% of equity if every stop is hit)."]
    if not groups:
        lines.append("  No driver is held by two or more positions the same way.")
        return lines
    for (theme, sg), members in sorted(groups.items(), key=lambda kv: -sum(m["risk_gbp"] for m in kv[1])):
        risk = sum(m["risk_gbp"] for m in members)
        names = ", ".join(f"{m['direction'].lower()} {m['market']}" for m in members)
        label = theme if theme.startswith("dollar") else _word(theme, sg)
        if theme.startswith("dollar"):
            label += ", net " + ("long dollar" if sg > 0 else "short dollar")
        lines.append(f"  {label}: {len(members)} positions ({names}); stop risk £{risk:.2f} ({(100 * risk / equity) if equity else 0:.0f}% of equity), one bet if they all turn together")
    return lines


def driver_stack(market: str, direction: str, new_risk_gbp: float, rows: list[dict[str, Any]], equity: float) -> dict[str, Any]:
    """Structured version of the driver check, for recording on an idea: the book's stop risk before the trade, and for each driver the new
    trade shares the same way with open positions, how many positions and how much combined stop risk there would be with it added."""
    mine = exposure(market, direction)
    new_row = {"market": canonical(market), "direction": direction.upper(), "risk_gbp": new_risk_gbp}
    pct = lambda x: (100 * x / equity) if equity else 0.0
    book = sum(r["risk_gbp"] for r in rows)
    out: dict[str, Any] = {"book_risk_gbp": round(book, 2), "book_risk_pct": round(pct(book), 1), "new_risk_gbp": round(new_risk_gbp, 2), "drivers": []}
    if not mine:
        return out
    before = _same_sign_groups(rows)
    for key, members in _same_sign_groups(rows + [new_row]).items():
        if new_row not in members or key not in before:
            continue
        risk = sum(m["risk_gbp"] for m in members)
        out["drivers"].append({"driver": key[0], "sign": key[1], "positions": len(members), "risk_gbp": round(risk, 2), "risk_pct": round(pct(risk), 1),
                               "with": [f"{m['direction'].lower()} {m['market']}" for m in members if m is not new_row]})
    return out


def stack_bucket(features: dict[str, Any] | None) -> str:
    """For the report: 'stacked' if the idea shared a driver with an open position when written, 'independent' if not, 'not recorded' for older ideas."""
    stack = (features or {}).get("stack")
    if not stack:
        return "not recorded"
    return "stacked" if stack.get("drivers") else "independent"


def pre_trade_exposure(market: str, direction: str, new_risk_gbp: float, rows: list[dict[str, Any]], equity: float) -> list[str]:
    """Drivers the new trade would stack on: for each driver it shares the same way with open positions, the count and the combined stop risk with it added."""
    lines = []
    for d in driver_stack(market, direction, new_risk_gbp, rows, equity)["drivers"]:
        label = f"{d['driver']}, net {'long' if d['sign'] > 0 else 'short'} dollar" if d["driver"].startswith("dollar") else _word(d["driver"], d["sign"])
        lines.append(f"driver check: {label} would be position {d['positions']} of the same bet (with {', '.join(d['with'])}); "
                     f"combined stop risk £{d['risk_gbp']:.2f} ({d['risk_pct']:.0f}% of equity)")
    return lines
