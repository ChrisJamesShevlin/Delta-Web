"""Pre-registered test of the VIX and oil rules in ig_swing.context, on free FRED history.

Run from the repo root:  python3 -m ig_swing.rules_test
Settings are fixed below and in CLAUDE.md. Do not change them after seeing a result.

Event study. For each rule, find the trigger days (the rule's own thresholds applied to the
FRED day-over-day percentage change), keep the first trigger in any cluster (events at least
HORIZON bars apart), and compare the US 500's forward return from that day's close with the
unconditional forward return over all days. Statistic: the difference in means over
sd(all days) / sqrt(n events), a rough z that treats events as independent draws.

The rules claim: after a VIX spike or an oil spike, the forward return is LOWER than usual
(so blocking new longs helps); after a VIX collapse it is HIGHER than usual (so blocking
new shorts helps). A significant difference the OTHER way means the rule is harmful.
Also reported, descriptively only: how many of the backtest's trades each rule would have vetoed.

Not modelled: the live rule uses IG's intraday move at the time of the plan, this uses the
daily close-to-close change. Overlapping windows and few events make every z rough.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from .context import ContextRules

CACHE_DIR = Path("data/history")
START = "2021-12-01"
HORIZON = 5          # primary forward horizon, in bars. 1 bar is shown as a secondary read.
MIN_EVENTS = 30      # fewer events than this is no conclusion
Z_BAR = 2.0
SERIES = {"VIX": "VIXCLS", "Oil": "DCOILWTICO"}
FRED_URL = "https://api.stlouisfed.org/fred/series/observations"


@dataclass(frozen=True)
class RuleSpec:
    name: str
    series: str          # key in SERIES
    test: str            # "ge" or "le"
    threshold_pct: float
    expect: str          # "lower" or "higher": what the rule claims about forward returns
    blocks: str          # the direction the live rule vetoes


def specs(rules: ContextRules = ContextRules()) -> list[RuleSpec]:
    return [
        RuleSpec("VIX spike", "VIX", "ge", rules.vix_spike_pct, "lower", "BUY"),
        RuleSpec("VIX collapse", "VIX", "le", -rules.vix_spike_pct, "higher", "SELL"),
        RuleSpec("Oil spike", "Oil", "ge", rules.oil_spike_pct, "lower", "BUY"),
    ]


def fetch_fred(session: Any, key: str, series_id: str) -> pd.Series:
    resp = session.get(FRED_URL, params={"series_id": series_id, "api_key": key, "file_type": "json", "sort_order": "asc",
                                         "observation_start": START, "limit": 100000}, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}")
    rows = {}
    for o in resp.json().get("observations", []):
        try:
            rows[pd.Timestamp(o["date"])] = float(o["value"])
        except (KeyError, ValueError, TypeError):
            continue
    return pd.Series(rows, name=series_id).sort_index()


def load_series(session: Any, key: str | None, series_id: str, cache: Path = CACHE_DIR) -> pd.Series:
    path = cache / f"fred_{series_id}.csv"
    if path.exists():
        df = pd.read_csv(path, parse_dates=["date"])
        return pd.Series(df["value"].values, index=df["date"], name=series_id)
    if not key:
        raise RuntimeError("FRED_API_KEY is not set")
    s = fetch_fred(session, key, series_id)
    cache.mkdir(parents=True, exist_ok=True)
    s.rename_axis("date").rename("value").reset_index().to_csv(path, index=False)
    return s


def trigger_dates(series: pd.Series, spec: RuleSpec) -> list[pd.Timestamp]:
    pct = series.pct_change() * 100
    hit = pct >= spec.threshold_pct if spec.test == "ge" else pct <= spec.threshold_pct
    return list(pct.index[hit.fillna(False)])


def forward_returns(close: pd.Series, k: int) -> pd.Series:
    """Return from this bar's close to the close k bars later, in percent. NaN at the end."""
    return (close.shift(-k) / close - 1) * 100


def event_study(close: pd.Series, triggers: list[pd.Timestamp], expect: str, k: int = HORIZON) -> dict[str, Any]:
    fwd = forward_returns(close, k).dropna()
    positions = {d: i for i, d in enumerate(close.index)}
    events, last = [], -10**9
    for d in sorted(triggers):
        i = positions.get(d)
        if i is None or d not in fwd.index or i - last < k:
            continue
        events.append(d)
        last = i
    n = len(events)
    base_mean, base_sd = float(fwd.mean()), float(fwd.std())
    ev = fwd.loc[events] if events else fwd.iloc[0:0]
    ev_mean = float(ev.mean()) if n else float("nan")
    diff = ev_mean - base_mean if n else float("nan")
    z = diff / (base_sd / math.sqrt(n)) if n else float("nan")
    return {"k": k, "n": n, "event_mean": ev_mean, "base_mean": base_mean, "diff": diff, "z": z, "expect": expect}


