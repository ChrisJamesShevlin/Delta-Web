"""The £45 play-money challenge: a daily briefing, a shadow book of ideas, and a check on whether the ideas are any good.

  python3 -m ig_swing.challenge brief     read-only briefing: prices, sentiment, volatility, events, headlines, sizing hints
  python3 -m ig_swing.challenge idea ...  record an idea BEFORE the outcome is known (the assistant writes these)
  python3 -m ig_swing.challenge report    score finished ideas, split by playbook and confidence, and test the probabilities

Nothing here places an order. Live orders are a separate, deliberate step.

Kelly staking. Each idea records the assistant's estimated win probability p and the reward to risk b. Full Kelly bets
f* = p - (1 - p) / b_eff of the bankroll, and is only right if p is true. An overestimate of a few points turns a big
bet into a ruinous one, so the tool (a) uses an EIGHTH of Kelly, (b) caps the stake, and (c) holds the cap at CAP_UNTIL_CALIBRATED
until at least CALIBRATION_MIN ideas have finished and the stated probabilities have been shown to match the hit rate.
If f* is zero or negative the answer is: do not trade.

Limits: ideas are scored on daily bars (stop checked before target, gaps at the open), so a stop and a target inside one
bar count as a stop. Results on a handful of ideas are noise and the report says so.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import pandas as pd

UTC = timezone.utc
KIND = "challenge_idea"

# name -> (daily-bet epic, IG client sentiment id, margin %). Epics, sentiment ids, margins and minimum stakes (all 0.01 a point)
# were read from IG's batch market call on 2026-11-11. The list is a starting set of liquid markets with spreads under about 0.05%
# (the Hong Kong indices at 0.15% and above and copper at 0.2% and above were left out for cost).
MARKETS: dict[str, tuple[str, str, float]] = {
    "EUR/USD": ("CS.D.EURUSD.TODAY.IP", "EURUSD", 3.33),
    "GBP/USD": ("CS.D.GBPUSD.TODAY.IP", "GBPUSD", 3.33),
    "USD/JPY": ("CS.D.USDJPY.TODAY.IP", "USDJPY", 3.33),
    "AUD/USD": ("CS.D.AUDUSD.TODAY.IP", "AUDUSD", 5.0),
    "EUR/GBP": ("CS.D.EURGBP.TODAY.IP", "EURGBP", 3.33),
    "USD/CAD": ("CS.D.USDCAD.TODAY.IP", "USDCAD", 3.33),
    "USD/CHF": ("CS.D.USDCHF.TODAY.IP", "USDCHF", 3.33),
    "NZD/USD": ("CS.D.NZDUSD.TODAY.IP", "NZDUSD", 5.0),
    "GBP/JPY": ("CS.D.GBPJPY.TODAY.IP", "GBPJPY", 3.33),
    "EUR/JPY": ("CS.D.EURJPY.TODAY.IP", "EURJPY", 3.33),
    "Gold": ("CS.D.USCGC.TODAY.IP", "GC", 5.0),
    "Silver": ("CS.D.USCSI.TODAY.IP", "SI", 10.0),
    "Crude oil": ("CC.D.CL.USS.IP", "CL", 10.0),
    "Brent crude": ("CC.D.LCO.USS.IP", "LCO", 10.0),
    "Natural gas": ("CC.D.NG.USS.IP", "NG", 10.0),
    "US Tech 100": ("IX.D.NASDAQ.CASH.IP", "USTECH", 5.0),
    "US 500": ("IX.D.SPTRD.DAILY.IP", "US500", 5.0),
    "Wall Street": ("IX.D.DOW.DAILY.IP", "WALL", 5.0),
    "US Russell 2000": ("IX.D.RUSSELL.DAILY.IP", "R2000", 10.0),
    "Germany 40": ("IX.D.DAX.DAILY.IP", "DE30", 5.0),
    "FTSE 100": ("IX.D.FTSE.DAILY.IP", "FT100", 5.0),
    "France 40": ("IX.D.CAC.DAILY.IP", "FR40", 5.0),
    "Japan 225": ("IX.D.NIKKEI.DAILY.IP", "JP225", 5.0),
}
ASSET_CLASS: dict[str, str] = {
    **{m: "Currencies" for m in ("EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD", "EUR/GBP", "USD/CAD", "USD/CHF", "NZD/USD", "GBP/JPY", "EUR/JPY")},
    **{m: "Metals" for m in ("Gold", "Silver")},
    **{m: "Energy" for m in ("Crude oil", "Brent crude", "Natural gas")},
    **{m: "Stock indices" for m in ("US Tech 100", "US 500", "Wall Street", "US Russell 2000", "Germany 40", "FTSE 100", "France 40", "Japan 225")},
}
CLASS_ORDER = ("Stock indices", "Metals", "Energy", "Currencies")
PLAYBOOKS = ("event_reaction", "news_thesis", "earnings", "trend", "sentiment", "other")
RESEARCH_LEVELS = ("none", "headlines", "web")          # how much reading went into the idea, so the report can test whether it helps
MAX_SOURCES = 6
CONFIDENCES = ("LOW", "MEDIUM", "HIGH")
COST_R = 0.05                       # assumed cost of a trade in multiples of the risk
KELLY_FRACTION = 0.125            # the user's choice: an eighth of Kelly guards against my probabilities being a few points too high
CAP_UNTIL_CALIBRATED = 5.0          # percent of the bankroll at risk per trade (lowered from 10 by the user's staking decision, 2026-11-11)
CAP_CALIBRATED = 10.0
CALIBRATION_MIN = 30
CALIBRATION_TOLERANCE = 0.10
MAX_THESIS = 300


# ---- Kelly ----------------------------------------------------------------


def kelly_fraction(p: float, reward_risk: float, cost_r: float = COST_R) -> float:
    """Full Kelly fraction of the bankroll to put at risk, after costs. Never negative."""
    if not (0 < p < 1) or reward_risk <= 0:
        return 0.0
    b = (reward_risk - cost_r) / (1 + cost_r)
    return max(0.0, p - (1 - p) / b)


def suggested_risk_pct(p: float, reward_risk: float, calibrated: bool = False, fraction: float = KELLY_FRACTION) -> float:
    cap = CAP_CALIBRATED if calibrated else CAP_UNTIL_CALIBRATED
    return min(cap, 100 * fraction * kelly_fraction(p, reward_risk))


# ---- the shadow book ------------------------------------------------------


class InvalidIdea(ValueError):
    """The idea does not match the rules."""


def make_idea(idea_id: int, now: datetime, market: str, direction: str, entry: float, spread_pts: float, stop_pts: float, target_pts: float,
              p_win: float, playbook: str, confidence: str, thesis: str, horizon_days: int = 5, live: bool = False,
              research: str = "headlines", sources: list[str] | None = None) -> dict[str, Any]:
    direction, confidence = direction.upper(), confidence.upper()
    if market not in MARKETS:
        raise InvalidIdea(f"market must be one of {list(MARKETS)}")
    if direction not in ("BUY", "SELL"):
        raise InvalidIdea("direction must be BUY or SELL")
    if not (stop_pts > 0 and target_pts > 0 and entry > 0 and spread_pts >= 0):
        raise InvalidIdea("entry, stop and target must be above zero")
    if not 0.05 <= p_win <= 0.95:
        raise InvalidIdea("p_win must be between 0.05 and 0.95")
    if playbook not in PLAYBOOKS or confidence not in CONFIDENCES:
        raise InvalidIdea(f"playbook must be one of {PLAYBOOKS} and confidence one of {CONFIDENCES}")
    if not thesis.strip() or len(thesis) > MAX_THESIS:
        raise InvalidIdea(f"a thesis of 1 to {MAX_THESIS} characters is required")
    if not 1 <= horizon_days <= 30:
        raise InvalidIdea("horizon must be 1 to 30 days")
    if research not in RESEARCH_LEVELS:
        raise InvalidIdea(f"research must be one of {RESEARCH_LEVELS}")
    sources = [x.strip() for x in (sources or []) if x.strip()]
    if len(sources) > MAX_SOURCES or any(len(x) > 300 for x in sources):
        raise InvalidIdea(f"at most {MAX_SOURCES} sources of up to 300 characters each")
    return {"id": idea_id, "ts": now.isoformat(), "market": market, "direction": direction, "entry": float(entry), "spread_pts": float(spread_pts),
            "stop_pts": float(stop_pts), "target_pts": float(target_pts), "p_win": float(p_win), "playbook": playbook, "confidence": confidence,
            "thesis": thesis.strip(), "horizon_days": int(horizon_days), "live": bool(live), "research": research, "sources": sources}


def load_ideas(events: list[dict]) -> list[dict]:
    out = []
    for e in events:
        if e["kind"] == KIND:
            try:
                out.append(json.loads(e["detail"]))
            except (ValueError, TypeError):
                continue
    return out


def score_idea(idea: dict, bars: pd.DataFrame) -> dict[str, Any] | None:
    """The outcome in points and R once the idea has run its course, else None. Bars must be complete daily bars."""
    start = pd.Timestamp(idea["ts"]).tz_convert("UTC").tz_localize(None).normalize()
    after = bars[pd.to_datetime(bars["timestamp"]) > start].reset_index(drop=True)
    buy = idea["direction"] == "BUY"
    entry, stop_d, target_d = idea["entry"], idea["stop_pts"], idea["target_pts"]
    stop, target = (entry - stop_d, entry + target_d) if buy else (entry + stop_d, entry - target_d)
    half = idea["spread_pts"] / 2
    horizon = idea["horizon_days"]
    for j in range(min(len(after), horizon)):
        o, h, lo, c = (float(after[k].iloc[j]) for k in ("open", "high", "low", "close"))
        if buy:
            if o - half <= stop:
                return _result(idea, o - half, "stop_gap")
            if o - half >= target:
                return _result(idea, o - half, "target_gap")
            if lo - half <= stop:
                return _result(idea, stop, "stop")
            if h - half >= target:
                return _result(idea, target, "target")
        else:
            if o + half >= stop:
                return _result(idea, o + half, "stop_gap")
            if o + half <= target:
                return _result(idea, o + half, "target_gap")
            if h + half >= stop:
                return _result(idea, stop, "stop")
            if lo + half <= target:
                return _result(idea, target, "target")
        if j == horizon - 1:
            return _result(idea, c - half if buy else c + half, "time")
    return None


def _result(idea: dict, exit_price: float, reason: str) -> dict[str, Any]:
    move = exit_price - idea["entry"] if idea["direction"] == "BUY" else idea["entry"] - exit_price
    return {"id": idea["id"], "exit": float(exit_price), "reason": reason, "pts": float(move), "r": float(move / idea["stop_pts"])}


def mean_se(xs: list[float]) -> tuple[float, float]:
    n = len(xs)
    if n == 0:
        return float("nan"), float("nan")
    m = sum(xs) / n
    return m, (math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1) / n) if n > 1 else float("nan"))


def calibration(finished: list[tuple[dict, dict]]) -> dict[str, Any]:
    """Do the stated probabilities match the hit rate? A win here means the idea made money (R above zero)."""
    if not finished:
        return {"n": 0, "predicted": float("nan"), "actual": float("nan"), "brier": float("nan"), "calibrated": False, "buckets": []}
    ps = [i["p_win"] for i, _ in finished]
    wins = [1.0 if r["r"] > 0 else 0.0 for _, r in finished]
    n = len(ps)
    pred, act = sum(ps) / n, sum(wins) / n
    brier = sum((p - w) ** 2 for p, w in zip(ps, wins)) / n
    buckets = []
    for lo, hi in ((0.0, 0.35), (0.35, 0.45), (0.45, 1.01)):
        sel = [(p, w) for p, w in zip(ps, wins) if lo <= p < hi]
        if sel:
            buckets.append((lo, min(hi, 1.0), len(sel), sum(p for p, _ in sel) / len(sel), sum(w for _, w in sel) / len(sel)))
    return {"n": n, "predicted": pred, "actual": act, "brier": brier, "buckets": buckets,
            "calibrated": n >= CALIBRATION_MIN and abs(pred - act) <= CALIBRATION_TOLERANCE}


def measure(ideas: list[dict], get_bars: Callable[[str], pd.DataFrame]) -> dict[str, Any]:
    finished, pending = [], 0
    for idea in ideas:
        res = score_idea(idea, get_bars(idea["market"]))
        if res is None:
            pending += 1
        else:
            finished.append((idea, res))
    return {"finished": finished, "pending": pending, "total": len(ideas), "calibration": calibration(finished)}


def format_report(m: dict[str, Any]) -> str:
    fin = m["finished"]
    rs = [r["r"] for _, r in fin]
    mean, se = mean_se(rs)
    pct = lambda v: "n/a" if v != v else f"{v * 100:.0f}%"
    lines = [f"Challenge shadow book: {m['total']} ideas recorded, {len(fin)} finished (n={len(fin)}), {m['pending']} still running"]
    if fin:
        lines.append(f"  all: mean {mean:+.2f}R, SE {('n/a' if se != se else f'{se:.2f}')}, win rate {pct(sum(1 for r in rs if r > 0) / len(rs))}")
        for label, key in (("playbook", "playbook"), ("confidence", "confidence"), ("research", "research")):
            groups: dict[str, list[float]] = {}
            for i, r in fin:
                groups.setdefault(i.get(key, "headlines"), []).append(r["r"])
            for g, xs in sorted(groups.items()):
                gm, _ = mean_se(xs)
                lines.append(f"  {label} {g}: n={len(xs)}, mean {gm:+.2f}R" + ("  (under 30: noise)" if len(xs) < 30 else ""))
        live = [r["r"] for i, r in fin if i.get("live")]
        if live:
            lines.append(f"  taken live: n={len(live)}, mean {mean_se(live)[0]:+.2f}R")
    cal = m["calibration"]
    lines.append("Probability check (does the stated chance of winning match what happened?)")
    if cal["n"]:
        lines.append(f"  stated average {pct(cal['predicted'])}, actual hit rate {pct(cal['actual'])}, Brier score {cal['brier']:.3f} (0.25 = coin flip)")
        for lo, hi, n, p, a in cal["buckets"]:
            lines.append(f"    stated {lo:.2f} to {hi:.2f}: n={n}, stated {pct(p)}, actual {pct(a)}")
    lines.append("  Kelly cap: " + (f"{CAP_CALIBRATED:.0f}% (calibrated)" if cal["calibrated"] else
                                   f"held at {CAP_UNTIL_CALIBRATED:.0f}% until at least {CALIBRATION_MIN} ideas finish and the stated average is within "
                                   f"{CALIBRATION_TOLERANCE * 100:.0f} points of the hit rate"))
    lines.append("Nothing here was traded unless marked live. Ideas are scored on daily bars.")
    return "\n".join(lines)


# ---- the briefing ---------------------------------------------------------


def stake_hint(equity: float, risk_pct: float, stop_pts: float, price: float, margin_pct: float, min_stake: float = 0.01, max_margin_share: float = 0.6) -> tuple[float, str]:
    """Stake in pounds per point for the chosen risk, limited by margin. Returns (stake, note)."""
    if stop_pts <= 0 or price <= 0:
        return 0.0, "no stop"
    stake = math.floor(equity * risk_pct / 100 / stop_pts / min_stake + 1e-9) * min_stake
    margin_cap = equity * max_margin_share / (price * margin_pct / 100)
    if stake > margin_cap:
        stake, note = math.floor(margin_cap / min_stake + 1e-9) * min_stake, "margin-limited"
    else:
        note = ""
    if stake < min_stake:
        return 0.0, "below the minimum stake"
    return round(stake, 2), note


def format_briefing(now: datetime, rows: list[dict], equity: float, positions: int, notes: list[str], events_text: list[str], headlines_text: list[str]) -> str:
    lines = [f"Challenge briefing, {now:%a %d %b %Y %H:%M} UTC. Equity £{equity:.2f}, open positions {positions}", ""]
    header = f"{'market':16} {'price':>9} {'day':>7} {'ATR pts':>8} {'ATR %':>6} {'20d range':>9} {'IG clients':>11} {'spread':>7}  stake for a 1.5 ATR stop at 10% risk"
    for cls in CLASS_ORDER:
        group = [r for r in rows if ASSET_CLASS.get(r["market"]) == cls]
        if not group:
            continue
        lines += [f"{cls} ({len(group)})", header]
        for r in group:
            stake = f"£{r['stake']:.2f}/pt {r['note']}".strip() if r["stake"] else r["note"]
            sent = "n/a" if r["long_pct"] is None else f"{r['long_pct']:.0f}% long"
            pos = "n/a" if r["range_pos"] is None else f"{r['range_pos'] * 100:.0f}%"
            lines.append(f"{r['market']:16} {r['price']:>9.1f} {r['day_pct']:>+6.2f}% {r['atr']:>8.1f} {r['atr_pct']:>5.2f}% {pos:>9} {sent:>11} {r['spread_pct']:>6.3f}%  {stake}")
        lines.append("")
    fits = [r["market"] for r in rows if r["stake"]]
    shadow = [r["market"] for r in rows if not r["stake"]]
    lines.append(f"Fits the live account at 10% risk ({len(fits)}): " + (", ".join(fits) or "none"))
    lines.append(f"Shadow book only at this account size ({len(shadow)}): " + (", ".join(shadow) or "none"))
    lines += ["", "Events, next 48 hours:"] + (events_text or ["  none found"])
    lines += ["", "Headlines:"] + (headlines_text or ["  none found"])
    if notes:
        lines += ["", "Notes:"] + [f"  {n}" for n in notes]
    lines += ["", "Write ideas with: python3 -m ig_swing.challenge idea --market ... --direction ... --stop ... --target ... --p-win ... --playbook ... --confidence ... --thesis ...",
              "Data only. No order has been placed."]
    return "\n".join(lines)


MARKET_KEYWORDS: dict[str, tuple[str, ...]] = {
    "EUR/USD": ("euro", "ecb", "eur", "dollar", "fed", "lagarde", "eurozone"),
    "GBP/USD": ("pound", "sterling", "bank of england", "boe", "bailey", "gilt", "uk ", "britain"),
    "USD/JPY": ("yen", "boj", "japan", "dollar", "fed", "treasury yield"),
    "AUD/USD": ("aussie", "australia", "rba", "china", "dollar", "iron ore"),
    "Gold": ("gold", "bullion", "dollar", "fed", "treasury yield", "safe haven", "inflation"),
    "Crude oil": ("oil", "crude", "opec", "iran", "brent", "wti", "energy", "sanction"),
    "US Tech 100": ("nasdaq", "tech", "nvidia", "chip", " ai ", "apple", "microsoft", "fed", "earnings"),
    "US 500": ("s&p", "wall street", "stocks", "stock market", "fed", "earnings", "dow"),
    "Germany 40": ("dax", "germany", "ecb", "euro", "europe", "german"),
    "EUR/GBP": ("euro", "pound", "sterling", "ecb", "bank of england", "eur", "gbp"),
    "USD/CAD": ("canad", "loonie", "boc", "oil", "dollar", "fed"),
    "USD/CHF": ("swiss", "franc", "snb", "dollar", "fed", "safe haven"),
    "NZD/USD": ("new zealand", "kiwi", "rbnz", "dollar", "china"),
    "GBP/JPY": ("pound", "sterling", "yen", "boj", "bank of england"),
    "EUR/JPY": ("euro", "yen", "ecb", "boj"),
    "Silver": ("silver", "gold", "dollar", "fed", "industrial", "bullion"),
    "Brent crude": ("brent", "oil", "opec", "iran", "crude", "energy"),
    "Natural gas": ("natural gas", "lng", "weather", "storage", "energy", "europe"),
    "Wall Street": ("dow", "wall street", "stocks", "fed", "earnings"),
    "US Russell 2000": ("russell", "small cap", "small-cap", "fed", "stocks"),
    "FTSE 100": ("ftse", "uk ", "london", "bank of england", "britain"),
    "France 40": ("cac", "france", "french", "euro", "ecb"),
    "Japan 225": ("nikkei", "japan", "yen", "boj"),
}
MACRO_WORDS = ("fed", "fomc", "cpi", "inflation", "jobs", "payroll", "tariff", "rate", "recession", "war", "strike", "sanction")


def relevant_headlines(headlines: list, market: str, limit: int = 6) -> list:
    """Headlines that mention the market's own keywords first, then broad macro ones. Newest first within each group."""
    own = tuple(k.lower() for k in MARKET_KEYWORDS.get(market, ()))
    hit, macro = [], []
    for h in headlines:
        t = f" {h.title.lower()} "
        if any(k in t for k in own):
            hit.append(h)
        elif any(k in t for k in MACRO_WORDS):
            macro.append(h)
    return (hit + macro)[:limit]


