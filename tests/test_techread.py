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


def test_long_read_labels_the_50_and_200_day_alignment():
    up = techread.long_read(bars([100 + i * 0.5 for i in range(250)]))
    assert up["long_trend"] == "above the 50 and 200 day averages (50 over 200)" and up["d50_pct"] > 0 and up["d200_pct"] > 0
    down = techread.long_read(bars([300 - i * 0.5 for i in range(250)]))
    assert down["long_trend"] == "below the 50 and 200 day averages (50 under 200)"
    short = techread.long_read(bars([100 + i for i in range(80)]))
    assert short["d200_pct"] is None and short["long_trend"] == "above the 50-day"
    assert techread.long_read(bars([1.0] * 30)) is None


def test_a_share_gets_a_full_read_from_rescaled_yahoo_bars_and_ig_atr_is_untouched(tmp_path, monkeypatch):
    from ig_swing import challenge as ch, yahoo_bars
    yb = bars([100 + i * 0.5 for i in range(250)])
    yb["timestamp"] = pd.date_range(end="2026-11-11", periods=250)
    monkeypatch.setattr(yahoo_bars, "load_daily", lambda session, sym, today, **kw: yb)
    rows = [{"market": "NVIDIA", "epic": "UC.D.NVDA.DAILY.IP", "price": 22450.0, "atr": None, "tech": None}]
    assert ch.enrich_with_yahoo(None, rows, pd.Timestamp("2026-11-12").date()) == 1
    t = rows[0]["tech"]
    assert t["trend"].startswith("above a rising") and t["long_trend"].startswith("above the 50 and 200") and rows[0]["atr"] is None
    assert 22000 < t["prev_high"] < 23000                          # levels rescaled to IG's price scale
    assert "longer view" in techread.format_tech(t)


def test_yahoo_loader_drops_today_uses_the_cache_and_fails_soft(tmp_path):
    from datetime import date
    from ig_swing import yahoo_bars
    seen = {"n": 0}
    def fake_fetch(session, symbol, start, end):
        seen["n"] += 1
        df = bars([100 + i for i in range(40)])
        df["timestamp"] = pd.date_range(end="2026-11-12", periods=40)
        return df
    orig = yahoo_bars.fetch_prices
    yahoo_bars.fetch_prices = fake_fetch
    try:
        a = yahoo_bars.load_daily(None, "^GSPC", date(2026, 11, 12), cache=tmp_path, pause=0)
        b = yahoo_bars.load_daily(None, "^GSPC", date(2026, 11, 12), cache=tmp_path, pause=0)
        assert seen["n"] == 1 and len(a) == 39 and a["timestamp"].max() < pd.Timestamp("2026-11-12")   # today's bar dropped, second call cached
        assert len(b) == 39
        yahoo_bars.fetch_prices = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))
        assert yahoo_bars.load_daily(None, "OTHER", date(2026, 11, 12), cache=tmp_path, pause=0) is None
    finally:
        yahoo_bars.fetch_prices = orig
