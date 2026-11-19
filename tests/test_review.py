import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from ig_swing.place import exit_line_problem
from ig_swing.review import (exit_line_record, exit_lines, exit_status, flags, latest_reviews, summary_html, summary_rows,
                             validate_review)

NOW = datetime(2026, 11, 19, 14, 0, tzinfo=timezone.utc)
OK = dict(news="nothing new", events_due="US claims 13:30", why="still below the line", sources=["IG headlines"])


def ev(kind, detail, ts=NOW):
    return {"kind": kind, "detail": json.dumps(detail), "ts": ts.isoformat()}


def test_exit_status_sell_crossed_near_clear():
    # the crude case: SELL, old 20-day low about 8316, price back at 8330
    assert exit_status("SELL", 8330, 8316, 412)[0] == "crossed"
    assert exit_status("SELL", 8290, 8316, 412)[0] == "near"        # 26 points < 0.25 x 412
    assert exit_status("SELL", 8150, 8316, 412)[0] == "clear"
    assert exit_status("BUY", 99, 100, 10)[0] == "crossed"
    assert exit_status("BUY", 110, 100, 10)[0] == "clear"
    assert exit_status("SELL", 8330, None, 412)[0] == "none"


def test_exit_line_record_side_and_validation():
    assert exit_line_record("D1", "sell", 8316, "old low")["side"] == "above"
    assert exit_line_record("D1", "BUY", 100, "old high")["side"] == "below"
    with pytest.raises(ValueError):
        exit_line_record("D1", "SELL", 8316, " ")


def test_review_refuses_holding_a_trade_i_would_not_open_now():
    assert validate_review("intact", "no", "hold", "clear", **OK)
    assert validate_review("broken", "yes", "tighten", "clear", **OK)
    assert validate_review("weakened", "yes", "hold", "clear", **OK)
    assert validate_review("intact", "smaller", "hold", "clear", **OK)
    assert not validate_review("intact", "no", "close", "clear", **OK)
    assert not validate_review("weakened", "smaller", "tighten", "clear", **OK)
    assert not validate_review("intact", "yes", "hold", "clear", **OK)


def test_review_refuses_holding_through_a_crossed_line_without_a_reason():
    assert validate_review("intact", "yes", "hold", "crossed", **OK)
    assert not validate_review("intact", "yes", "hold", "crossed", override="waiting for the daily close", **OK)
    assert validate_review("intact", "yes", "hold", "none", **OK)


def test_review_needs_news_and_a_source():
    assert validate_review("intact", "yes", "hold", "clear", news=" ", events_due="x", why="y", sources=["s"])
    assert validate_review("intact", "yes", "hold", "clear", news="x", events_due="x", why="y", sources=[])


def test_flags_unreviewed_stale_and_crossed():
    lines = exit_lines([ev("exit_line", exit_line_record("D1", "SELL", 8316, "old low"))])
    assert any("NOT REVIEWED" in f for f in flags("SELL", "D1", 8200, lines, {}, 412, NOW))
    fresh = latest_reviews([ev("position_review", {"deal_id": "D1", "verdict": "hold"}, NOW - timedelta(minutes=10))])
    assert flags("SELL", "D1", 8200, lines, fresh, 412, NOW) == []
    stale = latest_reviews([ev("position_review", {"deal_id": "D1", "verdict": "hold"}, NOW - timedelta(hours=5))])
    assert any("REVIEW DUE" in f for f in flags("SELL", "D1", 8200, lines, stale, 412, NOW))
    assert any("CROSSED" in f for f in flags("SELL", "D1", 8330, lines, fresh, 412, NOW))
    assert any("NO EXIT LINE" in f for f in flags("SELL", "D2", 8330, lines, fresh, 412, NOW))


def test_latest_exit_line_wins_and_summary_renders():
    evs = [ev("exit_line", exit_line_record("D1", "SELL", 8316, "old low")), ev("exit_line", exit_line_record("D1", "SELL", 8300, "moved <b>"))]
    lines = exit_lines(evs)
    assert lines["D1"]["level"] == 8300
    pos = SimpleNamespace(deal_id="D1", direction="SELL", instrument_name="Oil - US Crude", epic="E", bid=8329, offer=8330)
    rows = summary_rows([pos], lines, {}, {"E": 412}, NOW)
    assert rows[0]["exit_state"] == "crossed"
    out = summary_html(rows, NOW)
    assert "EXIT LINE CROSSED" in out and "<b>moved" not in out


def test_place_exit_line_must_sit_between_entry_and_stop():
    assert exit_line_problem("SELL", 8258.7, 480, 8316) == ""
    assert exit_line_problem("SELL", 8258.7, 480, 8200)            # on the winning side
    assert exit_line_problem("SELL", 8258.7, 480, 8800)            # beyond the stop
    assert exit_line_problem("BUY", 100, 10, 95) == ""
    assert exit_line_problem("BUY", 100, 10, 101)


def test_giveback_flag_and_peak():
    from ig_swing.review import giveback_flag, peak_r
    evs = [ev("position_check", {"deal_id": "D1", "r": 0.2}), ev("position_check", {"deal_id": "D1", "r": 0.77}), ev("position_check", {"deal_id": "D1", "r": 0.4})]
    assert peak_r(evs)["D1"] == 0.77
    assert giveback_flag(0.77, 0.38)                 # EUR/USD on 19 Nov: half handed back
    assert not giveback_flag(0.77, 0.6)
    assert not giveback_flag(0.4, -0.2)              # never well in profit: the exit line and stop cover it
    assert not giveback_flag(None, 0.1)


def test_newswatch_position_news_and_market_names():
    from ig_swing.news import Headline
    from ig_swing.newswatch import keyword_market, markets_named, position_news
    assert keyword_market("Oil - US Crude") == "Crude oil" and keyword_market("Spot Silver") == "Silver"
    opened = NOW - timedelta(days=2)
    hs = [Headline("Silver slides as dollar firms", NOW - timedelta(hours=1), "X"), Headline("Silver bounces", NOW - timedelta(days=3), "X"),
          Headline("Apple launches phone", NOW - timedelta(hours=1), "X")]
    got = position_news("Spot Silver", opened, hs, NOW, last_review=NOW - timedelta(hours=2))
    assert [h.title for h, _ in got] == ["Silver slides as dollar firms"] and got[0][1] is True
    assert "Silver" in markets_named("Silver slides") and markets_named("The dollar and the Fed") == []
