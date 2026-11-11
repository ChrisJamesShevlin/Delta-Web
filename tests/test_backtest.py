"""Offline tests for ig_swing.backtest. Signals are injected so every number can be checked by hand."""

from __future__ import annotations

import pandas as pd
import pytest

from ig_swing import backtest
from ig_swing.backtest import format_result, load_bars_csv, run_backtest
from ig_swing.config import AGREED, LIVE_TEST
from ig_swing.strategy import Signal, StrategyParams

STRAT = StrategyParams(min_bars=3)
SD, TD = 10.0, 20.0


def frame(overrides=None, n=12):
    rows = [dict(open=100.0, high=101.0, low=99.0, close=100.0) for _ in range(n)]
    for idx, vals in (overrides or {}).items():
        rows[idx].update(vals)
    df = pd.DataFrame(rows)
    df.insert(0, "timestamp", pd.date_range("2025-01-01", periods=n, freq="D"))
    return df


def inject(monkeypatch, at, direction="BUY", sd=SD, td=TD):
    def fake(f, i, p, allow_long=True, allow_short=True):
        if i != at:
            return None
        return Signal(direction, "breakout", sd, td, 100.0, 5.0, i, "test")
    monkeypatch.setattr(backtest, "signal_at", fake)


def run(df, **kw):
    kw.setdefault("strat", STRAT)
    kw.setdefault("limits", LIVE_TEST)
    kw.setdefault("start_equity", 1000.0)
    kw.setdefault("spread", 1.0)
    kw.setdefault("trend_break_exit", False)
    kw.setdefault("max_hold", 100)
    return run_backtest(df, **kw)


def test_signal_enters_next_open_and_target_pays_exactly_the_distance(monkeypatch):
    inject(monkeypatch, at=3)
    r = run(frame({4: dict(open=100.0, high=125.0, low=99.0, close=124.0)}))
    t = r.trades[0]
    assert t.entry == pytest.approx(100.5) and t.entry_date == pd.Timestamp("2025-01-05")  # bar 4, offer = open + half spread
    assert t.exit_reason == "target" and t.exit == pytest.approx(120.5)
    assert t.size == 5.0 and t.pnl == pytest.approx(100.0) and t.r_multiple == pytest.approx(2.0)
    assert r.end_equity == pytest.approx(1100.0)


def test_stop_loses_exactly_one_r(monkeypatch):
    inject(monkeypatch, at=3)
    t = run(frame({4: dict(low=85.0, close=90.0)})).trades[0]
    assert t.exit_reason == "stop" and t.exit == pytest.approx(90.5) and t.r_multiple == pytest.approx(-1.0)


def test_when_stop_and_target_are_both_in_range_the_stop_wins(monkeypatch):
    inject(monkeypatch, at=3)
    t = run(frame({4: dict(high=130.0, low=80.0)})).trades[0]
    assert t.exit_reason == "stop"


def test_gap_through_the_stop_fills_at_the_open_not_the_stop(monkeypatch):
    inject(monkeypatch, at=3)
    t = run(frame({5: dict(open=80.0, high=81.0, low=79.0, close=80.0)})).trades[0]
    assert t.exit_reason == "stop_gap" and t.exit == pytest.approx(79.5)
    assert t.r_multiple == pytest.approx(-2.1)          # worse than the planned -1R


def test_short_trade_mirrors_the_long(monkeypatch):
    inject(monkeypatch, at=3, direction="SELL")
    t = run(frame({4: dict(low=75.0, close=76.0)})).trades[0]      # entry 99.5, target 79.5
    assert t.entry == pytest.approx(99.5) and t.exit_reason == "target" and t.r_multiple == pytest.approx(2.0)
    t2 = run(frame({4: dict(high=115.0, close=114.0)})).trades[0]  # stop 109.5
    assert t2.exit_reason == "stop" and t2.r_multiple == pytest.approx(-1.0)


