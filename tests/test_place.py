"""Offline tests for ig_swing.place (the manual order command) and the quick market read."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from ig_swing import challenge as ch
from ig_swing import place
from ig_swing.config import CHALLENGE
from ig_swing.ig_client import Confirmation, MarketRules
from ig_swing.journal import Journal
from ig_swing.risk import AccountState, KillSwitch, RiskEngine

NOW = datetime(2026, 11, 11, 21, 0, tzinfo=timezone.utc)
EPIC = "CS.D.EURUSD.TODAY.IP"
ACCT = AccountState(45.57, 45.57, 45.57, 45.57)


class FakeClient:
    def __init__(self):
        self.calls = []

    def get_market_rules(self, epic):
        return MarketRules(epic, "EUR/USD", "DFB", 0.01, 2.0, 11093.2, 11094.0)

    def get_positions(self):
        return []

    def place_market_order(self, **kw):
        self.calls.append(("place", kw))
        return "REF"

    def confirm_deal(self, ref):
        return Confirmation("REF", "D1", "ACCEPTED", "SUCCESS", EPIC, "SELL", 0.01, 11093.2, 11193.2)


def make_env(tmp_path):
    return FakeClient(), Journal(tmp_path / "j.db"), RiskEngine(CHALLENGE, KillSwitch(tmp_path / "ks.json"))


def plan(tmp_path, p=0.40, **kw):
    c, j, e = make_env(tmp_path)
    return c, j, e, place.plan_order(c, j, e, "EUR/USD", "SELL", 100.0, 200.0, p, ACCT, NOW, **kw)


def test_a_small_kelly_stake_rounds_up_to_the_minimum_and_has_a_token(tmp_path):
    _, _, _, pl = plan(tmp_path)
    assert pl["allowed"] and pl["stake"] == 0.01 and pl["direction"] == "SELL" and pl["token"]
    assert pl["decision"].risk_gbp == pytest.approx(1.0) and pl["kelly_eighth_pct"] == pytest.approx(0.96, abs=0.05)
    assert pl["margin"] == pytest.approx(0.01 * 11093.6 * 3.33 / 100, rel=1e-3)
    text = place.describe(pl, 45.57)
    for needle in ("ORDER: SELL EUR/USD", "stake £0.01", "stop 100 points", "target 200", "full Kelly", "1/8 Kelly"):
        assert needle in text


def test_kelly_zero_refuses_unless_overridden(tmp_path):
    _, _, _, pl = plan(tmp_path, p=0.30)
    assert not pl["allowed"] and any("Kelly says do not trade" in r for r in pl["reasons"])
    _, _, _, over = plan(tmp_path, p=0.30, ignore_kelly=True)
    assert over["allowed"] and over["stake"] == 0.01                                   # the minimum stake, by explicit override


def test_the_token_is_tied_to_the_exact_order():
    base = place.token_for("EUR/USD", "SELL", 0.02, 100.0, 200.0)
    assert base == place.token_for("EUR/USD", "sell", 0.02, 100.0, 200.0)
    for other in (place.token_for("EUR/USD", "BUY", 0.02, 100.0, 200.0), place.token_for("EUR/USD", "SELL", 0.03, 100.0, 200.0),
                  place.token_for("EUR/USD", "SELL", 0.02, 120.0, 200.0), place.token_for("EUR/USD", "SELL", 0.02, 100.0, 250.0),
                  place.token_for("GBP/USD", "SELL", 0.02, 100.0, 200.0)):
        assert other != base


def test_no_order_without_the_matching_token(tmp_path):
    c, j, e, pl = plan(tmp_path)
    for bad in (None, "", "deadbeef", place.token_for("EUR/USD", "SELL", 0.05, 100.0, 200.0)):
        r = place.execute(c, j, e, pl, ACCT, NOW, bad, "t", "news_thesis")
        assert not r["placed"] and "token" in r["status"]
    assert c.calls == []                                                               # nothing reached IG


def test_the_matching_token_places_with_a_stop_and_journals_it(tmp_path):
    c, j, e, pl = plan(tmp_path)
    r = place.execute(c, j, e, pl, ACCT, NOW, pl["token"], "Dollar strength fades", "news_thesis")
    assert r["placed"] and r["deal_id"] == "D1"
    kind, kw = c.calls[0]
    assert kind == "place" and kw["user_confirmed"] is True and kw["stop_distance"] == 100.0 and kw["limit_distance"] == 200.0 and kw["expiry"] == "DFB"
    t = j.open_trades()[0]
    assert t["market"] == "EUR/USD" and t["setup"] == "news_thesis" and t["stop"] == 11193.2 and t["driver"] == "challenge"


def test_a_plan_the_risk_rules_refuse_cannot_be_placed_even_with_a_token(tmp_path):
    c, j, e = make_env(tmp_path)
    low = AccountState(14.0, 14.0, 14.0, 45.57)                                          # under the £15 floor
    pl = place.plan_order(c, j, e, "EUR/USD", "SELL", 100.0, 200.0, 0.40, low, NOW)
    assert not pl["allowed"] and any("floor" in r for r in pl["reasons"])
    r = place.execute(c, j, e, pl, low, NOW, pl["token"], "t", "news_thesis")
    assert not r["placed"] and c.calls == []


def test_cooldown_after_two_losses_blocks_the_plan(tmp_path):
    c, j, e = make_env(tmp_path)
    for _ in range(2):
        tid = j.open_trade(opened_at=NOW.isoformat(), epic=EPIC, direction="BUY", size=0.01, entry=11000.0, stop=10900.0, risk_gbp=1.0, env="live")
        j.close_trade(tid, (NOW - timedelta(hours=2)).isoformat(), 10900.0, "stop")
    pl = place.plan_order(c, j, e, "EUR/USD", "SELL", 100.0, 200.0, 0.40, ACCT, NOW)
    assert not pl["allowed"] and any("cooldown" in r for r in pl["reasons"])


def test_build_account_records_equity_and_reads_the_peak(tmp_path):
    class Acct:
        equity = 40.0

    class C(FakeClient):
        def get_account(self, account_id):
            return Acct()

    j = Journal(tmp_path / "j.db")
    j.record_equity("2026-11-09", 52.0, "live")
    a = place.build_account(C(), j, "ACC", NOW)
    assert a.equity == 40.0 and a.peak_equity == 52.0


def test_quick_read_describes_the_move_in_daily_ranges():
    hourly = pd.DataFrame({"high": [11200.0, 11150.0, 11100.0], "low": [11150.0, 11080.0, 11040.0], "close": [11160.0, 11090.0, 11050.0]})
    info = {"price": 11050.0, "pct_change": -1.5, "net_change": -170.0, "spread_pct": 0.008}
    text = ch.format_quick("EUR/USD", info, 70.0, hourly, 65.0, (11100.0, 11400.0), 0)
    for needle in ("EUR/USD: 11050.0", "-1.50%", "-2.4 x", "over two daily ranges", "last 3 hours", "below the range (breakdown)", "65% long", "open positions: 0"):
        assert needle in text
    calm = ch.format_quick("EUR/USD", {**info, "net_change": -20.0, "pct_change": -0.2}, 70.0, None, None, (11000.0, 11200.0), 1)
    assert "over two daily ranges" not in calm and "n/a" in calm and "% of the way up" in calm


def test_relevant_headlines_put_the_markets_own_news_first_then_macro():
    from datetime import datetime, timezone
    from ig_swing.news import Headline
    now = datetime(2026, 11, 11, 20, 0, tzinfo=timezone.utc)
    hs = [Headline("Fed minutes point to slower cuts", now, "a"), Headline("Oil slides as Iran strikes seen unlikely", now, "b"),
          Headline("Celebrity chef opens new restaurant", now, "c"), Headline("OPEC+ weighs output cut", now, "d"),
          Headline("Tariff talks resume with Mexico", now, "e")]
    out = ch.relevant_headlines(hs, "Crude oil", limit=10)
    assert [h.source for h in out] == ["b", "d", "a", "e"]                      # oil and OPEC first, then macro (Fed, tariff), never the chef
    assert ch.relevant_headlines(hs, "Crude oil", limit=2) == out[:2] and ch.relevant_headlines([], "Gold") == []
    assert ch.relevant_headlines(hs, "Not a market") != [] and all(k in ch.MARKET_KEYWORDS for k in ch.MARKETS)


def test_events_are_listed_with_times_when_known_and_only_high_or_medium():
    from datetime import date, datetime, timezone
    from ig_swing.events import Event
    ev = [Event(date(2026, 11, 13), "Producer Price Index (PPI)", "high", "BLS", datetime(2026, 11, 13, 13, 30, tzinfo=timezone.utc)),
          Event(date(2026, 11, 12), "Weekly jobless claims", "medium", "FRED", None), Event(date(2026, 11, 12), "Minor thing", "low", "FRED", None)]
    lines = ch.format_events(ev)
    assert len(lines) == 2 and "Fri 13 Nov 13:30Z" in lines[0] and "PPI" in lines[0] and "(time n/a)" in lines[1] and all("Minor" not in l for l in lines)


def test_the_daily_cap_shows_up_in_the_plan(tmp_path):
    c, j, e = make_env(tmp_path)
    for _ in range(2):
        j.open_trade(opened_at=NOW.isoformat(), epic=EPIC, direction="BUY", size=0.01, entry=11000.0, stop=10900.0, risk_gbp=1.0, env="live")
    pl = place.plan_order(c, j, e, "EUR/USD", "SELL", 100.0, 200.0, 0.40, ACCT, NOW)
    assert not pl["allowed"] and any("daily limit" in r for r in pl["reasons"])


def test_uncalibrated_stake_is_the_minimum_and_kelly_only_filters(tmp_path):
    c, j, e, pl = plan(tmp_path, 0.45)
    assert pl["allowed"] and pl["stake"] == 0.01              # minimum stake, not the Kelly size
    c, j, e, bad = plan(tmp_path, 0.20)
    assert not bad["allowed"]                                  # Kelly still vetoes a negative bet