def format_events(events: list, limit: int = 8) -> list[str]:
    from .events import HIGH, MEDIUM

    out = []
    for e in events:
        if e.tier not in (HIGH, MEDIUM):
            continue
        when = f"{e.time_utc:%a %d %b %H:%M}Z" if getattr(e, "time_utc", None) else f"{e.day:%a %d %b} (time n/a)"
        out.append(f"  {when}  {e.label} [{e.tier.upper()}]")
    return out[:limit]


def format_quick(name: str, snap: dict, atr_pts: float, hourly: pd.DataFrame | None, long_pct: float | None, range20: tuple[float, float] | None, positions: int) -> str:
    """A compact read on one market, for when the user sees a sudden move and wants to know if there is a trade."""
    price = snap["price"]
    move_atr = snap["net_change"] / atr_pts if atr_pts else float("nan")
    lines = [f"{name}: {price:.1f} ({snap['pct_change']:+.2f}% today, {snap['net_change']:+.1f} points = {move_atr:+.1f} x the 14-day average daily range of {atr_pts:.1f}), "
             f"spread {snap['spread_pct']:.3f}%"]
    if abs(move_atr) >= 2:
        lines.append("  The move is over two daily ranges: unusual. Check for news before fading or following it.")
    if hourly is not None and len(hourly):
        hi, lo, last = float(hourly["high"].max()), float(hourly["low"].min()), float(hourly["close"].iloc[-1])
        lines.append(f"  last {len(hourly)} hours: high {hi:.1f}, low {lo:.1f}, now {last:.1f} ({(last - hi) / hi * 100:+.2f}% from the high, {(last - lo) / lo * 100:+.2f}% from the low)")
    if range20:
        lines.append(f"  20-day range {range20[0]:.1f} to {range20[1]:.1f}; price is "
                     + ("below the range (breakdown)" if price < range20[0] else "above the range (breakout)" if price > range20[1] else
                        f"{(price - range20[0]) / (range20[1] - range20[0]) * 100:.0f}% of the way up it"))
    lines.append("  IG clients: " + ("n/a" if long_pct is None else f"{long_pct:.0f}% long, {100 - long_pct:.0f}% short"))
    lines.append(f"  open positions: {positions}")
    return "\n".join(lines)


