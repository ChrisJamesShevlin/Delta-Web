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


# ---- native trailing stops ------------------------------------------------


def test_trailing_plan_needs_one_r_and_never_widens():
    # long from 11000, 100 points of initial risk, now 11100 (+1R) with the stop at 10900: stop to breakeven, trail the 100 point gap
    plan = mg.native_trailing_plan(pos(bid=11100.0), 100.0, min_stop=10.0)
    assert plan == (11000.0, 100.0, 20.0)
    assert mg.native_trailing_plan(pos(bid=11050.0), 100.0) is None                                 # only +0.5R
    assert mg.native_trailing_plan(pos(bid=11200.0, stop=11100.0), 100.0) == (11100.0, 100.0, 20.0)  # the stop is already tighter: trail from there
    assert mg.native_trailing_plan(Position("D1", EPIC, "EUR/USD", "BUY", 0.01, 11000.0, 10900.0, 11200.0, 11100.0, 11101.0, 1.0, 100.0), 100.0) is None   # already trailing
    assert mg.native_trailing_plan(pos(bid=11100.0), 100.0, min_stop=150.0) is None                 # the gap is under the market minimum
    s = pos("SELL", 11000.0, 11100.0, 10800.0, bid=10899.0, offer=10900.0)
    assert mg.native_trailing_plan(s, 100.0) == (11000.0, 100.0, 20.0)                              # a short mirrors it
    assert mg.native_trailing_plan(pos(stop=None, bid=11100.0), 100.0) is None


def test_describe_shows_when_ig_is_trailing():
    p = Position("D1", EPIC, "EUR/USD", "BUY", 0.01, 11000.0, 11000.0, 11200.0, 11100.0, 11101.0, 1.0, 80.0)
    assert "IG trailing 80 points" in mg.describe(p, None)


class TrailClient(FakeClient):
    def amend_position(self, **kw):
        self.calls.append(kw)
        return "REF"


def test_apply_trailing_records_the_stop_and_an_event(tmp_path):
    j = Journal(tmp_path / "j.db")
    open_trade(j)
    r = mg.apply_trailing(TrailClient(), j, pos(bid=11100.0), 11000.0, 100.0, 20.0, NOW)
    assert r == {"deal_id": "D1", "stop": 11000.0, "distance": 100.0, "step": 20.0}
    assert j.open_trades()[0]["stop"] == 11000.0 and [e["kind"] for e in j.events()] == ["trailing_set"]


def test_the_manual_fallback_gives_the_exact_numbers():
    text = mg.MANUAL_TRAILING.format(distance=100.0, step=20.0)
    assert "TRAILING stop" in text and "distance 100 points" in text and "step 20 points" in text and "by hand" in text


def test_client_sends_the_trailing_fields_and_refuses_a_widening_distance(tmp_path):
    from ig_swing.ig_client import MarketRules

    class S(Session):
        def request(self, method, url, headers=None, params=None, json=None, timeout=None):
            path = url[len("https://demo-api.ig.com/gateway/deal"):]
            if path.startswith("/markets/"):
                self.calls.append({"method": method, "path": path, "json": json})
                return Resp({"instrument": {"name": "EUR/USD", "expiry": "DFB"}, "dealingRules": {"minDealSize": {"value": 0.01}, "minNormalStopOrLimitDistance": {"unit": "POINTS", "value": 4.0}},
                             "snapshot": {"bid": 11100.0, "offer": 11101.0}})
            return super().request(method, url, headers, params, json, timeout)

    s = S()
    c = IGClient(CREDS, session=s, cache=PriceCache(tmp_path))
    p = pos(bid=11100.0, stop=11000.0)                                                              # a 100 point gap to the stop
    c.amend_position(position=p, trailing=(100.0, 20.0), user_confirmed=True)
    body = [x for x in s.calls if x["method"] == "PUT"][-1]["json"]
    assert body == {"stopLevel": 11000.0, "trailingStop": True, "trailingStopDistance": 100.0, "trailingStopIncrement": 20.0, "limitLevel": 11200.0}
    with pytest.raises(ValueError):
        c.amend_position(position=p, trailing=(150.0, 20.0), user_confirmed=True)                    # wider than the gap: could widen the stop
    with pytest.raises(ValueError):
        c.amend_position(position=p, trailing=(2.0, 1.0), user_confirmed=True)                       # under the market minimum
    with pytest.raises(ValueError):
        c.amend_position(position=p, trailing=(0.0, 1.0), user_confirmed=True)


# ---- reconciling trades that IG closed on its own -------------------------


def trade_row(**kw):
    base = {"id": 1, "deal_id": "D1", "market": "EUR/USD", "entry": 11000.0, "stop": 10900.0, "target": 11200.0, "size": 0.01,
            "opened_at": "2026-11-12T09:00:00+00:00"}
    base.update(kw)
    return base


def tx(**kw):
    base = {"instrument": "EUR/USD", "open_level": 11000.5, "close_level": 10900.0, "size": 0.01, "pnl_text": "£-1.00", "closed_utc": "2026-11-12T14:00:00",
            "opened_utc": "2026-11-12T09:00:20", "reference": "X"}
    base.update(kw)
    return base


