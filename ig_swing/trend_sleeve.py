"""Pre-registered backtest of the multi-market trend sleeve on IG's own monthly prices. See CLAUDE.md.

Fetch the data once (read-only, spends about 14 x 300 allowance points):  python3 -m ig_swing.trend_sleeve --fetch
Run the test once:                                                         python3 -m ig_swing.trend_sleeve
Do NOT change any constant below after seeing a result: that moves the goalposts.

Each month end: a signal per market, a size per market from its own volatility, the whole sleeve scaled to a 10%
volatility target, held for the next month. Costs: observed IG spread times 2, financing 2.5% a year on gross notional.
The sleeve is an overlay, so its return is the excess over cash.

User bars: (1) net return of at least 4% a year over EVERY rolling 120-month window, (2) the static mix plus the sleeve
has a shallower worst drawdown and no lower return than the static mix alone. Missing either ends the idea.

Limits: monthly bars understate drawdowns, daily bets omit the carry of quarterly futures, rates are the US 10-year ETF
bet only (IG has no gilt or bund daily bet), historical spreads are unknown.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

MARKETS: dict[str, dict[str, Any]] = {
    "US 500": dict(epic="IX.D.SPTRD.DAILY.IP", cls="equity", spread=0.000092),
    "US Tech 100": dict(epic="IX.D.NASDAQ.CASH.IP", cls="equity", spread=0.000035),
    "Germany 40": dict(epic="IX.D.DAX.DAILY.IP", cls="equity", spread=0.000116),
    "Japan 225": dict(epic="IX.D.NIKKEI.DAILY.IP", cls="equity", spread=0.000193),
    "US 10Y Treasury ETF": dict(epic="UD.D.UTENUS.DAILY.IP", cls="rates", spread=0.003475),
    "EUR/USD": dict(epic="CS.D.EURUSD.TODAY.IP", cls="fx", spread=0.00008),
    "GBP/USD": dict(epic="CS.D.GBPUSD.TODAY.IP", cls="fx", spread=0.00011),
    "USD/JPY": dict(epic="CS.D.USDJPY.TODAY.IP", cls="fx", spread=0.00006),
    "AUD/USD": dict(epic="CS.D.AUDUSD.TODAY.IP", cls="fx", spread=0.000131),
    "Gold": dict(epic="CS.D.USCGC.TODAY.IP", cls="metal", spread=0.00008),
    "Silver": dict(epic="CS.D.USCSI.TODAY.IP", cls="metal", spread=0.000772),
    "Copper": dict(epic="CS.D.COPPER.TODAY.IP", cls="metal", spread=0.00218),
    "Crude oil": dict(epic="CC.D.CL.USS.IP", cls="energy", spread=0.0006),
    "Natural gas": dict(epic="CC.D.NG.USS.IP", cls="energy", spread=0.000922),
}
BARS = 300
VOL_TARGET = 0.10
VOL_WINDOW, VOL_MIN = 36, 12
LEV_MIN, LEV_MAX = 1.0, 3.0
SPREAD_MULT = 2.0
FINANCING = 0.025
LOOKBACKS = (1, 3, 12)
WINDOW = 120
RETURN_BAR = 0.04
PRIMARY = "V2"
STATIC_WEIGHTS = {"US 500": 0.7, "bond": 0.3}
CACHE_DIR = Path("data/history/trend")
BOND_DURATION = 8.0                      # modified duration used for the FRED-based 10-year proxy
BOND_NAME = "US 10Y (FRED proxy)"
FRED_URL = "https://api.stlouisfed.org/fred/series/observations"

FIXED: dict[str, Any] = dict(vol_target=VOL_TARGET, vol_window=(VOL_MIN, VOL_WINDOW), leverage=(LEV_MIN, LEV_MAX), spread_mult=SPREAD_MULT,
                             financing=FINANCING, lookbacks=LOOKBACKS, window=WINDOW, return_bar=RETURN_BAR, primary=PRIMARY)


# ---- data -----------------------------------------------------------------


def monthly_closes(df: pd.DataFrame, today: pd.Timestamp | None = None) -> pd.Series:
    """Month-end close per calendar month. The current unfinished month is dropped."""
    today = today or pd.Timestamp.today()
    s = pd.Series(df["close"].to_numpy(float), index=pd.to_datetime(df["timestamp"]).dt.to_period("M"))
    s = s[~s.index.duplicated(keep="last")].sort_index()
    return s[s.index < today.to_period("M")]


def build_panel(frames: dict[str, pd.DataFrame], today: pd.Timestamp | None = None) -> pd.DataFrame:
    cols = {name: monthly_closes(df, today) for name, df in frames.items()}
    panel = pd.DataFrame(cols)
    if panel.empty:
        return panel
    full = pd.period_range(panel.index.min(), panel.index.max(), freq="M")
    return panel.reindex(full)


def fetch_all(cache: Path = CACHE_DIR) -> int:
    from .config import base_url, ig_env, load_credentials
    from .ig_client import IGClient

    cache.mkdir(parents=True, exist_ok=True)
    try:
        env = ig_env()
        client = IGClient(load_credentials(env), base_url=base_url(env), allow_live=(env == "live"))
    except Exception as exc:
        print(f"FAILED: {type(exc).__name__}")
        return 1
    done = 0
    for name, m in MARKETS.items():
        path = cache / f"{m['epic']}.csv"
        if path.exists():
            done += 1
            continue
        try:
            df = client.get_prices(m["epic"], "MONTH", BARS)
            df[["timestamp", "close"]].to_csv(path, index=False)
            print(f"{name}: {len(df)} monthly bars, {df['timestamp'].iloc[0]:%Y-%m} to {df['timestamp'].iloc[-1]:%Y-%m}", flush=True)
            done += 1
        except Exception as exc:  # type name only
            print(f"{name}: FAILED {type(exc).__name__}", flush=True)
    print(f"{done} of {len(MARKETS)} markets cached. Allowance left: {client._cache.load_allowance()}")
    return 0 if done == len(MARKETS) else 1


def load_panel(cache: Path = CACHE_DIR) -> pd.DataFrame:
    frames = {}
    for name, m in MARKETS.items():
        path = cache / f"{m['epic']}.csv"
        if path.exists():
            frames[name] = pd.read_csv(path, parse_dates=["timestamp"])
    return build_panel(frames)


def bond_returns_from_yields(yields: pd.Series) -> pd.Series:
    """Monthly return proxy for a 10-year Treasury from month-end yields in percent: carry less duration times the change.

    return_t = y_{t-1} / 12 - D x (y_t - y_{t-1}), with D = BOND_DURATION. Convexity is ignored.
    """
    y = yields.astype(float) / 100.0
    return (y.shift(1) / 12 - BOND_DURATION * (y - y.shift(1))).dropna()


def fetch_yields(session: Any, api_key: str, start: str = "2001-01-01") -> pd.Series:
    resp = session.get(FRED_URL, params={"series_id": "DGS10", "api_key": api_key, "file_type": "json", "sort_order": "asc",
                                         "observation_start": start, "limit": 100000}, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}")
    rows = {}
    for o in resp.json().get("observations", []):
        try:
            rows[pd.Timestamp(o["date"])] = float(o["value"])
        except (KeyError, ValueError, TypeError):
            continue
    daily = pd.Series(rows).sort_index()
    return daily.groupby(daily.index.to_period("M")).last()


def load_bond_proxy(cache: Path = CACHE_DIR) -> pd.Series:
    """Monthly 10-year Treasury return proxy from FRED (cached). Needs FRED_API_KEY the first time."""
    path = cache / "fred_DGS10_monthly.csv"
    if path.exists():
        y = pd.read_csv(path, index_col=0)["yield"]
        y.index = pd.PeriodIndex(y.index, freq="M")
    else:
        import requests

        from .config import optional_key

        key = optional_key("FRED_API_KEY")
        if not key:
            raise RuntimeError("FRED_API_KEY is not set")
        y = fetch_yields(requests.Session(), key)
        cache.mkdir(parents=True, exist_ok=True)
        y.rename("yield").to_frame().to_csv(path)
    return bond_returns_from_yields(y)


# ---- the strategy: signals, sizes, leverage, portfolio ---------------------


def signals(panel: pd.DataFrame, variant: str) -> pd.DataFrame:
    """Signal at each month end, from closes up to and including that month. NaN where history is missing."""
    signs = {L: np.sign(panel / panel.shift(L) - 1) for L in LOOKBACKS}
    if variant == "V1":
        return signs[12]
    if variant == "V2":
        return (signs[1] + signs[3] + signs[12]) / 3.0
    raise ValueError("variant must be V1 or V2")


def annual_vol(returns: pd.DataFrame | pd.Series) -> Any:
    return returns.rolling(VOL_WINDOW, min_periods=VOL_MIN).std() * math.sqrt(12)


def raw_weights(sig: pd.DataFrame, returns: pd.DataFrame) -> pd.DataFrame:
    """Signal times 10% over the market's own volatility, divided by the number of active markets."""
    vol = annual_vol(returns)
    w = sig * (VOL_TARGET / vol)
    n = w.notna().sum(axis=1).replace(0, np.nan)
    return w.div(n, axis=0)


