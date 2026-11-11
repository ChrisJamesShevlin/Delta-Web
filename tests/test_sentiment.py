"""Offline tests for ig_swing.sentiment (FRED backdrop)."""

from __future__ import annotations

from datetime import date

import pytest

from ig_swing.sentiment import SERIES, backdrop_snapshot, fetch_series, format_backdrop, load_backdrop
from ig_swing.sources import SourceError


class Resp:
    def __init__(self, status=200, body=None):
        self.status_code, self._b = status, body or {}

    def json(self):
        return self._b


class Session:
    def __init__(self, data, fail=()):
        self.data, self.fail, self.params = data, set(fail), []

    def get(self, url, params=None, headers=None, timeout=None):
        self.params.append(params)
        sid = params["series_id"]
        if sid in self.fail:
            return Resp(500)
        obs = [{"date": d, "value": v} for d, v in self.data.get(sid, [])]
        return Resp(200, {"observations": obs})


DATA = {
    "VIXCLS": [("2026-11-10", "19.46"), ("2026-11-09", "19.92"), ("2026-11-06", "25.64")],
    "BAMLH0A0HYM2": [("2026-11-09", "3.34"), ("2026-11-06", "3.42"), ("2026-11-05", "3.48"), ("2026-11-04", "."),
                     ("2026-11-03", "3.50"), ("2026-11-02", "3.60"), ("2026-10-30", "3.70")],
    "STLFSI4": [("2026-10-30", "-0.5224"), ("2026-10-23", "-0.5842")],
    "T10Y2Y": [("2026-11-10", "0.25")],
}


def test_fetch_skips_missing_values_and_keeps_newest_first():
    obs = fetch_series(Session(DATA), "k", "BAMLH0A0HYM2")
    assert obs[0] == (date(2026, 11, 9), 3.34) and all(v != "." for _, v in obs) and len(obs) == 6


def test_backdrop_levels_and_changes():
    b, notes = load_backdrop(Session(DATA), "k")
    assert notes == [] and b["vix"].value == 19.46 and b["vix"].change == pytest.approx(19.46 - 19.92)
    # high-yield spread: 5 valid observations back from 3.34 is 3.70 (the "." row is skipped)
    assert b["hy_spread"].prior == 3.70 and b["hy_spread"].change == pytest.approx(-0.36)
    assert b["curve"].prior is None and b["curve"].change is None       # not enough history: no invented change


def test_one_failing_series_never_stops_the_rest_and_no_key_leaks():
    b, notes = load_backdrop(Session(DATA, fail={"VIXCLS"}), "SECRETKEY")
    assert "vix" not in b and "hy_spread" in b
    assert notes == ["VIXCLS: unavailable (HTTP 500)"] and "SECRETKEY" not in " ".join(notes)


def test_no_key_is_a_skip_not_a_crash():
    b, notes = load_backdrop(Session(DATA), None)
    assert b == {} and "FRED_API_KEY" in notes[0]


def test_empty_series_is_reported():
    b, notes = load_backdrop(Session({}), "k")
    assert b == {} and len(notes) == len(SERIES)


def test_format_and_snapshot():
    b, _ = load_backdrop(Session(DATA), "k")
    text = "\n".join(format_backdrop(b))
    assert "VIX close: 19.46 on 10 Nov" in text and "lags a day" in text
    snap = backdrop_snapshot(b)
    assert snap["vix"]["date"] == "2026-11-10" and snap["curve"]["change"] is None
    assert format_backdrop({}) == []
