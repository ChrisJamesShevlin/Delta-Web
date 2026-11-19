"""Market check report: one self-contained HTML page in reports/ (gitignored), opened in the browser. Read-only.

Numbers come from code (account, ideas recorded this check, trades, book, sentiment, scan, calendar); the reading is a
plain text file the assistant writes (sections start with "## ").
Usage: python3 -m ig_swing.report_check --verdict "<one sentence>" --text-file <path> [--since-hours 4] [--no-open]
"""
from __future__ import annotations

import argparse
import html
import json
import sqlite3
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .report_html import AMBER, BLUE, CARD, CSS, FG, GREEN, MUTED, RED, ROOT, REPORTS, START_EQUITY, LADDER_NOTE, START_DAY, equity_svg, ladder_levels, ladder_svg, position_cards, release_rows, text_sections

COST_R = 0.05          # assumed cost per trade in R, as in the Kelly sizing
CROWDED = 70.0         # IG clients this long or short is crowded (the trend playbook's skip rule)
EXTRA_CSS = f".bar{{background:#2b313c;border-radius:4px;height:12px;position:relative;margin:4px 0}}.bar i{{display:block;height:12px;border-radius:4px}}td.n{{text-align:right}}.tag{{color:{AMBER}}}"


def expected_r(p_win: float, reward_risk: float, cost: float = COST_R) -> float:
    return p_win * reward_risk - (1 - p_win) - cost


def split_bar(left_pct: float, left_col: str, right_col: str) -> str:
    return (f'<div class=bar style="background:{right_col}"><i style="width:{left_pct:.0f}%;background:{left_col}"></i>'
            f'<span style="position:absolute;left:50%;top:-2px;height:16px;border-left:1px solid {FG}"></span></div>')


def sentiment_cell(long_pct: float | None, direction: str) -> str:
    if long_pct is None:
        return '<span class=sub>none published</span>'
    mine = long_pct if direction == "BUY" else 100 - long_pct
    crowd = max(long_pct, 100 - long_pct)
    flag = ""
    if crowd >= CROWDED:
        flag = " crowded with us" if mine >= CROWDED else " crowded against us (a mild supporting point, not a rule)"
    return f'{split_bar(long_pct, GREEN, RED)}<span class=sub>{long_pct:.0f}% long / {100 - long_pct:.0f}% short{flag}</span>'


def book_exposure(rows: list[dict[str, Any]], equity: float) -> dict[str, Any]:
    """rows: {market, direction, notional}. Long and short notional, count, and signed net per theme."""
    from .themes import _dollar_link, exposure
    long_n = sum(r["notional"] for r in rows if r["direction"] == "BUY")
    short_n = sum(r["notional"] for r in rows if r["direction"] == "SELL")
    themes: dict[str, float] = {}
    usd = 0.0
    dollar_group = 0.0
    for r in rows:
        ex = exposure(r["market"], r["direction"])
        usd += ex.get("USD", 0) * r["notional"]
        dollar_group += _dollar_link(ex) * r["notional"]
        for k, v in ex.items():
            if k != "USD" and len(k) != 3:
                themes[k] = themes.get(k, 0.0) + v * r["notional"]
    return {"long": long_n, "short": short_n, "n_long": sum(1 for r in rows if r["direction"] == "BUY"), "n_short": sum(1 for r in rows if r["direction"] == "SELL"),
            "usd": usd, "dollar_group": dollar_group, "themes": themes, "equity": equity}


