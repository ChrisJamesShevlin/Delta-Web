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
            "week_high": float(week["high"].max()), "week_low": float(week["low"].min())}


def format_tech(t: dict[str, Any] | None) -> str:
    if not t:
        return "technical read: not enough bars"
    def d(x):  # distance in ATRs, negative means beyond the extreme
        return "n/a" if x is None else f"{x:+.1f} ATR"
    return (f"{t['trend']} ({t['slope_pct']:+.2f}% in 5 days), RSI {t['rsi']:.0f}, {d(t['to_hi20_atr'])} below the 20-day high, {d(t['to_lo20_atr'])} above the 20-day low (a minus sign means beyond it), "
            f"prior day {t['prev_low']:.1f} to {t['prev_high']:.1f}, last 5 days {t['week_low']:.1f} to {t['week_high']:.1f}")
