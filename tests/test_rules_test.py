"""Offline tests for ig_swing.rules_test. Synthetic numbers chosen so each answer can be checked by hand."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from ig_swing import rules_test as rt


def idx(n, start="2024-01-01"):
    return pd.bdate_range(start, periods=n)


def test_specs_use_the_live_rule_thresholds():
    s = {x.name: x for x in rt.specs()}
    assert s["VIX spike"].threshold_pct == 10.0 and s["VIX collapse"].threshold_pct == -10.0 and s["Oil spike"].threshold_pct == 3.0
    assert s["VIX spike"].expect == "lower" and s["VIX collapse"].expect == "higher" and s["VIX spike"].blocks == "BUY"


def test_trigger_days_apply_the_threshold_to_day_over_day_change():
    v = pd.Series([20.0, 22.1, 22.0, 19.0, 19.1], index=idx(5))       # +10.5%, -0.5%, -13.6%, +0.5%
    spike, collapse = rt.specs()[0], rt.specs()[1]
    assert rt.trigger_dates(v, spike) == [v.index[1]] and rt.trigger_dates(v, collapse) == [v.index[3]]
    exactly = pd.Series([20.0, 22.0], index=idx(2))                    # exactly +10%: triggers (>=)
    assert rt.trigger_dates(exactly, spike) == [exactly.index[1]]


def test_forward_returns_look_k_bars_ahead_and_end_with_nan():
    c = pd.Series([100.0, 110.0, 121.0, 133.1], index=idx(4))
    f = rt.forward_returns(c, 2)
    assert f.iloc[0] == pytest.approx(21.0) and f.iloc[1] == pytest.approx(21.0) and f.iloc[2:].isna().all()


def test_event_study_difference_and_z_by_hand():
    rng = np.random.default_rng(1)
    c = pd.Series(100 * np.cumprod(1 + rng.normal(0, 0.01, 400)), index=idx(400))
    fwd = rt.forward_returns(c, 5).dropna()
    triggers = [c.index[i] for i in (20, 60, 100, 140)]
    r = rt.event_study(c, triggers, "lower", 5)
    ev = fwd.loc[triggers]
    assert r["n"] == 4 and r["event_mean"] == pytest.approx(ev.mean()) and r["base_mean"] == pytest.approx(fwd.mean())
    assert r["z"] == pytest.approx((ev.mean() - fwd.mean()) / (fwd.std() / math.sqrt(4)))


def test_clustered_triggers_count_once_and_unknown_dates_are_ignored():
    c = pd.Series(np.linspace(100, 120, 60), index=idx(60))
    r = rt.event_study(c, [c.index[10], c.index[11], c.index[12], c.index[20], pd.Timestamp("1999-01-01")], "lower", 5)
    assert r["n"] == 2                                                  # day 10 (11 and 12 are inside its cluster), then day 20


def test_verdicts_need_enough_events_and_a_big_enough_z():
    base = dict(k=5, event_mean=0.0, base_mean=0.0, diff=0.0, expect="lower")
    assert "no conclusion" in rt.verdict({**base, "n": 10, "z": -5.0})
    assert "not validated" in rt.verdict({**base, "n": 40, "z": -1.9})
    assert rt.verdict({**base, "n": 40, "z": -2.5}) == "SUPPORTS the rule"
    assert "CONTRADICTS" in rt.verdict({**base, "n": 40, "z": +2.5})
    assert rt.verdict({**base, "n": 40, "z": +2.5, "expect": "higher"}) == "SUPPORTS the rule"


def test_vetoed_trades_use_the_signal_bar_and_only_the_blocked_direction():
    from ig_swing.backtest import BTrade
    dates = idx(10)
    bars = pd.DataFrame({"timestamp": dates})
    vix = pd.Series([20.0] * 10, index=dates)
    vix.iloc[4] = 25.0                                                    # +25% spike on bar 4
    mk = lambda i, d, r: BTrade(dates[i], dates[i], d, "x", 1, 1, 1, 1, 1, r, r, 1, "x")
    trades = [mk(5, "BUY", -1.0), mk(5, "SELL", 2.0), mk(8, "BUY", 2.0)]  # entry bar 5 means the signal was bar 4
    out = rt.vetoed_trades(trades, bars, {"VIX": vix, "Oil": vix}, rt.specs())
    assert out["VIX spike"]["vetoed"] == 1 and out["VIX spike"]["vetoed_mean_r"] == -1.0 and out["VIX spike"]["kept"] == 2


def test_series_cache_round_trip_and_missing_values_skipped(tmp_path):
    class Resp:
        status_code = 200
        def json(self):
            return {"observations": [{"date": "2024-01-02", "value": "1.5"}, {"date": "2024-01-03", "value": "."}, {"date": "2024-01-04", "value": "2.5"}]}

    class S:
        calls = 0
        def get(self, *a, **k):
            S.calls += 1
            return Resp()

    s = rt.load_series(S(), "k", "X", tmp_path)
    assert list(s.values) == [1.5, 2.5]
    again = rt.load_series(S(), None, "X", tmp_path)                    # from cache: no key and no call needed
    assert list(again.values) == [1.5, 2.5] and S.calls == 1
