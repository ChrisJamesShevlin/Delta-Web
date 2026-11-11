"""Offline tests for ig_swing.shadow. Bars are built so every outcome can be checked by hand."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from ig_swing.news_view import make_view
from ig_swing.shadow import KIND, already_recorded, evaluate, format_evaluation, news_only_idea, simulate_idea

NOW = datetime(2026, 11, 11, 10, 0, tzinfo=timezone.utc)


def frame(overrides=None, n=12):
    rows = [dict(open=100.0, high=101.0, low=99.0, close=100.0) for _ in range(n)]
    for idx, vals in (overrides or {}).items():
        rows[idx].update(vals)
    df = pd.DataFrame(rows)
    df.insert(0, "timestamp", pd.date_range("2025-01-01", periods=n, freq="D"))
    return df


def idea(at, direction="BUY", sd=10.0, td=20.0, df=None):
    d = (df if df is not None else frame())["timestamp"].iloc[at]
    return {"direction": direction, "bar_date": str(d), "stop_distance": sd, "target_distance": td}


def view(direction="BULLISH", confidence="HIGH"):
    return make_view(direction, confidence, ["r"], NOW)


def flat_bars(n=100):
    c = [100.0] * n
    return pd.DataFrame({"timestamp": pd.date_range("2025-01-01", periods=n), "open": [c[0]] + c[:-1],
                         "high": [x + 1 for x in c], "low": [x - 1 for x in c], "close": c})


def test_only_a_high_confidence_directional_view_makes_an_idea():
    df = flat_bars()
    i = news_only_idea(df, view("BULLISH", "HIGH"))
    assert i["direction"] == "BUY" and i["stop_distance"] == pytest.approx(3.0) and i["target_distance"] == pytest.approx(6.0)  # ATR 2
    assert news_only_idea(df, view("BEARISH", "HIGH"))["direction"] == "SELL"
    for v in (view("BULLISH", "MEDIUM"), view("NEUTRAL", "HIGH"), None):
        assert news_only_idea(df, v) is None
    assert news_only_idea(flat_bars(50), view()) is None            # not enough history


def test_target_pays_exactly_the_distance_after_the_spread():
    o = simulate_idea(idea(3), frame({5: dict(high=125.0)}))
    assert o["entry"] == pytest.approx(100.5) and o["reason"] == "target" and o["r"] == pytest.approx(2.0)


def test_stop_loses_one_r_and_stop_wins_a_tie():
    assert simulate_idea(idea(3), frame({5: dict(low=85.0)}))["r"] == pytest.approx(-1.0)
    assert simulate_idea(idea(3), frame({5: dict(high=130.0, low=80.0)}))["reason"] == "stop"


def test_gap_through_the_stop_fills_at_the_open():
    o = simulate_idea(idea(3), frame({5: dict(open=80.0, high=81.0, low=79.0, close=80.0)}))
    assert o["reason"] == "stop_gap" and o["r"] == pytest.approx(-2.1)


def test_short_mirrors_the_long():
    o = simulate_idea(idea(3, "SELL"), frame({5: dict(low=75.0)}))
    assert o["entry"] == pytest.approx(99.5) and o["reason"] == "target" and o["r"] == pytest.approx(2.0)


def test_time_exit_and_unfinished_ideas():
    assert simulate_idea(idea(3), frame(), max_hold=2)["reason"] == "time"
    assert simulate_idea(idea(3), frame(), max_hold=100) is None        # still running
    assert simulate_idea(idea(11), frame()) is None                      # no entry bar yet


def test_evaluate_skips_overlapping_ideas_and_counts_pending():
    df = frame({5: dict(high=125.0), 9: dict(high=125.0)})
    ideas = [idea(3, df=df), idea(4, df=df), idea(8, df=df), idea(11, df=df)]
    res = evaluate(ideas, df, max_hold=100)
    # idea 3 enters bar 4, exits bar 5 (target). Idea 4 enters bar 5 (overlap) -> skipped.
    # idea 8 enters bar 9 and exits bar 9. Idea 11 has no entry bar -> pending.
    assert res["n"] == 2 and res["pending"] == 1 and res["mean_r"] == pytest.approx(2.0) and res["win_rate"] == 1.0


def test_report_text_states_sample_size_and_refuses_a_conclusion():
    df = frame({5: dict(high=125.0), 9: dict(low=85.0)})
    text = format_evaluation(evaluate([idea(3, df=df), idea(8, df=df)], df, max_hold=100))
    assert "n=2" in text and "no conclusion" in text and "Not traded" in text
    assert "No shadow" not in text
    empty = format_evaluation(evaluate([], df))
    assert "n=0" in empty and "no conclusion" in empty


def test_already_recorded_matches_on_date_and_direction():
    i = idea(3)
    events = [{"kind": KIND, "detail": json.dumps(i)}, {"kind": "other", "detail": "{}"}, {"kind": KIND, "detail": "garbage"}]
    assert already_recorded(events, i)
    assert not already_recorded(events, {**i, "direction": "SELL"})
    assert not already_recorded(events, {**i, "bar_date": "2030-01-01"})
