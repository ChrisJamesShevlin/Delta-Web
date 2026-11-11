"""Backtest of the technical engine on daily OHLC bars (mid prices).

Rules of the simulation, chosen to be pessimistic:
- A signal on the close of bar i is entered at the open of bar i+1, paying half
  the spread (buy at the offer, sell at the bid).
- Stop and target are distances from the fill. A stop is checked on the exit
  side of the quote, so the spread makes stops easier to hit and targets harder.
- If a bar could hit both stop and target, the stop is assumed to hit first.
- A gap through a stop fills at the open, not at the stop.
- Sizing uses the same risk module as live trading, so a trade the live code
  would refuse is skipped here and counted.
- A trend break (close back across the slow EMA) exits at the next open. There
  is also a time stop.

Not modelled: financing (daily funded bets only), slippage beyond gaps, the
loss limits and kill switch, event blackouts, and the news and cross-asset
layers. A good result here is a reason to keep testing, not to trade.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field

import pandas as pd

from .config import LIVE_TEST, RiskLimits
from .report import Stats, format_stats, summarise
from .risk import size_position
from .strategy import StrategyParams, compute_features, signal_at


@dataclass(frozen=True)
class BTrade:
    entry_date: object
    exit_date: object
    direction: str
    setup: str
    size: float
    entry: float
    exit: float
    stop: float
    risk_gbp: float
    pnl: float
    r_multiple: float
    bars_held: int
    exit_reason: str


@dataclass
class BacktestResult:
    trades: list[BTrade]
    skipped: dict[str, int]
    start_equity: float
    end_equity: float
    notes: list[str] = field(default_factory=list)

    def stats(self) -> Stats:
        return summarise([t.pnl for t in self.trades], [t.r_multiple for t in self.trades], self.start_equity)


def run_backtest(
    df: pd.DataFrame,
    strat: StrategyParams = StrategyParams(),
    limits: RiskLimits = LIVE_TEST,
    spread: float = 1.0,
    start_equity: float = 30.0,
    min_size: float = 0.01,
    step: float = 0.01,
    allow_long: bool = True,
    allow_short: bool = True,
    max_hold: int = 40,
    trend_break_exit: bool = True,
) -> BacktestResult:
    f = compute_features(df, strat)
    n = len(f)
    half = spread / 2
    equity = start_equity
    trades: list[BTrade] = []
    skipped: dict[str, int] = {}
    pos: dict | None = None
    pending = None

    def date_of(j):
        return f["timestamp"].iloc[j] if "timestamp" in f.columns else j

    def close_pos(j, price, reason):
        nonlocal pos, equity
        d = pos
        move = price - d["entry"] if d["dir"] == "BUY" else d["entry"] - price
        pnl = d["size"] * move
        risk = d["size"] * d["stop_dist"]
        trades.append(BTrade(date_of(d["idx"]), date_of(j), d["dir"], d["setup"], d["size"], d["entry"], price,
                             d["stop"], risk, pnl, pnl / risk if risk else 0.0, d["bars"], reason))
        equity += pnl
        pos = None

    for j in range(max(strat.min_bars - 1, 0), n):
        row = f.iloc[j]
        o, h, lo, c = row["open"], row["high"], row["low"], row["close"]

        entered = False
        if pending is not None:
            sig, size = pending
            pending = None
            buy = sig.direction == "BUY"
            entry = o + half if buy else o - half
            sd, td = sig.stop_distance, sig.target_distance
            stop, target = (entry - sd, entry + td) if buy else (entry + sd, entry - td)
            pos = dict(dir=sig.direction, setup=sig.setup, size=size, entry=entry, stop=stop, target=target,
                       stop_dist=sd, idx=j, bars=0, pending_exit=False)
            entered = True

        if pos is not None and not entered and pos["pending_exit"]:
            close_pos(j, o - half if pos["dir"] == "BUY" else o + half, "trend_break")

        if pos is not None:
            buy = pos["dir"] == "BUY"
            if not entered:  # an overnight gap can jump past the stop or the target
                if buy:
                    if o - half <= pos["stop"]:
                        close_pos(j, o - half, "stop_gap")
                    elif o - half >= pos["target"]:
                        close_pos(j, o - half, "target")
                else:
                    if o + half >= pos["stop"]:
                        close_pos(j, o + half, "stop_gap")
                    elif o + half <= pos["target"]:
                        close_pos(j, o + half, "target")
            if pos is not None:
                if buy:
                    if lo - half <= pos["stop"]:
                        close_pos(j, pos["stop"], "stop")
                    elif h - half >= pos["target"]:
                        close_pos(j, pos["target"], "target")
                else:
                    if h + half >= pos["stop"]:
                        close_pos(j, pos["stop"], "stop")
                    elif lo + half <= pos["target"]:
                        close_pos(j, pos["target"], "target")

        if pos is not None:
            buy = pos["dir"] == "BUY"
            pos["bars"] += 1
            if trend_break_exit and ((c < row["ema_slow"]) if buy else (c > row["ema_slow"])):
                pos["pending_exit"] = True
            if pos["bars"] >= max_hold:
                close_pos(j, c - half if buy else c + half, "time")

        if pos is None and pending is None and j < n - 1:
            sig = signal_at(f, j, strat, allow_long, allow_short)
            if sig is not None:
                res = size_position(equity, sig.stop_distance, limits, min_size, step)
                if res.allowed:
                    pending = (sig, res.size)
                else:
                    skipped["size"] = skipped.get("size", 0) + 1

    if pos is not None and n:
        last = f.iloc[-1]["close"]
        close_pos(n - 1, last - half if pos["dir"] == "BUY" else last + half, "end_of_data")

    return BacktestResult(trades, skipped, start_equity, equity)


def format_result(result: BacktestResult, title: str = "Backtest") -> str:
    lines = [format_stats(title, result.stats())]
    if result.skipped:
        lines.append("  skipped signals: " + ", ".join(f"{k} {v}" for k, v in sorted(result.skipped.items())))
    reasons: dict[str, int] = {}
    for t in result.trades:
        reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1
    if reasons:
        lines.append("  exits: " + ", ".join(f"{k} {v}" for k, v in sorted(reasons.items())))
    lines.append("  Not modelled: financing, slippage beyond gaps, loss limits, news and cross-asset layers.")
    return "\n".join(lines)


def load_bars_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    missing = {"open", "high", "low", "close"} - set(df.columns)
    if missing:
        raise ValueError(f"CSV is missing columns: {sorted(missing)}")
    if "timestamp" not in df.columns and "date" in df.columns:
        df = df.rename(columns={"date": "timestamp"})
    return df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Backtest the technical engine on a CSV of daily bars (timestamp/date, open, high, low, close).")
    ap.add_argument("csv")
    ap.add_argument("--spread", type=float, default=1.0, help="spread in index points (default 1.0)")
    ap.add_argument("--equity", type=float, default=30.0)
    ap.add_argument("--no-short", action="store_true")
    args = ap.parse_args(argv)
    df = load_bars_csv(args.csv)
    result = run_backtest(df, spread=args.spread, start_equity=args.equity, allow_short=not args.no_short)
    print(f"{len(df)} bars, spread {args.spread}, start equity £{args.equity:.2f}, profile {LIVE_TEST.name}")
    print(format_result(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
