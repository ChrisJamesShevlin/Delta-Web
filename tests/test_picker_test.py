"""Offline tests for ig_swing.picker_test (Test A of the stock picker). Every outcome is checkable by hand."""

from __future__ import annotations

import math
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from ig_swing import picker_test as pk
from ig_swing.picker_test import Prepared

UTC = timezone.utc


def flat(n=60, sma=90.0):
    """Flat 100 stock whose SMA50 sits at 90, so the technical exit never fires unless a test moves it."""
    d = pd.bdate_range("2026-01-05", periods=n)
    z = np.full(n, 100.0)
    return Prepared(d, z.copy(), z + 1, z - 1, z.copy(), np.full(n, sma), np.full(n, 80.0), z.copy(), np.full(n, 5.0))


E, SD = 10, 10.0        # entry session 10, stop distance 10: stop 90, target 130 (3R)


def test_target_pays_three_r():
    p = flat()
    p.high[15] = 135.0
    out = pk.simulate_long(p, E, SD)
    assert out == {"idx": 15, "price": 130.0, "reason": "target"}


def test_stop_and_a_tie_with_the_target_go_to_the_stop():
    p = flat()
    p.low[12] = 80.0
    assert pk.simulate_long(p, E, SD) == {"idx": 12, "price": 90.0, "reason": "stop"}
    p.high[12] = 140.0
    assert pk.simulate_long(p, E, SD)["reason"] == "stop"


def test_gaps_fill_at_the_open():
    p = flat()
    p.open[12], p.low[12] = 70.0, 69.0
    assert pk.simulate_long(p, E, SD) == {"idx": 12, "price": 70.0, "reason": "stop_gap"}
    q = flat()
    q.open[12], q.high[12] = 140.0, 141.0
    assert pk.simulate_long(q, E, SD) == {"idx": 12, "price": 140.0, "reason": "target_gap"}


def test_technical_exit_happens_at_the_next_open_not_the_signal_close():
    p = flat()
    p.sma_fast[14] = 105.0                          # close 100 is below SMA50 at the close of session 14
    p.open[15] = 99.0
    assert pk.simulate_long(p, E, SD) == {"idx": 15, "price": 99.0, "reason": "technical"}
    q = flat()
    q.sma_fast[E] = 105.0                           # it can fire on the entry session's own close too
    assert pk.simulate_long(q, E, SD)["idx"] == E + 1


def test_time_exit_is_the_close_of_the_40th_session():
    out = pk.simulate_long(flat(), E, SD)
    assert out == {"idx": E + 39, "price": 100.0, "reason": "time"}
    short = flat(n=30)                              # data ends first
    assert pk.simulate_long(short, E, SD)["reason"] == "end_of_data"


def test_cluster_robust_difference_by_hand():
    d, se = pk.cluster_diff([3.0, 1.0, 5.0, 2.0], [1, 0, 1, 0], ["a", "a", "b", "b"])
    assert d == pytest.approx(2.5) and se == pytest.approx(math.sqrt(0.125))
    nan_d, nan_se = pk.cluster_diff([1.0, 2.0, 3.0], [1, 1, 1], ["a", "b", "c"])        # no unconfirmed group: nothing to compare
    assert math.isnan(nan_d) and math.isnan(nan_se)


# ---- a whole event ----------------------------------------------------------


R = 230


def stock(jump=0.022, n=300, slope=0.1):
    """Slow steady uptrend, then a reaction at R. Entry open at R+1 equals the reaction close."""
    d = pd.bdate_range("2025-01-06", periods=n)
    c = 100 + slope * np.arange(n)
    c[R:] = c[R - 1] * (1 + jump) + slope * np.arange(n - R)
    o = np.r_[c[0], c[:-1]]
    o[R + 1] = c[R]
    px = pd.DataFrame({"timestamp": d, "open": o, "high": c + 1, "low": c - 1, "close": c, "volume": 1.0})
    spy = pd.DataFrame({"timestamp": d, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1.0})
    return px, spy, d