def test_a_closed_deal_is_matched_on_market_size_level_and_time():
    t = trade_row()
    assert mg.match_transaction(t, [tx()]) is not None
    assert mg.match_transaction(t, [tx(instrument="Gold")]) is None                                     # another market
    assert mg.match_transaction(t, [tx(size=0.02)]) is None                                             # another size
    assert mg.match_transaction(t, [tx(open_level=11100.0)]) is None                                    # another entry level
    assert mg.match_transaction(t, [tx(opened_utc="2026-11-12T12:00:00")]) is None                      # opened three hours away
    near, far = tx(opened_utc="2026-11-12T09:00:20", close_level=1.0), tx(opened_utc="2026-11-12T09:30:00", close_level=2.0)
    assert mg.match_transaction(t, [far, near])["close_level"] == 1.0                                   # the nearest in time wins
    assert mg.match_transaction(t, [tx(closed_utc=None, opened_utc=None)]) is not None                  # missing times fall back to market, size and level


def test_exit_reasons_for_server_side_closes():
    t = trade_row()
    assert mg.infer_exit_reason(t, 10900.0) == "stop (closed by IG)"
    assert mg.infer_exit_reason(t, 11199.0) == "target (closed by IG)"
    assert mg.infer_exit_reason(t, 11050.0) == "closed outside a session"
    assert mg.infer_exit_reason(trade_row(target=None), 11200.0) == "closed outside a session"


def test_reconcile_finds_the_trades_ig_no_longer_holds_and_unknown_positions():
    held = pos()                                                                                          # D1 still open
    other = Position("D2", EPIC, "EUR/USD", "BUY", 0.01, 11000.0, 10900.0, None, 11050.0, 11051.0, 0.5)
    plan = mg.reconcile_plan([trade_row(deal_id="D1"), trade_row(id=2, deal_id="D3")], [held, other], [tx()])
    assert [i["trade"]["deal_id"] for i in plan["closed"]] == ["D3"] and plan["closed"][0]["reason"] == "stop (closed by IG)"
    assert [p.deal_id for p in plan["unknown_positions"]] == ["D2"]
    empty = mg.reconcile_plan([trade_row(deal_id="D1")], [held], [])
    assert empty == {"closed": [], "unknown_positions": []}


def test_applying_a_reconcile_closes_the_journal_trade_with_the_reason_and_the_observed_range(tmp_path):
    j = Journal(tmp_path / "j.db")
    open_trade(j)
    row = j.open_trades()[0]
    plan = mg.reconcile_plan([row], [], [tx(opened_utc=row["opened_at"].replace("+00:00", ""))])
    out = mg.apply_reconcile(j, plan, {"D1": [0.4, 1.2, -0.3]}, NOW)
    assert "recorded as closed at 10900" in out[0] and "stop (closed by IG)" in out[0]
    closed = j.closed_trades()[0]
    assert closed["exit"] == 10900.0 and closed["exit_reason"] == "stop (closed by IG)" and closed["r_multiple"] == pytest.approx(-1.0)
    assert "best +1.20R and worst -0.30R seen at 3 check(s)" in closed["notes"] and "£-1.00" in closed["notes"]
    assert j.open_trades() == []


def test_an_unmatched_close_is_reported_not_guessed(tmp_path):
    j = Journal(tmp_path / "j.db")
    open_trade(j)
    plan = mg.reconcile_plan(j.open_trades(), [], [])
    out = mg.apply_reconcile(j, plan, {}, NOW)
    assert "no matching closed deal was found" in out[0] and len(j.open_trades()) == 1                    # left open on record: a human must check


def test_excursion_note_wording():
    assert "no position checks" in mg.excursion_note([])
    assert mg.excursion_note([0.5]) == "best +0.50R and worst +0.50R seen at 1 check(s) (coarse: only moments when a session looked)"


def test_transactions_are_normalised_by_the_client(tmp_path):
    class S(Session):
        def request(self, method, url, headers=None, params=None, json=None, timeout=None):
            path = url[len("https://demo-api.ig.com/gateway/deal"):]
            if path == "/history/transactions":
                self.calls.append({"method": method, "path": path, "params": params})
                return Resp({"transactions": [{"instrumentName": "EUR/USD", "openLevel": "11000.5", "closeLevel": "10900", "size": "-0.01", "profitAndLoss": "£-1.00",
                                               "dateUtc": "2026-11-12T14:00:00", "openDateUtc": "2026-11-12T09:00:20", "reference": "X", "transactionType": "DEAL"}]})
            return super().request(method, url, headers, params, json, timeout)

    from datetime import date
    s = S()
    c = IGClient(CREDS, session=s, cache=PriceCache(tmp_path))
    got = c.get_transactions(date(2026, 11, 12))
    assert got == [{"instrument": "EUR/USD", "open_level": 11000.5, "close_level": 10900.0, "size": 0.01, "pnl_text": "£-1.00", "closed_utc": "2026-11-12T14:00:00",
                    "opened_utc": "2026-11-12T09:00:20", "reference": "X", "type": "DEAL"}]


def test_limit_and_trailing_events_keep_the_why_and_notes_are_journalled(tmp_path):
    import json
    j = Journal(tmp_path / "j.db")
    open_trade(j)
    mg.apply_limit(FakeClient(), j, pos(), 11300.0, NOW, "momentum intact, calendar clear")
    mg.apply_trailing(TrailClient(), j, pos(bid=11100.0), 11000.0, 100.0, 20.0, NOW, "up 1R")
    mg.record_note(j, NOW, "Gold", "flat, spread 3x normal", "no action")
    ev = {e["kind"]: json.loads(e["detail"]) for e in j.events()}
    assert ev["limit_set"]["why"] == "momentum intact, calendar clear" and ev["trailing_set"]["why"] == "up 1R"
    assert ev["check_note"] == {"market": "Gold", "saw": "flat, spread 3x normal", "decision": "no action"}
