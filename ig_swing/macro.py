"""A one-block macro backdrop for the briefing: the US 10-year yield, the dollar index and the VIX, with the move today and over five sessions.

Same-day levels from Yahoo's chart endpoint (unofficial, no key); FRED would lag a day or more. Context only: rates, the dollar and risk appetite
drive currencies, gold and the indices, but no rule here turns them into a trade, and any failure just drops the line.
"""

from __future__ import annotations

from typing import Any

SERIES: dict[str, tuple[str, str]] = {
    "US 10-year yield": ("^TNX", "%"),       # Yahoo quotes the yield in percent
    "Dollar index": ("DX-Y.NYB", ""),
    "VIX": ("^VIX", ""),
}
CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/"


def parse_macro(payload: dict) -> dict[str, float] | None:
    """Latest level, change today and change over five sessions from a Yahoo chart payload. None if there is too little data."""
    result = payload["chart"]["result"][0]
    closes = [c for c in result["indicators"]["quote"][0]["close"] if c is not None]
    level = result.get("meta", {}).get("regularMarketPrice")
    if len(closes) < 7 or level is None:
        return None
    series = closes[:-1] + [float(level)]          # the last bar is today's partial one: replace it with the live level
    return {"level": series[-1], "day": series[-1] - series[-2], "five_day": series[-1] - series[-6],
            "day_pct": (series[-1] / series[-2] - 1) * 100, "five_day_pct": (series[-1] / series[-6] - 1) * 100}


def load_macro(session: Any) -> dict[str, dict[str, float]]:
    out = {}
    for name, (symbol, _unit) in SERIES.items():
        try:
            resp = session.get(f"{CHART_URL}{symbol}", params={"range": "1mo", "interval": "1d"}, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
            if resp.status_code != 200:
                continue
            m = parse_macro(resp.json())
            if m:
                out[name] = m
        except Exception:
            continue
    return out


def format_macro(data: dict[str, dict[str, float]]) -> list[str]:
    lines = []
    for name, (_symbol, unit) in SERIES.items():
        m = data.get(name)
        if not m:
            continue
        if unit == "%":   # a yield: show moves in percentage points
            lines.append(f"  {name}: {m['level']:.2f}%, {m['day']:+.2f} points today, {m['five_day']:+.2f} over 5 sessions")
        else:
            lines.append(f"  {name}: {m['level']:.2f}, {m['day_pct']:+.2f}% today, {m['five_day_pct']:+.2f}% over 5 sessions")
    return lines


def regime_read(data: dict[str, dict[str, float]], equity_day_pct: float | None) -> str | None:
    """One descriptive line on what kind of day it is, from the US 10-year yield, the VIX, the dollar and the US 500's move today.
    Context only, no rule attached: stocks and bonds do not always move against each other (they fell together on 2026-11-16)."""
    y, v, d = data.get("US 10-year yield"), data.get("VIX"), data.get("Dollar index")
    if not y or not v or equity_day_pct is None:
        return None
    dollar = "" if not d else f", dollar {d['five_day_pct']:+.1f}% over 5 sessions"
    facts = f"US 500 {equity_day_pct:+.2f}%, 10-year yield {y['day']:+.2f} points, VIX {v['day_pct']:+.0f}%{dollar}"
    stocks_down, stocks_up = equity_day_pct <= -0.7, equity_day_pct >= 0.7
    fear, calm = v["day_pct"] >= 5.0, v["day_pct"] <= -5.0
    yields_up, yields_down = y["day"] >= 0.03, y["day"] <= -0.03
    if stocks_down and fear and yields_up:
        label = "RATE FEAR: stocks and bonds falling together, so bonds and gold are not a haven today"
    elif stocks_down and fear and yields_down:
        label = "FLIGHT TO SAFETY: bonds bid, stocks down, fear rising"
    elif stocks_down and fear:
        label = "STOCKS DOWN, FEAR RISING: bonds flat, no clear haven flow"
    elif stocks_down:
        label = "EQUITIES SOFT without a fear spike" + (", with yields rising" if yields_up else "")
    elif fear and not stocks_up:
        label = "VIX UP WITHOUT A STOCK SELLOFF: hedging demand, often ahead of an event"
    elif stocks_up and calm:
        label = "RISK-ON: stocks up, fear falling"
    else:
        label = "NO CLEAR REGIME"
    return f"  Regime read: {label} ({facts}). Descriptive only; the stock and bond link changes with the regime."
