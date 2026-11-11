"""Free market headlines: RSS feeds plus Finnhub general news.

Plain data only. No LLM, no sentiment scoring, no signals. Tested read-only on
2026-11-11: every feed below and Finnhub general news returned fresh items on
the free tier. Headlines without a publish time are dropped, because we cannot
tell how old they are.
"""

from __future__ import annotations

import re
from calendar import timegm
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import feedparser

from .sources import TIMEOUT_SECONDS, USER_AGENT, SourceError

UTC = timezone.utc
FINNHUB_NEWS_URL = "https://finnhub.io/api/v1/news"

RSS_FEEDS = (
    ("CNBC", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
    ("MarketWatch", "https://feeds.marketwatch.com/marketwatch/topstories"),
    ("MarketWatch bulletins", "https://feeds.content.dowjones.io/public/rss/mw_bulletins"),
    ("Federal Reserve", "https://www.federalreserve.gov/feeds/press_all.xml"),
    ("Bank of England", "https://www.bankofengland.co.uk/rss/news"),
    ("ECB", "https://www.ecb.europa.eu/rss/press.xml"),
)

KEYWORDS = (
    "fed", "federal reserve", "fomc", "powell", "interest rate", "rate cut", "rate hike",
    "monetary policy", "inflation", "cpi", "pce", "ppi", "gdp", "jobs", "payroll",
    "nonfarm", "unemployment", "recession", "treasury", "yield", "bond", "tariff",
    "trade war", "s&p", "nasdaq", "dow", "wall street", "stock market", "earnings",
    "oil", "crude", "opec", "gold", "dollar", "ecb", "bank of england", "lagarde",
    "bailey", "sanction",
    "crash", "plunge", "selloff", "sell-off", "turmoil", "panic", "war",  # stress words, used by ig_swing.context
)
# Whole words (plus an optional plural), so "fed" does not match "federation".
# A leading hyphen also blocks a match, so "trillion-dollar" is not "dollar".
# "geopolit" is a stem on purpose: geopolitics, geopolitical.
_KEYWORD_RE = re.compile(
    r"(?<![-\w])(?:" + "|".join(re.escape(k) for k in KEYWORDS) + r")s?\b|(?<![-\w])geopolit\w*", re.I
)


@dataclass(frozen=True)
class Headline:
    title: str
    published: datetime | None  # UTC
    source: str
    url: str | None = None


def fetch_rss(session: Any, name: str, url: str) -> list[Headline]:
    resp = session.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_SECONDS)
    if resp.status_code != 200:
        raise SourceError(f"HTTP {resp.status_code}")
    out = []
    for entry in feedparser.parse(resp.content).entries:
        title = (entry.get("title") or "").strip()
        if not title:
            continue
        stamp = entry.get("published_parsed") or entry.get("updated_parsed")
        published = datetime.fromtimestamp(timegm(stamp), UTC) if stamp else None
        out.append(Headline(title, published, name, entry.get("link")))
    return out


def fetch_finnhub_general(session: Any, api_key: str) -> list[Headline]:
    resp = session.get(
        FINNHUB_NEWS_URL,
        params={"category": "general", "token": api_key},
        headers={"User-Agent": USER_AGENT},
        timeout=TIMEOUT_SECONDS,
    )
    if resp.status_code != 200:
        raise SourceError(f"HTTP {resp.status_code}")
    out = []
    for item in resp.json():
        title = (item.get("headline") or "").strip()
        stamp = item.get("datetime")
        if not title or not stamp:
            continue
        out.append(Headline(title, datetime.fromtimestamp(int(stamp), UTC), item.get("source") or "Finnhub", item.get("url")))
    return out


def _dedupe_key(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def select_headlines(items: list[Headline], now: datetime, lookback_hours: float = 12, max_items: int = 15) -> list[Headline]:
    """Fresh, market-relevant, de-duplicated headlines, newest first."""
    cutoff = now - timedelta(hours=lookback_hours)
    seen: set[str] = set()
    out: list[Headline] = []
    for h in sorted((h for h in items if h.published), key=lambda h: h.published, reverse=True):
        if h.published < cutoff or not _KEYWORD_RE.search(h.title):
            continue
        key = _dedupe_key(h.title)
        if key in seen:
            continue
        seen.add(key)
        out.append(h)
        if len(out) >= max_items:
            break
    return out


def load_headlines(session: Any, finnhub_key: str | None, now: datetime, lookback_hours: float = 12, max_items: int = 15) -> tuple[list[Headline], list[str]]:
    """Fetch every source. One failing source never stops the others."""
    notes: list[str] = []
    items: list[Headline] = []
    jobs = [(name, (lambda n=name, u=url: fetch_rss(session, n, u))) for name, url in RSS_FEEDS]
    if finnhub_key:
        jobs.append(("Finnhub", lambda: fetch_finnhub_general(session, finnhub_key)))
    else:
        notes.append("Finnhub: no FINNHUB_API_KEY set, skipped")
    for name, fn in jobs:
        try:
            items += fn()
        except SourceError as exc:
            notes.append(f"{name}: unavailable ({exc})")
        except Exception as exc:  # type name only: messages can contain URLs with keys
            notes.append(f"{name}: unavailable ({type(exc).__name__})")
    return select_headlines(items, now, lookback_hours, max_items), notes