def held_returns(weights: pd.DataFrame, returns: pd.DataFrame) -> pd.Series:
    """Weights decided at the end of month t-1 earn the return of month t."""
    return (weights.shift(1).fillna(0.0) * returns.fillna(0.0)).sum(axis=1)


def leverage(raw_gross: pd.Series) -> pd.Series:
    """Scale to the volatility target from the trailing raw sleeve returns. Leverage 1 until enough history. No look-ahead."""
    active = raw_gross.copy()
    first = raw_gross.ne(0.0).idxmax() if raw_gross.ne(0.0).any() else None
    if first is None:
        return pd.Series(1.0, index=raw_gross.index)
    active[active.index < first] = np.nan
    lev = (VOL_TARGET / annual_vol(active)).clip(LEV_MIN, LEV_MAX)
    return lev.fillna(1.0)


def portfolio(W: pd.DataFrame, returns: pd.DataFrame, spreads: pd.Series, mult: float = SPREAD_MULT, financing: float = FINANCING) -> pd.DataFrame:
    """Gross, spread cost, financing and net return for each month, from weights held during that month."""
    Wf = W.fillna(0.0)
    gross = held_returns(W, returns)
    trade = (Wf - Wf.shift(1).fillna(0.0)).abs()
    cost = (trade * spreads.reindex(W.columns)).sum(axis=1).shift(1).fillna(0.0) * mult / 2
    fin = Wf.abs().sum(axis=1).shift(1).fillna(0.0) * financing / 12
    out = pd.DataFrame({"gross": gross, "spread_cost": cost, "financing": fin})
    out["net"] = out["gross"] - out["spread_cost"] - out["financing"]
    out["turnover"] = trade.sum(axis=1).shift(1).fillna(0.0)
    started = Wf.shift(1).fillna(0.0).abs().sum(axis=1) > 0
    return out[started.cummax()] if started.any() else out.iloc[0:0]


