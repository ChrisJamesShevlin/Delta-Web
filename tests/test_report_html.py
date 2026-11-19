from datetime import datetime, timezone
from types import SimpleNamespace

from ig_swing.report_html import build, ladder_levels, text_sections


def test_ladder_is_profit_positive_for_both_directions():
    s = ladder_levels("SELL", 100.0, 100.0, 80.0, 90.0, 10.0)
    assert round(s["now"], 2) == 1.0 and s["target"] == 2.0 and s["stop"] == 0.0 and s["to_stop_pts"] == 10.0
    b = ladder_levels("BUY", 100.0, 90.0, 120.0, 95.0, 10.0)
    assert round(b["now"], 2) == -0.5 and b["stop"] == -1.0 and b["target"] == 2.0 and b["to_plus1r_pts"] == 15.0


def test_text_sections_are_escaped_and_split():
    out = text_sections("## One\nhello <b>\n## Two\nbye")
    assert "<h2>One</h2>" in out and "&lt;b&gt;" in out and "<h2>Two</h2>" in out


def test_build_handles_a_position_without_a_journal_record():
    p = SimpleNamespace(deal_id="X", instrument_name="Test", direction="SELL", open_level=100.0, stop_level=None, limit_level=None, bid=99.0, offer=99.5, unrealised_pnl=0.1, size=0.01)
    page = build([p], {}, 50.0, [("11-12", 50.0)], {}, [], "ok", "## A\nb", datetime.now(timezone.utc))
    assert "not drawn" in page and "Health check" in page and "<h2>A</h2>" in page


def test_position_cards_show_the_new_stop_steps():
    from datetime import datetime, timezone
    from ig_swing.ig_client import Position
    from ig_swing.report_html import position_cards
    now = datetime(2026, 11, 19, 14, 0, tzinfo=timezone.utc)
    silver = Position("D1", "CS.D.USCSI.TODAY.IP", "Spot Silver", "SELL", 0.01, 4734.4, 4665.0, 4178.4, 4520.0, 4522.6, 1.0, 148.1)
    cards, pl, kept = position_cards([silver], {"D1": {"risk_gbp": 2.78, "size": 0.01}}, {}, now)
    html_ = cards[0]
    assert "stop keeps £+0.69" in html_ and "IG trailing 148.1 pts behind" in html_ and "profit locked" in html_
    assert "+0.5R: breakeven, IG trail on" in html_ and "+1R trail" not in html_
    assert abs(kept - 0.694) < 0.01
    fresh = Position("D2", "E", "EUR/USD", "BUY", 0.01, 100.0, 90.0, 120.0, 102.0, 102.1, 1.0, None)
    c2 = position_cards([fresh], {"D2": {"risk_gbp": 0.1, "size": 0.01}}, {}, now)[0][0]
    assert "3 pts to +0.5R" in c2 and "no IG trailing stop" in c2


def test_release_affects_only_what_is_held():
    from ig_swing.report_html import affected
    held = [("Spot Silver", "SELL"), ("EUR/USD", "SELL")]
    assert affected("Initial Jobless Claims (USD)", held) == "Spot Silver sell, EUR/USD sell"
    assert affected("ECB President Lagarde Speech (EUR)", held) == "EUR/USD sell"
    assert affected("Retail Sales MoM (GBP)", held) == ""
    assert affected("EIA Crude Oil Stocks Change (USD)", [("Oil - US Crude", "SELL")]) == "Oil - US Crude sell"
