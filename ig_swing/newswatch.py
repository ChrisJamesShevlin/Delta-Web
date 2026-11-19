"""News watch: what has been said about every open trade since it opened, and what is new across all markets.

  python3 -m ig_swing.newswatch [--hours 3]

No IG calls and no price allowance: it reads the ~40 free headline feeds (titles only) and the journal. Added 2026-11-19 on the user's
instruction to keep constantly abreast of the news for open trades and for possible trades. It is a prompt to read, not a signal:
the matching is by keywords, so read the story behind anything that matters and search the web for the risk named at entry.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from typing import Any

UTC = timezone.utc
MAX_LOOKBACK_H = 120     # the free feeds rarely hold more than a few days


def keyword_market(name: str) -> str:
    """IG's name for a held market -> the key used by the headline keyword map."""
    from .challenge import MARKET_KEYWORDS
    from .themes import canonical
    low = name.lower()
    if "brent" in low:
        return "Brent crude"
    if "crude" in low or "oil" in low:
        return "Crude oil"
    if "silver" in low:
        return "Silver"
    if "gold" in low:
        return "Gold"
    c = canonical(name)
    return c if c in MARKET_KEYWORDS else name


def _ts(value: Any) -> datetime | None:
    try:
        d = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def position_news(market: str, opened_at: datetime, headlines: list, now: datetime, last_review: datetime | None = None) -> list[tuple[Any, bool]]:
    """Headlines that name this market's own keywords since the trade opened, newest first, each with 'new since the last review'."""
    from .challenge import relevant_headlines
    since = max(opened_at, now - timedelta(hours=MAX_LOOKBACK_H))
    own = relevant_headlines([h for h in headlines if h.published and h.published >= since], keyword_market(market), limit=40, macro_padding=False)
    return [(h, last_review is None or h.published > last_review) for h in own]


def markets_named(title: str) -> list[str]:
    """Markets whose distinctive keywords appear in a headline (the broad words such as 'dollar' and 'fed' are left out)."""
    from .challenge import MARKET_KEYWORDS, _has_word
    broad = {"dollar", "fed", "stocks", "earnings", "energy", "europe", "inflation", "treasury yield", "china", "tech", "chip", "safe haven", "weather"}
    t = f" {title.lower()} "
    return [m for m, keys in MARKET_KEYWORDS.items() if any(_has_word(t, k) for k in keys if k.strip() not in broad)]


def market_watch(headlines: list, now: datetime, hours: float) -> list[tuple[Any, list[str]]]:
    cutoff = now - timedelta(hours=hours)
    out = []
    for h in sorted((h for h in headlines if h.published and h.published >= cutoff), key=lambda h: h.published, reverse=True):
        names = markets_named(h.title)
        if names:
            out.append((h, names))
    return out


def render(positions: list[dict], watch: list, hours: float, notes: list[str]) -> str:
    lines = ["NEWS WATCH (free headline feeds, titles only; read the story behind anything that matters)"]
    if not positions:
        lines.append("No open positions.")
    for p in positions:
        new = sum(1 for _, is_new in p["news"] if is_new)
        lines.append(f"\n{p['direction']} {p['market']}: {len(p['news'])} headlines since entry ({p['opened']:%a %d %b %H:%M} UTC), "
                     f"{new} new since the last review")
        for h, is_new in p["news"][:12]:
            lines.append(f"  {'NEW ' if is_new else '    '}{h.published:%a %H:%M} {h.title[:150]} - {h.source}")
        if not p["news"]:
            lines.append("  none found in the feeds: search the web for the risk named at entry before saying there is no news")
    lines.append(f"\nAcross all markets, last {hours:g} hours (headlines that name a market; possible trades or risks to what we hold):")
    for h, names in watch[:40]:
        lines.append(f"  {h.published:%H:%M} [{', '.join(names[:3])}] {h.title[:140]} - {h.source}")
    if not watch:
        lines.append("  nothing matched")
    if notes:
        lines.append(f"\nFeeds unavailable: {len(notes)} (" + "; ".join(n.split(':')[0] for n in notes[:8]) + ")")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ig_swing.newswatch", description=__doc__.splitlines()[0])
    ap.add_argument("--hours", type=float, default=3.0, help="window for the all-markets part")
    args = ap.parse_args(argv)

    import requests
    from .config import optional_key
    from .journal import Journal
    from .news import load_headlines
    from .review import latest_reviews

    now = datetime.now(UTC)
    journal = Journal()
    open_trades = journal.open_trades()
    oldest = min((_ts(t["opened_at"]) for t in open_trades if _ts(t.get("opened_at"))), default=now - timedelta(hours=args.hours))
    lookback = min(MAX_LOOKBACK_H, max(args.hours, (now - oldest).total_seconds() / 3600 + 1))
    from .challenge import drop_promotional
    heads, notes = load_headlines(requests.Session(), optional_key("FINNHUB_API_KEY"), now, lookback, 2000)
    heads = drop_promotional(heads)
    reviews = latest_reviews(journal.events())
    positions = []
    for t in open_trades:
        opened = _ts(t.get("opened_at")) or now
        rv = reviews.get(t.get("deal_id") or "")
        positions.append({"market": t["market"], "direction": t["direction"], "opened": opened,
                          "news": position_news(t["market"], opened, heads, now, _ts(rv["ts"]) if rv else None)})
    watch = market_watch(heads, now, args.hours)
    print(render(positions, watch, args.hours, notes))
    journal.record_event(now.isoformat(), "news_watch", json.dumps({"hours": args.hours, "positions": {p["market"]: len(p["news"]) for p in positions},
                                                                  "watch": len(watch)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