def run_sleeve(panel: pd.DataFrame, variant: str, extra_spreads: dict[str, float] | None = None) -> dict[str, Any]:
    returns = panel.pct_change(fill_method=None)
    sig = signals(panel, variant)
    raw = raw_weights(sig, returns)
    lev = leverage(held_returns(raw, returns))
    W = raw.mul(lev, axis=0)
    spreads = pd.Series({n: m["spread"] for n, m in MARKETS.items()} | (extra_spreads or {}))
    pf = portfolio(W, returns, spreads)
    return {"variant": variant, "returns": returns, "weights": W, "leverage": lev, "pf": pf}


# ---- metrics ---------------------------------------------------------------


def annualised(r: pd.Series) -> float:
    return float((1 + r).prod() ** (12 / len(r)) - 1) if len(r) else float("nan")


def drawdown(r: pd.Series) -> tuple[float, int]:
    """Worst peak-to-trough fall (as a negative number) and the longest run of months below a prior peak."""
    eq = (1 + r).cumprod()
    peak = eq.cummax()
    dd = eq / peak - 1
    longest = run = 0
    for below in (eq < peak).tolist():
        run = run + 1 if below else 0
        longest = max(longest, run)
    return float(dd.min()), longest


def rolling_windows(r: pd.Series, window: int = WINDOW) -> pd.Series:
    vals = [annualised(r.iloc[k:k + window]) for k in range(len(r) - window + 1)]
    return pd.Series(vals, index=r.index[window - 1:]) if vals else pd.Series(dtype=float)


