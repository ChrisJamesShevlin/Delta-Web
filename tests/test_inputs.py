"""Offline tests for ig_swing.inputs, the snapshot call and the briefing section."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ig_swing.briefing import format_briefing
from ig_swing.config import Credentials
from ig_swing.ig_client import DEMO_BASE_URL, IGClient, MarketDataError, PriceCache, Snapshot
from ig_swing.inputs import FLAT_BAND_PCT, INPUT_EPICS, CrossAssetInput, load_inputs

NOW = datetime(2026, 11, 11, 14, 0, tzinfo=timezone.utc)
CREDS = Credentials(api_key="k", username="u", password="p", account_id="A")


class Resp:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body or {}, headers or {}

    def json(self):
        return self._body


class Session:
    def __init__(self, routes):
        self.routes = routes

    def request(self, method, url, headers=None, params=None, json=None, timeout=None):
        path = url[len(DEMO_BASE_URL):]
        if (method, path) == ("POST", "/session"):
            return Resp(200, {"currentAccountId": "A"}, {"CST": "c", "X-SECURITY-TOKEN": "x"})
        return self.routes[(method, path)]


def client_with(routes, tmp_path):
    return IGClient(CREDS, session=Session(routes), cache=PriceCache(tmp_path))


def market_body(**snap):
    return {"instrument": {"name": "x", "expiry": "DFB"}, "dealingRules": {}, "snapshot": snap}


def test_snapshot_parses_change_fields_and_tolerates_missing_bid(tmp_path):
    body = market_body(marketStatus="TRADEABLE", bid=None, offer=None, netChange=-0.49, percentageChange=-2.51, high=19.64, low=18.92, updateTime="14:12:49")
    s = client_with({("GET", "/markets/E"): Resp(200, body)}, tmp_path).get_snapshot("E")
    assert s.bid is None and s.pct_change == -2.51 and s.net_change == -0.49 and s.update_time == "14:12:49"


def test_snapshot_without_a_snapshot_block_raises(tmp_path):
    c = client_with({("GET", "/markets/E"): Resp(200, {"instrument": {}})}, tmp_path)
    with pytest.raises(MarketDataError):
        c.get_snapshot("E")


class FakeClient:
    def __init__(self, snaps, fail=()):
        self.snaps, self.fail = snaps, fail

    def get_snapshot(self, epic):
        if epic in self.fail:
            raise RuntimeError("token=SECRET-IN-MESSAGE")
        return self.snaps[epic]


def snap(epic, pct, bid=None, offer=None):
    return Snapshot(epic, "TRADEABLE", bid, offer, 1.0, pct, None, None, "14:00:00")


def test_load_inputs_direction_and_flat_band():
    snaps = {
        INPUT_EPICS["VIX"]: snap(INPUT_EPICS["VIX"], -2.51),
        INPUT_EPICS["Oil (US crude)"]: snap(INPUT_EPICS["Oil (US crude)"], 1.02, 8611.9, 8614.9),
        INPUT_EPICS["Gold"]: snap(INPUT_EPICS["Gold"], FLAT_BAND_PCT / 2, 4029.25, 4029.55),
    }
    got, notes = load_inputs(FakeClient(snaps))
    by = {i.name: i for i in got}
    assert notes == []
    assert (by["VIX"].direction, by["Oil (US crude)"].direction, by["Gold"].direction) == ("DOWN", "UP", "FLAT")
    assert by["VIX"].level is None and by["Oil (US crude)"].level == pytest.approx(8613.4)


def test_missing_percentage_gives_no_direction():
    assert CrossAssetInput("X", "E", "TRADEABLE", None, None, None, None).direction is None


def test_one_failing_market_does_not_stop_the_rest_and_never_leaks():
    snaps = {INPUT_EPICS["Gold"]: snap(INPUT_EPICS["Gold"], 0.5, 1, 2)}
    got, notes = load_inputs(FakeClient(snaps, fail=(INPUT_EPICS["VIX"], INPUT_EPICS["Oil (US crude)"])))
    assert [i.name for i in got] == ["Gold"]
    assert "VIX: unavailable (RuntimeError)" in notes
    assert "SECRET-IN-MESSAGE" not in " ".join(notes)


def test_briefing_shows_cross_asset_section_first():
    inputs = [CrossAssetInput("VIX", "E", "TRADEABLE", -2.51, -0.49, None, None), CrossAssetInput("Gold", "G", "TRADEABLE", 0.5, 3.0, 4029.4, None)]
    text = format_briefing(NOW, [], [], [], inputs=inputs)
    assert text.index("Cross-asset today") < text.index("High impact")
    assert "-2.51%" in text and "DOWN" in text and "UP" in text


def test_briefing_without_inputs_omits_the_section():
    assert "Cross-asset" not in format_briefing(NOW, [], [], [])