def batch_snapshots(client: Any, epics: list[str]) -> dict[str, Any]:
    """One IG call for up to 50 markets. Returns epic -> an object with bid, offer, pct_change, net_change, status."""
    from types import SimpleNamespace

    out: dict[str, Any] = {}
    for k in range(0, len(epics), 50):
        d = client._call("GET", "/markets", "2", params={"epics": ",".join(epics[k:k + 50]), "filter": "ALL"}, context="batch markets")
        for m in d.get("marketDetails", []):
            s = m.get("snapshot", {})
            if s.get("bid") is None or s.get("offer") is None:
                continue
            out[m["instrument"]["epic"]] = SimpleNamespace(bid=float(s["bid"]), offer=float(s["offer"]), pct_change=s.get("percentageChange"),
                                                           net_change=s.get("netChange"), status=s.get("marketStatus"))
    return out


def batch_sentiment(client: Any, ids: list[str]) -> dict[str, float]:
    """One IG call for the share of clients that are long, by sentiment id. Empty on any failure."""
    try:
        r = client._call("GET", "/clientsentiment", "1", params={"marketIds": ",".join(ids)}, context="client sentiment")
        return {x["marketId"]: float(x["longPositionPercentage"]) for x in r.get("clientSentiments", [])}
    except Exception:
        return {}