def exposure_html(b: dict[str, Any]) -> str:
    tot = (b["long"] + b["short"]) or 1.0
    eq = b["equity"] or 1.0
    def money(x: float) -> str:
        return f"£{abs(x):.0f} {'long' if x > 0 else 'short'} ({abs(x) / eq:.1f}x equity)" if abs(x) > 0.5 else "flat"
    rows = "".join(f"<tr><td>{html.escape(k)}</td><td class=n>{money(v)}</td></tr>" for k, v in sorted(b["themes"].items(), key=lambda kv: -abs(kv[1])))
    return (f'{split_bar(100 * b["long"] / tot, GREEN, RED)}<div class=sub>Long £{b["long"]:.0f} ({b["n_long"]} positions) · short £{b["short"]:.0f} ({b["n_short"]} positions) · '
            f'net {money(b["long"] - b["short"])}. Notional = stake × price, so £0.01 a point on a 8,000 price is about £80.</div>'
            f'<table><tr><td>Net US dollar (every position that is long or short USD)</td><td class=n>{money(b["usd"])}</td></tr>'
            f'<tr><td>Dollar-and-yields group (assumed link: dollar, minus precious metals)</td><td class=n>{money(b["dollar_group"])}</td></tr>{rows}</table>')


def trade_card(idea: dict, trade: dict | None, pos: Any, equity: float, margin_pct: float | None, chart: bool = True) -> str:
    sign = 1 if idea["direction"] == "BUY" else -1
    entry = trade["entry"] if trade and trade.get("entry") else idea["entry"]
    stop_pts, tgt_pts = idea["stop_pts"], idea["target_pts"]
    stop_lv, tgt_lv = entry - sign * stop_pts, entry + sign * tgt_pts
    now_px = (pos.bid if idea["direction"] == "SELL" else pos.offer) if pos is not None and pos.bid is not None else entry
    lv = ladder_levels(idea["direction"], entry, stop_lv, tgt_lv, now_px, stop_pts)
    rr = tgt_pts / stop_pts
    size = (trade or {}).get("size") or (pos.size if pos is not None else None)
    risk = (trade or {}).get("risk_gbp") or ((size or 0) * stop_pts)
    reward = (size or 0) * tgt_pts
    margin = (size * entry * margin_pct / 100) if (size and margin_pct) else None
    p = idea["p_win"]
    status = "live" if trade else "proposed only"
    cells = [f"stake £{size:g} a point" if size else "stake not set", f"risk £{risk:.2f} ({100 * risk / equity:.1f}% of equity)", f"reward £{reward:.2f} if the target is hit",
             f"margin about £{margin:.2f} ({100 * margin / equity:.0f}% of equity)" if margin else "margin not read",
             f"win chance stated {p:.0%} (not shown, a prior)", f"expected {expected_r(p, rr):+.2f}R per trade after an assumed {COST_R}R cost",
             f"break-even win rate {100 * (1 + COST_R) / (1 + rr):.0f}% at {rr:.1f} to 1"]
    return (f'<div class=card><div class=hd><b>{idea["direction"]} {html.escape(idea["market"])} <span class=tag>{status}</span></b><span>{rr:.1f} to 1 · {idea["confidence"]} · {html.escape(idea["playbook"])}</span></div>'
            f'{ladder_svg(lv) if chart else ""}<div class=sub>entry {entry:g} · stop {stop_lv:g} ({stop_pts:g} pts) · target {tgt_lv:g} ({tgt_pts:g} pts)</div>'
            f'<div class=sub>{" · ".join(cells)}</div><div class=sub style="color:{FG};margin-top:6px">{html.escape(idea["thesis"])}</div></div>')


