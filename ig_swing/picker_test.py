"""Test A of the pre-registered stock picker (see CLAUDE.md): the mechanical half, on history. No assessments.

Run once, after the data is fetched (python3 -m ig_swing.pead_test --fetch):  python3 -m ig_swing.picker_test
Do NOT change the constants below after seeing a result: that moves the goalposts.

Question: among earnings reactions of +2.0% or more in the 97 fixed stocks, does the technical confirmation
(close above SMA50, SMA50 above SMA200, close not more than 2 x ATR above EMA20, all at the reaction-day close)
pick better trades, and do the exits (2 x ATR stop, 3R target, close below SMA50, 40-session time limit) make a
long-only trade pay after IG's observed costs (scenario D)?

PRIMARY measure: market-adjusted gross return, entry at the open after the reaction day, exit at the close of the
20th session, minus SPY over the same dates. SE clustered by calendar quarter. The difference between confirmed and
unconfirmed events uses a cluster-robust regression on a confirmed indicator. TRADED variant: the full exit rules,
returns in percent and in R, gross and net of scenario D costs.

Disclosure: this reuses the earnings dataset of the drift test, so it is a second look at the same data and its holdout
is not pristine. Survivorship and hindsight bias make every number an upper bound.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from . import pead_test as pt
from .indicators import atr, ema

REACTION_MIN = 0.02
SMA_FAST, SMA_SLOW, EMA_LEN, ATR_LEN = 50, 200, 20, 14
STRETCH_ATR = 2.0
STOP_ATR = 2.0
TARGET_R = 3.0
TIME_SESSIONS = 40
PRIMARY_SESSIONS = 20
COST = pt.SCENARIOS["D observed IG ticket (spread 0.125%, long 6.2% a year, short 3.0% assumed)"]

FIXED: dict[str, Any] = dict(reaction_min=REACTION_MIN, sma=(SMA_FAST, SMA_SLOW), ema=EMA_LEN, stretch_atr=STRETCH_ATR,
                             stop_atr=STOP_ATR, target_r=TARGET_R, time_sessions=TIME_SESSIONS, primary_sessions=PRIMARY_SESSIONS)


@dataclass(frozen=True)
class Prepared:
    dates: pd.DatetimeIndex
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    sma_fast: np.ndarray
    sma_slow: np.ndarray
    ema_fast: np.ndarray
    atr: np.ndarray


def prepare(px: pd.DataFrame) -> Prepared:
    c = px["close"]
    return Prepared(
        pd.DatetimeIndex(px["timestamp"]), px["open"].to_numpy(float), px["high"].to_numpy(float), px["low"].to_numpy(float),
        c.to_numpy(float), c.rolling(SMA_FAST).mean().to_numpy(float), c.rolling(SMA_SLOW).mean().to_numpy(float),
        ema(c, EMA_LEN).to_numpy(float), atr(px, ATR_LEN).to_numpy(float),
    )


def confirmation(p: Prepared, r: int) -> bool | None:
    """Technical confirmation at the close of session r, or None when there is not enough history."""
    vals = (p.sma_fast[r], p.sma_slow[r], p.ema_fast[r], p.atr[r])
    if r < SMA_SLOW - 1 or not all(math.isfinite(v) for v in vals) or p.atr[r] <= 0:
        return None
    return bool(p.close[r] > p.sma_fast[r] and p.sma_fast[r] > p.sma_slow[r] and p.close[r] <= p.ema_fast[r] + STRETCH_ATR * p.atr[r])


def simulate_long(p: Prepared, e: int, stop_dist: float) -> dict[str, Any]:
    """Walk the sessions from the entry session e. Stop before target, gaps fill at the open, technical exit at the next open."""
    entry = p.open[e]
    stop, target = entry - stop_dist, entry + TARGET_R * stop_dist
    last = min(e + TIME_SESSIONS - 1, len(p.close) - 1)
    pending = False
    for j in range(e, last + 1):
        if pending:
            return {"idx": j, "price": float(p.open[j]), "reason": "technical"}
        if j > e:
            if p.open[j] <= stop:
                return {"idx": j, "price": float(p.open[j]), "reason": "stop_gap"}
            if p.open[j] >= target:
                return {"idx": j, "price": float(p.open[j]), "reason": "target_gap"}
        if p.low[j] <= stop:
            return {"idx": j, "price": float(stop), "reason": "stop"}
        if p.high[j] >= target:
            return {"idx": j, "price": float(target), "reason": "target"}
        if j == e + TIME_SESSIONS - 1:
            return {"idx": j, "price": float(p.close[j]), "reason": "time"}
        if math.isfinite(p.sma_fast[j]) and p.close[j] < p.sma_fast[j]:
            pending = True
    return {"idx": last, "price": float(p.close[last]), "reason": "end_of_data"}


@dataclass(frozen=True)
class PickEvent:
    ticker: str
    react_date: pd.Timestamp
    reaction: float
    confirmed: bool
    gross_adj: float        # PRIMARY: market-adjusted gross return over the 20 sessions
    traded_pct: float       # gross return with the exit rules
    net_pct: float          # the same net of scenario D costs
    traded_r: float
    net_r: float
    stop_pct: float
    exit_reason: str
    cal_days: int


def evaluate(ticker: str, p: Prepared, spy: Prepared, accepted_utc: datetime) -> PickEvent | str:
    r = pt.reaction_index(p.dates, accepted_utc)
    if r is None:
        return "intraday" if pt.classify(accepted_utc, p.dates) is None else "no_data"
    e, x = r + 1, r + PRIMARY_SESSIONS
    if r < 1 or r + TIME_SESSIONS >= len(p.close):
        return "no_data"
    react = float(p.close[r] / p.close[r - 1] - 1)
    if react < REACTION_MIN:
        return "below_threshold"
    conf = confirmation(p, r)
    if conf is None:
        return "no_data"
    try:
        se, sx = spy.dates.get_loc(p.dates[e]), spy.dates.get_loc(p.dates[x])
    except KeyError:
        return "no_data"
    entry = float(p.open[e])
    if entry <= 0:
        return "no_data"
    gross_adj = float(p.close[x] / entry - 1) - float(spy.close[sx] / spy.open[se] - 1)
    stop_dist = STOP_ATR * float(p.atr[r])
    out = simulate_long(p, e, stop_dist)
    pct = out["price"] / entry - 1
    cal = max(1, (p.dates[out["idx"]] - p.dates[e]).days)
    net = pct - COST["spread"] - COST["fx"] - COST["fin_long"] * cal / 365
    stop_pct = stop_dist / entry
    return PickEvent(ticker, p.dates[r], react, conf, gross_adj, pct, net, pct / stop_pct, net / stop_pct, stop_pct, out["reason"], cal)


# ---- statistics -----------------------------------------------------------


def cluster_diff(ys: list[float], xs: list[int], keys: list[Any]) -> tuple[float, float]:
    """Difference in means (x=1 minus x=0) with a cluster-robust standard error (sandwich, no small-sample correction)."""
    X = np.column_stack([np.ones(len(ys)), np.asarray(xs, float)])
    y = np.asarray(ys, float)
    if len(y) < 3 or X[:, 1].sum() in (0, len(y)):
        return float("nan"), float("nan")
    xtx_inv = np.linalg.inv(X.T @ X)
    beta = xtx_inv @ X.T @ y
    u = y - X @ beta
    meat = np.zeros((2, 2))
    for k in set(keys):
        idx = [i for i, kk in enumerate(keys) if kk == k]
        g = X[idx].T @ u[idx]
        meat += np.outer(g, g)
    v = xtx_inv @ meat @ xtx_inv
    return float(beta[1]), float(math.sqrt(max(v[1, 1], 0.0)))


def stat(events: list[PickEvent], field: str) -> dict[str, float]:
    xs = [getattr(e, field) for e in events]
    m, se_iid = pt.mean_se(xs)
    se = pt.clustered_se(xs, [pt.quarter(e.react_date) for e in events])
    return {"n": len(xs), "mean": m, "se": se, "se_iid": se_iid, "z": (m / se) if se and not math.isnan(se) and se > 0 else float("nan")}


def run(events: dict[str, list[datetime]], prices: dict[str, pd.DataFrame], spy_px: pd.DataFrame) -> tuple[list[PickEvent], dict[str, int]]:
    spy = prepare(spy_px)
    start = pd.Timestamp(pt.EVENT_START)
    out: list[PickEvent] = []
    skipped: dict[str, int] = {}
    for ticker, times in events.items():
        if ticker not in prices:
            skipped["no_prices"] = skipped.get("no_prices", 0) + len(times)
            continue
        p = prepare(prices[ticker])
        for t in times:
            if pd.Timestamp(t.astimezone(pt.NY).date()) < start:
                continue
            res = evaluate(ticker, p, spy, t)
            if isinstance(res, str):
                skipped[res] = skipped.get(res, 0) + 1
            else:
                out.append(res)
    out.sort(key=lambda e: e.react_date)
    return out, skipped


def split(events: list[PickEvent]) -> dict[str, list[PickEvent]]:
    cut = pd.Timestamp(pt.HOLDOUT_START)
    return {"All": events, "In-sample (before 2017)": [e for e in events if e.react_date < cut],
            "Holdout (2017 onwards)": [e for e in events if e.react_date >= cut]}


def format_report(events: list[PickEvent], skipped: dict[str, int]) -> str:
    pct = lambda v: "n/a" if v is None or v != v else f"{v * 100:+.2f}%"
    if not events:
        return "No events."
    lines = [
        f"Test A, the mechanical half: {len(events)} earnings reactions of +{REACTION_MIN:.0%} or more, {events[0].react_date:%Y-%m-%d} to {events[-1].react_date:%Y-%m-%d}",
        "Skipped: " + (", ".join(f"{k} {v}" for k, v in sorted(skipped.items())) or "none"),
        "Costs: scenario D (spread 0.125% round trip, financing 6.2% a year, no currency fee). SEs are clustered by calendar quarter.",
        "",
    ]
    parts = split(events)
    lines.append("PRIMARY: market-adjusted gross return over 20 sessions")
    for name, evs in parts.items():
        for label, sub in (("confirmed", [e for e in evs if e.confirmed]), ("unconfirmed", [e for e in evs if not e.confirmed])):
            s = stat(sub, "gross_adj")
            lines.append(f"  {name} | {label}: n={s['n']}, mean {pct(s['mean'])}, SE {pct(s['se'])}, z {s['z']:+.2f}")
        d, se = cluster_diff([e.gross_adj for e in evs], [int(e.confirmed) for e in evs], [pt.quarter(e.react_date) for e in evs])
        z = d / se if se and se > 0 else float("nan")
        lines.append(f"  {name} | confirmed minus unconfirmed: {pct(d)}, SE {pct(se)}, z {z:+.2f}")
    hold = parts["Holdout (2017 onwards)"]
    hd, hse = cluster_diff([e.gross_adj for e in hold], [int(e.confirmed) for e in hold], [pt.quarter(e.react_date) for e in hold])
    hz = hd / hse if hse and hse > 0 else float("nan")
    helps = ("the confirmation HELPS (holdout z of 2 or more)" if hz >= pt.Z_BAR else
             "the confirmation HURTS (holdout z of -2 or less)" if hz <= -pt.Z_BAR else "no evidence that the confirmation helps")
    lines += ["", f"Reading 1: {helps}.", "", "TRADED: exit rules, long only, per trade"]
    for name, evs in parts.items():
        for label, sub in (("confirmed", [e for e in evs if e.confirmed]), ("unconfirmed", [e for e in evs if not e.confirmed])):
            g, n_, r_ = stat(sub, "traded_pct"), stat(sub, "net_pct"), stat(sub, "net_r")
            lines.append(f"  {name} | {label}: n={g['n']}, gross {pct(g['mean'])}, net {pct(n_['mean'])} (SE {pct(n_['se'])}, z {n_['z']:+.2f}), net {r_['mean']:+.3f}R")
    hc = [e for e in hold if e.confirmed]
    hs = stat(hc, "net_pct")
    worth = hs["n"] > 0 and hs["mean"] > 0 and hs["z"] >= pt.Z_BAR
    lines += ["", "Reading 2: the mechanical half " + ("IS" if worth else "is NOT") + " worth building on alone "
              f"(holdout confirmed net {pct(hs['mean'])}, z {hs['z']:+.2f}, needs a positive mean and z of 2 or more).",
              "Exits (confirmed): " + ", ".join(f"{k} {v}" for k, v in sorted({r: sum(1 for e in events if e.confirmed and e.exit_reason == r)
                                                                              for r in {e.exit_reason for e in events}}.items())),
              "Limits: survivorship and hindsight bias (an upper bound), a second look at data already used, positions not capped."]
    return "\n".join(lines)


def main() -> int:
    from .edgar import load_events
    from .stock_prices import CACHE_DIR

    try:
        events = load_events()
        prices = {s: pd.read_csv(CACHE_DIR / f"{s}.csv", parse_dates=["timestamp"]) for s in events if (CACHE_DIR / f"{s}.csv").exists()}
        spy = pd.read_csv(CACHE_DIR / f"{pt.BENCHMARK}.csv", parse_dates=["timestamp"])
    except Exception as exc:
        print(f"FAILED: {type(exc).__name__} (run python3 -m ig_swing.pead_test --fetch first)")
        return 1
    res, skipped = run(events, prices, spy)
    print(format_report(res, skipped))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