def build_rows(client: Any, equity: float, now: datetime, clean: Callable[[pd.DataFrame, datetime], pd.DataFrame]) -> tuple[list[dict], list[str]]:
    from .indicators import atr
    rows, notes = [], []
    try:
        snaps = batch_snapshots(client, [v[0] for v in MARKETS.values()])
    except Exception as exc:  # type name only
        return [], [f"markets: unavailable ({type(exc).__name__})"]
    for name, (epic, sent_id, margin) in MARKETS.items():
        try:
            snap = snaps.get(epic)
            if snap is None:
                notes.append(f"{name}: no quote")
                continue
            bars = clean(client.get_prices(epic, "DAY", 30), now)
            price = (snap.bid + snap.offer) / 2
            a = float(atr(bars, 14).iloc[-1])
            hi, lo = float(bars["high"].tail(20).max()), float(bars["low"].tail(20).min())
            stake, note = stake_hint(equity, 10.0, 1.5 * a, price, margin)
            rows.append({"market": name, "price": price, "day_pct": snap.pct_change or 0.0, "atr": a, "atr_pct": a / price * 100,
                         "range_pos": (price - lo) / (hi - lo) if hi > lo else None, "long_pct": None, "stake": stake, "note": note,
                         "spread_pct": (snap.offer - snap.bid) / price * 100, "sent_id": sent_id})
        except Exception as exc:  # type name only
            notes.append(f"{name}: unavailable ({type(exc).__name__})")
    return rows, notes


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ig_swing.challenge")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("brief")
    q = sub.add_parser("quick", help="a fast read on one market, for a sudden move")
    q.add_argument("--market", required=True, choices=list(MARKETS))
    sub.add_parser("report")
    i = sub.add_parser("idea")
    i.add_argument("--market", required=True, choices=list(MARKETS))
    i.add_argument("--direction", required=True, choices=["BUY", "SELL", "buy", "sell"])
    i.add_argument("--stop", type=float, required=True, help="stop distance in points")
    i.add_argument("--target", type=float, required=True, help="target distance in points")
    i.add_argument("--p-win", type=float, required=True)
    i.add_argument("--playbook", required=True, choices=PLAYBOOKS)
    i.add_argument("--confidence", required=True, choices=CONFIDENCES + tuple(c.lower() for c in CONFIDENCES))
    i.add_argument("--thesis", required=True)
    i.add_argument("--horizon", type=int, default=5)
    i.add_argument("--live", action="store_true", help="mark that this idea was taken live")
    i.add_argument("--research", choices=RESEARCH_LEVELS, default="headlines", help="none, the briefing headlines only, or a web research pass")
    i.add_argument("--source", action="append", default=[], help="a source you relied on (a URL or a title), repeatable")
    args = ap.parse_args(argv)

    from .bars import clean_daily
    from .config import base_url, ig_env, load_credentials, risk_limits
    from .ig_client import IGClient
    from .journal import Journal

    now = datetime.now(UTC)
    journal = Journal()
    try:
        env = ig_env()
        creds = load_credentials(env)
        from .ig_client import PriceCache

        client = IGClient(creds, base_url=base_url(env), allow_live=(env == "live"), cache=PriceCache(ttl_seconds=20 * 3600))
        if args.cmd == "idea":
            epic = MARKETS[args.market][0]
            snap = client.get_snapshot(epic)
            direction = args.direction.upper()
            entry = snap.offer if direction == "BUY" else snap.bid
            n = len(load_ideas(journal.events())) + 1
            idea = make_idea(n, now, args.market, direction, entry, snap.offer - snap.bid, args.stop, args.target, args.p_win, args.playbook,
                             args.confidence, args.thesis, args.horizon, args.live, args.research, args.source)
            journal.record_event(now.isoformat(), KIND, json.dumps(idea))
            rr = args.target / args.stop
            ks = kelly_fraction(args.p_win, rr)
            print(f"Recorded idea {n}: {direction} {args.market} at {entry}, stop {args.stop:g}, target {args.target:g} (reward to risk {rr:.1f}), p_win {args.p_win:.0%}")
            print(f"  full Kelly {ks * 100:.1f}% of the bankroll, 1/8 Kelly {100 * KELLY_FRACTION * ks:.1f}%; suggested risk {suggested_risk_pct(args.p_win, rr):.1f}% (cap {CAP_UNTIL_CALIBRATED:.0f}% until calibrated)"
                  + ("" if ks > 0 else "  -> Kelly says do not trade this"))
            return 0
        if args.cmd == "quick":
            from .indicators import atr
            epic, sent_id, _ = MARKETS[args.market]
            snap = client.get_snapshot(epic)
            bars = clean_daily(client.get_prices(epic, "DAY", 30), now)
            try:
                hourly = client.get_prices(epic, "HOUR", 24)
            except Exception:
                hourly = None
            long_pct = None
            try:
                s_ = client._call("GET", "/clientsentiment", "1", params={"marketIds": sent_id}, context="client sentiment")
                long_pct = float(s_["clientSentiments"][0]["longPositionPercentage"])
            except Exception:
                pass
            price = (snap.bid + snap.offer) / 2
            info = {"price": price, "pct_change": snap.pct_change or 0.0, "net_change": snap.net_change or 0.0, "spread_pct": (snap.offer - snap.bid) / price * 100}
            print(format_quick(args.market, info, float(atr(bars, 14).iloc[-1]), hourly, long_pct,
                               (float(bars["low"].tail(20).min()), float(bars["high"].tail(20).max())), len(client.get_positions())))
            import requests

            from .config import optional_key
            from .events import load_events
            from .news import load_headlines

            session = requests.Session()
            events, ev_notes = load_events(session, now.date(), now.date() + timedelta(days=2), optional_key("FRED_API_KEY"))
            heads, hd_notes = load_headlines(session, optional_key("FINNHUB_API_KEY"), now, 12, 40)
            print("  Events, next 48 hours: " + ("" if format_events(events) else "none found"))
            for line in format_events(events):
                print(line)
            rel = relevant_headlines(heads, args.market)
            print("  Headlines that mention this market or the macro backdrop (last 12 hours):" + ("" if rel else " none found"))
            for h in rel:
                print(f"    {h.published:%H:%M} {h.title}")
            for n in ev_notes + hd_notes:
                print(f"  note: {n}")
            return 0
        if args.cmd == "report":
            get_bars = lambda m: clean_daily(client.get_prices(MARKETS[m][0], "DAY", 60), now)
            print(format_report(measure(load_ideas(journal.events()), get_bars)))
            return 0
        import requests

        from .config import optional_key
        from .events import load_events
        from .news import load_headlines

        acct = client.get_account(creds.account_id)
        rows, notes = build_rows(client, acct.equity, now, clean_daily)
        session = requests.Session()
        sentiment = batch_sentiment(client, [r["sent_id"] for r in rows])
        for r in rows:
            r["long_pct"] = sentiment.get(r["sent_id"])
        events, ev_notes = load_events(session, now.date(), now.date() + timedelta(days=2), optional_key("FRED_API_KEY"))
        heads, hd_notes = load_headlines(session, optional_key("FINNHUB_API_KEY"), now, 12, 10)
        ev_text = format_events(events)
        hd_text = [f"  {h.published:%H:%M} {h.title}" for h in heads]
        print(format_briefing(now, rows, acct.equity, len(client.get_positions()), notes + ev_notes + hd_notes, ev_text, hd_text))
        limits = risk_limits(env)
        print(f"Risk profile: {limits.name} (cap {limits.max_risk_per_trade_pct:g}% a trade = £{acct.equity * limits.max_risk_per_trade_pct / 100:.2f}, floor £{limits.min_equity_gbp:g}, max {limits.max_open_positions} positions)")
        return 0
    except InvalidIdea as exc:
        print(f"REFUSED: {exc}")
        return 1
    except Exception as exc:  # type name only: messages can carry sensitive detail
        print(f"FAILED: {type(exc).__name__}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
