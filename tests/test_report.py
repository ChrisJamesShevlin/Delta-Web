"""Offline tests for ig_swing.report."""

from __future__ import annotations

import math

import pytest

from ig_swing.report import Stats, format_report, group_stats, max_drawdown_pct, summarise, summarise_trades, verdict


def test_expectancy_and_standard_error_match_hand_calculation():
    s = summarise([2.0, -1.0, 3.0], [1.0, -0.5, 1.5], 30.0)
    assert s.n == 3 and s.wins == 2 and s.win_rate == pytest.approx(2 / 3)
    assert s.mean_pnl == pytest.approx(4 / 3)
    assert s.se_pnl == pytest.approx(math.sqrt(4.3333333 / 3), rel=1e-4)   # sd 2.0817 / sqrt(3)
    assert s.total_pnl == pytest.approx(4.0) and s.end_equity == pytest.approx(34.0)


def test_max_drawdown_is_peak_to_trough_percentage():
    assert max_drawdown_pct([5.0, -10.0, 3.0], 30.0) == pytest.approx((35 - 25) / 35 * 100)
    assert max_drawdown_pct([1.0, 2.0], 30.0) == 0.0
    assert max_drawdown_pct([], 30.0) is None


def test_one_trade_has_a_mean_but_no_standard_error():
    s = summarise([1.0], [0.5], 30.0)
    assert s.mean_pnl == 1.0 and s.se_pnl is None


def test_verdict_calls_small_samples_noise_and_flat_results_zero():
    few = summarise([1.0, -1.0, 2.0], [0.5, -0.5, 1.0], 30.0)
    assert "Only 3 of the 100" in verdict(few) and "indistinguishable from zero" in verdict(few)
    assert verdict(summarise([], [], 30.0)) == "No closed trades yet."


def test_verdict_notes_a_clear_edge_but_does_not_call_it_proof():
    s = Stats(n=150, wins=100, win_rate=0.67, mean_pnl=1.0, se_pnl=0.1, mean_r=0.5, se_r=0.05,
              total_pnl=150.0, max_drawdown_pct=10.0, end_equity=180.0)
    text = verdict(s)
    assert "meets the 100 trade minimum" in text and "positive" in text and "not proof" in text


def test_summarise_trades_skips_open_rows_and_groups_by_tag():
    trades = [
        {"net_pnl": 1.0, "r_multiple": 1.0, "setup": "breakout", "driver": "rates", "direction": "BUY"},
        {"net_pnl": -1.0, "r_multiple": -1.0, "setup": "pullback", "driver": "rates", "direction": "SELL"},
        {"net_pnl": None, "r_multiple": None, "setup": "pullback", "driver": None, "direction": "BUY"},
    ]
    assert summarise_trades(trades, 30.0).n == 2
    g = group_stats(trades, lambda t: t.get("driver"), 30.0)
    assert set(g) == {"rates", "untagged"} and g["rates"].n == 2


def test_report_text_states_sample_size_and_se():
    trades = [{"net_pnl": p, "r_multiple": p, "setup": "breakout", "driver": None, "direction": "BUY"} for p in (1.0, -0.5, 2.0)]
    text = format_report(trades, 30.0)
    assert "n=3" in text and "SE" in text and "Only 3 of the 100" in text
