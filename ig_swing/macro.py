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
