"""Technical engine: long and short swing signals on completed daily bars.

A signal is evaluated at the close of bar i and would be acted on at the next
open. Nothing here looks at bars after i.

Trend: price and the fast EMA both on the same side of the slow EMA.
Setups, in the trend direction only:
- breakout: close beyond the prior N-day high (long) or low (short).
- pullback: price touched near the fast EMA this bar and closed back on the
  trend side of it, with RSI not stretched.
Stop is a multiple of ATR and the target is a multiple of the stop distance.

These parameters are starting points, not tuned values. They must be tested on
history, and any change after seeing results moves the goalposts.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .indicators import atr, ema, rsi


@dataclass(frozen=True)
class StrategyParams:
    ema_fast: int = 20
    ema_slow: int = 50
    atr_period: int = 14
    rsi_period: int = 14
    stop_atr: float = 1.5
    target_r: float = 2.0
    breakout_lookback: int = 20
    pullback_atr: float = 0.5
    rsi_pullback_long_max: float = 60.0
    rsi_pullback_short_min: float = 40.0
    min_bars: int = 80  # warm-up before any signal


@dataclass(frozen=True)
class Signal:
    direction: str  # BUY or SELL
    setup: str      # breakout or pullback
    stop_distance: float
    target_distance: float
    ref_price: float
    atr: float
    bar_date: object
    reason: str


def compute_features(df: pd.DataFrame, p: StrategyParams = StrategyParams()) -> pd.DataFrame:
    f = df.reset_index(drop=True).copy()
    f["ema_fast"] = ema(f["close"], p.ema_fast)
    f["ema_slow"] = ema(f["close"], p.ema_slow)
    f["atr"] = atr(f, p.atr_period)
    f["rsi"] = rsi(f["close"], p.rsi_period)
    f["hh"] = f["high"].rolling(p.breakout_lookback).max().shift(1)  # prior N bars only
    f["ll"] = f["low"].rolling(p.breakout_lookback).min().shift(1)
    return f


def signal_at(f: pd.DataFrame, i: int, p: StrategyParams = StrategyParams(), allow_long: bool = True, allow_short: bool = True) -> Signal | None:
    if i < p.min_bars - 1 or i >= len(f):
        return None
    r = f.iloc[i]
    if r[["ema_fast", "ema_slow", "atr", "rsi", "hh", "ll"]].isna().any() or r["atr"] <= 0:
        return None

    stop = p.stop_atr * r["atr"]
    target = p.target_r * stop
    date = r["timestamp"] if "timestamp" in f.columns else i

    def make(direction: str, setup: str, why: str) -> Signal:
        return Signal(direction, setup, float(stop), float(target), float(r["close"]), float(r["atr"]), date, why)

    up = r["close"] > r["ema_slow"] and r["ema_fast"] > r["ema_slow"]
    down = r["close"] < r["ema_slow"] and r["ema_fast"] < r["ema_slow"]

    if up and allow_long:
        if r["close"] > r["hh"]:
            return make("BUY", "breakout", f"uptrend, close above prior {p.breakout_lookback}-day high")
        if r["low"] <= r["ema_fast"] + p.pullback_atr * r["atr"] and r["close"] > r["ema_fast"] and r["rsi"] <= p.rsi_pullback_long_max:
            return make("BUY", "pullback", "uptrend, pulled back to the fast EMA and closed above it")
    if down and allow_short:
        if r["close"] < r["ll"]:
            return make("SELL", "breakout", f"downtrend, close below prior {p.breakout_lookback}-day low")
        if r["high"] >= r["ema_fast"] - p.pullback_atr * r["atr"] and r["close"] < r["ema_fast"] and r["rsi"] >= p.rsi_pullback_short_min:
            return make("SELL", "pullback", "downtrend, rallied to the fast EMA and closed below it")
    return None


def generate_signal(df: pd.DataFrame, p: StrategyParams = StrategyParams(), allow_long: bool = True, allow_short: bool = True) -> Signal | None:
    """Signal on the latest completed bar, or None."""
    if len(df) < p.min_bars:
        return None
    f = compute_features(df, p)
    return signal_at(f, len(f) - 1, p, allow_long, allow_short)