def bar1(r: pd.Series) -> dict[str, Any]:
    w = rolling_windows(r)
    if w.empty:
        return {"windows": 0, "worst": float("nan"), "share": float("nan"), "passes": False}
    return {"windows": len(w), "worst": float(w.min()), "worst_end": w.idxmin(), "share": float((w >= RETURN_BAR).mean()), "passes": bool((w >= RETURN_BAR).all())}


def static_mix(returns: pd.DataFrame, bond: pd.Series) -> pd.Series:
    """70% US 500 and 30% bond proxy, monthly, price returns for shares (no income). Months where either leg is missing are skipped."""
    both = pd.concat([returns["US 500"], bond.rename("bond")], axis=1).dropna()
    return both["US 500"] * STATIC_WEIGHTS["US 500"] + both["bond"] * STATIC_WEIGHTS["bond"]


def bar2(sleeve: pd.Series, static: pd.Series) -> dict[str, Any]:
    idx = sleeve.index.intersection(static.index)
    base = static.loc[idx]
    combined = base + sleeve.loc[idx]
    dd_s, _ = drawdown(base)
    dd_c, _ = drawdown(combined)
    ret_s, ret_c = annualised(base), annualised(combined)
    return {"months": len(idx), "static_dd": dd_s, "combined_dd": dd_c, "static_ret": ret_s, "combined_ret": ret_c,
            "passes": bool(len(idx) > 0 and dd_c > dd_s and ret_c >= ret_s)}


def summary(res: dict[str, Any]) -> dict[str, Any]:
    pf, returns = res["pf"], res["returns"]
    net = pf["net"]
    dd, under = drawdown(net)
    us = returns["US 500"].reindex(net.index)
    years = net.groupby(net.index.year).apply(lambda x: float((1 + x).prod() - 1))
    periods = {"to 2009": net[net.index.year <= 2009], "2010 to 2019": net[(net.index.year >= 2010) & (net.index.year <= 2019)],
               "2020 onwards": net[net.index.year >= 2020]}
    vol = float(net.std() * math.sqrt(12))
    ann = annualised(net)
    return {"months": len(net), "start": net.index[0], "end": net.index[-1], "ann": ann, "vol": vol, "ratio": ann / vol if vol else float("nan"),
            "dd": dd, "under": under, "corr": float(net.corr(us)), "years": years,
            "periods": {k: (annualised(v), len(v)) for k, v in periods.items() if len(v)},
            "spread_drag": float(pf["spread_cost"].mean() * 12), "fin_drag": float(pf["financing"].mean() * 12),
            "turnover": float(pf["turnover"].mean() * 12), "bar1": bar1(net)}


