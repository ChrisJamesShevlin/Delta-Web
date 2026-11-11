"""Reporting against the pre-registered criteria. Always states sample size and standard error.

Judge by expectancy per trade after spread and financing, with a standard
error, not by the P&L figure alone. A mean within about 2 standard errors of
zero is treated as indistinguishable from zero.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable

MIN_TRADES = 100  # pre-registered: no live decision before this many trades


@dataclass(frozen=True)
class Stats:
    n: int
    wins: int
    win_rate: float | None
    mean_pnl: float | None
    se_pnl: float | None
    mean_r: float | None
    se_r: float | None
    total_pnl: float
    max_drawdown_pct: float | None
    end_equity: float


def _mean_se(xs: list[float]) -> tuple[float | None, float | None]:
    if not xs:
        return None, None
    mean = sum(xs) / len(xs)
    if len(xs) < 2:
        return mean, None
    var = sum((x - mean) ** 2 for x in xs) / (len(xs) - 1)
    return mean, math.sqrt(var / len(xs))


def max_drawdown_pct(pnls: list[float], start_equity: float) -> float | None:
    """Largest peak-to-trough fall of the closed-trade equity curve, as a percentage of the peak."""
    if not pnls or start_equity <= 0:
        return None
    equity = peak = start_equity
    worst = 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        worst = max(worst, (peak - equity) / peak * 100 if peak > 0 else 0.0)
    return worst


def summarise(pnls: list[float], rs: list[float], start_equity: float) -> Stats:
    mean_p, se_p = _mean_se(pnls)
    mean_r, se_r = _mean_se(rs)
    wins = sum(1 for p in pnls if p > 0)
    return Stats(
        n=len(pnls), wins=wins, win_rate=(wins / len(pnls) if pnls else None),
        mean_pnl=mean_p, se_pnl=se_p, mean_r=mean_r, se_r=se_r, total_pnl=sum(pnls),
        max_drawdown_pct=max_drawdown_pct(pnls, start_equity), end_equity=start_equity + sum(pnls),
    )


def summarise_trades(trades: list[dict[str, Any]], start_equity: float) -> Stats:
    pnls = [t["net_pnl"] for t in trades if t.get("net_pnl") is not None]
    rs = [t["r_multiple"] for t in trades if t.get("r_multiple") is not None]
    return summarise(pnls, rs, start_equity)


def group_stats(trades: list[dict[str, Any]], key: Callable[[dict[str, Any]], str], start_equity: float) -> dict[str, Stats]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for t in trades:
        groups.setdefault(key(t) or "untagged", []).append(t)
    return {k: summarise_trades(v, start_equity) for k, v in sorted(groups.items())}


def verdict(s: Stats, min_trades: int = MIN_TRADES) -> str:
    if s.n == 0:
        return "No closed trades yet."
    if s.n < min_trades:
        base = f"Only {s.n} of the {min_trades} trades needed. Treat any result as noise."
    else:
        base = f"Sample size {s.n} meets the {min_trades} trade minimum."
    if s.mean_r is None or s.se_r is None:
        return base + " Too few trades for a standard error."
    if abs(s.mean_r) < 2 * s.se_r:
        return base + " Expectancy is indistinguishable from zero."
    side = "positive" if s.mean_r > 0 else "negative"
    return base + f" Expectancy is {side} by more than 2 standard errors. That is not proof: check costs, the sample and regime."


def _fmt(v: float | None, spec: str = ".2f", suffix: str = "") -> str:
    return "n/a" if v is None else f"{v:{spec}}{suffix}"


def format_stats(title: str, s: Stats) -> str:
    return "\n".join([
        f"{title}",
        f"  trades {s.n}, win rate {_fmt(None if s.win_rate is None else s.win_rate * 100, '.0f', '%')}",
        f"  expectancy per trade £{_fmt(s.mean_pnl)} (SE £{_fmt(s.se_pnl)}, n={s.n})",
        f"  expectancy in R {_fmt(s.mean_r)} (SE {_fmt(s.se_r)}, n={s.n})",
        f"  total £{s.total_pnl:.2f}, max drawdown {_fmt(s.max_drawdown_pct, '.1f', '%')}, end equity £{s.end_equity:.2f}",
        f"  {verdict(s)}",
    ])


def format_report(trades: list[dict[str, Any]], start_equity: float) -> str:
    lines = [format_stats("All closed trades", summarise_trades(trades, start_equity))]
    for label, key in (("By setup", lambda t: t.get("setup")), ("By driver", lambda t: t.get("driver")), ("By direction", lambda t: t.get("direction"))):
        groups = group_stats(trades, key, start_equity)
        if len(groups) > 1 or (groups and "untagged" not in groups):
            lines += ["", label]
            for name, s in groups.items():
                lines.append(f"  {name:12} n={s.n:<4} expectancy £{_fmt(s.mean_pnl)} (SE £{_fmt(s.se_pnl)}) R {_fmt(s.mean_r)}")
    return "\n".join(lines)
