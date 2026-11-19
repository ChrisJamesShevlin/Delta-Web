import json
from datetime import date

import pytest

from ig_swing import options_test as ot
from ig_swing import wheel_shadow as ws


def test_monthly_target_nearest_35_days_with_21_minimum():
    assert ws.monthly_target(date(2026, 11, 19)) == (date(2026, 12, 18), "DEC-26")
    assert ws.monthly_target(date(2026, 11, 30)) == (date(2027, 1, 15), "JAN-27")


def test_implied_vol_round_trip():
    p, _ = ot.black("P", 7250.0, 7000.0, 30 / 365, 0.25)
    assert ws.implied_vol("P", 7250.0, 7000.0, 30 / 365, p) == pytest.approx(0.25, abs=1e-4)
    assert ws.implied_vol("P", 7250.0, 7500.0, 30 / 365, 200.0) is None   # below intrinsic


def test_pick_strike_uses_ig_prices_and_respects_the_floor():
    f, t = 7250.0, 30 / 365
    quotes = {}
    for k in (6900.0, 7000.0, 7100.0):
        mid = ot.black("P", f, k, t, 0.22)[0]
        quotes[k] = (mid - 1, mid + 1)
    best = ws.pick_strike(quotes, "P", f, t)
    assert best["k"] == 7000.0 or best["k"] == 7100.0
    assert abs(abs(best["delta"]) - 0.30) < 0.1
    calls = {k: (ot.black("C", f, k, t, 0.2)[0] - 1, ot.black("C", f, k, t, 0.2)[0] + 1) for k in (7300.0, 7400.0, 7500.0)}
    assert ws.pick_strike(calls, "C", f, t, floor_k=7450.0)["k"] == 7500.0


def test_replay_tracks_the_wheel_cycle():
    ev = lambda d: {"kind": ws.KIND, "ts": "t", "detail": json.dumps(d)}
    evs = [ev({"market": "US500", "action": "start", "epic": "F1", "price": 7000.0, "last_dealing": "2026-12-18", "index": 7000}),
           ev({"market": "US500", "action": "sell", "kind": "P", "k": 6800.0, "code": "DEC-26", "settle": "2026-12-18", "epic": "O1", "bid": 50.0, "offer": 52.0, "mid": 51.0, "model_mid": 50.0}),
           ev({"market": "US500", "action": "settle", "epic": "O1", "kind": "P", "k": 6800.0, "settle_value": 6700.0, "intrinsic": 100.0, "itm": True}),
           ev({"market": "US500", "action": "long_open", "epic": "F1", "price": 6710.0, "last_dealing": "2026-12-18", "basis": 6800.0})]
    s = ws.replay(evs)["US500"]
    assert s["phase"] == "LONG" and s["basis"] == 6800.0
    assert s["wheel_cash"] == pytest.approx(-50.0)
    assert s["premium"] == 50.0 and s["sales"] == 1
    out = ws.pnl(ws.replay(evs), {"US500": 6750.0, "RUT": 0.0}, {"US500": None})
    assert out["US500"]["wheel"] == pytest.approx(-10.0)
    assert out["US500"]["l0"] == pytest.approx(-250.0)
