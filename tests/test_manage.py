"""Offline tests for ig_swing.manage and IGClient.amend_position. Every number is hand-checkable."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ig_swing import manage as mg
from ig_swing.config import Credentials, LIVE_BASE_URL
from ig_swing.ig_client import Confirmation, IGClient, Position, PriceCache
from ig_swing.journal import Journal
from ig_swing.risk import RiskViolation

NOW = datetime(2026, 11, 12, 9, 0, tzinfo=timezone.utc)
EPIC = "CS.D.EURUSD.TODAY.IP"
CREDS = Credentials("k", "u", "p", "ACC1")


def pos(direction="BUY", open_level=11000.0, stop=10900.0, limit=11200.0, bid=11050.0, offer=11051.0, size=0.01, pnl=0.5):
    return Position("D1", EPIC, "EUR/USD", direction, size, open_level, stop, limit, bid, offer, pnl)


# ---- the maths ------------------------------------------------------------


def test_initial_risk_comes_from_the_journal_else_from_the_stop():
    assert mg.initial_risk_points(pos(), {"risk_gbp": 1.0, "size": 0.01}) == pytest.approx(100.0)
    assert mg.initial_risk_points(pos(stop=10950.0), None) == pytest.approx(50.0)                 # the current stop distance
    assert mg.initial_risk_points(pos(stop=None), None) is None


def test_move_in_r_for_long_and_short_in_favour_and_against():
    assert mg.move_in_r(pos(bid=11100.0), 100.0) == pytest.approx(1.0)
    assert mg.move_in_r(pos(bid=10950.0), 100.0) == pytest.approx(-0.5)
    s = pos("SELL", 11000.0, 11100.0, 10800.0, bid=10899.0, offer=10900.0)
    assert mg.move_in_r(s, 100.0) == pytest.approx(1.0) and mg.move_in_r(s, None) is None


def test_trail_rule_breakeven_at_one_r_lock_one_r_at_two_and_never_loosens():
    assert mg.trail_target(pos(bid=11050.0), 100.0) is None                                        # only +0.5R
    assert mg.trail_target(pos(bid=11100.0), 100.0) == 11000.0                                     # +1R: stop to breakeven
    assert mg.trail_target(pos(bid=11200.0), 100.0) == 11100.0                                     # +2R: lock in 1R
    assert mg.trail_target(pos(bid=11100.0, stop=11000.0), 100.0) is None                          # already at breakeven: nothing to do
    assert mg.trail_target(pos(bid=11200.0, stop=11150.0), 100.0) is None                          # the current stop is already tighter
    s = pos("SELL", 11000.0, 11100.0, 10800.0, bid=10899.0, offer=10900.0)
    assert mg.trail_target(s, 100.0) == 11000.0
    s2 = pos("SELL", 11000.0, 11100.0, 10800.0, bid=10799.0, offer=10800.0)
    assert mg.trail_target(s2, 100.0) == 10900.0
    assert mg.trail_target(pos(stop=None, bid=11100.0), 100.0) is None                             # no stop: not this rule's job


def test_describe_shows_pnl_r_levels_risk_and_the_thesis():
    trade = {"risk_gbp": 1.0, "size": 0.01, "thesis": "Dollar fades on soft data", "invalidation": "stop 100 points"}
    text = mg.describe(pos(bid=11100.0, pnl=1.0), trade)
    for needle in ("BUY EUR/USD [D1]", "P&L £+1.00", "+1.00R", "stop 10900", "take-profit 11200", "if stopped out: £-1.00", "Dollar fades", "stop 100 points"):
        assert needle in text
    assert "NONE (unprotected!)" in mg.describe(pos(stop=None), None)
    assert "(breakeven)" in mg.describe(pos(stop=11000.0, bid=11100.0), None)                        # the stop is at the entry
    assert "£+0.50 (profit locked in)" in mg.describe(pos(stop=11050.0, bid=11100.0), None)          # beyond the entry: profit is locked
    sell = pos("SELL", 11000.0, 11100.0, 10800.0, bid=10899.0, offer=10900.0)
    assert "if stopped out: £-1.00" in mg.describe(sell, None)


def test_history_groups_early_exits_apart_from_stop_and_target_hits():
    closed = [{"exit_reason": "thesis_invalidated: broke the range", "r_multiple": -0.4}, {"exit_reason": "thesis_invalidated: gap", "r_multiple": 0.2},
              {"exit_reason": "stop", "r_multiple": -1.0}, {"exit_reason": "target", "r_multiple": 2.0}, {"exit_reason": "reversal: flipped", "r_multiple": -0.3},
              {"exit_reason": None, "r_multiple": 0.1}]
    g = mg.history_by_reason(closed)
    assert g["thesis_invalidated"] == [-0.4, 0.2] and g["stop hit"] == [-1.0] and g["target hit"] == [2.0] and g["reversal"] == [-0.3] and g["other"] == [0.1]
    text = mg.format_history(closed)
    assert "Closed trades: 6" in text and "thesis_invalidated: n=2, mean -0.10R" in text and "too few to say anything" in text
    assert mg.format_history([]) == "No closed trades yet."


# ---- the client: amending a stop ------------------------------------------


class Resp:
    def __init__(self, body, status=200, headers=None):
        self.status_code, self._b, self.headers = status, body, headers or {}
        self.text = ""

    def json(self):
        return self._b


class Session:
    def __init__(self):
        self.calls = []

    def request(self, method, url, headers=None, params=None, json=None, timeout=None):
        path = url[len("https://demo-api.ig.com/gateway/deal"):]
        self.calls.append({"method": method, "path": path, "json": json, "headers": headers})
        if path == "/session":
            return Resp({"currentAccountId": "ACC1"}, headers={"CST": "c", "X-SECURITY-TOKEN": "x"})
        return Resp({"dealReference": "REF3"})


def amend_client(tmp_path):
    s = Session()
    return IGClient(CREDS, session=s, cache=PriceCache(tmp_path)), s


def test_amend_sends_both_levels_and_tightens(tmp_path):
    c, s = amend_client(tmp_path)
    assert c.amend_position(position=pos(), new_stop=10950.0, user_confirmed=True) == "REF3"
    call = s.calls[-1]
    assert call["method"] == "PUT" and call["path"] == "/positions/otc/D1" and call["json"] == {"stopLevel": 10950.0, "trailingStop": False, "limitLevel": 11200.0}
    c.amend_position(position=pos(), new_limit=11300.0, user_confirmed=True)
    assert s.calls[-1]["json"] == {"stopLevel": 10900.0, "trailingStop": False, "limitLevel": 11300.0}      # the current stop is re-sent so it is not cleared


def test_amend_refuses_widening_removing_wrong_side_and_unconfirmed(tmp_path):
    c, s = amend_client(tmp_path)
    with pytest.raises(RiskViolation):
        c.amend_position(position=pos(), new_stop=10800.0, user_confirmed=True)                    # widens a long's stop
    with pytest.raises(RiskViolation):
        c.amend_position(position=pos("SELL", 11000.0, 11100.0, 10800.0, 10999.0, 11000.0), new_stop=11200.0, user_confirmed=True)
    with pytest.raises(ValueError):
        c.amend_position(position=pos(stop=None), user_confirmed=True)                             # a stop is mandatory
    with pytest.raises(ValueError):
        c.amend_position(position=pos(bid=11050.0), new_stop=11060.0, user_confirmed=True)         # a long's stop must be below the price
    with pytest.raises(PermissionError):
        c.amend_position(position=pos(), new_stop=10950.0, user_confirmed=False)
    assert all(call["method"] != "PUT" for call in s.calls)                                         # nothing reached IG


def test_amend_is_blocked_on_live_unless_orders_are_enabled(tmp_path):
    c = IGClient(CREDS, base_url=LIVE_BASE_URL, allow_live=True, session=Session(), cache=PriceCache(tmp_path))
    with pytest.raises(PermissionError):
        c.amend_position(position=pos(), new_stop=10950.0, user_confirmed=True)


# ---- applying changes and the journal -------------------------------------


class FakeClient:
    def __init__(self):
        self.calls = []

    def amend_position(self, **kw):
        self.calls.append(kw)
        return "REF"

    def confirm_deal(self, ref):
        stop = self.calls[-1].get("new_stop")
        return Confirmation("REF", "D1", "ACCEPTED", "SUCCESS", EPIC, "BUY", 0.01, 11000.0, stop)


def open_trade(j):
    return j.open_trade(opened_at=NOW.isoformat(), epic=EPIC, direction="BUY", size=0.01, entry=11000.0, stop=10900.0, target=11200.0, risk_gbp=1.0,
                        env="live", market="EUR/USD", deal_id="D1", thesis="t", invalidation="i")


def test_apply_stop_and_limit_update_the_journal_and_log_events(tmp_path):
    j = Journal(tmp_path / "j.db")
    open_trade(j)
    r = mg.apply_stop(FakeClient(), j, pos(), 11000.0, NOW, "trail rule")
    assert r == {"deal_id": "D1", "from": 10900.0, "to": 11000.0}
    assert j.open_trades()[0]["stop"] == 11000.0
    mg.apply_limit(FakeClient(), j, pos(), 11300.0, NOW)
    assert j.open_trades()[0]["target"] == 11300.0
    kinds = [e["kind"] for e in j.events()]
    assert kinds == ["stop_moved", "limit_set"]


def test_journal_update_levels_only_touches_open_trades(tmp_path):
    j = Journal(tmp_path / "j.db")
    tid = open_trade(j)
    assert j.update_levels("NOPE", stop=1.0) is False
    j.close_trade(tid, NOW.isoformat(), 11100.0, "target")
    assert j.update_levels("D1", stop=11050.0) is False                                              # already closed
