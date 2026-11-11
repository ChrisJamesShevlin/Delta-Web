"""Offline tests for ig_swing.history and ig_swing.technical_test."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ig_swing import technical_test as tt
from ig_swing.history import COLUMNS, fetch_history


def series(n=600, seed=3):
    rng = np.random.default_rng(seed)
    c = 1000 + np.cumsum(rng.normal(1.0, 8.0, n))
    return pd.DataFrame({"timestamp": pd.date_range("2022-01-01", periods=n), "open": np.r_[c[0], c[:-1]],
                         "high": c + 4, "low": c - 4, "close": c})


class FakeClient:
    def __init__(self, df):
        self.df, self.calls = df, []

    def get_prices(self, epic, resolution, n):
        self.calls.append((epic, resolution, n))
        return self.df.assign(bid=1.0, ask=2.0)


def test_fetch_history_saves_clean_csv_with_expected_columns(tmp_path):
    messy = pd.concat([series(50), series(50).iloc[:5]])               # duplicates in
    path = tmp_path / "h" / "x.csv"
    c = FakeClient(messy)
    df = fetch_history(c, 1500, path)
    assert c.calls == [("IX.D.SPTRD.DAILY.IP", "DAY", 1500)]
    assert list(df.columns) == COLUMNS and len(df) == 50 and df["timestamp"].is_monotonic_increasing
    assert list(pd.read_csv(path).columns) == COLUMNS


def test_settings_are_the_pre_registered_ones():
    assert tt.HOLDOUT_FRACTION == 0.30 and tt.FIXED["spread"] == 1.0 and tt.FIXED["limits"].name == "agreed"
    assert tt.FIXED["start_equity"] == 100_000.0


def test_split_partitions_every_trade_exactly_once():
    out = tt.run_fixed(series())
    s = out["stats"]
    assert s["All trades"].n > 0
    assert s["In-sample (first 70% of bars)"].n + s["Holdout (last 30% of bars)"].n == s["All trades"].n
    assert s["Long only"].n + s["Short only"].n == s["All trades"].n
    assert out["cut"] == out["first"] + pd.Timedelta(days=int(600 * 0.7) - 1)


def test_report_states_sample_size_and_refuses_a_small_sample_conclusion():
    text = tt.format_test(tt.run_fixed(series()))
    assert "Pre-registered" in text and "Holdout" in text and "n=" in text and "Parameters were not tuned" in text
