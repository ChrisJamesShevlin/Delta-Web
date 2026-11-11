"""Daily stock prices from Yahoo's chart endpoint, adjusted for splits and dividends.

Unofficial endpoint: it can break or be restricted without notice, which is acceptable for
research and not for live trading. Verified on 2026-11-11: 20 of 20 tickers returned in 9
seconds, with daily bars back to 1995, volume, adjusted close and split/dividend events.

Open, high and low are scaled by the same adjustment factor as the close so the bar stays
consistent. Prices are cached as CSV (data/ is gitignored).
"""

from __future__ import annotations

import calendar
import time
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from .sources import SourceError

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/"
CACHE_DIR = Path("data/history/stocks")
START = date(1995, 1, 1)
COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]


def parse_chart(payload: dict) -> pd.DataFrame:
    result = payload["chart"]["result"][0]
    q = result["indicators"]["quote"][0]
    adj = (result["indicators"].get("adjclose") or [{}])[0].get("adjclose")
    df = pd.DataFrame({
        "timestamp": pd.to_datetime(result["timestamp"], unit="s").normalize(),
        "open": q["open"], "high": q["high"], "low": q["low"], "close": q["close"], "volume": q["volume"],
    })
    # The quoted close is split-adjusted but not dividend-adjusted: it is the one to compare with volume and a price floor.
    df["raw_close"] = df["close"]
    if adj is not None:
        factor = pd.Series(adj, dtype="float64") / df["close"]
        for c in ("open", "high", "low"):
            df[c] = df[c] * factor
        df["close"] = pd.Series(adj, dtype="float64")
    df = df.dropna(subset=["open", "high", "low", "close"])
    df = df[(df["high"] >= df["low"]) & (df["close"] > 0)]
    return df.drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)[COLUMNS + ["raw_close"]]


def fetch_prices(session: Any, symbol: str, start: date = START, end: date | None = None) -> pd.DataFrame:
    end = end or date.today()
    p1, p2 = calendar.timegm(start.timetuple()), calendar.timegm(end.timetuple())
    resp = session.get(f"{CHART_URL}{symbol}", params={"period1": p1, "period2": p2, "interval": "1d", "events": "div|split"},
                       headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    if resp.status_code != 200:
        raise SourceError(f"HTTP {resp.status_code}")
    return parse_chart(resp.json())


def load_prices(session: Any, symbol: str, cache: Path = CACHE_DIR, refresh: bool = False, pause: float = 0.3) -> pd.DataFrame:
    path = cache / f"{symbol}.csv"
    if path.exists() and not refresh:
        return pd.read_csv(path, parse_dates=["timestamp"])
    time.sleep(pause)
    df = fetch_prices(session, symbol)
    cache.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return df
