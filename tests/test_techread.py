"""Offline tests for the technical read."""

from __future__ import annotations

import json
import sqlite3

import pandas as pd

from ig_swing import techread
from ig_swing.journal import Journal


def bars(closes):
    return pd.DataFrame({"close": closes, "high": [c + 1 for c in closes], "low": [c - 1 for c in closes], "open": closes})


def test_a_steady_rise_reads_as_above_a_rising_average_with_high_rsi():
    t = techread.tech_read(bars([100 + i for i in range(30)]), 130.0)
    assert t["trend"] == "above a rising 20-day average" and t["rsi"] == 100.0 and t["slope_pct"] > 0
    assert t["prev_high"] == 130.0 and t["week_low"] == 124.0 and t["to_hi20_atr"] < 0.5


def test_a_break_below_the_20_day_low_has_a_negative_distance():
    t = techread.tech_read(bars([200 - i for i in range(30)]), 160.0)
    assert t["trend"] == "below a falling 20-day average" and t["to_lo20_atr"] < 0


def test_too_few_bars_gives_none_and_a_plain_message():
    assert techread.tech_read(bars([1.0] * 10), 1.0) is None
    assert "not enough bars" in techread.format_tech(None)


def test_the_snapshot_archive_keeps_the_tech_read_and_migrates_an_old_journal(tmp_path):
    path = tmp_path / "j.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, day TEXT NOT NULL, market TEXT NOT NULL, epic TEXT NOT NULL, "
                "price REAL, bid REAL, offer REAL, spread_pct REAL, day_pct REAL, atr REAL, atr_pct REAL, range_pos REAL, long_pct REAL, sma20 REAL)")
    old.commit()
    old.close()
    j = Journal(path)                                         # opening it adds the new column
    t = techread.tech_read(bars([100 + i for i in range(30)]), 130.0)
    j.record_snapshots("2026-11-12T10:00:00+00:00", [{"market": "X", "epic": "E", "price": 1.0, "tech": t}])
    stored = sqlite3.connect(path).execute("SELECT tech FROM snapshots").fetchone()[0]
    assert json.loads(stored)["trend"] == t["trend"]
