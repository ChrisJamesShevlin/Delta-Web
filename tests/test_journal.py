"""Offline tests for ig_swing.journal."""

from __future__ import annotations

import json

import pytest

from ig_swing.journal import Journal


def journal(tmp_path):
    return Journal(tmp_path / "j.db")


def open_long(j, **over):
    kw = dict(opened_at="2026-11-12T09:00:00Z", epic="E", direction="BUY", size=0.02, entry=7600.0, stop=7500.0,
              target=7800.0, risk_gbp=2.0, env="live", setup="breakout", driver="rates")
    kw.update(over)
    return j.open_trade(**kw)


def test_long_trade_pnl_and_r_multiple(tmp_path):
    j = journal(tmp_path)
    tid = open_long(j)
    assert len(j.open_trades()) == 1 and j.closed_trades() == []
    row = j.close_trade(tid, "2026-11-20T09:00:00Z", 7700.0, "target")
    assert row["gross_pnl"] == pytest.approx(2.0)      # 0.02 x 100
    assert row["net_pnl"] == pytest.approx(2.0)
    assert row["r_multiple"] == pytest.approx(1.0)      # risk was £2
    assert j.open_trades() == [] and len(j.closed_trades()) == 1


def test_short_trade_pnl_and_financing_is_deducted(tmp_path):
    j = journal(tmp_path)
    tid = open_long(j, direction="SELL", entry=7600.0, stop=7700.0, size=0.01, risk_gbp=1.0)
    row = j.close_trade(tid, "2026-11-20T09:00:00Z", 7500.0, "target", financing=0.10, spread_cost=0.02)
    assert row["gross_pnl"] == pytest.approx(1.0)
    assert row["net_pnl"] == pytest.approx(0.90)
    assert row["spread_cost"] == pytest.approx(0.02)    # recorded only, not deducted again


def test_a_stopped_out_trade_is_minus_one_r(tmp_path):
    j = journal(tmp_path)
    tid = open_long(j)
    assert j.close_trade(tid, "2026-11-13T09:00:00Z", 7500.0, "stop")["r_multiple"] == pytest.approx(-1.0)


def test_closing_twice_or_an_unknown_trade_is_an_error(tmp_path):
    j = journal(tmp_path)
    tid = open_long(j)
    j.close_trade(tid, "t", 7600.0, "manual")
    with pytest.raises(ValueError):
        j.close_trade(tid, "t", 7600.0, "manual")
    with pytest.raises(KeyError):
        j.close_trade(999, "t", 1.0, "x")


def test_bad_direction_is_refused(tmp_path):
    with pytest.raises(ValueError):
        open_long(journal(tmp_path), direction="HOLD")


def test_decisions_log_refusals_with_reasons(tmp_path):
    j = journal(tmp_path)
    j.record_decision("2026-11-11T21:00:00Z", "E", "BUY", "breakout", False, 0.0, ["event blackout: FOMC"], {"stop": 112})
    d = j.decisions()[0]
    assert d["allowed"] == 0 and json.loads(d["reasons"]) == ["event blackout: FOMC"] and json.loads(d["context"]) == {"stop": 112}


def test_events_and_equity_start_of_day_is_kept(tmp_path):
    j = journal(tmp_path)
    j.record_event("2026-11-11T10:00:00Z", "kill_switch", "daily loss limit")
    assert j.events()[0]["kind"] == "kill_switch"
    j.record_equity("2026-11-11", 30.0, "live")
    j.record_equity("2026-11-11", 27.0, "live")        # later in the day: ignored
    j.record_equity("2026-11-12", 28.0, "live")
    assert j.equity_on_or_before("2026-11-11") == 30.0
    assert j.equity_on_or_before("2026-11-13") == 28.0
    assert j.equity_on_or_before("2026-11-10") is None


def test_data_survives_reopening_the_database(tmp_path):
    j = journal(tmp_path)
    open_long(j)
    assert len(Journal(tmp_path / "j.db").open_trades()) == 1


def test_a_partial_close_banks_profit_keeps_the_original_risk_and_the_final_close_adds_up(tmp_path):
    j = Journal(tmp_path / "j.db")
    tid = j.open_trade(opened_at="2026-11-12T10:00:00+00:00", epic="E", direction="SELL", size=0.07, entry=6833.4, stop=6903.4, risk_gbp=4.9)
    row = j.partial_close(tid, 0.06, 6827.8)                             # 0.06 closed 5.6 points in profit
    assert row["size"] == 0.01 and row["initial_size"] == 0.07 and round(row["realised_partial"], 4) == 0.336
    closed = j.close_trade(tid, "2026-11-13T10:00:00+00:00", 6813.4, "target")      # the last 0.01 closes 20 points in profit
    assert round(closed["gross_pnl"], 4) == round(0.336 + 0.01 * 20.0, 4) and round(closed["r_multiple"], 4) == round(0.536 / 4.9, 4)


def test_a_partial_close_must_be_smaller_than_the_open_size_and_the_schema_migrates(tmp_path):
    import sqlite3
    import pytest
    j = Journal(tmp_path / "j.db")
    tid = j.open_trade(opened_at="2026-11-12T10:00:00+00:00", epic="E", direction="BUY", size=0.02, entry=100.0, stop=90.0, risk_gbp=1.0)
    with pytest.raises(ValueError):
        j.partial_close(tid, 0.02, 101.0)
    old = tmp_path / "old.db"
    c = sqlite3.connect(old)
    c.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY AUTOINCREMENT, env TEXT, opened_at TEXT NOT NULL, closed_at TEXT, market TEXT, epic TEXT NOT NULL, "
              "expiry TEXT, direction TEXT NOT NULL, size REAL NOT NULL, entry REAL NOT NULL, stop REAL NOT NULL, target REAL, risk_gbp REAL, setup TEXT, "
              "driver TEXT, thesis TEXT, invalidation TEXT, deal_ref TEXT, deal_id TEXT, exit REAL, exit_reason TEXT, gross_pnl REAL, spread_cost REAL, "
              "financing REAL, net_pnl REAL, r_multiple REAL, notes TEXT)")
    c.commit()
    c.close()
    Journal(old)
    cols = {r[1] for r in sqlite3.connect(old).execute("PRAGMA table_info(trades)")}
    assert {"realised_partial", "initial_size"} <= cols