def test_wider_spread_makes_a_marginal_stop_hit(monkeypatch):
    inject(monkeypatch, at=3)
    df = frame({4: dict(low=91.0, close=95.0)})
    assert run(df, spread=0.0).trades == [] or run(df, spread=0.0).trades[0].exit_reason != "stop"
    assert run(df, spread=2.0).trades[0].exit_reason == "stop"


def test_trade_the_live_rules_would_refuse_is_skipped_and_counted(monkeypatch):
    inject(monkeypatch, at=3, sd=169.0, td=338.0)
    r = run(frame(), start_equity=30.0)
    assert r.trades == [] and r.skipped == {"size": 1}


def test_agreed_profile_refuses_what_live_test_allows(monkeypatch):
    inject(monkeypatch, at=3, sd=112.0, td=224.0)
    assert run(frame(), start_equity=30.0, limits=LIVE_TEST).skipped == {}
    assert run(frame(), start_equity=30.0, limits=AGREED).skipped == {"size": 1}


def test_trend_break_exits_at_the_next_open(monkeypatch):
    inject(monkeypatch, at=3)
    big_drop = {5: dict(open=100.0, high=101.0, low=96.0, close=70.0)}  # closes far below the slow EMA, above the stop
    df = frame({**big_drop, 6: dict(open=97.0, high=98.0, low=96.0, close=97.0)})
    t = run(df, trend_break_exit=True).trades[0]
    assert t.exit_reason == "trend_break" and t.exit == pytest.approx(96.5)   # bar 6 open 97, minus half spread


def test_time_stop_and_end_of_data_exit(monkeypatch):
    inject(monkeypatch, at=3)
    assert run(frame(), max_hold=2).trades[0].exit_reason == "time"
    assert run(frame()).trades[0].exit_reason == "end_of_data"


def test_one_position_at_a_time(monkeypatch):
    calls = []

    def fake(f, i, p, allow_long=True, allow_short=True):
        calls.append(i)
        return Signal("BUY", "breakout", SD, TD, 100.0, 5.0, i, "t") if i in (3, 4, 5) else None

    monkeypatch.setattr(backtest, "signal_at", fake)
    r = run(frame())
    assert len(r.trades) == 1                         # signals at 4 and 5 were ignored while in the trade
    assert 4 not in calls and 5 not in calls


def test_stats_and_report_text(monkeypatch):
    inject(monkeypatch, at=3)
    r = run(frame({4: dict(high=125.0)}))
    s = r.stats()
    assert s.n == 1 and s.total_pnl == pytest.approx(100.0)
    text = format_result(r)
    assert "n=1" in text and "Not modelled" in text and "exits: target 1" in text


def test_csv_loader_checks_columns(tmp_path):
    good = tmp_path / "g.csv"
    good.write_text("Date,Open,High,Low,Close\n2025-01-01,1,2,0.5,1.5\n2025-01-02,,,,\n")
    df = load_bars_csv(str(good))
    assert list(df.columns) == ["timestamp", "open", "high", "low", "close"] and len(df) == 1
    bad = tmp_path / "b.csv"
    bad.write_text("open,high\n1,2\n")
    with pytest.raises(ValueError):
        load_bars_csv(str(bad))


def test_real_engine_runs_end_to_end_on_a_long_synthetic_trend():
    import numpy as np
    rng = np.random.default_rng(3)
    closes = 1000 + np.cumsum(rng.normal(1.0, 8.0, 600))
    df = pd.DataFrame({"timestamp": pd.date_range("2022-01-01", periods=600), "open": np.r_[closes[0], closes[:-1]],
                       "high": closes + 4, "low": closes - 4, "close": closes})
    r = run_backtest(df, start_equity=1000.0, limits=LIVE_TEST)
    assert r.trades, "expected at least one trade on a trending series"
    assert all(t.size > 0 and t.risk_gbp > 0 for t in r.trades)
    assert abs(sum(t.pnl for t in r.trades) - (r.end_equity - 1000.0)) < 1e-6
