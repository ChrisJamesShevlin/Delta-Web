"""Longer daily history from Yahoo for the technical read, so the weekly IG price allowance is not spent on it.

Context only: Yahoo's series is a different instrument from IG's daily bet (an index is the cash index, gold and oil are the front
future, currencies are the Yahoo spot rate), so levels are rescaled to IG's price by the ratio of the latest closes and are approximate.
Stops and ATR stay on IG's own bars. Unofficial endpoint: any failure returns None and the briefing carries on without it.
Cached for 12 hours in data/cache/yahoo (gitignored).
"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from .stock_prices import fetch_prices

CACHE_DIR = Path("data/cache/yahoo")
TTL_HOURS = 12
LOOKBACK_DAYS = 330

YAHOO: dict[str, str] = {
    "US Tech 100": "^NDX", "US 500": "^GSPC", "Wall Street": "^DJI", "US Russell 2000": "^RUT", "Germany 40": "^GDAXI",
    "FTSE 100": "^FTSE", "France 40": "^FCHI", "Japan 225": "^N225",
    "Apple": "AAPL", "Microsoft": "MSFT", "NVIDIA": "NVDA", "Amazon": "AMZN", "Alphabet": "GOOGL", "Meta": "META", "Tesla": "TSLA",
    "Netflix": "NFLX", "Intel": "INTC",
    "Gold": "GC=F", "Silver": "SI=F", "Crude oil": "CL=F", "Brent crude": "BZ=F", "Natural gas": "NG=F",
    "EUR/USD": "EURUSD=X", "GBP/USD": "GBPUSD=X", "USD/JPY": "JPY=X", "AUD/USD": "AUDUSD=X", "EUR/GBP": "EURGBP=X",
    "USD/CAD": "CAD=X", "USD/CHF": "CHF=X", "NZD/USD": "NZDUSD=X", "GBP/JPY": "GBPJPY=X", "EUR/JPY": "EURJPY=X",
}


def load_daily(session: Any, symbol: str, today: date, cache: Path = CACHE_DIR, now_ts: float | None = None, pause: float = 0.2) -> pd.DataFrame | None:
    """Complete daily bars (nothing dated today or later) for about the last 330 days, or None on any failure."""
    path = cache / f"{symbol.replace('^', '_').replace('=', '_')}.csv"
    now_ts = time.time() if now_ts is None else now_ts
    try:
        if path.exists() and now_ts - path.stat().st_mtime < TTL_HOURS * 3600:
            df = pd.read_csv(path, parse_dates=["timestamp"])
        else:
            time.sleep(pause)
            df = fetch_prices(session, symbol, start=today - timedelta(days=LOOKBACK_DAYS), end=today + timedelta(days=1))
            cache.mkdir(parents=True, exist_ok=True)
            df.to_csv(path, index=False)
        df = df[df["timestamp"] < pd.Timestamp(today)].reset_index(drop=True)
        return df if len(df) >= 25 else None
    except Exception:
        return None


def scale_to(bars: pd.DataFrame, ig_price: float) -> pd.DataFrame | None:
    """Rescale the Yahoo bars so the latest close equals IG's price (index points, pence for shares, pip-scaled currencies)."""
    last = float(bars["close"].iloc[-1])
    if last <= 0 or ig_price <= 0:
        return None
    k = ig_price / last
    out = bars.copy()
    for c in ("open", "high", "low", "close"):
        out[c] = out[c].astype(float) * k
    return out


def symbol_for(name: str, epic: str, ticker: str | None) -> str | None:
    """Yahoo symbol for a market: from the table by its default-list name, else a US share by ticker."""
    if name in YAHOO:
        return YAHOO[name]
    return ticker