def verdict(r: dict[str, Any]) -> str:
    if r["n"] < MIN_EVENTS:
        return f"only {r['n']} events (need {MIN_EVENTS}): no conclusion"
    z = r["z"]
    if abs(z) < Z_BAR:
        return "within noise: the rule is not validated"
    supports = (z < 0) if r["expect"] == "lower" else (z > 0)
    return "SUPPORTS the rule" if supports else "CONTRADICTS the rule (it looks harmful)"


def vetoed_trades(trades: list, bars: pd.DataFrame, series_by_key: dict[str, pd.Series], rule_specs: list[RuleSpec]) -> dict[str, dict[str, Any]]:
    """Descriptive: for each rule, the backtest trades it would have vetoed, versus the rest."""
    stamps = pd.to_datetime(bars["timestamp"]).reset_index(drop=True)
    index = {pd.Timestamp(t): i for i, t in enumerate(stamps)}
    out = {}
    for sp in rule_specs:
        trig = set(trigger_dates(series_by_key[sp.series], sp))
        vetoed, kept = [], []
        for t in trades:
            j = index.get(pd.Timestamp(t.entry_date))
            sig_day = stamps[j - 1] if j else None        # the signal was on the bar before the entry bar
            (vetoed if (sig_day in trig and t.direction == sp.blocks) else kept).append(t.r_multiple)
        mean = lambda xs: (sum(xs) / len(xs)) if xs else float("nan")
        out[sp.name] = {"vetoed": len(vetoed), "vetoed_mean_r": mean(vetoed), "kept": len(kept), "kept_mean_r": mean(kept)}
    return out


def format_results(bars_n: int, first, last, studies: dict[str, dict[int, dict]], veto: dict[str, dict]) -> str:
    lines = [f"Pre-registered VIX and oil rule test: {bars_n} US 500 bars, {first:%Y-%m-%d} to {last:%Y-%m-%d}",
             f"Primary read: {HORIZON}-bar forward return after the first trigger in a cluster. |z| of {Z_BAR:g} or more, and {MIN_EVENTS}+ events, needed for any claim.", ""]
    for name, by_k in studies.items():
        for k in sorted(by_k, reverse=True):
            r = by_k[k]
            tag = "PRIMARY" if k == HORIZON else "secondary"
            lines.append(f"{name}, {k}-bar forward ({tag}): n={r['n']} events, mean {r['event_mean']:+.2f}% vs {r['base_mean']:+.2f}% for all days, "
                         f"diff {r['diff']:+.2f}%, z {r['z']:+.2f}; rule expects {r['expect']}")
            if k == HORIZON:
                lines.append(f"  -> {verdict(r)}")
    lines.append("")
    lines.append("Backtest trades each rule would have vetoed (descriptive only):")
    for name, v in veto.items():
        lines.append(f"  {name}: vetoed {v['vetoed']} trades (mean {v['vetoed_mean_r']:+.2f}R), kept {v['kept']} (mean {v['kept_mean_r']:+.2f}R)"
                     + ("  [far too few to say anything]" if v["vetoed"] < MIN_EVENTS else ""))
    lines.append("Not modelled: the live rule uses IG's intraday move, this uses the daily close-to-close change. z values are rough.")
    return "\n".join(lines)


def main() -> int:
    import requests

    from .backtest import run_backtest, load_bars_csv
    from .config import optional_key
    from .history import DEFAULT_PATH
    from .technical_test import FIXED

    try:
        bars = load_bars_csv(str(DEFAULT_PATH))
        bars["timestamp"] = pd.to_datetime(bars["timestamp"])
        session = requests.Session()
        key = optional_key("FRED_API_KEY")
        by_key = {k: load_series(session, key, sid) for k, sid in SERIES.items()}
    except Exception as exc:  # type name only: messages can carry sensitive detail
        print(f"FAILED: {type(exc).__name__}")
        return 1
    close = pd.Series(bars["close"].values, index=bars["timestamp"].dt.normalize())
    rule_specs = specs()
    studies = {sp.name: {k: event_study(close, trigger_dates(by_key[sp.series], sp), sp.expect, k) for k in (HORIZON, 1)} for sp in rule_specs}
    veto = vetoed_trades(run_backtest(bars, **FIXED).trades, bars, by_key, rule_specs)
    print(format_results(len(bars), bars["timestamp"].iloc[0], bars["timestamp"].iloc[-1], studies, veto))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
