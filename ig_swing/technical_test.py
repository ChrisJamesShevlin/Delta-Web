"""The pre-registered backtest of the technical engine alone. Settings are fixed below and in CLAUDE.md.

Run from the repo root:  python3 -m ig_swing.technical_test [csv]
Do NOT change FIXED or HOLDOUT_FRACTION after seeing a result: that moves the goalposts.
Tuning, if ever done, uses the in-sample part only and is judged once on the holdout part.

The whole series is run once (so indicators warm up properly) and the trades are then
split by entry date: the first 70% of bars is in-sample, the last 30% is the holdout.
Results are in R multiples (profit as a multiple of the amount risked), which do not depend
on account size. Not modelled: financing, loss limits, the news and cross-asset layers.
"""

from __future__ import annotations

import sys
from typing import Any

import pandas as pd

from .backtest import BTrade, load_bars_csv, run_backtest
from .config import AGREED
from .history import DEFAULT_PATH
from .report import Stats, format_stats, summarise, verdict
from .strategy import StrategyParams

FIXED: dict[str, Any] = dict(strat=StrategyParams(), limits=AGREED, spread=1.0, start_equity=100_000.0)
HOLDOUT_FRACTION = 0.30


def split_timestamp(df: pd.DataFrame) -> pd.Timestamp:
    """Trades entering on or before this bar are in-sample, later ones are holdout."""
    k = int(len(df) * (1 - HOLDOUT_FRACTION)) - 1
    return pd.Timestamp(df["timestamp"].iloc[k])


def stats_for(trades: list[BTrade], start_equity: float) -> Stats:
    return summarise([t.pnl for t in trades], [t.r_multiple for t in trades], start_equity)


def run_fixed(df: pd.DataFrame) -> dict[str, Any]:
    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    result = run_backtest(df, **FIXED)
    cut = split_timestamp(df)
    eq = FIXED["start_equity"]
    trades = result.trades
    parts = {
        "All trades": trades,
        "In-sample (first 70% of bars)": [t for t in trades if pd.Timestamp(t.entry_date) <= cut],
        "Holdout (last 30% of bars)": [t for t in trades if pd.Timestamp(t.entry_date) > cut],
        "Long only": [t for t in trades if t.direction == "BUY"],
        "Short only": [t for t in trades if t.direction == "SELL"],
    }
    return {"bars": len(df), "first": df["timestamp"].iloc[0], "last": df["timestamp"].iloc[-1], "cut": cut,
            "result": result, "stats": {k: stats_for(v, eq) for k, v in parts.items()}}


def format_test(out: dict[str, Any]) -> str:
    years = (out["last"] - out["first"]).days / 365.25
    lines = [
        f"Pre-registered technical backtest: {out['bars']} daily bars, {out['first']:%Y-%m-%d} to {out['last']:%Y-%m-%d} ({years:.1f} years)",
        f"Fixed settings: spread {FIXED['spread']}, profile {FIXED['limits'].name}, default strategy parameters. "
        f"In-sample ends {out['cut']:%Y-%m-%d}.",
        "",
    ]
    for name, s in out["stats"].items():
        lines.append(format_stats(name, s))
        lines.append(f"  verdict: {verdict(s)}")
    r = out["result"]
    if r.skipped:
        lines.append("Skipped signals: " + ", ".join(f"{k} {v}" for k, v in sorted(r.skipped.items())))
    reasons: dict[str, int] = {}
    for t in r.trades:
        reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1
    if reasons:
        lines.append("Exits: " + ", ".join(f"{k} {v}" for k, v in sorted(reasons.items())))
    lines.append("Not modelled: financing, loss limits, the news and cross-asset layers. Parameters were not tuned.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    path = (argv if argv is not None else sys.argv[1:]) or [str(DEFAULT_PATH)]
    print(format_test(run_fixed(load_bars_csv(path[0]))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
