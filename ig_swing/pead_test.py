"""Pre-registered test of post-earnings drift on liquid US stocks. Settings are fixed below and in CLAUDE.md.

Fetch the data once (read-only, cached, nothing is traded):  python3 -m ig_swing.pead_test --fetch
Run the test once:                                           python3 -m ig_swing.pead_test
Do NOT change FIXED, UNIVERSE or the splits after seeing a result: that moves the goalposts.

Hypothesis (continuation): after an earnings release that moves the stock a lot, the stock keeps
moving the same way for the next few weeks. Long after a big up reaction, short after a big down one.

Definitions
- Event: an 8-K with item 2.02 (ig_swing.edgar). The acceptance time decides the reaction day: before the
  open (or on a weekend) the first session on or after the filing date reacts, after the close the first
  session after it. Filings during the session are excluded and counted.
- Reaction: the adjusted close of the reaction day over the close of the session before it.
- Trigger: |reaction| of REACTION_THRESHOLD or more. Direction follows the sign.
- Entry: the open of the session after the reaction day (we would only know the reaction after its close).
- PRIMARY measure: market-adjusted gross return over HOLD_DAYS sessions, entry open to the close of the last
  session, direction-signed, minus the same direction-signed SPY return over the same dates. No costs, no stop,
  so it measures the effect and nothing else. SE is clustered by calendar quarter, because earnings seasons
  put many events on the same market days.
- TRADED variant (secondary): the same entry with a mandatory stop at STOP_ATR x ATR(14) as at the reaction
  day close (gaps fill at the open), time exit at the close of the last session. Reported gross and under
  three cost scenarios (see SCENARIOS): spread only, plus financing, plus IG's listed 0.8% currency fee.

Limits: survivorship bias (current large caps only, so results are an upper bound), the cost scenarios are
assumptions taken from IG's published pages and must be checked, positions are not capped (this is per-trade
expectancy, not an account simulation), no dividends adjustments beyond Yahoo's adjusted prices.
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import pandas as pd

from .edgar import NY
from .indicators import atr

REACTION_THRESHOLD = 0.05
HOLD_DAYS = 20
STOP_ATR = 2.0
ATR_PERIOD = 14
EVENT_START = date(2005, 1, 1)
HOLDOUT_START = date(2017, 1, 1)
MIN_EVENTS = 100
Z_BAR = 2.0
BENCHMARK = "SPY"

FIXED: dict[str, Any] = dict(reaction_threshold=REACTION_THRESHOLD, hold_days=HOLD_DAYS, stop_atr=STOP_ATR,
                             event_start=EVENT_START, holdout_start=HOLDOUT_START)

# round-trip spread, annual financing paid by longs and by shorts, one-off currency conversion fee
SCENARIOS: dict[str, dict[str, float]] = {
    "A gross, no costs": dict(spread=0.0, fin_long=0.0, fin_short=0.0, fx=0.0),
    "B spread 0.10% + financing (long 6.5% a year, short 3.0%)": dict(spread=0.0010, fin_long=0.065, fin_short=0.030, fx=0.0),
    "C as B plus 0.8% currency fee": dict(spread=0.0010, fin_long=0.065, fin_short=0.030, fx=0.008),
}

UNIVERSE = (
    "AAPL ABBV ABT ACN ADBE AIG AMD AMGN AMT AMZN AVGO AXP BA BAC BK BKNG BLK BMY C CAT CHTR CL CMCSA COF COP COST CRM CSCO "
    "CVS CVX DE DHR DIS DUK EMR F FDX GD GE GILD GM GOOGL GS HD HON IBM INTC INTU JNJ JPM KHC KO LIN LLY LMT LOW MA MCD MDLZ "
    "MDT MET META MMM MO MRK MS MSFT NEE NFLX NKE NVDA ORCL PEP PFE PG PM PYPL QCOM RTX SBUX SCHW SO SPG T TGT TMO TMUS TSLA "
    "TXN UNH UNP UPS USB V VZ WBA WFC WMT XOM"
).split()


# ---- events -> reaction day -----------------------------------------------


def classify(accepted_utc: datetime, dates: pd.DatetimeIndex) -> tuple[str, pd.Timestamp] | None:
    """('BMO'|'AMC'|'WEEKEND', filing date in New York) or None for a filing during the session."""
    et = accepted_utc.astimezone(NY)
    d = pd.Timestamp(et.date())
    minutes = et.hour * 60 + et.minute
    if d.weekday() >= 5:
        return "WEEKEND", d
    if minutes >= 16 * 60:
        return "AMC", d
    if minutes < 9 * 60 + 30:
        return "BMO", d
    return None if d in dates else ("AMC", d)   # a holiday filing behaves like an after-close one


def reaction_index(dates: pd.DatetimeIndex, accepted_utc: datetime) -> int | None:
    c = classify(accepted_utc, dates)
    if c is None:
        return None
    kind, d = c
    i = int(dates.searchsorted(d, side="right" if kind == "AMC" else "left"))
    return i if i < len(dates) else None


# ---- one event ------------------------------------------------------------


@dataclass(frozen=True)
class EventResult:
    ticker: str
    react_date: pd.Timestamp
    reaction: float
    direction: int          # +1 long, -1 short
    gross_raw: float        # direction-signed stock return, entry open to final close
    gross_adj: float        # the same minus the direction-signed SPY return: the PRIMARY measure
    traded_pct: float       # direction-signed return with the stop
    traded_r: float         # same, in multiples of the stop distance
    stop_pct: float         # stop distance as a fraction of the entry price
    cal_days: int
    exit_reason: str        # stop, stop_gap or time


def evaluate_event(ticker: str, px: pd.DataFrame, spy: pd.DataFrame, accepted_utc: datetime) -> EventResult | str:
    """An EventResult, or the plain reason the event was skipped."""
    dates = pd.DatetimeIndex(px["timestamp"])
    r = reaction_index(dates, accepted_utc)
    if r is None:
        c = classify(accepted_utc, dates)
        return "intraday" if c is None else "no_data"
    if r < 1 or r + 1 + HOLD_DAYS > len(px):
        return "no_data"
    base, e, x = r - 1, r + 1, r + HOLD_DAYS            # exit on the close of session e + HOLD_DAYS - 1 == r + HOLD_DAYS
    react = float(px["close"].iloc[r] / px["close"].iloc[base] - 1)
    if abs(react) < REACTION_THRESHOLD:
        return "below_threshold"
    d = 1 if react > 0 else -1
    spy_idx = pd.DatetimeIndex(spy["timestamp"])
    try:
        se, sx = spy_idx.get_loc(dates[e]), spy_idx.get_loc(dates[x])
    except KeyError:
        return "no_data"
    entry = float(px["open"].iloc[e])
    if entry <= 0:
        return "no_data"
    gross_raw = d * (float(px["close"].iloc[x]) / entry - 1)
    spy_ret = float(spy["close"].iloc[sx] / spy["open"].iloc[se] - 1)
    gross_adj = gross_raw - d * spy_ret

    a = float(atr(px.iloc[: r + 1], ATR_PERIOD).iloc[-1])
    if not math.isfinite(a) or a <= 0:
        return "no_data"
    stop_dist = STOP_ATR * a
    stop = entry - d * stop_dist
    exit_price, reason, exit_i = float(px["close"].iloc[x]), "time", x
    for j in range(e, x + 1):
        o, h, lo = float(px["open"].iloc[j]), float(px["high"].iloc[j]), float(px["low"].iloc[j])
        if j > e and ((d == 1 and o <= stop) or (d == -1 and o >= stop)):
            exit_price, reason, exit_i = o, "stop_gap", j
            break
        if (d == 1 and lo <= stop) or (d == -1 and h >= stop):
            exit_price, reason, exit_i = stop, "stop", j
            break
    traded_pct = d * (exit_price / entry - 1)
    cal = max(1, (dates[exit_i] - dates[e]).days)
    return EventResult(ticker, dates[r], react, d, gross_raw, gross_adj, traded_pct, d * (exit_price - entry) / stop_dist,
                       stop_dist / entry, cal, reason)


def net_pct(res: EventResult, scenario: dict[str, float]) -> float:
    fin = scenario["fin_long"] if res.direction == 1 else scenario["fin_short"]
    return res.traded_pct - scenario["spread"] - scenario["fx"] - fin * res.cal_days / 365


# ---- statistics -----------------------------------------------------------


def mean_se(xs: list[float]) -> tuple[float, float]:
    n = len(xs)
    if n == 0:
        return float("nan"), float("nan")
    m = sum(xs) / n
    if n < 2:
        return m, float("nan")
    return m, math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1) / n)


def clustered_se(xs: list[float], keys: list[Any]) -> float:
    """Cluster-robust standard error of the mean: events in one cluster move together."""
    n = len(xs)
    if n < 2:
        return float("nan")
    m = sum(xs) / n
    sums: dict[Any, float] = {}
    for x, k in zip(xs, keys):
        sums[k] = sums.get(k, 0.0) + (x - m)
    return math.sqrt(sum(s * s for s in sums.values())) / n


def quarter(ts: pd.Timestamp) -> str:
    return f"{ts.year}Q{(ts.month - 1) // 3 + 1}"


def summarise(results: list[EventResult], field: str = "gross_adj") -> dict[str, float]:
    xs = [getattr(r, field) for r in results]
    m, se_iid = mean_se(xs)
    se_c = clustered_se(xs, [quarter(r.react_date) for r in results])
    z = m / se_c if se_c and not math.isnan(se_c) and se_c > 0 else float("nan")
    return {"n": len(xs), "mean": m, "se_iid": se_iid, "se_cluster": se_c, "z": z,
            "win": (sum(1 for x in xs if x > 0) / len(xs)) if xs else float("nan")}


def split(results: list[EventResult]) -> dict[str, list[EventResult]]:
    cut = pd.Timestamp(HOLDOUT_START)
    return {
        "All events": results,
        "In-sample (before 2017)": [r for r in results if r.react_date < cut],
        "Holdout (2017 onwards)": [r for r in results if r.react_date >= cut],
        "Long only": [r for r in results if r.direction == 1],
        "Short only": [r for r in results if r.direction == -1],
    }


def verdict(s: dict[str, float]) -> str:
    if s["n"] < MIN_EVENTS:
        return f"only {s['n']} events (need {MIN_EVENTS}): no conclusion"
    if math.isnan(s["z"]) or abs(s["z"]) < Z_BAR:
        return "within noise: no evidence of drift"
    return "SUPPORTS continuation" if s["z"] > 0 else "CONTRADICTS continuation (reversal)"


# ---- running it -----------------------------------------------------------


def run(events: dict[str, list[datetime]], prices: dict[str, pd.DataFrame], spy: pd.DataFrame) -> tuple[list[EventResult], dict[str, int]]:
    results: list[EventResult] = []
    skipped: dict[str, int] = {}
    start = pd.Timestamp(EVENT_START)
    for ticker, times in events.items():
        px = prices.get(ticker)
        if px is None:
            skipped["no_prices"] = skipped.get("no_prices", 0) + len(times)
            continue
        for t in times:
            if pd.Timestamp(t.astimezone(NY).date()) < start:
                continue
            out = evaluate_event(ticker, px, spy, t)
            if isinstance(out, str):
                skipped[out] = skipped.get(out, 0) + 1
            else:
                results.append(out)
    results.sort(key=lambda r: r.react_date)
    return results, skipped


def format_report(results: list[EventResult], skipped: dict[str, int], tickers: int) -> str:
    pct = lambda v: "n/a" if v is None or math.isnan(v) else f"{v * 100:+.2f}%"
    lines = [
        f"Pre-registered post-earnings drift test: {len(results)} triggered events, {tickers} stocks, "
        f"{results[0].react_date:%Y-%m-%d} to {results[-1].react_date:%Y-%m-%d}" if results else "No events.",
        f"Fixed settings: trigger |reaction| >= {REACTION_THRESHOLD:.0%}, hold {HOLD_DAYS} sessions, stop {STOP_ATR:g} x ATR, holdout from {HOLDOUT_START}.",
        "Skipped events: " + (", ".join(f"{k} {v}" for k, v in sorted(skipped.items())) or "none"),
        "",
        "PRIMARY: market-adjusted gross return per trade (SE clustered by calendar quarter)",
    ]
    if not results:
        return "\n".join(lines)
    parts = split(results)
    for name, rs in parts.items():
        s = summarise(rs)
        lines.append(f"  {name}: n={s['n']}, mean {pct(s['mean'])}, SE {pct(s['se_cluster'])} (iid {pct(s['se_iid'])}), z {s['z']:+.2f}  -> {verdict(s)}")
    lines += ["", "By year (primary measure):"]
    years: dict[int, list[EventResult]] = {}
    for r in results:
        years.setdefault(r.react_date.year, []).append(r)
    for y in sorted(years):
        s = summarise(years[y])
        lines.append(f"  {y}: n={s['n']:3d}, mean {pct(s['mean'])}")
    lines += ["", "TRADED variant (stop, direction-signed, unhedged), mean return per trade net of each cost scenario:"]
    for name in ("All events", "In-sample (before 2017)", "Holdout (2017 onwards)"):
        rs = parts[name]
        for sc_name, sc in SCENARIOS.items():
            m, se = mean_se([net_pct(r, sc) for r in rs])
            lines.append(f"  {name} | {sc_name}: n={len(rs)}, mean {pct(m)}, SE {pct(se)}")
    rr = [r.traded_r for r in parts["Holdout (2017 onwards)"]]
    m, se = mean_se(rr)
    lines.append(f"  Holdout expectancy in R (gross): mean {m:+.2f}R, SE {se:.2f}")
    reasons: dict[str, int] = {}
    for r in results:
        reasons[r.exit_reason] = reasons.get(r.exit_reason, 0) + 1
    lines.append("  Exits: " + ", ".join(f"{k} {v}" for k, v in sorted(reasons.items())))
    lines += ["", "Break-even: the primary mean is the most one trade can cost before the edge is gone. Compare it with the scenario costs above.",
              "Limits: survivorship bias (an upper bound), cost scenarios are assumptions, positions not capped, SEs are rough."]
    return "\n".join(lines)


def fetch_all_data() -> int:
    import requests

    from .edgar import fetch_all, save_events
    from .stock_prices import load_prices

    session = requests.Session()
    try:
        events, notes = fetch_all(session, list(UNIVERSE))
        save_events(events)
        got = 0
        for sym in list(events) + [BENCHMARK]:
            try:
                load_prices(session, sym)
                got += 1
            except Exception as exc:  # type name only
                notes.append(f"{sym} prices: unavailable ({type(exc).__name__})")
    except Exception as exc:
        print(f"FAILED: {type(exc).__name__}")
        return 1
    print(f"Earnings events for {len(events)} stocks ({sum(len(v) for v in events.values())} filings), prices for {got} symbols.")
    for n in notes:
        print(f"  note: {n}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if "--fetch" in args:
        return fetch_all_data()
    from .edgar import load_events
    from .stock_prices import CACHE_DIR

    try:
        events = load_events()
        prices = {s: pd.read_csv(CACHE_DIR / f"{s}.csv", parse_dates=["timestamp"]) for s in events if (CACHE_DIR / f"{s}.csv").exists()}
        spy = pd.read_csv(CACHE_DIR / f"{BENCHMARK}.csv", parse_dates=["timestamp"])
    except Exception as exc:
        print(f"FAILED: {type(exc).__name__} (run with --fetch first)")
        return 1
    results, skipped = run(events, prices, spy)
    print(format_report(results, skipped, len(prices)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
