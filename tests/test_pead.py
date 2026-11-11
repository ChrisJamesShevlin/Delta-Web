"""Offline tests for ig_swing.edgar, ig_swing.stock_prices and ig_swing.pead_test."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from ig_swing import edgar, pead_test as pt
from ig_swing.indicators import atr
from ig_swing.stock_prices import parse_chart

UTC = timezone.utc


def utc(y, m, d, h, mi=0, sec=0):
    return datetime(y, m, d, h, mi, sec, tzinfo=UTC)


# ---- EDGAR ----------------------------------------------------------------


def test_earnings_times_only_8k_with_item_202():
    block = {"form": ["8-K", "8-K", "10-Q", "8-K/A", "8-K"],
             "items": ["2.02,9.01", "5.02", "2.02", "2.02", "2.02"],
             "acceptanceDateTime": ["2026-11-02T21:30:23.000Z", "2026-11-03T12:00:00.000Z", "2026-11-04T12:00:00.000Z",
                                    "2026-11-05T12:00:00.000Z", "garbage"]}
    assert edgar.earnings_times(block) == [utc(2026, 11, 2, 21, 30, 23)]


def test_dedupe_keeps_the_first_filing_in_a_cluster():
    ts = [utc(2026, 1, 28, 21), utc(2026, 1, 30, 21), utc(2026, 5, 1, 21), utc(2026, 5, 2, 12)]
    assert edgar.dedupe(ts) == [utc(2026, 1, 28, 21), utc(2026, 5, 1, 21)]


class Resp:
    def __init__(self, body, status=200):
        self.status_code, self._b = status, body

    def json(self):
        return self._b


class EdgarSession:
    def __init__(self):
        self.urls = []

    def get(self, url, headers=None, timeout=None, params=None):
        self.urls.append(url)
        if url.endswith("company_tickers.json"):
            return Resp({"0": {"cik_str": 320193, "ticker": "AAPL"}})
        if "CIK0000320193.json" in url:
            return Resp({"filings": {"recent": {"form": ["8-K"], "items": ["2.02"], "acceptanceDateTime": ["2026-11-02T21:30:23.000Z"]},
                                     "files": [{"name": "CIK0000320193-submissions-001.json"}]}})
        if "submissions-001" in url:
            return Resp({"form": ["8-K"], "items": ["2.02"], "acceptanceDateTime": ["2015-10-27T20:30:00.000Z"]})
        return Resp({}, 404)


def test_fetch_all_reads_recent_and_older_pages_and_reports_unknown_tickers(tmp_path):
    s = EdgarSession()
    events, notes = edgar.fetch_all(s, ["AAPL", "ZZZZ"], pause=0)
    assert events["AAPL"] == [utc(2015, 10, 27, 20, 30), utc(2026, 11, 2, 21, 30, 23)]
    assert notes == ["ZZZZ: not in the EDGAR ticker list"]
    assert any("submissions-001" in u for u in s.urls)
    edgar.save_events(events, tmp_path / "e.json")
    assert edgar.load_events(tmp_path / "e.json") == events


# ---- prices ---------------------------------------------------------------


def test_parse_chart_scales_ohlc_by_the_adjustment_and_drops_bad_rows():
    payload = {"chart": {"result": [{"timestamp": [1700000000, 1700086400, 1700172800],
                                     "indicators": {"quote": [{"open": [100, None, 102], "high": [110, 111, 112], "low": [90, 91, 92],
                                                               "close": [100, 101, 102], "volume": [1, 2, 3]}],
                                                    "adjclose": [{"adjclose": [50, 50.5, 51]}]}}]}}
    df = parse_chart(payload)
    assert len(df) == 2                                                  # the row with a missing open is dropped
    assert df["close"].tolist() == [50.0, 51.0] and df["open"].iloc[0] == pytest.approx(50.0) and df["high"].iloc[0] == pytest.approx(55.0)


# ---- reaction day ---------------------------------------------------------


def days(n=80, start="2026-01-05"):
    return pd.bdate_range(start, periods=n)


def test_reaction_day_before_open_after_close_weekend_and_intraday():
    d = days()
    wed = pd.Timestamp("2026-01-14")
    i_wed, i_thu = d.get_loc(wed), d.get_loc(pd.Timestamp("2026-01-15"))
    assert pt.reaction_index(d, utc(2026, 1, 14, 21, 30)) == i_thu        # 16:30 ET Wednesday, after the close: Thursday reacts
    assert pt.reaction_index(d, utc(2026, 1, 14, 12, 30)) == i_wed        # 07:30 ET Wednesday, before the open: Wednesday reacts
    assert pt.reaction_index(d, utc(2026, 1, 14, 17, 0)) is None          # 12:00 ET, during the session: excluded
    sat = pt.reaction_index(d, utc(2026, 1, 17, 15, 0))                   # a Saturday: the next Monday reacts
    assert d[sat] == pd.Timestamp("2026-01-19")


def test_daylight_saving_time_is_respected():
    d = days(200, "2026-03-02")
    jul = pd.Timestamp("2026-07-30")
    assert d[pt.reaction_index(d, utc(2026, 7, 30, 20, 30))] == pd.Timestamp("2026-07-31")   # 16:30 EDT: after the close
    assert d[pt.reaction_index(d, utc(2026, 7, 30, 13, 0))] == jul                           # 09:00 EDT: before the open
    assert pt.reaction_index(days(), utc(2026, 1, 14, 14, 30)) is None                       # 09:30 EST in winter is the open itself


# ---- one event, by hand ---------------------------------------------------


def frames(n=90, r=30, jump=0.10, spy_drift=0.0):
    """Flat 100 stock with a reaction at r (close jumps), entry open at r+1 equal to the new level. SPY optionally drifts."""
    dates = days(n)
    close = np.full(n, 100.0)
    close[r:] = 100 * (1 + jump)
    open_ = np.r_[close[0], close[:-1]]
    open_[r + 1] = close[r]
    px = pd.DataFrame({"timestamp": dates, "open": open_, "high": close + 1, "low": close - 1, "close": close, "volume": 1.0})
    spy_c = 100 * np.cumprod(np.r_[1, np.full(n - 1, 1 + spy_drift)])
    spy = pd.DataFrame({"timestamp": dates, "open": spy_c, "high": spy_c + 1, "low": spy_c - 1, "close": spy_c, "volume": 1.0})
    return px, spy, dates


def accepted_for(dates, r):
    """A filing accepted after the close of the session before r, so r reacts."""
    d = dates[r - 1]
    return datetime(d.year, d.month, d.day, 22, 0, tzinfo=UTC)


def test_long_event_time_exit_matches_hand_calculation():
    px, spy, dates = frames()
    px.loc[50, "close"] = 121.0                                           # exit session (r + 20) closes 10% above the entry open of 110
    res = pt.evaluate_event("T", px, spy, accepted_for(dates, 30))
    assert res.direction == 1 and res.reaction == pytest.approx(0.10) and res.exit_reason == "time"
    assert res.gross_raw == pytest.approx(0.10) and res.gross_adj == pytest.approx(0.10) and res.traded_pct == pytest.approx(0.10)
    a = float(atr(px.iloc[:31], 14).iloc[-1])
    assert res.stop_pct == pytest.approx(2 * a / 110) and res.traded_r == pytest.approx((121 - 110) / (2 * a))


def test_market_adjustment_removes_the_benchmark_move():
    px, spy, dates = frames(spy_drift=0.01)
    px.loc[50, "close"] = 121.0
    res = pt.evaluate_event("T", px, spy, accepted_for(dates, 30))
    spy_move = spy["close"].iloc[50] / spy["open"].iloc[31] - 1
    assert res.gross_adj == pytest.approx(0.10 - spy_move) and spy_move > 0.15


def test_stop_costs_exactly_one_r_and_a_gap_costs_more():
    px, spy, dates = frames()
    px.loc[33, "low"] = 10.0
    res = pt.evaluate_event("T", px, spy, accepted_for(dates, 30))
    assert res.exit_reason == "stop" and res.traded_r == pytest.approx(-1.0) and res.traded_pct == pytest.approx(-res.stop_pct)
    px2, spy2, dates2 = frames()
    px2.loc[33, ["open", "low"]] = [50.0, 49.0]
    gap = pt.evaluate_event("T", px2, spy2, accepted_for(dates2, 30))
    assert gap.exit_reason == "stop_gap" and gap.traded_r < -1.0


def test_short_event_mirrors_the_long():
    px, spy, dates = frames(jump=-0.10)
    px.loc[50, "close"] = 81.0                                            # entry open 90, exit 81: the short gains 10%
    res = pt.evaluate_event("T", px, spy, accepted_for(dates, 30))
    assert res.direction == -1 and res.gross_raw == pytest.approx(0.10) and res.exit_reason == "time"


def test_skips_are_named():
    px, spy, dates = frames(jump=0.03)
    assert pt.evaluate_event("T", px, spy, accepted_for(dates, 30)) == "below_threshold"
    px, spy, dates = frames()
    assert pt.evaluate_event("T", px, spy, accepted_for(dates, 85)) == "no_data"            # not enough bars after the event
    d = dates[30]
    assert pt.evaluate_event("T", px, spy, datetime(d.year, d.month, d.day, 17, 0, tzinfo=UTC)) == "intraday"


def test_net_returns_under_each_cost_scenario():
    res = pt.EventResult("T", pd.Timestamp("2020-01-02"), 0.1, 1, 0.05, 0.05, 0.05, 1.0, 0.05, 30, "time")
    a, b, c, d = (pt.net_pct(res, s) for s in pt.SCENARIOS.values())
    assert a == pytest.approx(0.05) and b == pytest.approx(0.05 - 0.001 - 0.065 * 30 / 365) and c == pytest.approx(b - 0.008)
    assert d == pytest.approx(0.05 - 0.00125 - 0.062 * 30 / 365)
    short = pt.EventResult("T", pd.Timestamp("2020-01-02"), -0.1, -1, 0.05, 0.05, 0.05, 1.0, 0.05, 30, "time")
    assert pt.net_pct(short, pt.SCENARIOS["B spread 0.10% + financing (long 6.5% a year, short 3.0%)"]) == pytest.approx(0.05 - 0.001 - 0.03 * 30 / 365)


# ---- statistics -----------------------------------------------------------


def test_clustered_se_by_hand():
    xs, keys = [1.0, 1.0, -1.0, -1.0], ["a", "a", "b", "b"]
    assert pt.clustered_se(xs, keys) == pytest.approx(math.sqrt(8) / 4)               # cluster sums +2 and -2
    assert pt.mean_se(xs)[1] == pytest.approx(math.sqrt(4 / 3) / 2)
    assert pt.clustered_se(xs, ["a", "b", "c", "d"]) == pytest.approx(math.sqrt(4) / 4)   # singletons: sqrt(sum of squares)/n


def test_verdict_needs_enough_events_and_a_big_z():
    assert "no conclusion" in pt.verdict({"n": 50, "z": 9.0})
    assert "no evidence" in pt.verdict({"n": 500, "z": 1.9})
    assert pt.verdict({"n": 500, "z": 2.5}) == "SUPPORTS continuation"
    assert "reversal" in pt.verdict({"n": 500, "z": -2.5})


def test_pipeline_end_to_end_skips_early_events_and_reports():
    px, spy, dates = frames(n=120)
    px.loc[50, "close"] = 121.0
    events = {"AAA": [accepted_for(dates, 30), datetime(2003, 5, 1, 21, tzinfo=UTC)], "BBB": [accepted_for(dates, 30)]}
    results, skipped = pt.run(events, {"AAA": px}, spy)
    assert len(results) == 1 and results[0].ticker == "AAA" and skipped == {"no_prices": 1}
    text = pt.format_report(results, skipped, 1)
    assert "PRIMARY" in text and "clustered by calendar quarter" in text and "Holdout" in text and "no conclusion" in text


def test_settings_are_the_pre_registered_ones():
    assert (pt.REACTION_THRESHOLD, pt.HOLD_DAYS, pt.STOP_ATR) == (0.05, 20, 2.0)
    assert pt.HOLDOUT_START.year == 2017 and pt.EVENT_START.year == 2005 and len(pt.UNIVERSE) >= 95 and len(set(pt.UNIVERSE)) == len(pt.UNIVERSE)
    assert set(pt.SCENARIOS) and pt.SCENARIOS["C as B plus 0.8% currency fee"]["fx"] == 0.008
