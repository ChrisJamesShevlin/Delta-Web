"""Offline tests for ig_swing.orders, the account lookup and the daily plan."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from ig_swing.config import LIVE_TEST, Credentials
from ig_swing.context import ContextVerdict
from ig_swing.inputs import CrossAssetInput
from ig_swing.daily import build_plan, format_plan
from ig_swing.events import HIGH, Event
from ig_swing.ig_client import (
    DEMO_BASE_URL, Account, Confirmation, IGClient, MarketDataError, MarketRules, OrderRejected, Position, PriceCache,
)
from ig_swing.journal import Journal
from ig_swing.orders import OrderService
from ig_swing.risk import AccountState, KillSwitch, RiskEngine
from ig_swing.strategy import StrategyParams

UTC = timezone.utc
NOW = datetime(2026, 11, 11, 10, 0, tzinfo=UTC)
EPIC = "IX.D.SPTRD.MONTH1.IP"
RULES = MarketRules(EPIC, "US 500", "DEC-26", 0.01, 1.0, 7616.64, 7617.64)
ACCT = AccountState(30.0, 30.0, 30.0)


def conf(stop_level=7517.0, level=7617.64, deal_id="D1"):
    return Confirmation("REF", deal_id, "ACCEPTED", "SUCCESS", EPIC, "BUY", 0.01, level, stop_level)


class FakeClient:
    def __init__(self, positions=(), confirmation=None, reject=False):
        self.positions, self.confirmation, self.reject = list(positions), confirmation or conf(), reject
        self.calls = []

    def get_market_rules(self, epic):
        return RULES

    def get_positions(self):
        return list(self.positions)

    def place_market_order(self, **kw):
        self.calls.append(("place", kw))
        return "REF"

    def close_position(self, **kw):
        self.calls.append(("close", kw))
        return "REF2"

    def confirm_deal(self, ref):
        if self.reject:
            raise OrderRejected(ref, "MARKET_CLOSED")
        return self.confirmation


def service(tmp_path, client, answer=True, log=None):
    asked = [] if log is None else log
    engine = RiskEngine(LIVE_TEST, KillSwitch(tmp_path / "ks.json"))
    j = Journal(tmp_path / "j.db")

    def confirm(text):
        asked.append(text)
        if client.calls:
            asked.append("PLACED-BEFORE-CONFIRM")
        return answer

    return OrderService(client, engine, j, confirm, env="live"), j, asked


ENTRY = dict(epic=EPIC, expiry="DEC-26", market="US 500", direction="BUY", stop_distance=100.0, target_distance=200.0,
             setup="breakout", account=ACCT, events=[], now=NOW, context=ContextVerdict("BUY"), driver="rates", thesis="t", invalidation="i")


def test_happy_path_places_with_stop_and_journals_the_trade(tmp_path):
    c = FakeClient()
    s, j, asked = service(tmp_path, c)
    r = s.submit_entry(**ENTRY)
    assert r.placed and r.deal_id == "D1" and r.stop_level == 7517.0
    kind, kw = c.calls[0]
    assert kind == "place" and kw["user_confirmed"] is True and kw["stop_distance"] == 100.0 and kw["limit_distance"] == 200.0
    assert "PLACED-BEFORE-CONFIRM" not in asked and EPIC in asked[0]
    t = j.open_trades()[0]
    assert t["entry"] == 7617.64 and t["stop"] == 7517.0 and t["target"] == pytest.approx(7817.64)
    assert t["setup"] == "breakout" and t["driver"] == "rates" and t["deal_id"] == "D1" and t["env"] == "live"
    assert j.decisions()[0]["allowed"] == 1


def test_risk_refusal_never_reaches_the_client_and_is_logged(tmp_path):
    c = FakeClient()
    s, j, asked = service(tmp_path, c)
    blackout = Event(NOW.date(), "FOMC rate decision", HIGH, "Fed schedule", datetime(2026, 11, 11, 19, 0, tzinfo=UTC))
    r = s.submit_entry(**{**ENTRY, "events": [blackout]})
    assert not r.placed and r.status == "refused by risk rules" and any("blackout" in x for x in r.reasons)
    assert c.calls == [] and asked == []              # nobody was even asked
    assert j.decisions()[0]["allowed"] == 0


def test_declining_at_confirmation_places_nothing(tmp_path):
    c = FakeClient()
    s, j, _ = service(tmp_path, c, answer=False)
    r = s.submit_entry(**ENTRY)
    assert not r.placed and c.calls == [] and j.open_trades() == []
    assert j.events()[0]["kind"] == "declined"


def test_rejected_deal_is_recorded_and_no_trade_row_is_written(tmp_path):
    c = FakeClient(reject=True)
    s, j, _ = service(tmp_path, c)
    r = s.submit_entry(**ENTRY)
    assert not r.placed and "MARKET_CLOSED" in r.status and j.open_trades() == []
    assert j.events()[0]["kind"] == "deal_rejected"


def test_a_deal_without_a_stop_is_closed_straight_away(tmp_path):
    c = FakeClient(confirmation=conf(stop_level=None))
    s, j, _ = service(tmp_path, c)
    r = s.submit_entry(**ENTRY)
    assert not r.placed and "closed" in r.status
    assert [k for k, _ in c.calls] == ["place", "close"] and c.calls[1][1]["deal_id"] == "D1"
    assert j.open_trades() == [] and j.events()[0]["kind"] == "CRITICAL_no_stop"


def position(deal_id="D1", size=0.04):
    return Position(deal_id, EPIC, "US 500", "BUY", size, 7600.0, 7500.0, None, 7610.0, 7611.0, 4.0)


def test_close_updates_the_journal_trade(tmp_path):
    c = FakeClient(confirmation=conf(level=7700.0))
    s, j, _ = service(tmp_path, c)
    s.submit_entry(**ENTRY)
    c.calls.clear()
    r = s.submit_close(position("D1", size=0.01), "target reached", NOW)
    assert r.placed and r.status == "closed"
    assert c.calls[0][1]["direction"] == "BUY" and c.calls[0][1]["user_confirmed"] is True
    closed = j.closed_trades()[0]
    assert closed["exit"] == 7700.0 and closed["exit_reason"] == "target reached"


def test_partial_close_is_an_event_and_the_trade_stays_open(tmp_path):
    c = FakeClient(confirmation=conf(level=7650.0))
    s, j, _ = service(tmp_path, c)
    s.submit_entry(**ENTRY)
    r = s.submit_close(position("D1", size=0.04), "halve before CPI", NOW, size=0.02)
    assert r.status == "partly closed" and len(j.open_trades()) == 1
    assert j.events()[-1]["kind"] == "partial_close"


def test_declined_close_does_nothing(tmp_path):
    c = FakeClient()
    s, j, _ = service(tmp_path, c, answer=False)
    assert not s.submit_close(position(), "x", NOW).placed and c.calls == []


# ---- account lookup -------------------------------------------------------


class Resp:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body or {}, headers or {}

    def json(self):
        return self._body


class Session:
    def __init__(self, accounts):
        self.accounts = accounts

    def request(self, method, url, headers=None, params=None, json=None, timeout=None):
        path = url[len(DEMO_BASE_URL):]
        if path == "/session":
            return Resp(200, {"currentAccountId": "ACC1"}, {"CST": "c", "X-SECURITY-TOKEN": "x"})
        return Resp(200, {"accounts": self.accounts})


def test_account_equity_is_balance_plus_open_pnl_and_id_is_hidden(tmp_path):
    accounts = [{"accountId": "OTHER", "balance": {"balance": 999}}, {"accountId": "ACC1", "balance": {"balance": 30.0, "profitLoss": -1.5, "available": 25.0}}]
    c = IGClient(Credentials("k", "u", "p", "ACC1"), session=Session(accounts), cache=PriceCache(tmp_path))
    a = c.get_account("ACC1")
    assert a.equity == pytest.approx(28.5) and a.available == 25.0
    assert "ACC1" not in repr(a)


def test_account_missing_or_malformed_raises(tmp_path):
    c = IGClient(Credentials("k", "u", "p", "ACC1"), session=Session([{"accountId": "ACC1", "balance": {}}]), cache=PriceCache(tmp_path))
    with pytest.raises(MarketDataError):
        c.get_account("ACC1")
    with pytest.raises(MarketDataError):
        c.get_account("NOPE")


# ---- daily plan -----------------------------------------------------------


def bars(closes):
    closes = list(closes)
    return pd.DataFrame({"timestamp": pd.date_range("2025-01-01", periods=len(closes)), "open": [closes[0]] + closes[:-1],
                         "high": [c + 1 for c in closes], "low": [c - 1 for c in closes], "close": closes})


class PlanClient(FakeClient):
    def __init__(self, df, equity=30.0, positions=()):
        super().__init__(positions)
        self.df, self.equity = df, equity

    def get_account(self, account_id):
        return Account(account_id, self.equity, 0.0, self.equity)

    def get_prices(self, epic, resolution, n):
        return self.df


CALM = [CrossAssetInput("VIX", "v", "TRADEABLE", 0.5, 0.1, None, None), CrossAssetInput("Oil (US crude)", "o", "TRADEABLE", 0.2, 0.1, None, None)]


def plan_for(tmp_path, df, equity=30.0, positions=()):
    engine = RiskEngine(LIVE_TEST, KillSwitch(tmp_path / "ks.json"))
    j = Journal(tmp_path / "j.db")
    c = PlanClient(df, equity, positions)
    p = build_plan(c, j, engine, StrategyParams(), epic=EPIC, account_id="ACC1", env="live", events=[], now=NOW,
                   inputs=CALM, headlines=[])
    return p, j, c


def test_plan_reports_a_signal_and_the_risk_verdict_without_ordering(tmp_path):
    p, j, c = plan_for(tmp_path, bars(np.linspace(100, 200, 100)))
    assert p.signal.direction == "BUY" and p.decision.allowed
    assert c.calls == []                              # a plan never places or closes anything
    assert j.decisions()[0]["allowed"] == 1 and j.equity_on_or_before("2026-11-11") == 30.0
    text = format_plan(p)
    assert "ALLOWED" in text and "Plan only. No order was placed." in text


def test_plan_with_no_signal_is_still_logged(tmp_path):
    p, j, _ = plan_for(tmp_path, bars([100.0] * 100))
    assert p.signal is None and "none today" in format_plan(p)
    assert j.decisions()[0]["reasons"] == '["no signal"]'


def test_plan_refusal_lists_reasons_and_warns_about_unprotected_positions(tmp_path):
    naked = Position("D9", "OTHER", "Gold", "BUY", 0.01, 4000.0, None, None, None, None, None)
    p, _, _ = plan_for(tmp_path, bars(np.linspace(100, 200, 100)), positions=[naked])
    assert not p.decision.allowed
    text = format_plan(p)
    assert "REFUSED" in text and "no stop" in text and "WARNING" in text


def test_context_veto_refuses_the_order_before_anyone_is_asked(tmp_path):
    c = FakeClient()
    s, j, asked = service(tmp_path, c)
    r = s.submit_entry(**{**ENTRY, "context": ContextVerdict("BUY", vetoes=("VIX up +12.0% today",))})
    assert not r.placed and any("context veto" in x for x in r.reasons)
    assert c.calls == [] and asked == []


def test_context_halving_reduces_the_stake_and_driver_is_journalled(tmp_path):
    c = FakeClient()
    s, j, asked = service(tmp_path, c)
    # equity 30, LIVE_TEST target 5% = £1.50. Stop 10 pts: full stake 0.15, halved 0.07.
    ctx = ContextVerdict("BUY", size_factor=0.5, notes=("3 stress headlines",), drivers=("headline_stress",))
    r = s.submit_entry(**{**ENTRY, "stop_distance": 10.0, "target_distance": 20.0, "driver": "", "context": ctx})
    assert r.placed and r.size == 0.07
    assert "context: 3 stress headlines" in asked[0]
    assert j.open_trades()[0]["driver"] == "headline_stress"


def test_plan_blocks_a_long_when_the_vix_spikes_and_proposes_nothing_without_positions(tmp_path):
    spike = [CrossAssetInput("VIX", "v", "TRADEABLE", 15.0, 3.0, None, None), CALM[1]]
    engine = RiskEngine(LIVE_TEST, KillSwitch(tmp_path / "ks.json"))
    j = Journal(tmp_path / "j.db")
    c = PlanClient(bars(np.linspace(100, 200, 100)))
    p = build_plan(c, j, engine, StrategyParams(), epic=EPIC, account_id="ACC1", env="live", events=[], now=NOW,
                   inputs=spike, headlines=[])
    assert p.signal.direction == "BUY" and not p.decision.allowed
    assert any("VIX up" in r for r in p.decision.reasons) and p.reductions == []
    assert "vix_spike" in str(dict(j.decisions()[0]))      # the driver is on record


def test_plan_without_context_data_refuses(tmp_path):
    engine = RiskEngine(LIVE_TEST, KillSwitch(tmp_path / "ks.json"))
    j = Journal(tmp_path / "j.db")
    p = build_plan(PlanClient(bars(np.linspace(100, 200, 100))), j, engine, StrategyParams(), epic=EPIC, account_id="ACC1",
                   env="live", events=[], now=NOW, inputs=None, headlines=None)
    assert not p.decision.allowed and any("context not loaded" in r for r in p.decision.reasons)
