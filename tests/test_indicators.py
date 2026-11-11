"""Offline tests for ig_swing.indicators."""

from __future__ import annotations

import pandas as pd
import pytest

from ig_swing.indicators import atr, ema, rsi, true_range, wilder


def test_ema_of_a_constant_is_the_constant():
    assert ema(pd.Series([5.0] * 30), 10).iloc[-1] == pytest.approx(5.0)


def test_wilder_matches_hand_calculation():
    # alpha = 1/2: 1, then 0.5*2+0.5*1 = 1.5, then 0.5*3+0.5*1.5 = 2.25
    out = wilder(pd.Series([1.0, 2.0, 3.0]), 2)
    assert list(out) == pytest.approx([1.0, 1.5, 2.25])


def test_true_range_uses_gaps_not_just_the_range():
    df = pd.DataFrame({"high": [10.0, 20.0], "low": [9.0, 18.0], "close": [9.5, 19.0]})
    tr = true_range(df)
    assert tr.iloc[0] == 1.0                 # first bar: high - low only
    assert tr.iloc[1] == pytest.approx(10.5)  # |20 - 9.5| beats the 2.0 range


def test_atr_of_constant_range_bars_is_that_range():
    n = 60
    df = pd.DataFrame({"high": [101.0] * n, "low": [99.0] * n, "close": [100.0] * n})
    assert atr(df, 14).iloc[-1] == pytest.approx(2.0)


def test_rsi_extremes_and_balance():
    assert rsi(pd.Series(range(1, 60)), 14).iloc[-1] == 100.0           # only gains
    assert rsi(pd.Series(range(60, 1, -1)), 14).iloc[-1] == pytest.approx(0.0, abs=1e-6)
    alternating = pd.Series([100.0 + (1 if i % 2 else 0) for i in range(200)])
    assert rsi(alternating, 14).iloc[-1] == pytest.approx(50.0, abs=2.0)


def test_indicators_are_causal():
    base = pd.Series([float(x) for x in range(1, 80)])
    changed = base.copy()
    changed.iloc[-1] = 9999.0
    assert ema(base, 20).iloc[-2] == ema(changed, 20).iloc[-2]
    assert rsi(base, 14).iloc[-2] == rsi(changed, 14).iloc[-2]
