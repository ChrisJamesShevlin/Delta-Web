"""Offline tests for the macro backdrop."""

from __future__ import annotations

from ig_swing import macro
from ig_swing import challenge as ch


def payload(closes, level):
    return {"chart": {"result": [{"meta": {"regularMarketPrice": level}, "indicators": {"quote": [{"close": closes}]}}]}}


def test_parse_macro_replaces_the_partial_last_bar_with_the_live_level():
    closes = [4.00, 4.02, 4.05, 4.04, 4.10, 4.12, 4.15, 4.99]          # the last bar is today's partial one
    m = macro.parse_macro(payload(closes, 4.20))
    assert m["level"] == 4.20 and round(m["day"], 2) == 0.05 and round(m["five_day"], 2) == 0.15   # against 4.15 yesterday and 4.05 five sessions back


def test_parse_macro_needs_enough_data_and_ignores_gaps():
    assert macro.parse_macro(payload([1.0, 2.0], 3.0)) is None
    assert macro.parse_macro(payload([None, 1, 1, 1, 1, 1, 1, 1, 1], 1.0)) is not None


def test_load_macro_fails_soft_and_format_shows_yield_in_points():
    class Bad:
        def get(self, *a, **k):
            raise RuntimeError("down")
    assert macro.load_macro(Bad()) == {}
    lines = macro.format_macro({"US 10-year yield": {"level": 4.2, "day": 0.05, "five_day": 0.16, "day_pct": 1.2, "five_day_pct": 4.0},
                                "VIX": {"level": 18.0, "day": 1.0, "five_day": 2.0, "day_pct": 5.9, "five_day_pct": 12.5}})
    assert "4.20%, +0.05 points today" in lines[0] and "VIX: 18.00, +5.90% today" in lines[1]


def test_the_briefing_prints_the_macro_block_only_when_there_is_one():
    from datetime import datetime, timezone
    now = datetime(2026, 11, 12, 10, 0, tzinfo=timezone.utc)
    with_macro = ch.format_briefing(now, [], 50.0, 0, [], [], [], macro_text=["  VIX: 18.00, +1.00% today, +2.00% over 5 sessions"])
    assert "Macro backdrop" in with_macro and "VIX: 18.00" in with_macro
    assert "Macro backdrop" not in ch.format_briefing(now, [], 50.0, 0, [], [], [])


def _m(y_day, vix_pct, usd_5d=1.0):
    return {"US 10-year yield": {"level": 5.0, "day": y_day, "five_day": 0.0, "day_pct": 0.0, "five_day_pct": 0.0},
            "VIX": {"level": 20.0, "day": 1.0, "five_day": 0.0, "day_pct": vix_pct, "five_day_pct": 0.0},
            "Dollar index": {"level": 103.0, "day": 0.1, "five_day": 1.0, "day_pct": 0.1, "five_day_pct": usd_5d}}


def test_regime_read_tells_rate_fear_from_flight_to_safety():
    assert "RATE FEAR" in macro.regime_read(_m(0.05, 13), -1.6)          # 2026-11-16: stocks and bonds fell together
    assert "FLIGHT TO SAFETY" in macro.regime_read(_m(-0.08, 12), -1.6)
    assert "RISK-ON" in macro.regime_read(_m(0.0, -8), 1.0)
    assert "NO CLEAR REGIME" in macro.regime_read(_m(0.0, 1), 0.1)
    assert "EQUITIES SOFT" in macro.regime_read(_m(0.05, 1), -1.0) and "no fear" not in macro.regime_read(_m(0.0, 1), 0.1).lower()
    assert macro.regime_read({}, -1.0) is None and macro.regime_read(_m(0.0, 1), None) is None


def test_bond_etfs_stay_out_of_the_daily_baseline_but_other_markets_do_not():
    from datetime import datetime, timezone
    base = {"epic": "E", "bid": 99.0, "offer": 101.0, "sma20": 95.0, "price": 100.0, "atr": 2.0}
    rows = [{**base, "market": "UK Gilts ETF"}, {**base, "market": "Gold"}]
    out = ch.baseline_ideas_from_rows(rows, datetime(2026, 11, 17, 8, 0, tzinfo=timezone.utc))
    assert [b["market"] for b in out] == ["Gold"]


def test_regime_read_flags_a_vix_jump_without_a_selloff():
    assert "VIX UP WITHOUT A STOCK SELLOFF" in macro.regime_read(_m(-0.03, 19), -0.24)
