"""Offline tests for ig_swing.challenge. Every number is hand-checkable."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from ig_swing import challenge as ch

NOW = datetime(2026, 11, 2, 10, 0, tzinfo=timezone.utc)


def test_kelly_by_hand():
    assert ch.kelly_fraction(0.45, 2.0, cost_r=0.0) == pytest.approx(0.175)                  # 0.45 - 0.55 / 2
    b = (2.0 - 0.05) / 1.05
    assert ch.kelly_fraction(0.45, 2.0) == pytest.approx(0.45 - 0.55 / b)
    assert ch.kelly_fraction(0.33, 2.0, cost_r=0.0) == pytest.approx(0.0, abs=0.0051)         # no edge: bet nothing
    assert ch.kelly_fraction(0.30, 2.0) == 0.0 and ch.kelly_fraction(0.0, 2.0) == 0.0 and ch.kelly_fraction(0.5, 0.0) == 0.0


def test_one_eighth_kelly_absorbs_a_ten_point_overestimate():
    claimed = ch.kelly_fraction(0.45, 2.0, cost_r=0.0)                                         # 17.5%
    true = ch.kelly_fraction(0.35, 2.0, cost_r=0.0)                                            # 2.5%
    assert claimed == pytest.approx(0.175) and true == pytest.approx(0.025)
    assert ch.KELLY_FRACTION * claimed == pytest.approx(0.021875) and ch.KELLY_FRACTION * claimed < 1.0 * true   # inside the true Kelly bet


def test_suggested_risk_is_an_eighth_and_capped_until_calibrated():
    f = ch.kelly_fraction(0.45, 2.0)
    assert ch.suggested_risk_pct(0.45, 2.0) == pytest.approx(100 * 0.125 * f)
    assert ch.suggested_risk_pct(0.9, 3.0) == ch.CAP_UNTIL_CALIBRATED                          # a huge claimed edge is capped
    assert ch.suggested_risk_pct(0.9, 3.0, calibrated=True) == pytest.approx(min(ch.CAP_CALIBRATED, 100 * 0.125 * ch.kelly_fraction(0.9, 3.0)))
    assert ch.suggested_risk_pct(0.30, 2.0) == 0.0


def idea(**kw):
    base = dict(idea_id=1, now=NOW, market="EUR/USD", direction="BUY", entry=11000.0, spread_pts=2.0, stop_pts=100.0, target_pts=200.0,
                p_win=0.4, playbook="news_thesis", confidence="MEDIUM", thesis="Dollar weakens on soft data", horizon_days=5)
    base.update(kw)
    return ch.make_idea(**base)


@pytest.mark.parametrize("kw", [dict(market="Bitcoin"), dict(direction="HOLD"), dict(stop_pts=0), dict(target_pts=-1), dict(p_win=0.99), dict(p_win=0.01),
                                dict(playbook="vibes"), dict(confidence="CERTAIN"), dict(thesis=""), dict(thesis="x" * 301), dict(horizon_days=0), dict(horizon_days=31)])
def test_idea_validation(kw):
    with pytest.raises(ch.InvalidIdea):
        idea(**kw)


def bars(rows, start="2026-11-03"):
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
    df.insert(0, "timestamp", pd.bdate_range(start, periods=len(rows)))
    return df


FLAT = (11000.0, 11010.0, 10990.0, 11000.0)


def test_target_stop_gap_and_time_exits_by_hand():
    i = idea()                                                                     # entry 11000, stop 10900, target 11200, spread 2 (half 1)
    t = ch.score_idea(i, bars([FLAT, (11000.0, 11250.0, 10995.0, 11200.0)]))
    assert t["reason"] == "target" and t["r"] == pytest.approx((11200 - 11000) / 100)
    s = ch.score_idea(i, bars([FLAT, (11000.0, 11005.0, 10850.0, 10900.0)]))
    assert s["reason"] == "stop" and s["r"] == pytest.approx(-1.0)
    both = ch.score_idea(i, bars([(11000.0, 11250.0, 10850.0, 11000.0)]))
    assert both["reason"] == "stop"                                                 # a stop and a target in one bar count as a stop
    gap = ch.score_idea(i, bars([(10800.0, 10810.0, 10790.0, 10800.0)]))
    assert gap["reason"] == "stop_gap" and gap["r"] == pytest.approx((10799 - 11000) / 100)
    time = ch.score_idea(idea(horizon_days=2), bars([FLAT, (11000.0, 11010.0, 10990.0, 11050.0), FLAT]))
    assert time["reason"] == "time" and time["r"] == pytest.approx((11050 - 1 - 11000) / 100)       # exits at the close less half the spread


def test_short_mirrors_the_long_and_unfinished_ideas_return_none():
    i = idea(direction="SELL", entry=11000.0)                                       # stop 11100, target 10800
    t = ch.score_idea(i, bars([(11000.0, 11005.0, 10750.0, 10800.0)]))
    assert t["reason"] == "target" and t["r"] == pytest.approx(2.0)
    assert ch.score_idea(i, bars([FLAT])) is None                                   # horizon is 5 days: one quiet bar is not enough
    assert ch.score_idea(i, bars([FLAT] * 3, start="2026-10-20")) is None            # bars all before the idea: nothing to score


def test_bars_on_the_day_of_the_idea_are_ignored():
    i = idea()
    wild_same_day = bars([(11000.0, 11500.0, 10000.0, 11000.0)], start="2026-11-02")  # the day the idea was written: not scored
    assert ch.score_idea(i, wild_same_day) is None


def test_calibration_flags_overconfidence():
    ideas = []
    for k in range(40):
        i = idea(idea_id=k + 1, p_win=0.45)
        ideas.append((i, {"r": 2.0 if k < 14 else -1.0}))                           # 14 of 40 win: 35%
    cal = ch.calibration(ideas)
    assert cal["n"] == 40 and cal["predicted"] == pytest.approx(0.45) and cal["actual"] == pytest.approx(0.35)
    assert not cal["calibrated"] or abs(cal["predicted"] - cal["actual"]) <= ch.CALIBRATION_TOLERANCE + 1e-9
    honest = [(idea(idea_id=k + 1, p_win=0.35), {"r": 2.0 if k < 14 else -1.0}) for k in range(40)]
    assert ch.calibration(honest)["calibrated"]                                      # stated 35%, actual 35%, and 40 ideas
    assert not ch.calibration(honest[:10])["calibrated"]                             # too few ideas to trust


def test_measure_and_report_state_sample_size_and_the_cap():
    ideas = [idea(idea_id=1), idea(idea_id=2, playbook="trend")]
    b = {"EUR/USD": bars([FLAT, (11000.0, 11250.0, 10995.0, 11200.0)])}
    m = ch.measure(ideas, lambda market: b[market])
    assert m["total"] == 2 and len(m["finished"]) == 2
    text = ch.format_report(m)
    for needle in ("n=2", "playbook news_thesis", "Probability check", "Kelly cap", "held at 10%", "under 30: noise"):
        assert needle in text


def test_stake_hint_by_hand():
    stake, note = ch.stake_hint(45.57, 10.0, 40.0, 11093.0, 3.33)                    # 10% = 4.557 over 40 points = 0.1139, limited by margin
    assert stake == pytest.approx(0.07) and note == "margin-limited"
    assert ch.stake_hint(45.57, 10.0, 400.0, 11093.0, 3.33) == (0.01, "")           # wide stop: the minimum stake
    assert ch.stake_hint(1.0, 10.0, 400.0, 11093.0, 3.33)[1] == "below the minimum stake"


def test_loading_ideas_ignores_other_events_and_garbage():
    import json
    events = [{"kind": "news_view", "detail": "{}"}, {"kind": ch.KIND, "detail": json.dumps(idea())}, {"kind": ch.KIND, "detail": "garbage"}]
    assert [i["id"] for i in ch.load_ideas(events)] == [1]


def test_briefing_text_contains_the_pieces():
    rows = [{"market": "EUR/USD", "price": 11093.3, "day_pct": 0.1, "atr": 60.0, "atr_pct": 0.54, "range_pos": 0.4, "long_pct": 65.0, "stake": 0.07,
             "note": "margin-limited", "spread_pct": 0.008, "sent_id": "EURUSD"}]
    text = ch.format_briefing(NOW, rows, 45.57, 0, ["x: unavailable"], ["  Fri 13 Nov PPI [HIGH]"], ["  09:00 Fed holds"])
    for needle in ("Equity £45.57", "EUR/USD", "65% long", "£0.07/pt margin-limited", "PPI", "Fed holds", "No order has been placed"):
        assert needle in text


def test_constants():
    assert ch.KELLY_FRACTION == 0.125 and ch.CAP_UNTIL_CALIBRATED == 10.0 and ch.CAP_CALIBRATED == 20.0 and ch.CALIBRATION_MIN == 30
    assert len(ch.MARKETS) == 9
