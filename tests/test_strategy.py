"""Offline tests for ig_swing.strategy."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ig_swing.strategy import StrategyParams, compute_features, generate_signal, signal_at

P = StrategyParams()


def bars(closes, half_range=1.0):
    closes = list(closes)
    opens = [closes[0]] + closes[:-1]
    return pd.DataFrame({
        "timestamp": pd.date_range("2025-01-01", periods=len(closes), freq="D"),
        "open": opens,
        "high": [c + half_range for c in closes],
        "low": [c - half_range for c in closes],
        "close": closes,
    })


UP = bars(np.linspace(100, 200, 100))
DOWN = bars(np.linspace(200, 100, 100))
FLAT = bars([100.0] * 100)


def test_uptrend_new_high_is_a_long_breakout_with_atr_stop_and_2r_target():
    s = generate_signal(UP)
    assert s.direction == "BUY" and s.setup == "breakout"
    assert s.stop_distance == pytest.approx(P.stop_atr * s.atr)
    assert s.target_distance == pytest.approx(P.target_r * s.stop_distance)
    assert s.ref_price == pytest.approx(200.0)


def test_downtrend_new_low_is_a_short_breakout():
    s = generate_signal(DOWN)
    assert s.direction == "SELL" and s.setup == "breakout"


def test_no_trend_no_signal():
    assert generate_signal(FLAT) is None


def test_direction_switches_are_respected():
    assert generate_signal(DOWN, allow_short=False) is None
    assert generate_signal(UP, allow_long=False) is None


def test_not_enough_bars_means_no_signal():
    assert generate_signal(UP.head(P.min_bars - 1)) is None


def _forced_row(df, **values):
    f = compute_features(df, P)
    i = len(f) - 1
    for k, v in values.items():
        f.loc[i, k] = v
    return f, i


def test_long_pullback_to_the_fast_ema():
    f0 = compute_features(UP, P)
    ema_fast = f0["ema_fast"].iloc[-1]
    f, i = _forced_row(UP, hh=1e9, low=ema_fast - 1, close=ema_fast + 1, rsi=50.0)
    s = signal_at(f, i, P)
    assert s.direction == "BUY" and s.setup == "pullback"


def test_pullback_refused_when_rsi_is_stretched():
    f0 = compute_features(UP, P)
    ema_fast = f0["ema_fast"].iloc[-1]
    f, i = _forced_row(UP, hh=1e9, low=ema_fast - 1, close=ema_fast + 1, rsi=80.0)
    assert signal_at(f, i, P) is None


def test_short_pullback_to_the_fast_ema():
    f0 = compute_features(DOWN, P)
    ema_fast = f0["ema_fast"].iloc[-1]
    f, i = _forced_row(DOWN, ll=-1e9, high=ema_fast + 1, close=ema_fast - 1, rsi=50.0)
    s = signal_at(f, i, P)
    assert s.direction == "SELL" and s.setup == "pullback"


def test_signals_never_use_future_bars():
    rng = np.random.default_rng(7)
    closes = 100 + np.cumsum(rng.normal(0.4, 1.5, 300))
    df = bars(closes)
    full = compute_features(df, P)
    for i in range(P.min_bars, 300, 7):
        truncated = compute_features(df.iloc[: i + 1], P)
        assert signal_at(full, i, P) == signal_at(truncated, i, P)