def format_report(results: dict[str, dict[str, Any]], panel: pd.DataFrame, bond: pd.Series, sensitivity: dict[str, Any] | None = None) -> str:
    pct = lambda v: "n/a" if v is None or v != v else f"{v * 100:+.2f}%"
    first = {c: panel[c].first_valid_index() for c in panel.columns}
    lines = [f"Trend sleeve on IG monthly prices: {panel.index.min()} to {panel.index.max()} ({len(panel)} months), {panel.shape[1]} markets",
             "Markets and first month: " + ", ".join(f"{c} {first[c]}" for c in panel.columns),
             f"Fixed: 10% volatility target, leverage {LEV_MIN:g} to {LEV_MAX:g}, spread x{SPREAD_MULT:g}, financing {FINANCING:.1%} a year, "
             f"bars: net >= {RETURN_BAR:.0%} a year in every rolling {WINDOW}-month window, and a shallower drawdown with the sleeve added.", ""]
    verdict_parts = {}
    for variant in ("V2", "V1"):
        res = results[variant]
        s = summary(res)
        tag = "PRIMARY" if variant == PRIMARY else "comparison only"
        lines += [f"{variant} ({tag}): {s['months']} months, {s['start']} to {s['end']}",
                  f"  net return {pct(s['ann'])} a year, volatility {pct(s['vol'])}, return per unit of volatility {s['ratio']:.2f}",
                  f"  worst monthly drawdown {pct(s['dd'])}, longest time under water {s['under']} months, correlation with the US 500 {s['corr']:+.2f}",
                  f"  drag a year: spread {pct(s['spread_drag'])}, financing {pct(s['fin_drag'])}; turnover {s['turnover']:.1f}x a year",
                  "  by period: " + ", ".join(f"{k} {pct(v[0])} ({v[1]} months)" for k, v in s["periods"].items()),
                  "  calendar years: " + ", ".join(f"{y} {pct(v)}" for y, v in s["years"].items() if y in (2008, 2011, 2015, 2018, 2020, 2022)),
                  f"  Bar 1: {s['bar1']['windows']} rolling {WINDOW}-month windows, worst {pct(s['bar1']['worst'])}, share at or above {RETURN_BAR:.0%}: "
                  f"{s['bar1']['share'] * 100:.0f}% -> " + ("passes" if s["bar1"]["passes"] else "FAILS")]
        static = static_mix(res["returns"], bond)
        b2 = bar2(res["pf"]["net"], static)
        lines += [f"  Bar 2 over {b2['months']} months: static mix worst drawdown {pct(b2['static_dd'])}, return {pct(b2['static_ret'])} a year; "
                  f"with the sleeve added {pct(b2['combined_dd'])}, {pct(b2['combined_ret'])} -> " + ("passes" if b2["passes"] else "FAILS"), ""]
        verdict_parts[variant] = (s["bar1"]["passes"], b2["passes"])
    if sensitivity is not None:
        s2 = summary(sensitivity)
        b2s = bar2(sensitivity["pf"]["net"], static_mix(sensitivity["returns"], bond))
        lines += ["SENSITIVITY, NOT THE VERDICT: V2 with a synthetic FRED-based 10-year bond market added as a rates leg (cost of the US 10-year ETF bet, x2)",
                  f"  net return {pct(s2['ann'])} a year, volatility {pct(s2['vol'])}, worst monthly drawdown {pct(s2['dd'])}, longest time under water {s2['under']} months, "
                  f"correlation with the US 500 {s2['corr']:+.2f}",
                  "  by period: " + ", ".join(f"{k} {pct(v[0])} ({v[1]} months)" for k, v in s2["periods"].items()),
                  "  calendar years: " + ", ".join(f"{y} {pct(v)}" for y, v in s2["years"].items() if y in (2008, 2011, 2015, 2018, 2020, 2022)),
                  f"  Bar 1: worst rolling {WINDOW}-month window {pct(s2['bar1']['worst'])}, share at or above {RETURN_BAR:.0%}: {s2['bar1']['share'] * 100:.0f}% -> "
                  + ("passes" if s2["bar1"]["passes"] else "FAILS") + f"; Bar 2: " + ("passes" if b2s["passes"] else "FAILS"),
                  "  This cannot change the verdict. If the primary fails and this passes, we say so and the user decides whether a new pre-registration is worth writing.", ""]
    p1, p2 = verdict_parts[PRIMARY]
    lines.append(f"VERDICT on the primary ({PRIMARY}): bar 1 {'passes' if p1 else 'FAILS'}, bar 2 {'passes' if p2 else 'FAILS'} -> "
                 + ("PASSES both bars: the next step is paper-tracking on IG's live prices, not real money." if p1 and p2 else
                    "FAILS the pre-registered bars: under the rule we stop and the money stays in the ISA."))
    lines.append("Limits: monthly bars understate drawdowns, daily bets omit quarterly-futures carry, rates are the US 10-year ETF bet only, "
                 "historical spreads unknown, about two independent decades so no significance is claimed.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import sys

    args = sys.argv[1:] if argv is None else argv
    if "--fetch" in args:
        return fetch_all()
    panel = load_panel()
    if panel.empty or panel.shape[1] < len(MARKETS):
        print(f"FAILED: only {panel.shape[1] if not panel.empty else 0} of {len(MARKETS)} markets cached (run with --fetch first)")
        return 1
    bond = load_bond_proxy()
    results = {v: run_sleeve(panel, v) for v in ("V1", "V2")}
    synth = (1 + bond).cumprod().rename(BOND_NAME)
    synth_panel = panel.join(synth.reindex(panel.index))
    sens = run_sleeve(synth_panel, "V2", {BOND_NAME: MARKETS["US 10Y Treasury ETF"]["spread"]})
    print(format_report(results, panel, bond, sens))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
