"""Daily bar hygiene. IG's DAY bars need two fixes before any indicator sees them.

1. Sunday mini-sessions. IG stamps bars by UTC date, so the short Sunday-evening session
   (about 2 hours, mean range 15.7 points against about 75 for a full day, found 2026-11-11)
   arrives as its own bar. It is merged into the next trading day's bar: open from the
   Sunday, high and low across both, close from the later bar.
2. The unfinished bar. The last bar returned is the session still in progress. A bar stamped
   with date D is complete only once the UTC date is later than D, so any bar dated today or
   later is dropped. A trailing Sunday with no following bar is dropped too.

Both are applied before signals, the history file and the backtest. Idempotent.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

SUNDAY = 6


def consolidate_sundays(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values("timestamp").reset_index(drop=True)
    out: list[dict] = []
    carry: dict | None = None
    for row in df.to_dict("records"):
        if pd.Timestamp(row["timestamp"]).dayofweek == SUNDAY:
            carry = row if carry is None else {**row, "open": carry["open"], "high": max(carry["high"], row["high"]), "low": min(carry["low"], row["low"])}
            continue
        if carry is not None:
            row = {**row, "open": carry["open"], "high": max(carry["high"], row["high"]), "low": min(carry["low"], row["low"])}
            carry = None
        out.append(row)
    return pd.DataFrame(out, columns=df.columns) if out else df.iloc[0:0]


def drop_unfinished(df: pd.DataFrame, now: datetime) -> pd.DataFrame:
    if df.empty:
        return df
    return df[pd.to_datetime(df["timestamp"]).dt.date < now.date()].reset_index(drop=True)


def clean_daily(df: pd.DataFrame, now: datetime) -> pd.DataFrame:
    """Merge Sunday mini-sessions, then drop the unfinished bar. ``now`` must be a UTC time."""
    d = df.copy()
    d["timestamp"] = pd.to_datetime(d["timestamp"])
    return drop_unfinished(consolidate_sundays(d), now)


def assert_clean(df: pd.DataFrame) -> None:
    if (pd.to_datetime(df["timestamp"]).dt.dayofweek == SUNDAY).any():
        raise ValueError("bars still contain Sunday mini-sessions: run them through ig_swing.bars.clean_daily first")