def scan_html(rows: list[dict]) -> str:
    """Today's biggest moves across the scan list, in daily ranges (ATR). Left of the line is down."""
    pts = []
    for r in rows:
        if r.get("day_pct") is None or not r.get("atr_pct"):
            continue
        pts.append((r["market"], r["day_pct"] / r["atr_pct"], r.get("range_pos"), r.get("long_pct")))
    pts.sort(key=lambda x: -abs(x[1]))
    pts = pts[:14]
    if not pts:
        return '<div class=sub>No snapshot rows for today.</div>'
    out = ['<svg viewBox="0 0 640 %d" width="100%%">' % (16 * len(pts) + 8), f'<line x1="330" x2="330" y1="0" y2="{16 * len(pts) + 4}" stroke="{MUTED}"/>']
    for i, (m, a, rp, lp) in enumerate(pts):
        w = min(abs(a), 3) / 3 * 150
        x = 330 if a >= 0 else 330 - w
        flag = " below 20-day low" if rp is not None and rp < 0 else " above 20-day high" if rp is not None and rp > 1 else ""
        out.append(f'<text x="8" y="{16 * i + 12}" fill="{FG}" font-size="11">{html.escape(m[:22])}</text><rect x="{x:.0f}" y="{16 * i + 3}" width="{w:.0f}" height="10" fill="{GREEN if a >= 0 else RED}"/>'
                   f'<text x="{(x + w + 6) if a >= 0 else (x - 6):.0f}" y="{16 * i + 12}" fill="{MUTED}" font-size="10" text-anchor="{"start" if a >= 0 else "end"}">{a:+.1f} ATR{flag}</text>')
    return "".join(out + ["</svg>"])


def capacity_html(equity: float, available: float | None, open_risk: float, new_risk: float, lim: Any, peak: float | None, dod: float | None, cooldown: str) -> str:
    to_floor = getattr(lim, "total_risk_to_floor", False)
    cap = max(0.0, equity - lim.min_equity_gbp) if to_floor else equity * lim.max_total_open_risk_pct / 100
    cap_desc = f"everything above the £{lim.min_equity_gbp:.0f} floor" if to_floor else f"{lim.max_total_open_risk_pct:.0f}% of equity"
    used, free = max(0.0, open_risk), max(0.0, cap - open_risk)
    pct = lambda x: 100 * x / cap if cap else 0
    bar = (f'<div class=bar style="background:#2b313c"><i style="width:{pct(used):.0f}%;background:{AMBER}"></i></div>'
           f'<div class=sub>Open stop risk £{used:.2f} of the £{cap:.2f} limit ({cap_desc}); £{free:.2f} of headroom'
           f'{f", and the trade placed this check accounts for £{new_risk:.2f} of it" if new_risk else ""}.</div>')
    dd = f"{100 * (peak - equity) / peak:.1f}% below its peak (brake at {lim.drawdown_brake_pct:.0f}%)" if peak else "no peak recorded"
    rows = [("Equity", f"£{equity:.2f} ({equity - START_EQUITY:+.2f} vs the £{START_EQUITY:.0f} start)"),
            ("Free to use as margin", f"£{available:.2f}" if available is not None else "unknown"),
            ("Margin in use", f"£{equity - available:.2f}" if available is not None else "unknown"),
            ("Distance above the floor", f"£{equity - lim.min_equity_gbp:.2f} (floor £{lim.min_equity_gbp:.0f}, no new entries below it)"),
            ("Drawdown", dd), ("Day so far", f"{dod:+.2f}%  (halts new orders at -{lim.daily_loss_limit_pct:.0f}%)" if dod is not None else "unknown"),
            ("Cooldown", cooldown), ("Per-trade ceiling", f"{lim.max_risk_per_trade_pct:.0f}% of equity, £{equity * lim.max_risk_per_trade_pct / 100:.2f} (live stake is the market minimum until calibrated)")]
    return bar + "<table>" + "".join(f"<tr><td>{a}</td><td>{html.escape(b)}</td></tr>" for a, b in rows) + "</table>"


def cooldown_text(closed: list[dict], lim: Any, now: datetime) -> str:
    done = sorted((t for t in closed if t.get("closed_at")), key=lambda t: t["closed_at"])
    streak = 0
    for t in reversed(done):
        if (t.get("r_multiple") or 0) < 0:
            streak += 1
        else:
            break
    if lim.cooldown_losses and streak >= lim.cooldown_losses:
        until = datetime.fromisoformat(done[-1]["closed_at"]) + timedelta(hours=lim.cooldown_hours)
        if until > now:
            return f"ACTIVE until {until:%a %H:%M} UTC after {streak} losses in a row"
    return f"not active ({streak} loss{'es' if streak != 1 else ''} in a row; {lim.cooldown_losses} would pause new entries for {lim.cooldown_hours:.0f}h)"


