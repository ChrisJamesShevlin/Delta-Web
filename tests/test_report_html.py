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
