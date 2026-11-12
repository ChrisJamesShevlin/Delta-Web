"""SQLite trade journal. Every decision and trade is logged, including refusals.

Prices stored for a trade are actual fill levels (the buy side pays the offer,
the sell side gets the bid), so gross P&L already contains the spread.
``spread_cost`` is recorded for reporting only and is not deducted again.
Financing is deducted. Futures bets have none, daily funded bets do.

Never put credentials, tokens or account ids in any field, note or context.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    env TEXT, opened_at TEXT NOT NULL, closed_at TEXT,
    market TEXT, epic TEXT NOT NULL, expiry TEXT,
    direction TEXT NOT NULL, size REAL NOT NULL,
    entry REAL NOT NULL, stop REAL NOT NULL, target REAL, risk_gbp REAL,
    setup TEXT, driver TEXT, thesis TEXT, invalidation TEXT,
    deal_ref TEXT, deal_id TEXT,
    exit REAL, exit_reason TEXT,
    gross_pnl REAL, spread_cost REAL, financing REAL, net_pnl REAL, r_multiple REAL,
    notes TEXT
);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL, epic TEXT, direction TEXT, setup TEXT,
    allowed INTEGER NOT NULL, size REAL, reasons TEXT, context TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL, kind TEXT NOT NULL, detail TEXT
);
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL, day TEXT NOT NULL, market TEXT NOT NULL, epic TEXT NOT NULL,
    price REAL, bid REAL, offer REAL, spread_pct REAL, day_pct REAL, atr REAL, atr_pct REAL,
    range_pos REAL, long_pct REAL, sma20 REAL
);
CREATE INDEX IF NOT EXISTS idx_snapshots_day ON snapshots(day, epic);
CREATE TABLE IF NOT EXISTS baseline_ideas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL, epic TEXT NOT NULL, detail TEXT NOT NULL,
    UNIQUE(day, epic)
);
CREATE TABLE IF NOT EXISTS equity_daily (
    day TEXT PRIMARY KEY, equity REAL NOT NULL, env TEXT
);
"""