def build(ctx: dict, verdict: str, text: str, now: datetime) -> str:
    eq = ctx["equity"]
    live = [c for c in ctx["cards"] if c["live"]]
    sections = [f'<h1>Market check</h1><div class=sub>{now:%a %d %b %H:%M} UTC</div><div class=v>{html.escape(verdict)}</div>',
                f'<div class=k><div><span class=sub>Equity</span><b>£{eq:.2f}</b><span class=sub>{eq - START_EQUITY:+.2f} vs start</span></div>'
                f'<div><span class=sub>Placed this check</span><b>{len(live)}</b><span class=sub>{len(ctx["cards"]) - len(live)} proposed only</span></div>'
                f'<div><span class=sub>Ideas recorded</span><b>{len(ctx["ideas"])}</b><span class=sub>{ctx["total_ideas"]} in all</span></div></div>',
                f'<div class=v>Open now: {len(ctx.get("open_now", []))} position{"s" if len(ctx.get("open_now", [])) != 1 else ""}'
                + (": " + ", ".join(html.escape(x) for x in ctx.get("open_now", [])) if ctx.get("open_now") else "") + '</div>',
                f'<h2>What we can use</h2>{ctx["capacity"]}']
    if ctx.get("reviews"):
        sections.append(f'<h2>Open positions: exit line and would I open it now</h2>{ctx["reviews"]}')
    if ctx.get("position_cards"):
        sections.append(f'<h2>Open positions: where each one is and what its stop keeps</h2><div class=sub>{LADDER_NOTE}</div>' + "".join(ctx["position_cards"]))
    closed_note = (f'<div class=sub>Opened and closed again since the last check, not open now: {html.escape("; ".join(ctx["closed_in_window"]))}.</div>'
                   if ctx.get("closed_in_window") else "")
    sections.append("<h2>New trades this check (details; each one's chart is in Open positions above)</h2>" + closed_note
                    + ("".join(c["html"] for c in ctx["cards"]) or '<div class=sub>No new trade was placed this check.</div>'))
    sections.append(f'<h2>How the whole book sits</h2>{ctx["exposure"]}')
    if ctx.get("equity_days") is not None:
        pts = [(d[5:], e) for d, e in ctx["equity_days"]] + [("now", eq)]
        sections.append(f'<h2>Profit and loss against the £{START_EQUITY:.0f} start</h2><div class=sub>The £15 test deposit before 12 Nov is left out.</div>{equity_svg(pts)}')
    rows = "".join(f'<tr><td>{html.escape(i["market"])}</td><td>{i["direction"]}</td><td>{i["confidence"]}</td><td class=n>{i["p_win"]:.0%}</td><td>{"live" if i.get("live") else "shadow"}</td>'
                   f'<td style="min-width:170px">{sentiment_cell((i.get("features") or {}).get("long_pct"), i["direction"])}</td></tr>' for i in ctx["ideas"])
    sections.append(f'<h2>The view and client sentiment</h2><div class=sub>{html.escape(ctx["backdrop"])}</div><table><tr><th>Market</th><th>Side</th><th>Confidence</th><th>Win chance</th><th>Book</th><th>IG clients</th></tr>{rows or "<tr><td colspan=6>No ideas recorded in this window.</td></tr>"}</table>')
    sections.append(f'<h2>Market scan: biggest moves today</h2><div class=sub>In daily ranges (ATR). The ones beyond their 20-day range are labelled.</div>{scan_html(ctx["scan"])}')
    sections.append(f'<h2>Next 26 hours: high-impact releases</h2><table><tr><th>When</th><th>Release</th><th>Forecast</th><th>Affects</th></tr>{release_rows(ctx["events"], ctx.get("held", []))}</table>')
    sections.append(f'<h2>Learning and data</h2><table>{"".join(f"<tr><td>{a}</td><td>{html.escape(b)}</td></tr>" for a, b in ctx["learning"])}</table>')
    sections.append(text_sections(text))
    sections.append('<p class=sub>No edge is shown. Results this small are noise.</p>')
    return f'<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>Market check</title><style>{CSS}{EXTRA_CSS}</style>' + "".join(sections)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ig_swing.report_check", description=__doc__.splitlines()[0])
    ap.add_argument("--verdict", required=True)
    ap.add_argument("--text-file", required=True)
    ap.add_argument("--since-hours", type=float, default=None,
                    help="ideas recorded in this window belong to this check (default: since the previous market report, at most 12 hours, else 1 hour)")
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args(argv)

    import requests
    from .challenge import calibration, load_ideas, resolve_market, scored_ideas
    from .config import base_url, ig_env, load_credentials, optional_key, risk_limits
    from .events import events_within, load_events
    from .ig_client import IGClient
    from .journal import Journal
    from .themes import rows_from_positions, stop_risk_gbp

    now = datetime.now(timezone.utc)
    env = ig_env()
    creds = load_credentials(env)
    client = IGClient(creds, base_url=base_url(env), allow_live=(env == "live"), live_orders_enabled=False)
    lim = risk_limits(env)
    acct = client.get_account(creds.account_id)
    equity = acct.equity
    positions = client.get_positions()
    journal = Journal()
    db = sqlite3.connect(str(ROOT / "data" / "journal.db"))
    ev_rows = [{"kind": k, "detail": d, "ts": ts, "id": i} for i, ts, k, d in db.execute("select id, ts, kind, detail from events order by id")]
    all_ideas = scored_ideas(ev_rows)
    if args.since_hours is not None:
        since = now - timedelta(hours=args.since_hours)
    else:
        prev = sorted((f.stat().st_mtime for f in (ROOT / "reports").glob("*_market.html")), reverse=True) if (ROOT / "reports").exists() else []
        prev = [datetime.fromtimestamp(t, timezone.utc) for t in prev if now - datetime.fromtimestamp(t, timezone.utc) <= timedelta(hours=12)]
        since = prev[0] if prev else now - timedelta(hours=1)
    cutoff = since.isoformat()
    ideas = [i for i in all_ideas if (i.get("ts") or "") >= cutoff]
    live_links = {}
    for e in ev_rows:
        if e["kind"] == "challenge_live":
            d = json.loads(e["detail"]); live_links[d.get("idea_id")] = d.get("deal_id")
    by_deal = {p.deal_id: p for p in positions}
    trades = {t["deal_id"]: t for t in journal.open_trades()}
    trades.update({t["deal_id"]: t for t in journal.closed_trades() if t.get("deal_id")})
    cards, new_risk = [], 0.0
    closed_in_window: list[str] = []
    for i in ideas:
        deal = live_links.get(i.get("id"))
        trade = trades.get(deal) if deal else None
        if not (trade or i.get("live")):
            continue
        if trade and trade.get("closed_at"):
            closed_in_window.append(f'{trade.get("market")} {trade.get("direction")} (closed, £{(trade.get("net_pnl") or 0):+.2f})')
            continue
        try:
            margin_pct = resolve_market(client, i["epic"] if i.get("epic") else i["market"])["margin"]
        except Exception:
            margin_pct = None
        card = trade_card(i, trade, by_deal.get(deal), equity, margin_pct, chart=deal not in by_deal)   # an open position is drawn once, above
        cards.append({"live": bool(trade), "html": card})
        new_risk += (trade or {}).get("risk_gbp") or 0.0
    rows = [{"market": p.instrument_name, "direction": p.direction, "notional": p.size * (p.bid or p.open_level)} for p in positions]
    from .themes import canonical
    rows = [{**r, "market": "Crude oil" if "crude" in r["market"].lower() else canonical(r["market"])} for r in rows]
    open_risk = sum(stop_risk_gbp(p) for p in positions)
    today = now.date().isoformat()
    sod = journal.equity_on_or_before(today) or equity
    dod = 100 * (equity - sod) / sod if sod else None
    cap_html = capacity_html(equity, acct.available, open_risk, new_risk, lim, journal.peak_equity(), dod, cooldown_text(journal.closed_trades(), lim, now))
    mb = [json.loads(d) for (d,) in db.execute("select detail from events where kind='macro_backdrop' order by id desc limit 1")]
    backdrop = "Backdrop (last recorded): " + ", ".join(f"{k} {v['level']:g} ({v['day_pct']:+.1f}% today, {v['five_day_pct']:+.1f}% in 5 days)" for k, v in mb[0].items()) if mb else "No macro backdrop recorded."
    snap = db.execute("select s.market, s.day_pct, s.atr_pct, s.range_pos, s.long_pct from snapshots s join (select market, max(ts) mts from snapshots "
                      "where day = (select max(day) from snapshots) group by market) m on s.market = m.market and s.ts = m.mts").fetchall()   # newest per market, no repeats
    scan = [{"market": m, "day_pct": d, "atr_pct": a, "range_pos": r, "long_pct": l} for m, d, a, r, l in snap]
    evs, _ = load_events(requests.Session(), now.date(), now.date() + timedelta(days=2), optional_key("FRED_API_KEY"), world=True)
    upcoming = [e for e in events_within(evs, now, 26) if e.time_utc]
    allowance = ""
    try:
        a = json.loads((ROOT / "data" / "cache" / "allowance.json").read_text())
        allowance = f"{a['remaining']} of 10,000 points left; resets about {datetime.fromtimestamp(a['resets_at'], timezone.utc):%a %d %b %H:%M} UTC" if a.get("resets_at") else f"{a['remaining']} points left"
    except Exception:
        allowance = "unknown"
    closed = journal.closed_trades()
    mean_r = sum((t.get("r_multiple") or 0) for t in closed) / len(closed) if closed else float("nan")
    learning = [("IG price allowance", allowance), ("Ideas recorded in all", f"{len(all_ideas)} (live: {sum(1 for i in all_ideas if i.get('live'))})"),
                ("Closed live trades", f"{len(closed)}, mean {mean_r:+.2f}R" if closed else "none yet"),
                ("Reading", "far too few finished results to judge any playbook or my stated probabilities; run `challenge review` for the comparison with the baseline")]
    open_now = []
    for p in positions:
        nm = "Crude oil" if "crude" in p.instrument_name.lower() else p.instrument_name
        open_now.append(f"{nm} {'short' if p.direction == 'SELL' else 'long'}")
    ctx = {"open_now": open_now, "closed_in_window": closed_in_window, "equity": equity, "cards": cards, "ideas": ideas, "total_ideas": len(all_ideas), "capacity": cap_html, "exposure": exposure_html(book_exposure(rows, equity)),
           "backdrop": backdrop, "scan": scan, "events": upcoming, "learning": learning}
    from .report_html import reviews_section
    ctx["reviews"] = reviews_section(journal, positions, now)
    checks: dict = {}
    for e in ev_rows:
        if e["kind"] == "position_check":
            d = json.loads(e["detail"])
            if d.get("r") is not None:
                checks.setdefault(d["deal_id"], []).append((datetime.fromisoformat(e["ts"]), d["r"]))
    ctx["held"] = [(p.instrument_name, p.direction) for p in positions]
    ctx["equity_days"] = [(d, e) for d, e in db.execute("select day, equity from equity_daily where day >= ? order by day", (START_DAY,))]
    ctx["position_cards"] = position_cards(positions, {t["deal_id"]: t for t in journal.open_trades()}, checks, now)[0]
    page = build(ctx, args.verdict, Path(args.text_file).read_text(), now)
    REPORTS.mkdir(exist_ok=True)
    path = REPORTS / f"{now:%Y-%m-%d_%H%M}_market.html"
    path.write_text(page)
    (REPORTS / "latest_market.html").write_text(page)
    print(f"REPORT WRITTEN: {path}")
    if not args.no_open:
        webbrowser.open(path.as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
