"""Offline tests for ig_swing.risk."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from ig_swing.config import AGREED, LIVE_TEST
from ig_swing.events import HIGH, MEDIUM, Event
from ig_swing.ig_client import Position
from ig_swing.risk import (
    AccountState,
    EntryProposal,
    EventRules,
    KillSwitch,
    RiskEngine,
    RiskViolation,
    blocking_events,
    check_loss_limits,
    next_day_start,
    next_week_start,
    open_risk,
    plan_event_reductions,
    size_position,
    validate_stop_move,
)

UTC = timezone.utc
NOW = datetime(2026, 11, 11, 10, 0, tzinfo=UTC)  # a Wednesday
EPIC = "IX.D.SPTRD.MONTH1.IP"


def pos(deal="D1", epic=EPIC, direction="BUY", size=0.01, level=7600.0, stop=7500.0):
    return Position(deal, epic, "US 500", direction, size, level, stop, None, None, None, None)


def proposal(stop=100.0, epic=EPIC, direction="BUY"):
    return EntryProposal(epic, direction, stop, min_deal_size=0.01, min_stop_distance=1.0)


def acct(equity=30.0, sod=None, sow=None):
    return AccountState(equity, sod if sod is not None else equity, sow if sow is not None else equity)


def timed(label, hours_from_now, tier=HIGH):
    t = NOW + timedelta(hours=hours_from_now)
    return Event(t.date(), label, tier, "T", t)


def engine(tmp_path, limits=LIVE_TEST):
    return RiskEngine(limits, KillSwitch(tmp_path / "ks.json"))


# ---- sizing ---------------------------------------------------------------


def test_sizing_matches_the_worked_numbers_for_30_pounds_live_test():
    ok = size_position(30, 112, LIVE_TEST, 0.01)
    assert ok.allowed and ok.size == 0.01 and ok.risk_gbp == pytest.approx(1.12)
    refused = size_position(30, 169, LIVE_TEST, 0.01)
    assert not refused.allowed and "over the 5.0% cap" in refused.reason


def test_more_money_makes_the_wider_stop_fit():
    assert size_position(50, 169, LIVE_TEST, 0.01).allowed


def test_agreed_profile_refuses_a_swing_stop_on_30_pounds():
    assert not size_position(30, 112, AGREED, 0.01).allowed  # 1% cap is 30p


def test_larger_account_sizes_up_and_rounds_down_to_the_step():
    r = size_position(1000, 50, AGREED, 0.01)  # 0.5% = £5, stop 50 -> 0.10
    assert r.allowed and r.size == 0.10 and r.risk_gbp == pytest.approx(5.0)
    r2 = size_position(1000, 70, AGREED, 0.01)  # 5 / 70 = 0.0714 -> 0.07
    assert r2.size == 0.07 and r2.risk_gbp <= 5.0


def test_risk_pct_request_above_the_cap_is_clamped():
    r = size_position(1000, 10, AGREED, 0.01, risk_pct=50)
    assert r.risk_pct <= AGREED.max_risk_per_trade_pct + 1e-9


@pytest.mark.parametrize("bal,stop", [(0, 100), (-5, 100), (30, 0), (30, -1), (30, float("nan")), (float("inf"), 100)])
def test_bad_inputs_are_refused(bal, stop):
    assert not size_position(bal, stop, LIVE_TEST, 0.01).allowed


# ---- open risk and stops --------------------------------------------------


def test_open_risk_sums_stops_and_flags_missing_ones():
    total, bad = open_risk([pos("A", size=0.02, level=7600, stop=7500), pos("B", direction="SELL", level=7600, stop=7650, size=0.01)])
    assert total == pytest.approx(0.02 * 100 + 0.01 * 50) and bad == []
    _, bad = open_risk([pos("C", stop=None)])
    assert bad == ["C"]


def test_a_stop_past_breakeven_risks_nothing():
    total, _ = open_risk([pos(level=7600, stop=7650)])  # BUY with stop above entry
    assert total == 0.0


def test_stops_can_tighten_but_never_widen():
    validate_stop_move("BUY", 7500, 7550)
    validate_stop_move("SELL", 7700, 7650)
    validate_stop_move("BUY", 7500, 7500)
    with pytest.raises(RiskViolation):
        validate_stop_move("BUY", 7500, 7450)
    with pytest.raises(RiskViolation):
        validate_stop_move("SELL", 7700, 7750)


# ---- loss limits and kill switch -----------------------------------------


def test_loss_limits_use_the_profile():
    assert check_loss_limits(acct(equity=27.1, sod=30, sow=30), LIVE_TEST) == []        # just inside the 10% daily limit
    assert any("daily" in r for r in check_loss_limits(acct(equity=27.0, sod=30, sow=30), LIVE_TEST))  # reaching it halts
    assert any("weekly" in r for r in check_loss_limits(acct(equity=23.9, sod=24, sow=30), LIVE_TEST))
    assert any("daily" in r for r in check_loss_limits(acct(equity=29.3, sod=30, sow=30), AGREED))  # 2%


def test_breaching_a_loss_limit_trips_the_kill_switch_until_next_period(tmp_path):
    e = engine(tmp_path)
    d = e.evaluate_entry(proposal(), acct(equity=26, sod=30, sow=30), [], [], NOW)
    assert not d.allowed and any("daily loss limit" in r for r in d.reasons)
    active, _ = e.kill_switch.status(NOW)
    assert active
    # Even with recovered equity the same day, the switch still blocks.
    d2 = e.evaluate_entry(proposal(), acct(), [], [], NOW + timedelta(hours=3))
    assert not d2.allowed and any("kill switch active" in r for r in d2.reasons)
    # Next day it clears.
    d3 = e.evaluate_entry(proposal(), acct(), [], [], next_day_start(NOW) + timedelta(minutes=1))
    assert d3.allowed


def test_weekly_trip_lasts_until_next_monday(tmp_path):
    e = engine(tmp_path)
    e.evaluate_entry(proposal(), acct(equity=23, sod=23, sow=30), [], [], NOW)
    assert e.kill_switch.status(next_day_start(NOW) + timedelta(hours=1))[0]
    assert not e.kill_switch.status(next_week_start(NOW) + timedelta(minutes=1))[0]
    assert next_week_start(NOW) == datetime(2026, 11, 16, tzinfo=UTC)


def test_kill_switch_manual_trip_is_indefinite_until_reset(tmp_path):
    ks = KillSwitch(tmp_path / "ks.json")
    ks.trip("manual stop", now=NOW)
    assert ks.status(NOW + timedelta(days=30)) == (True, "manual stop")
    ks.trip("loss limit", until=next_day_start(NOW), now=NOW)   # must not shorten it
    assert ks.status(NOW + timedelta(days=30))[0]
    ks.reset()
    assert ks.status(NOW) == (False, "")


def test_kill_switch_unreadable_file_fails_safe(tmp_path):
    p = tmp_path / "ks.json"
    p.write_text("{not json")
    active, why = KillSwitch(p).status(NOW)
    assert active and "failing safe" in why


def test_a_longer_halt_is_not_shortened_by_a_shorter_one(tmp_path):
    ks = KillSwitch(tmp_path / "ks.json")
    ks.trip("weekly", until=next_week_start(NOW), now=NOW)
    ks.trip("daily", until=next_day_start(NOW), now=NOW)
    assert ks.status(next_day_start(NOW) + timedelta(hours=1))[0]


# ---- event blackout and reductions ---------------------------------------


def test_fomc_blocks_for_24h_before_and_cpi_for_12h():
    fomc, cpi = timed("FOMC rate decision", 20), timed("Consumer Price Index (CPI)", 20)
    assert blocking_events([fomc], NOW) == [fomc]
    assert blocking_events([cpi], NOW) == []
    assert blocking_events([timed("Consumer Price Index (CPI)", 11)], NOW)


def test_blackout_ends_two_hours_after_the_release():
    assert blocking_events([timed("Employment Situation (jobs report)", -1.5)], NOW)
    assert not blocking_events([timed("Employment Situation (jobs report)", -2.5)], NOW)


def test_date_only_events_block_their_own_day_and_medium_never_blocks():
    today_ev = Event(NOW.date(), "Retail sales (advance)", HIGH, "FRED", None)
    tomorrow_ev = Event(NOW.date() + timedelta(days=1), "Retail sales (advance)", HIGH, "FRED", None)
    med = Event(NOW.date(), "Weekly jobless claims", MEDIUM, "FRED", None)
    assert blocking_events([today_ev, tomorrow_ev, med], NOW) == [today_ev]


def test_reductions_halve_before_a_timed_event_only():
    ev = timed("Consumer Price Index (CPI)", 3)
    got = plan_event_reductions([pos("A", size=0.04), pos("B", size=0.01)], [ev], NOW, min_size=0.01)
    assert [(g.deal_id, g.close_size) for g in got] == [("A", 0.02)]   # 0.01 is too small to halve
    assert plan_event_reductions([pos(size=0.04)], [timed("Consumer Price Index (CPI)", 6)], NOW, 0.01) == []
    assert plan_event_reductions([pos(size=0.04)], [Event(NOW.date(), "GDP", HIGH, "FRED", None)], NOW, 0.01) == []


def test_reduction_never_leaves_less_than_the_minimum():
    got = plan_event_reductions([pos(size=0.03)], [timed("FOMC rate decision", 2)], NOW, min_size=0.02)
    assert got == []   # halving 0.03 gives 0.01 closed and 0.02 left, but 0.01 < min 0.02


# ---- the gate -------------------------------------------------------------


def test_clean_entry_is_allowed_with_size_and_risk(tmp_path):
    d = engine(tmp_path).evaluate_entry(proposal(stop=100), acct(30), [], [], NOW)
    assert d.allowed and d.size == 0.01 and d.risk_gbp == pytest.approx(1.0) and d.reasons == ()


def test_every_failing_rule_is_reported(tmp_path):
    e = engine(tmp_path)
    d = e.evaluate_entry(
        proposal(stop=500, epic=EPIC), acct(30), [pos(stop=None)], [timed("FOMC rate decision", 5)], NOW,
    )
    text = " | ".join(d.reasons)
    assert not d.allowed and d.size == 0.0
    assert "maximum is 1" in text            # live_test allows one open position
    assert "already holding this market" in text
    assert "no stop" in text
    assert "over the 5.0% cap" in text       # 0.01 x 500 = £5 on £30
    assert "event blackout: FOMC rate decision" in text


def test_total_open_risk_cap(tmp_path):
    limits = LIVE_TEST.__class__(name="t", risk_per_trade_pct=5, max_risk_per_trade_pct=5, daily_loss_limit_pct=50,
                                 weekly_loss_limit_pct=50, max_open_positions=3, max_total_open_risk_pct=5)
    e = RiskEngine(limits, KillSwitch(tmp_path / "ks.json"))
    open_pos = [pos("A", epic="OTHER", size=0.01, level=7600, stop=7500)]   # £1.00 at risk of £1.50 cap
    d = e.evaluate_entry(proposal(stop=100), acct(30), open_pos, [], NOW)
    assert not d.allowed and any("total open risk" in r for r in d.reasons)


def test_stop_below_market_minimum_and_bad_direction_refused(tmp_path):
    e = engine(tmp_path)
    assert any("below the market minimum" in r for r in e.evaluate_entry(proposal(stop=0.5), acct(), [], [], NOW).reasons)
    assert any("BUY or SELL" in r for r in e.evaluate_entry(proposal(direction="HOLD"), acct(), [], [], NOW).reasons)
    assert any("mandatory" in r for r in e.evaluate_entry(proposal(stop=0), acct(), [], [], NOW).reasons)