def accepted(d, r=R):
    x = d[r - 1]
    return datetime(x.year, x.month, x.day, 22, 0, tzinfo=UTC)


def test_confirmed_event_has_a_hand_checkable_primary_return():
    px, spy_px, d = stock()
    p, spy = pk.prepare(px), pk.prepare(spy_px)
    ev = pk.evaluate("T", p, spy, accepted(d))
    assert ev.confirmed and ev.reaction == pytest.approx(0.022)
    exit_close = px["close"].iloc[R + pk.PRIMARY_SESSIONS]
    assert ev.gross_adj == pytest.approx(exit_close / px["open"].iloc[R + 1] - 1)           # SPY is flat, so no adjustment
    assert ev.stop_pct == pytest.approx(2 * p.atr[R] / px["open"].iloc[R + 1])


def test_costs_are_scenario_d_applied_to_the_traded_return():
    px, spy_px, d = stock()
    ev = pk.evaluate("T", pk.prepare(px), pk.prepare(spy_px), accepted(d))
    assert ev.net_pct == pytest.approx(ev.traded_pct - 0.00125 - 0.062 * ev.cal_days / 365)
    assert ev.net_r == pytest.approx(ev.net_pct / ev.stop_pct) and ev.traded_r == pytest.approx(ev.traded_pct / ev.stop_pct)


def test_a_stretched_reaction_is_unconfirmed_and_a_downtrend_is_unconfirmed():
    px, spy_px, d = stock(jump=0.10)                                                         # far above EMA20: stretched
    assert pk.evaluate("T", pk.prepare(px), pk.prepare(spy_px), accepted(d)).confirmed is False
    px, spy_px, d = stock(slope=-0.1)
    px.loc[R:, ["open", "high", "low", "close"]] *= 1.0
    down = pk.evaluate("T", pk.prepare(px), pk.prepare(spy_px), accepted(d))
    assert isinstance(down, pk.PickEvent) and down.confirmed is False                        # price below its 50 and 200 day averages


def test_skip_reasons_are_named():
    px, spy_px, d = stock(jump=0.01)
    assert pk.evaluate("T", pk.prepare(px), pk.prepare(spy_px), accepted(d)) == "below_threshold"
    px, spy_px, d = stock()
    x = d[R]
    assert pk.evaluate("T", pk.prepare(px), pk.prepare(spy_px), datetime(x.year, x.month, x.day, 17, 0, tzinfo=UTC)) == "intraday"
    assert pk.evaluate("T", pk.prepare(px), pk.prepare(spy_px), accepted(d, r=280)) == "no_data"      # not enough sessions after it
    px, spy_px, d = stock(n=300)
    short = px.iloc[:120].reset_index(drop=True)
    assert pk.evaluate("T", pk.prepare(short), pk.prepare(spy_px), accepted(d, r=100)) == "no_data"   # under 200 bars: no SMA200


def test_pipeline_end_to_end_and_report_states_every_reading():
    px, spy_px, d = stock()
    events = {"AAA": [accepted(d), datetime(2003, 5, 1, 21, tzinfo=UTC)], "BBB": [accepted(d)]}
    res, skipped = pk.run(events, {"AAA": px}, spy_px)
    assert len(res) == 1 and skipped == {"no_prices": 1}
    text = pk.format_report(res, skipped)
    for needle in ("Test A", "PRIMARY", "TRADED", "Reading 1", "Reading 2", "second look", "scenario D"):
        assert needle in text


def test_pre_registered_constants():
    assert (pk.REACTION_MIN, pk.SMA_FAST, pk.SMA_SLOW, pk.EMA_LEN, pk.STRETCH_ATR) == (0.02, 50, 200, 20, 2.0)
    assert (pk.STOP_ATR, pk.TARGET_R, pk.TIME_SESSIONS, pk.PRIMARY_SESSIONS) == (2.0, 3.0, 40, 20)
    assert pk.COST["spread"] == 0.00125 and pk.COST["fin_long"] == 0.062 and pk.COST["fx"] == 0.0