class Journal:
    def __init__(self, path: str | Path = "data/journal.db"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)
            cols = {r["name"] for r in c.execute("PRAGMA table_info(snapshots)")}
            if "tech" not in cols:  # added 2026-11-12: the technical read archived as JSON
                c.execute("ALTER TABLE snapshots ADD COLUMN tech TEXT")

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    # ---- writes ----------------------------------------------------------

    def record_decision(self, ts: str, epic: str | None, direction: str | None, setup: str | None, allowed: bool,
                        size: float, reasons: list[str] | tuple[str, ...], context: dict | None = None) -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO decisions (ts, epic, direction, setup, allowed, size, reasons, context) VALUES (?,?,?,?,?,?,?,?)",
                (ts, epic, direction, setup, int(allowed), size, json.dumps(list(reasons)), json.dumps(context or {})),
            )
            return int(cur.lastrowid)

    def open_trade(self, *, opened_at: str, epic: str, direction: str, size: float, entry: float, stop: float,
                   target: float | None = None, risk_gbp: float | None = None, env: str | None = None,
                   market: str | None = None, expiry: str | None = None, setup: str | None = None,
                   driver: str | None = None, thesis: str | None = None, invalidation: str | None = None,
                   deal_ref: str | None = None, deal_id: str | None = None, notes: str | None = None) -> int:
        if direction not in ("BUY", "SELL"):
            raise ValueError("direction must be BUY or SELL")
        with self._conn() as c:
            cur = c.execute(
                """INSERT INTO trades (env, opened_at, market, epic, expiry, direction, size, entry, stop, target, risk_gbp,
                   setup, driver, thesis, invalidation, deal_ref, deal_id, notes)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (env, opened_at, market, epic, expiry, direction, size, entry, stop, target, risk_gbp,
                 setup, driver, thesis, invalidation, deal_ref, deal_id, notes),
            )
            return int(cur.lastrowid)

    def close_trade(self, trade_id: int, closed_at: str, exit_price: float, exit_reason: str,
                    financing: float = 0.0, spread_cost: float | None = None, notes: str | None = None) -> dict[str, Any]:
        with self._conn() as c:
            row = c.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
            if row is None:
                raise KeyError(f"no trade {trade_id}")
            if row["closed_at"] is not None:
                raise ValueError(f"trade {trade_id} is already closed")
            move = exit_price - row["entry"] if row["direction"] == "BUY" else row["entry"] - exit_price
            gross = row["size"] * move
            net = gross - financing
            r = net / row["risk_gbp"] if row["risk_gbp"] else None
            c.execute(
                """UPDATE trades SET closed_at=?, exit=?, exit_reason=?, gross_pnl=?, spread_cost=?, financing=?,
                   net_pnl=?, r_multiple=?, notes=COALESCE(?, notes) WHERE id=?""",
                (closed_at, exit_price, exit_reason, gross, spread_cost, financing, net, r, notes, trade_id),
            )
            return dict(c.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone())

    def record_event(self, ts: str, kind: str, detail: str = "") -> None:
        with self._conn() as c:
            c.execute("INSERT INTO events (ts, kind, detail) VALUES (?,?,?)", (ts, kind, detail))

    def record_equity(self, day: str, equity: float, env: str | None = None) -> None:
        """First value of the day is kept: it is the start-of-day equity."""
        with self._conn() as c:
            c.execute("INSERT OR IGNORE INTO equity_daily (day, equity, env) VALUES (?,?,?)", (day, equity, env))

    # ---- reads -----------------------------------------------------------

    def equity_on_or_before(self, day: str) -> float | None:
        with self._conn() as c:
            row = c.execute("SELECT equity FROM equity_daily WHERE day<=? ORDER BY day DESC LIMIT 1", (day,)).fetchone()
            return None if row is None else float(row["equity"])

    def record_snapshots(self, ts: str, rows: list[dict[str, Any]]) -> int:
        """Archive one briefing: every market's conditions at that moment. Mechanical data, written before any outcome is known."""
        day = ts[:10]
        with self._conn() as c:
            for r in rows:
                c.execute("INSERT INTO snapshots (ts, day, market, epic, price, bid, offer, spread_pct, day_pct, atr, atr_pct, range_pos, long_pct, sma20, tech) "
                          "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (ts, day, r["market"], r["epic"], r.get("price"), r.get("bid"), r.get("offer"), r.get("spread_pct"), r.get("day_pct"), r.get("atr"),
                           r.get("atr_pct"), r.get("range_pos"), r.get("long_pct"), r.get("sma20"),
                           json.dumps(r["tech"]) if r.get("tech") else None))
        return len(rows)

    def snapshot_counts(self) -> tuple[int, int]:
        """(rows, distinct days) in the archive."""
        with self._conn() as c:
            row = c.execute("SELECT COUNT(*) AS n, COUNT(DISTINCT day) AS d FROM snapshots").fetchone()
            return int(row["n"]), int(row["d"])

    def record_baselines(self, day: str, ideas: list[dict[str, Any]]) -> int:
        """Store the day's mechanical baseline ideas once (the first briefing of a UTC day). Returns how many were new."""
        new = 0
        with self._conn() as c:
            for idea in ideas:
                cur = c.execute("INSERT OR IGNORE INTO baseline_ideas (day, epic, detail) VALUES (?,?,?)", (day, idea["epic"], json.dumps(idea)))
                if cur.rowcount:
                    new += 1
                    c.execute("UPDATE baseline_ideas SET detail=? WHERE id=?", (json.dumps({**idea, "id": cur.lastrowid}), cur.lastrowid))
        return new

    def baselines(self) -> list[dict[str, Any]]:
        with self._conn() as c:
            return [json.loads(r["detail"]) for r in c.execute("SELECT detail FROM baseline_ideas ORDER BY id")]

    def baseline_day_recorded(self, day: str) -> bool:
        with self._conn() as c:
            return c.execute("SELECT 1 FROM baseline_ideas WHERE day=? LIMIT 1", (day,)).fetchone() is not None

    def update_levels(self, deal_id: str, stop: float | None = None, target: float | None = None) -> bool:
        """Record a moved stop and/or take-profit on a still-open trade. Returns whether a trade was updated."""
        with self._conn() as c:
            row = c.execute("SELECT id FROM trades WHERE deal_id=? AND closed_at IS NULL", (deal_id,)).fetchone()
            if row is None:
                return False
            if stop is not None:
                c.execute("UPDATE trades SET stop=? WHERE id=?", (stop, row["id"]))
            if target is not None:
                c.execute("UPDATE trades SET target=? WHERE id=?", (target, row["id"]))
            return True

    def trades_opened_on(self, day: str) -> int:
        """Trades opened on this UTC date (YYYY-MM-DD), open or closed."""
        with self._conn() as c:
            return int(c.execute("SELECT COUNT(*) FROM trades WHERE opened_at LIKE ?", (day + "%",)).fetchone()[0])

    def peak_equity(self) -> float | None:
        with self._conn() as c:
            row = c.execute("SELECT MAX(equity) AS m FROM equity_daily").fetchone()
            return float(row["m"]) if row and row["m"] is not None else None

    def closed_trades(self) -> list[dict[str, Any]]:
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM trades WHERE closed_at IS NOT NULL ORDER BY closed_at, id")]

    def open_trades(self) -> list[dict[str, Any]]:
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM trades WHERE closed_at IS NULL ORDER BY id")]

    def decisions(self) -> list[dict[str, Any]]:
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM decisions ORDER BY id")]

    def events(self) -> list[dict[str, Any]]:
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM events ORDER BY id")]
