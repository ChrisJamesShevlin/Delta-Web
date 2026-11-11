"""Shadow mode for news-only ideas: record them, never trade them, and measure what would have happened.

A news-only idea is a HIGH-confidence BULLISH or BEARISH news view on a day with no
technical signal and no open position. The point is to test the thesis that the
news alone carries an edge, at no cost and no risk, before any money follows it.

An idea is entered at the next daily open (paying half the spread), with the same
ATR stop and target as the technical engine, stop checked before target, gaps
filled at the open, and a time exit. One idea at a time: overlapping ideas in the
same stretch are not independent samples, so they are skipped.

Results are reported with the sample size and a standard error. Under 100 ideas
means no conclusion, whatever the average says. Not modelled: financing.

Measure:  python3 -m ig_swing.shadow     (read-only; uses a cached daily price pull)
"""

from __future__ import annotations

import json
import math
from typing import Any

import pandas as pd

from .news_view import NewsView
from .strategy import StrategyParams, compute_features

KIND = "shadow_news_idea"
MIN_SAMPLE = 100


def news_only_idea(df: pd.DataFrame, view: NewsView | None, strat: StrategyParams = StrategyParams()) -> dict | None:
    """The idea implied by a strong news view on the latest completed bar, or None."""
    if view is None or view.confidence != "HIGH" or view.direction == "NEUTRAL" or len(df) < strat.min_bars:
        return None
    r = compute_features(df, strat).iloc[-1]
    if pd.isna(r["atr"]) or r["atr"] <= 0:
        return None
    stop = float(strat.stop_atr * r["atr"])
    return {
        "direction": "BUY" if view.direction == "BULLISH" else "SELL",
        "bar_date": str(r["timestamp"]) if "timestamp" in df.columns else str(len(df) - 1),
        "ref_price": float(r["close"]), "atr": float(r["atr"]),
        "stop_distance": stop, "target_distance": float(strat.target_r * stop),
        "view": view.to_json(),
    }


def already_recorded(events: list[dict], idea: dict) -> bool:
    for e in events:
        if e["kind"] != KIND:
            continue
        try:
            d = json.loads(e["detail"])
        except (ValueError, TypeError):
            continue
        if d.get("bar_date") == idea["bar_date"] and d.get("direction") == idea["direction"]:
            return True
    return False


def simulate_idea(idea: dict, df: pd.DataFrame, spread: float = 1.0, max_hold: int = 40) -> dict | None:
    """Outcome in R, or None when the idea has not finished (no entry bar yet, or still running)."""
    f = df.reset_index(drop=True)
    stamp = pd.Timestamp(idea["bar_date"])
    idx = f.index[f["timestamp"] <= stamp]
    if len(idx) == 0 or idx[-1] + 1 >= len(f):
        return None
    j0 = int(idx[-1]) + 1
    half = spread / 2
    buy = idea["direction"] == "BUY"
    sd, td = idea["stop_distance"], idea["target_distance"]
    entry = f["open"].iloc[j0] + half if buy else f["open"].iloc[j0] - half
    stop, target = (entry - sd, entry + td) if buy else (entry + sd, entry - td)

    def result(j, price, reason):
        move = price - entry if buy else entry - price
        return {"entry_idx": j0, "exit_idx": j, "entry": float(entry), "exit": float(price), "reason": reason,
                "r": float(move / sd), "bar_date": idea["bar_date"], "direction": idea["direction"]}

    for j in range(j0, len(f)):
        o, h, lo, c = (float(f[k].iloc[j]) for k in ("open", "high", "low", "close"))
        if j > j0:  # an overnight gap can jump past the stop or the target
            if buy and o - half <= stop:
                return result(j, o - half, "stop_gap")
            if not buy and o + half >= stop:
                return result(j, o + half, "stop_gap")
            if buy and o - half >= target:
                return result(j, o - half, "target")
            if not buy and o + half <= target:
                return result(j, o + half, "target")
        if buy:
            if lo - half <= stop:
                return result(j, stop, "stop")
            if h - half >= target:
                return result(j, target, "target")
        else:
            if h + half >= stop:
                return result(j, stop, "stop")
            if lo + half <= target:
                return result(j, target, "target")
        if j - j0 + 1 >= max_hold:
            return result(j, c - half if buy else c + half, "time")
    return None


def evaluate(ideas: list[dict], df: pd.DataFrame, spread: float = 1.0, max_hold: int = 40) -> dict[str, Any]:
    """One idea at a time, oldest first. Returns the finished outcomes and summary statistics."""
    done: list[dict] = []
    pending = 0
    last_exit = -1
    for idea in sorted(ideas, key=lambda i: i["bar_date"]):
        out = simulate_idea(idea, df, spread, max_hold)
        if out is None:
            pending += 1
            continue
        if out["entry_idx"] <= last_exit:
            continue                      # overlaps an earlier idea: not an independent sample
        last_exit = out["exit_idx"]
        done.append(out)
    rs = [o["r"] for o in done]
    n = len(rs)
    mean = sum(rs) / n if n else 0.0
    se = math.sqrt(sum((x - mean) ** 2 for x in rs) / (n - 1) / n) if n > 1 else float("nan")
    return {"outcomes": done, "n": n, "pending": pending, "mean_r": mean, "se_r": se,
            "win_rate": (sum(1 for x in rs if x > 0) / n) if n else 0.0}


def format_evaluation(res: dict[str, Any]) -> str:
    n = res["n"]
    lines = [f"Shadow news-only ideas: {n} finished, {res['pending']} not finished yet (n={n})"]
    if n:
        se = "n/a" if math.isnan(res["se_r"]) else f"{res['se_r']:.2f}"
        lines.append(f"  mean {res['mean_r']:+.2f}R per idea, standard error {se}, win rate {res['win_rate']:.0%}")
    if n < MIN_SAMPLE:
        lines.append(f"  Fewer than {MIN_SAMPLE} ideas: no conclusion, whatever the average says.")
    elif not math.isnan(res["se_r"]) and abs(res["mean_r"]) < 2 * res["se_r"]:
        lines.append("  Within 2 standard errors of zero: indistinguishable from no edge.")
    lines.append("Not traded. Financing not modelled.")
    return "\n".join(lines)


def main() -> int:
    from .config import base_url, ig_env, load_credentials
    from .daily import HISTORY_EPIC
    from .ig_client import IGClient
    from .journal import Journal

    try:
        env = ig_env()
        client = IGClient(load_credentials(env), base_url=base_url(env), allow_live=(env == "live"))
        events = Journal().events()
        ideas = [json.loads(e["detail"]) for e in events if e["kind"] == KIND]
        if not ideas:
            print("No shadow news-only ideas recorded yet.")
            return 0
        df = client.get_prices(HISTORY_EPIC, "DAY", 120)
        print(format_evaluation(evaluate(ideas, df)))
    except Exception as exc:  # type name only: messages can carry sensitive detail
        print(f"FAILED: {type(exc).__name__}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
