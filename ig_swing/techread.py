"""A compact technical read from the daily bars we already pull (about 30 clean bars). Mechanical, no judgement, no trade signal:
it is context for a view and is archived so the review can test later whether it helped.

The 50 and 200 day averages need 60 to 200 bars a market, which the weekly price allowance cannot pay for across the list, so they are
not here. Trend is judged against the 20-day average and its five-day slope.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from .indicators import atr, rsi


def tech_read(bars: pd.DataFrame, price: float) -> dict[str, Any] | None:
    """Trend, RSI, distance to the 20-day extremes (in ATRs) and the prior day's and week's levels. None if there are too few bars."""
    if bars is None or len(bars) < 25 or price <= 0:
        return None
    close = bars["close"].astype(float)
    sma = close.rolling(20).mean()
    sma_now, sma_then = float(sma.iloc[-1]), float(sma.iloc[-6])
    slope_pct = (sma_now / sma_then - 1) * 100 if sma_then else 0.0
    a = float(atr(bars, 14).iloc[-1])
    hi20, lo20 = float(bars["high"].tail(20).max()), float(bars["low"].tail(20).min())
    week = bars.tail(5)
    above = price > sma_now
    rising = slope_pct > 0
    trend = ("above a rising" if above and rising else "above a falling" if above else "below a rising" if rising else "below a falling") + " 20-day average"
    return {"trend": trend, "sma20": sma_now, "slope_pct": slope_pct, "rsi": float(rsi(close, 14).iloc[-1]),
            "to_hi20_atr": (hi20 - price) / a if a else None, "to_lo20_atr": (price - lo20) / a if a else None,
            "prev_high": float(bars["high"].iloc[-1]), "prev_low": float(bars["low"].iloc[-1]),
            "week_high": float(week["high"].max()), "week_low": float(week["low"].min()),
            "pivots": pivots(float(bars["high"].iloc[-1]), float(bars["low"].iloc[-1]), float(close.iloc[-1])),
            "pivots_5d": pivots(float(week["high"].max()), float(week["low"].min()), float(close.iloc[-1]))}


def format_tech(t: dict[str, Any] | None) -> str:
    if not t:
        return "technical read: not enough bars"
    if "trend" not in t:
        return "20-day detail not available" + format_long(t)
    def d(x):  # distance in ATRs, negative means beyond the extreme
        return "n/a" if x is None else f"{x:+.1f} ATR"
    return (f"{t['trend']} ({t['slope_pct']:+.2f}% in 5 days), RSI {t['rsi']:.0f}, {d(t['to_hi20_atr'])} below the 20-day high, {d(t['to_lo20_atr'])} above the 20-day low (a minus sign means beyond it), "
            f"prior day {t['prev_low']:.1f} to {t['prev_high']:.1f}, last 5 days {t['week_low']:.1f} to {t['week_high']:.1f}" + format_long(t) + format_levels(t))


def long_read(bars: pd.DataFrame) -> dict[str, Any] | None:
    """Distance of the latest close from the 50 and 200 day averages, in percent, from a longer series. None if under 60 bars (the 200 day
    figure is None under 200 bars)."""
    if bars is None or len(bars) < 60:
        return None
    close = bars["close"].astype(float)
    last = float(close.iloc[-1])
    sma50 = float(close.tail(50).mean())
    sma200 = float(close.tail(200).mean()) if len(close) >= 200 else None
    d50 = (last / sma50 - 1) * 100
    d200 = (last / sma200 - 1) * 100 if sma200 else None
    if d200 is None:
        label = "above the 50-day" if d50 > 0 else "below the 50-day"
    elif d50 > 0 and d200 > 0:
        label = "above the 50 and 200 day averages" + (" (50 over 200)" if sma50 > sma200 else " (50 still under 200)")
    elif d50 < 0 and d200 < 0:
        label = "below the 50 and 200 day averages" + (" (50 under 200)" if sma50 < sma200 else " (50 still over 200)")
    else:
        label = "above the 50-day, below the 200-day" if d50 > 0 else "below the 50-day, above the 200-day"
    return {"long_trend": label, "d50_pct": d50, "d200_pct": d200}


def format_long(t: dict[str, Any] | None) -> str:
    if not t or "long_trend" not in t:
        return ""
    d200 = "n/a" if t.get("d200_pct") is None else f"{t['d200_pct']:+.1f}%"
    return f"; longer view (Yahoo, approximate): {t['long_trend']}, {t['d50_pct']:+.1f}% from the 50-day, {d200} from the 200-day"


def pivots(high: float, low: float, close: float) -> dict[str, float]:
    """Classic pivot points from one period's high, low and close: the pivot, two supports and two resistances."""
    p = (high + low + close) / 3
    return {"P": p, "R1": 2 * p - low, "S1": 2 * p - high, "R2": p + (high - low), "S2": p - (high - low)}


def swing_levels(bars: pd.DataFrame, price: float, atr_value: float, wing: int = 3, per_side: int = 2) -> dict[str, list[tuple[float, int]]] | None:
    """Support and resistance from swing points: a swing high is a bar whose high is the highest of `wing` bars either side, a swing low the same
    for lows. Levels within 0.4 ATR of the cluster's lowest point are merged and the number of reactions is counted, so a level that has held four times
    outranks one that held once. Returns the nearest `per_side` below (support) and above (resistance) price as (level, touches), nearest first.
    Needs about 60 bars to mean anything."""
    if bars is None or len(bars) < 2 * wing + 10 or not atr_value or atr_value <= 0:
        return None
    hi, lo = bars["high"].astype(float).reset_index(drop=True), bars["low"].astype(float).reset_index(drop=True)
    points: list[float] = []
    for i in range(wing, len(bars) - wing):
        if hi[i] == hi[i - wing:i + wing + 1].max():
            points.append(float(hi[i]))
        if lo[i] == lo[i - wing:i + wing + 1].min():
            points.append(float(lo[i]))
    if not points:
        return {"support": [], "resistance": []}
    tol = 0.4 * atr_value
    clusters: list[list[float]] = []
    for pt in sorted(points):
        if clusters and pt - clusters[-1][0] <= tol:      # anchored to the cluster's first point, so a chain of nearby points cannot stretch it
            clusters[-1].append(pt)
        else:
            clusters.append([pt])
    levels = [(sum(c) / len(c), len(c)) for c in clusters]
    below = sorted((lv for lv in levels if lv[0] < price), key=lambda lv: price - lv[0])[:per_side]
    above = sorted((lv for lv in levels if lv[0] > price), key=lambda lv: lv[0] - price)[:per_side]
    return {"support": below, "resistance": above}


def format_levels(t: dict[str, Any]) -> str:
    out = ""
    pv, pv5 = t.get("pivots"), t.get("pivots_5d")
    if pv:
        out += f"; daily pivots S2 {pv['S2']:.1f} S1 {pv['S1']:.1f} P {pv['P']:.1f} R1 {pv['R1']:.1f} R2 {pv['R2']:.1f}"
    if pv5:
        out += f"; 5-day pivots S1 {pv5['S1']:.1f} P {pv5['P']:.1f} R1 {pv5['R1']:.1f}"
    sw = t.get("swing")
    if sw:
        def fmt(lst):
            return ", ".join(f"{lv:.1f} ({n}x)" for lv, n in lst) or "none found"
        out += f"; swing support {fmt(sw['support'])}; swing resistance {fmt(sw['resistance'])}"
    return out
