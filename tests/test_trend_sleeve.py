"""Offline tests for ig_swing.trend_sleeve. Every number is hand-checkable."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from ig_swing import trend_sleeve as ts


def months(n, start="2015-01"):
    return pd.period_range(start, periods=n, freq="M")


def panel_of(columns: dict[str, list[float]]) -> pd.DataFrame:
    n = max(len(v) for v in columns.values())
    return pd.DataFrame({k: pd.Series(v, index=months(len(v))) for k, v in columns.items()}).reindex(months(n))


def test_monthly_closes_keeps_the_last_bar_per_month_and_drops_the_unfinished_month():
    df = pd.DataFrame({"timestamp": ["2026-08-01", "2026-09-01", "2026-09-15", "2026-10-01", "2026-11-01"], "close": [1.0, 2.0, 3.0, 4.0, 5.0]})
    s = ts.monthly_closes(df, pd.Timestamp("2026-11-11"))
    assert list(s.index.astype(str)) == ["2026-08", "2026-09", "2026-10"] and list(s) == [1.0, 3.0, 4.0]


def test_build_panel_fills_missing_months_with_nan_not_zero():
    a = pd.DataFrame({"timestamp": ["2020-01-01", "2020-02-01", "2020-04-01"], "close": [1.0, 2.0, 4.0]})
    p = ts.build_panel({"A": a}, pd.Timestamp("2030-01-01"))
    assert len(p) == 4 and math.isnan(p.loc[pd.Period("2020-03"), "A"])


def test_signals_by_hand_v1_and_v2():
    closes = [100.0 + i for i in range(13)] + [111.0]          # 13 months rising to 112, then a dip to 111
    p = panel_of({"A": closes})
    last = p.index[-1]
    assert ts.signals(p, "V1").loc[last, "A"] == 1.0           # 12-month return is up
    # 1-month is down (111 < 112), 3-month is up (111 > 110), 12-month is up: (-1 + 1 + 1) / 3
    assert ts.signals(p, "V2").loc[last, "A"] == pytest.approx(1 / 3)
    assert math.isnan(ts.signals(p, "V2").iloc[5, 0])           # not enough history for the 12-month leg yet
    with pytest.raises(ValueError):
        ts.signals(p, "V3")


def test_a_missing_month_switches_the_signal_off_until_history_returns():
    closes = [100.0 + i for i in range(30)]
    closes[10] = float("nan")
    p = panel_of({"A": closes})
    v2 = ts.signals(p, "V2")["A"]
    assert math.isnan(v2.iloc[10]) and math.isnan(v2.iloc[11])   # the gap and the month after it (1-month leg) are unusable
    assert math.isnan(v2.iloc[22])                              # the 12-month leg still touches the gap
    assert v2.iloc[23] == 1.0                                   # clear of the gap: all three legs up


def test_raw_weights_use_own_volatility_and_divide_by_active_markets():
    rng = np.random.default_rng(0)
    ra = pd.Series(rng.normal(0.01, 0.02, 40), index=months(40))
    rb = pd.Series(rng.normal(0.01, 0.05, 40), index=months(40))
    returns = pd.DataFrame({"A": ra, "B": rb})
    sig = pd.DataFrame({"A": 1.0, "B": -1.0}, index=returns.index)
    w = ts.raw_weights(sig, returns)
    t = returns.index[-1]
    va, vb = ra.iloc[-36:].std() * math.sqrt(12), rb.iloc[-36:].std() * math.sqrt(12)
    assert w.loc[t, "A"] == pytest.approx(0.10 / va / 2) and w.loc[t, "B"] == pytest.approx(-0.10 / vb / 2)
    assert w.loc[t, "A"] > abs(w.loc[t, "B"])                    # the calmer market gets the bigger position


def test_leverage_is_floored_capped_and_one_until_history_exists():
    calm = pd.Series(np.tile([0.001, -0.001], 30), index=months(60))        # tiny volatility: wants far more than 3x
    lev = ts.leverage(calm)
    assert lev.iloc[-1] == ts.LEV_MAX and lev.iloc[:11].eq(1.0).all()
    wild = pd.Series(np.tile([0.15, -0.15], 30), index=months(60))          # huge volatility: wants less than 1x, floor applies
    assert ts.leverage(wild).iloc[-1] == ts.LEV_MIN
    assert ts.leverage(pd.Series(0.0, index=months(20))).eq(1.0).all()       # nothing traded yet


def test_leverage_has_no_look_ahead():
    rng = np.random.default_rng(1)
    r = pd.Series(rng.normal(0.004, 0.02, 60), index=months(60))
    a = ts.leverage(r)
    r2 = r.copy()
    r2.iloc[-1] = 0.9                                                         # a wild final month
    b = ts.leverage(r2)
    assert a.iloc[:-1].equals(b.iloc[:-1]) and a.iloc[-1] != b.iloc[-1]


def test_portfolio_costs_and_returns_by_hand():
    idx = months(4)
    W = pd.DataFrame({"X": [0.5, -0.5, -0.5, -0.5]}, index=idx)               # decided at the end of each month
    ret = pd.DataFrame({"X": [0.0, 0.10, -0.10, 0.0]}, index=idx)
    spreads = pd.Series({"X": 0.002})
    pf = ts.portfolio(W, ret, spreads, mult=2.0, financing=0.024)
    # month 1 holds +0.5 (return +10%); month 2 holds -0.5 (return -10%)
    assert pf.index[0] == idx[1]
    assert pf.loc[idx[1], "gross"] == pytest.approx(0.05) and pf.loc[idx[2], "gross"] == pytest.approx(0.05)
    # trade of 0.5 at the end of month 0 costs 0.5 x 0.002 x 2 / 2 in month 1; the flip of 1.0 costs 1.0 x 0.002 in month 2
    assert pf.loc[idx[1], "spread_cost"] == pytest.approx(0.001) and pf.loc[idx[2], "spread_cost"] == pytest.approx(0.002)
    assert pf.loc[idx[3], "spread_cost"] == pytest.approx(0.0)                 # no trade, no spread cost
    assert pf.loc[idx[1], "financing"] == pytest.approx(0.5 * 0.024 / 12)
    assert pf.loc[idx[2], "net"] == pytest.approx(0.05 - 0.002 - 0.5 * 0.002)
    assert pf.loc[idx[2], "turnover"] == pytest.approx(1.0)


def test_the_full_run_has_no_look_ahead():
    rng = np.random.default_rng(7)
    names = list(ts.MARKETS)[:5]
    base = {n: list(100 * np.cumprod(1 + rng.normal(0.004, 0.04, 80))) for n in names}
    p = panel_of(base)
    a = ts.run_sleeve(p, "V2")["pf"]["net"]
    changed = {n: v[:-1] + [v[-1] * 3] for n, v in base.items()}              # the last month jumps threefold
    b = ts.run_sleeve(panel_of(changed), "V2")["pf"]["net"]
    assert a.iloc[:-1].round(12).equals(b.iloc[:-1].round(12)) and a.iloc[-1] != b.iloc[-1]


def test_a_rising_market_is_held_long_and_a_falling_one_short():
    up = [100 * 1.01 ** i for i in range(60)]
    down = [100 * 0.99 ** i for i in range(60)]
    res = ts.run_sleeve(panel_of({"US 500": up, "Gold": down}), "V2")
    w = res["weights"].iloc[-1]
    assert w["US 500"] > 0 and w["Gold"] < 0
    assert res["pf"]["net"].iloc[-12:].gt(0).all()                            # both trends pay


def test_metrics_by_hand():
    r = pd.Series([0.01] * 24, index=months(24))
    assert ts.annualised(r) == pytest.approx(1.01 ** 12 - 1)
    dd, under = ts.drawdown(r)
    assert dd == 0.0 and under == 0
    r2 = pd.Series([0.10, -0.20, 0.05, 0.05, 0.05, 0.05], index=months(6))
    dd2, under2 = ts.drawdown(r2)
    assert dd2 == pytest.approx(-0.20) and under2 == 5                          # months 2 to 6 stay below the month-1 peak? checked below
    w = ts.rolling_windows(pd.Series(0.01, index=months(130)), 120)
    assert len(w) == 11 and w.iloc[0] == pytest.approx(1.01 ** 12 - 1)


def test_bar_one_needs_every_window_at_or_above_the_bar():
    good = pd.Series(0.005, index=months(150))                                 # about 6.2% a year
    assert ts.bar1(good)["passes"] and ts.bar1(good)["share"] == 1.0
    bad = pd.Series([0.005] * 130 + [-0.01] * 20, index=months(150))             # the late slump drags the last windows under 4%
    b = ts.bar1(bad)
    assert not b["passes"] and 0 < b["share"] < 1 and b["worst"] < 0.04
    assert not ts.bar1(pd.Series(0.01, index=months(50)))["passes"]            # fewer than 120 months: cannot pass


def test_bar_two_wants_a_shallower_drawdown_and_no_lower_return():
    idx = months(8)
    static = pd.Series([0.05, 0.05, -0.20, -0.10, 0.05, 0.05, 0.05, 0.05], index=idx)
    sleeve_good = pd.Series([0.0, 0.0, 0.08, 0.06, 0.0, 0.0, 0.0, 0.0], index=idx)       # earns during the fall
    sleeve_bad = pd.Series([0.0, 0.0, -0.05, -0.05, 0.0, 0.0, 0.0, 0.0], index=idx)       # loses during the fall
    assert ts.bar2(sleeve_good, static)["passes"] and not ts.bar2(sleeve_bad, static)["passes"]


def test_static_mix_is_seventy_thirty_and_ignores_months_either_leg_is_missing():
    idx = months(3)
    returns = pd.DataFrame({"US 500": [0.10, np.nan, 0.0], "US 10Y Treasury ETF": [0.0, 0.05, 0.10]}, index=idx)
    m = ts.static_mix(returns)
    assert list(m.index) == [idx[0], idx[2]] and m.iloc[0] == pytest.approx(0.07) and m.iloc[1] == pytest.approx(0.03)


def test_pre_registered_constants():
    assert len(ts.MARKETS) == 14 and ts.BARS == 300 and ts.PRIMARY == "V2"
    assert (ts.VOL_TARGET, ts.LEV_MIN, ts.LEV_MAX, ts.SPREAD_MULT, ts.FINANCING, ts.WINDOW, ts.RETURN_BAR) == (0.10, 1.0, 3.0, 2.0, 0.025, 120, 0.04)
    assert ts.LOOKBACKS == (1, 3, 12) and (ts.VOL_MIN, ts.VOL_WINDOW) == (12, 36) and ts.STATIC_WEIGHTS == {"US 500": 0.7, "US 10Y Treasury ETF": 0.3}
    assert ts.MARKETS["Copper"]["spread"] == 0.00218 and ts.MARKETS["US 10Y Treasury ETF"]["spread"] == 0.003475
    assert len({m["epic"] for m in ts.MARKETS.values()}) == 14


def test_report_states_both_bars_and_a_verdict():
    rng = np.random.default_rng(3)
    names = list(ts.MARKETS)
    p = panel_of({n: list(100 * np.cumprod(1 + rng.normal(0.004, 0.04, 200))) for n in names})
    text = ts.format_report({v: ts.run_sleeve(p, v) for v in ("V1", "V2")}, p)
    for needle in ("PRIMARY", "comparison only", "Bar 1", "Bar 2", "VERDICT", "monthly bars understate"):
        assert needle in text
