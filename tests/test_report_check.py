from datetime import datetime, timezone
from types import SimpleNamespace

from ig_swing.report_check import book_exposure, cooldown_text, expected_r, sentiment_cell


def test_expected_r_matches_the_kelly_cost_assumption():
    assert round(expected_r(0.36, 2.0), 2) == 0.03
    assert expected_r(0.30, 2.0) < 0


def test_book_exposure_nets_the_dollar_across_pairs():
    rows = [{"market": "EUR/USD", "direction": "SELL", "notional": 100.0}, {"market": "AUD/USD", "direction": "SELL", "notional": 60.0},
            {"market": "Gold", "direction": "BUY", "notional": 40.0}]
    b = book_exposure(rows, 50.0)
    assert b["usd"] == 160.0 and b["long"] == 40.0 and b["short"] == 160.0 and b["n_short"] == 2
    assert b["dollar_group"] == 160.0 - 40.0


def test_sentiment_flags_crowding_against_and_with_us():
    assert "against us" in sentiment_cell(90.0, "SELL")
    assert "with us" in sentiment_cell(90.0, "BUY")
    assert "crowded" not in sentiment_cell(57.0, "SELL")
    assert "none published" in sentiment_cell(None, "BUY")


def test_cooldown_text_reports_streaks():
    lim = SimpleNamespace(cooldown_losses=2, cooldown_hours=24.0)
    now = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
    closed = [{"closed_at": "2026-11-17T08:00:00+00:00", "r_multiple": -0.3}, {"closed_at": "2026-11-17T14:00:00+00:00", "r_multiple": -0.7}]
    assert "ACTIVE" in cooldown_text(closed, lim, now)
    assert "not active" in cooldown_text(closed[:1], lim, now)
