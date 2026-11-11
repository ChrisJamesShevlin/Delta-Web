"""Offline tests for ig_swing.bars."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from ig_swing.bars import assert_clean, clean_daily, consolidate_sundays, drop_unfinished

FAR = datetime(2030, 1, 1, tzinfo=timezone.utc)


def df(rows):
    return pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close"]).assign(timestamp=lambda d: pd.to_datetime(d.timestamp))


# 2026-11-08 is a Sunday, 11-09 Monday, 11-10 Tuesday.
SUN_MON = [("2026-11-06", 100, 110, 90, 105), ("2026-11-08", 106, 108, 104, 107), ("2026-11-09", 107, 115, 106, 112), ("2026-11-10", 112, 120, 111, 118)]


def test_sunday_is_merged_into_monday():
    out = consolidate_sundays(df(SUN_MON))
    assert len(out) == 3 and (out.timestamp.dt.dayofweek != 6).all()
    mon = out.iloc[1]
    assert mon.timestamp == pd.Timestamp("2026-11-09")
    assert (mon.open, mon.high, mon.low, mon.close) == (106, 115, 104, 112)      # Sunday open, widest range, Monday close
    assert tuple(out.iloc[0][["open", "high", "low", "close"]]) == (100, 110, 90, 105)    # other bars untouched


def test_sunday_before_a_holiday_merges_into_the_next_trading_day():
    out = consolidate_sundays(df([("2026-11-06", 1, 2, 0, 1), ("2026-11-08", 1, 5, 1, 2), ("2026-11-10", 2, 3, 1, 3)]))
    assert len(out) == 2 and out.iloc[1].timestamp == pd.Timestamp("2026-11-10") and out.iloc[1].high == 5 and out.iloc[1].open == 1


def test_a_trailing_sunday_with_no_following_bar_is_dropped():
    out = clean_daily(df(SUN_MON[:1] + [("2026-11-08", 1, 2, 0, 1)]), FAR)
    assert len(out) == 1 and out.iloc[0].timestamp == pd.Timestamp("2026-11-06")


def test_unfinished_bar_is_dropped_only_when_dated_today_or_later():
    d = df(SUN_MON)
    now = datetime(2026, 11, 10, 17, 0, tzinfo=timezone.utc)
    assert drop_unfinished(d, now).timestamp.max() == pd.Timestamp("2026-11-09")        # 11-10 is in progress
    assert drop_unfinished(d, datetime(2026, 11, 11, 0, 1, tzinfo=timezone.utc)).timestamp.max() == pd.Timestamp("2026-11-10")


def test_unfinished_monday_takes_its_merged_sunday_with_it():
    out = clean_daily(df(SUN_MON[:3]), datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc))
    assert out.timestamp.tolist() == [pd.Timestamp("2026-11-06")]


def test_clean_daily_is_idempotent_and_assert_clean_catches_raw_data():
    once = clean_daily(df(SUN_MON), FAR)
    pd.testing.assert_frame_equal(once, clean_daily(once, FAR))
    assert_clean(once)
    with pytest.raises(ValueError):
        assert_clean(df(SUN_MON))


def test_empty_input_is_safe():
    assert clean_daily(df([]), FAR).empty
