"""Indicators on daily bars (pandas). All are causal: a value at bar i uses only bars up to i.

Wilder smoothing is an EWM with alpha 1/period and no SMA seed, so the first
few periods (about 3x the period) are less reliable. Strategies must wait for
a warm-up before trusting them.
"""

from __future__ import annotations

import pandas as pd


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def wilder(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(alpha=1 / period, adjust=False).mean()


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    return pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return wilder(true_range(df), period)


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = wilder(delta.clip(lower=0), period)
    loss = wilder((-delta).clip(lower=0), period)
    out = 100 - 100 / (1 + gain / loss)
    return out.where(loss != 0, 100.0)  # no losses at all means RSI 100
