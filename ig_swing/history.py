"""Pull daily bars once and keep them as a CSV, so a backtest never spends the price allowance twice.

Run from the repo root:  python3 -m ig_swing.history [--bars 1500]
Read-only. Spends up to --bars points of IG's weekly price allowance (10,000 a week,
verified live 2026-11-11). Writes data/history/US500_DAY.csv (data/ is gitignored).
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pandas as pd

HISTORY_EPIC = "IX.D.SPTRD.DAILY.IP"  # continuous daily funded bet: long history, no roll gaps
DEFAULT_PATH = Path("data/history/US500_DAY.csv")
COLUMNS = ["timestamp", "open", "high", "low", "close"]


def fetch_history(client: Any, bars: int, path: str | Path = DEFAULT_PATH, epic: str = HISTORY_EPIC) -> pd.DataFrame:
    df = client.get_prices(epic, "DAY", bars)[COLUMNS]
    df = df.dropna().drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(p, index=False)
    return df


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bars", type=int, default=1500)
    args = ap.parse_args(argv)
    from .config import base_url, ig_env, load_credentials
    from .ig_client import IGClient

    try:
        env = ig_env()
        client = IGClient(load_credentials(env), base_url=base_url(env), allow_live=(env == "live"))
        df = fetch_history(client, args.bars)
        remaining = client._cache.load_allowance()
    except Exception as exc:  # type name only: messages can carry sensitive detail
        print(f"FAILED: {type(exc).__name__}")
        return 1
    print(f"{len(df)} bars of {HISTORY_EPIC}, {df['timestamp'].iloc[0]:%Y-%m-%d} to {df['timestamp'].iloc[-1]:%Y-%m-%d}, saved to {DEFAULT_PATH}")
    print(f"Price allowance remaining: {remaining}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
