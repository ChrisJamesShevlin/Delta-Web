"""The pre-registered historical test of insider-buying clusters. An UPPER BOUND only. See CLAUDE.md.

Fetch the data once (read-only, cached, resumable, takes a while):  python3 -m ig_swing.insider_test --fetch
Run the test once:                                                  python3 -m ig_swing.insider_test
Do NOT change any constant below after seeing a result: that moves the goalposts.

Free price data has no failed or delisted companies (Yahoo has no prices for most of them, and some tickers now belong
to other companies), so this test is survivor-biased, worst exactly where the idea lives. It therefore reports how many
events had no usable prices, and the average return those events would have needed to erase the effect. Failing even
this upper bound ends the idea. Passing proves nothing and only earns the forward test.

Event: ig_swing.insiders (fixed cluster definition). Entry: the open of the first session strictly after the signal
filing date. PRIMARY measure: the market-adjusted gross return from the entry open to the close of the 40th session,
minus IWM over the same dates; SE clustered by calendar quarter. Secondary (not used for conclusions): 20 and 120
sessions, and SPY as the benchmark. TRADED variant: stop 2 x ATR(14) at the signal close, 3R target, 40-session time
exit, stop before target, gaps at the open, no technical exit, net of the fixed costs.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from . import pead_test as pt
from .indicators import atr
from .insiders import find_clusters, load_purchases, quarter_names
from .picker_test import Prepared, simulate_long
from .sources import SourceError

PRICE_MIN = 5.0
DOLLAR_VOLUME_MIN = 5_000_000.0
DOLLAR_VOLUME_DAYS = 20
HISTORY_MIN = 250
PRIMARY = 40
SECONDARY = (20, 120)
BENCH, BENCH2 = "IWM", "SPY"
SPREAD = 0.0055
FIN_LONG = 0.062
STOP_ATR = 2.0
Z_BAR = 2.5
EVENT_START = pd.Timestamp("2007-01-01")
HOLDOUT_START = pd.Timestamp("2017-01-01")
GAP_DAYS = 7
CRISES = ((pd.Timestamp("2008-10-01"), pd.Timestamp("2009-06-30")), (pd.Timestamp("2020-01-01"), pd.Timestamp("2020-06-30")))
PX_DIR = Path("data/history/insider_px")
PX_START = date(2005, 1, 1)

FIXED: dict[str, Any] = dict(price_min=PRICE_MIN, dollar_volume_min=DOLLAR_VOLUME_MIN, history_min=HISTORY_MIN, primary=PRIMARY,
                             secondary=SECONDARY, bench=BENCH, spread=SPREAD, fin_long=FIN_LONG, stop_atr=STOP_ATR, z_bar=Z_BAR)


def yahoo_symbol(symbol: Any) -> str | None:
    s = str(symbol).strip().upper()
    if not s or s in ("NAN", "NONE", "N/A", "NA") or " " in s or len(s) > 8:
        return None
    return s.replace(".", "-")


@dataclass(frozen=True)
class Stock:
    p: Prepared
    raw_close: np.ndarray
    volume: np.ndarray
    last_date: pd.Timestamp


def make_stock(px: pd.DataFrame) -> Stock:
    n = len(px)
    nan = np.full(n, np.nan)
    c = px["close"].to_numpy(float)
    raw = px["raw_close"].to_numpy(float) if "raw_close" in px.columns else c
    p = Prepared(pd.DatetimeIndex(px["timestamp"]), px["open"].to_numpy(float), px["high"].to_numpy(float), px["low"].to_numpy(float),
                 c, nan, nan, nan, atr(px, 14).to_numpy(float))
    return Stock(p, raw, px["volume"].to_numpy(float), p.dates[-1])


@dataclass(frozen=True)
class InsiderEvent:
    symbol: str
    signal_date: pd.Timestamp
    entry_date: pd.Timestamp
    n_owners: int
    total_value: float
    adj_primary: float            # PRIMARY: market-adjusted (IWM) gross return over 40 sessions
    adj_primary_spy: float
    adj_20: float
    adj_120: float
    traded_pct: float
    net_pct: float
    traded_r: float
    net_r: float
    stop_pct: float
    exit_reason: str
    cal_days: int


def evaluate(symbol: str, stock: Stock, bench: Stock, bench2: Stock, signal: pd.Timestamp, n_owners: int = 0, total_value: float = 0.0) -> InsiderEvent | str:
    """An InsiderEvent, or the plain reason it was left out: gap, history_short, filtered, ended_early, incomplete, no_data."""
    p = stock.p
    i = int(p.dates.searchsorted(signal, side="right")) - 1
    if i < 0 or (signal - p.dates[i]).days > GAP_DAYS:
        return "gap"
    if i + 1 < HISTORY_MIN:
        return "history_short"
    e = i + 1
    if e >= len(p.close):
        return "ended_early" if stock.last_date < bench.last_date - pd.Timedelta(days=10) else "incomplete"
    if (p.dates[e] - signal).days > GAP_DAYS:
        return "gap"
    lo = max(0, i - DOLLAR_VOLUME_DAYS + 1)
    dv = float(np.nanmean(stock.raw_close[lo:i + 1] * stock.volume[lo:i + 1]))
    if not (stock.raw_close[i] >= PRICE_MIN and dv >= DOLLAR_VOLUME_MIN):
        return "filtered"
    entry = float(p.open[e])
    if not (math.isfinite(entry) and entry > 0 and math.isfinite(p.atr[i]) and p.atr[i] > 0):
        return "no_data"

    def adj(h: int, b: Stock) -> float:
        x = e + h - 1
        if x >= len(p.close):
            return float("nan")
        try:
            be, bx = b.p.dates.get_loc(p.dates[e]), b.p.dates.get_loc(p.dates[x])
        except KeyError:
            return float("nan")
        return float(p.close[x] / entry - 1) - float(b.p.close[bx] / b.p.open[be] - 1)

    primary = adj(PRIMARY, bench)
    if math.isnan(primary):
        return "ended_early" if (e + PRIMARY - 1 >= len(p.close) and stock.last_date < bench.last_date - pd.Timedelta(days=10)) else (
            "incomplete" if e + PRIMARY - 1 >= len(p.close) else "no_data")
    stop_dist = STOP_ATR * float(p.atr[i])
    out = simulate_long(p, e, stop_dist)
    pct = out["price"] / entry - 1
    cal = max(1, (p.dates[out["idx"]] - p.dates[e]).days)
    net = pct - SPREAD - FIN_LONG * cal / 365
    sp = stop_dist / entry
    return InsiderEvent(symbol, signal, p.dates[e], n_owners, total_value, primary, adj(PRIMARY, bench2), adj(SECONDARY[0], bench), adj(SECONDARY[1], bench),
                        pct, net, pct / sp, net / sp, sp, out["reason"], cal)


# ---- statistics -----------------------------------------------------------


def stat(events: list[InsiderEvent], field: str) -> dict[str, float]:
    xs = [getattr(e, field) for e in events]
    keep = [(x, e) for x, e in zip(xs, events) if x == x]
    vals = [x for x, _ in keep]
    m, se_iid = pt.mean_se(vals)
    se = pt.clustered_se(vals, [pt.quarter(e.signal_date) for _, e in keep])
    return {"n": len(vals), "mean": m, "se": se, "se_iid": se_iid, "z": (m / se) if se and not math.isnan(se) and se > 0 else float("nan")}


def break_even_missing(mean_priced: float, n_priced: int, n_missing: int) -> float:
    """The average return the missing events would need for the pooled mean to be zero."""
    if n_missing <= 0 or math.isnan(mean_priced):
        return float("nan")
    return -n_priced * mean_priced / n_missing


def split(events: list[InsiderEvent]) -> dict[str, list[InsiderEvent]]:
    def crisis(e: InsiderEvent) -> bool:
        return any(a <= e.signal_date <= b for a, b in CRISES)
    return {"All": events, "In-sample (before 2017)": [e for e in events if e.signal_date < HOLDOUT_START],
            "Holdout (2017 onwards)": [e for e in events if e.signal_date >= HOLDOUT_START],
            "Excluding Q4 2008 to Q2 2009 and Q1 to Q2 2020": [e for e in events if not crisis(e)]}


def run(clusters: pd.DataFrame, prices: dict[str, pd.DataFrame], missing: set[str]) -> tuple[list[InsiderEvent], dict[int, dict[str, int]]]:
    """Evaluate every cluster. ``coverage`` counts outcomes by year: ok plus each reason an event was left out."""
    bench, bench2 = make_stock(prices[BENCH]), make_stock(prices[BENCH2])
    stocks: dict[str, Stock | None] = {}
    events: list[InsiderEvent] = []
    coverage: dict[int, dict[str, int]] = {}
    for c in clusters.itertuples():
        sig = pd.Timestamp(c.signal_date)
        if sig < EVENT_START:
            continue
        year = sig.year
        bucket = coverage.setdefault(year, {})
        sym = yahoo_symbol(c.symbol)
        if sym is None or sym in missing or sym not in prices:
            bucket["unpriced"] = bucket.get("unpriced", 0) + 1
            continue
        if sym not in stocks:
            stocks[sym] = make_stock(prices[sym])
        res = evaluate(sym, stocks[sym], bench, bench2, sig, int(c.n_owners), float(c.total_value))
        if isinstance(res, str):
            bucket[res] = bucket.get(res, 0) + 1
        else:
            bucket["ok"] = bucket.get("ok", 0) + 1
            events.append(res)
    events.sort(key=lambda e: e.signal_date)
    return events, coverage


def verdicts(events: list[InsiderEvent]) -> tuple[str, str, bool]:
    hold = [e for e in events if e.signal_date >= HOLDOUT_START]
    a, b = stat(hold, "adj_primary"), stat(hold, "net_pct")
    a_ok = a["n"] > 0 and a["mean"] > 0 and a["z"] >= Z_BAR
    b_ok = b["n"] > 0 and b["mean"] > 0
    ra = f"Reading A: holdout primary mean {a['mean'] * 100:+.2f}%, z {a['z']:+.2f} (needs {Z_BAR} or more): " + ("passes" if a_ok else "FAILS")
    rb = f"Reading B: holdout traded net mean {b['mean'] * 100:+.2f}% (needs to be above zero): " + ("passes" if b_ok else "FAILS")
    return ra, rb, a_ok and b_ok


def format_report(events: list[InsiderEvent], coverage: dict[int, dict[str, int]], n_clusters: int) -> str:
    pct = lambda v: "n/a" if v is None or v != v else f"{v * 100:+.2f}%"
    if not events:
        return "No priced events."
    reasons = ("ok", "filtered", "unpriced", "ended_early", "history_short", "gap", "incomplete", "no_data")
    lines = [f"Insider-buying clusters, historical UPPER BOUND: {n_clusters} clusters since 2007, {len(events)} priced and tradable-filtered events, "
             f"{events[0].signal_date:%Y-%m-%d} to {events[-1].signal_date:%Y-%m-%d}",
             f"Fixed: 40-session primary against {BENCH}, filter price >= ${PRICE_MIN:g} and 20-session dollar volume >= ${DOLLAR_VOLUME_MIN / 1e6:g}M, "
             f"costs spread {SPREAD:.2%} + financing {FIN_LONG:.1%} a year, evidence bar z of {Z_BAR}.", "",
             "Coverage by year (clusters): " + " ".join(f"{r}" for r in reasons)]
    for y in sorted(coverage):
        lines.append(f"  {y}: " + " ".join(f"{coverage[y].get(r, 0):4d}" for r in reasons))
    tot = {r: sum(coverage[y].get(r, 0) for y in coverage) for r in reasons}
    lines.append("  all: " + " ".join(f"{tot[r]:4d}" for r in reasons))
    lines += ["", "PRIMARY: market-adjusted (IWM) gross return over 40 sessions, SE clustered by calendar quarter"]
    parts = split(events)
    for name, evs in parts.items():
        s = stat(evs, "adj_primary")
        lines.append(f"  {name}: n={s['n']}, mean {pct(s['mean'])}, SE {pct(s['se'])} (iid {pct(s['se_iid'])}), z {s['z']:+.2f}")
    s_all = stat(events, "adj_primary")
    missing_n = tot["unpriced"] + tot["ended_early"]
    be = break_even_missing(s_all["mean"], s_all["n"], missing_n)
    lines += ["", f"Break-even for the missing: {missing_n} clusters had no usable prices or stopped trading early. They would have needed an average market-adjusted "
              f"40-session return of {pct(be)} to bring the pooled mean to zero (priced events carry {pct(s_all['mean'])})."]
    lines += ["", "Secondary, not used for conclusions:"]
    for name, evs in (("All", events), ("Holdout (2017 onwards)", parts["Holdout (2017 onwards)"])):
        for label, field in (("20 sessions", "adj_20"), ("120 sessions", "adj_120"), ("40 sessions vs SPY", "adj_primary_spy")):
            s = stat(evs, field)
            lines.append(f"  {name} | {label}: n={s['n']}, mean {pct(s['mean'])}, z {s['z']:+.2f}")
    lines += ["", "TRADED (stop 2 x ATR, 3R target, 40 sessions), net of costs, per trade"]
    for name, evs in parts.items():
        g, n_, r_ = stat(evs, "traded_pct"), stat(evs, "net_pct"), stat(evs, "net_r")
        lines.append(f"  {name}: n={g['n']}, gross {pct(g['mean'])}, net {pct(n_['mean'])} (SE {pct(n_['se'])}, z {n_['z']:+.2f}), net {r_['mean']:+.3f}R")
    reasons_count: dict[str, int] = {}
    for e in events:
        reasons_count[e.exit_reason] = reasons_count.get(e.exit_reason, 0) + 1
    lines.append("  Exits: " + ", ".join(f"{k} {v}" for k, v in sorted(reasons_count.items())))
    ra, rb, passed = verdicts(events)
    lines += ["", ra, rb,
              ("PASSES the upper bound: that only justifies the forward test and is not evidence of an edge." if passed else
               "FAILS the upper bound: under the pre-registered rule this ends the idea."),
              "Limits: survivor-biased prices (an upper bound), adjusted prices for the filter are approximate, positions not capped, SEs are rough."]
    return "\n".join(lines)


# ---- data -----------------------------------------------------------------


def _px_path(sym: str, cache: Path = PX_DIR) -> Path:
    return cache / f"{sym}.csv"


def load_cached_prices(symbols: list[str], cache: Path = PX_DIR) -> tuple[dict[str, pd.DataFrame], set[str]]:
    missing_file = cache / "_missing.json"
    missing = set(json.loads(missing_file.read_text())) if missing_file.exists() else set()
    prices = {s: pd.read_csv(_px_path(s, cache), parse_dates=["timestamp"]) for s in symbols if _px_path(s, cache).exists()}
    return prices, missing


def fetch_prices_resumable(session: Any, symbols: list[str], cache: Path = PX_DIR, pause: float = 0.35) -> tuple[int, int, int]:
    """Fetch each missing symbol once. 404s are remembered so reruns skip them. Returns (fetched, missing, errors)."""
    from .stock_prices import fetch_prices

    cache.mkdir(parents=True, exist_ok=True)
    mf = cache / "_missing.json"
    missing = set(json.loads(mf.read_text())) if mf.exists() else set()
    fetched = errors = 0
    for n, sym in enumerate(symbols, 1):
        if _px_path(sym, cache).exists() or sym in missing:
            continue
        for attempt in range(3):
            try:
                time.sleep(pause)
                df = fetch_prices(session, sym, start=PX_START)
                df.to_csv(_px_path(sym, cache), index=False)
                fetched += 1
                break
            except SourceError as exc:
                if "404" in str(exc):
                    missing.add(sym)
                    break
                time.sleep(5 * (attempt + 1))
            except Exception:  # type name only: never echo a message
                time.sleep(5 * (attempt + 1))
        else:
            errors += 1
        if n % 100 == 0:
            mf.write_text(json.dumps(sorted(missing)))
    mf.write_text(json.dumps(sorted(missing)))
    return fetched, len(missing), errors


def fetch_all() -> int:
    import requests

    session = requests.Session()
    try:
        purchases, notes = load_purchases(session, quarter_names())
        clusters = find_clusters(purchases)
        symbols = sorted({s for s in (yahoo_symbol(x) for x in clusters["symbol"]) if s} | {BENCH, BENCH2})
        print(f"{len(purchases)} qualifying purchases, {len(clusters)} clusters, {len(symbols)} symbols to price. Notes: {notes}", flush=True)
        fetched, missing, errors = fetch_prices_resumable(session, symbols)
    except Exception as exc:
        print(f"FAILED: {type(exc).__name__}")
        return 1
    print(f"Prices fetched {fetched}, no data for {missing} symbols, errors {errors}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    import sys

    args = sys.argv[1:] if argv is None else argv
    if "--fetch" in args:
        return fetch_all()
    import requests

    try:
        purchases, _ = load_purchases(requests.Session(), quarter_names())
        clusters = find_clusters(purchases)
        symbols = sorted({s for s in (yahoo_symbol(x) for x in clusters["symbol"]) if s} | {BENCH, BENCH2})
        prices, missing = load_cached_prices(symbols)
        if BENCH not in prices or BENCH2 not in prices:
            raise RuntimeError("benchmarks missing")
    except Exception as exc:
        print(f"FAILED: {type(exc).__name__} (run with --fetch first)")
        return 1
    events, coverage = run(clusters, prices, missing)
    print(format_report(events, coverage, int((clusters["signal_date"] >= EVENT_START).sum())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
