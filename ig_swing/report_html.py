"""Health check report: one self-contained HTML page written to reports/ (gitignored) and opened in the browser.

Read-only: it reads positions and the journal and places nothing. The written part is a plain text file the
assistant writes for each check (sections start with "## "), so the numbers come from code and the reading from me.
Usage: python3 -m ig_swing.report_html --verdict "<one sentence>" --text-file <path> [--no-open]
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sqlite3
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
START_EQUITY = 50.0
START_DAY = "2026-11-12"   # the £15 test deposit before this day is left out of the equity line
GREEN, RED, BLUE, AMBER, FG, MUTED, CARD = "#4ec98a", "#ef6b6b", "#6ab0ff", "#e8b44c", "#d8dee9", "#8a93a3", "#1b212b"
LADDER_W, X_MIN, X_MAX = 640, -1.25, 2.3


def ladder_levels(direction: str, open_level: float, stop: float, limit: float | None, now_px: float, risk_pts: float) -> dict:
    """Everything on one trade's ladder, in R (1R = the initial risk), with profit as positive."""
    sign = -1 if direction == "SELL" else 1
    def r(price: float) -> float:
        return sign * (price - open_level) / risk_pts
    return {"now": r(now_px), "stop": r(stop), "target": r(limit) if limit else None,
            "to_stop_pts": abs(now_px - stop), "to_plus1r_pts": max(0.0, risk_pts - sign * (now_px - open_level)),
            "to_target_pts": abs(limit - now_px) if limit else None, "from_entry_pts": abs(now_px - open_level)}


def _x(r: float) -> float:
    return 30 + (r - X_MIN) / (X_MAX - X_MIN) * (LADDER_W - 60)


def ladder_svg(lv: dict) -> str:
    stop, tp, now = lv["stop"], lv["target"], lv["now"]
    s = [f'<svg viewBox="0 0 {LADDER_W} 74" width="100%">']
    s.append(f'<rect x="{_x(X_MIN + .05):.0f}" y="30" width="{_x(stop) - _x(X_MIN + .05):.0f}" height="14" fill="{RED}" opacity=".25"/>')
    s.append(f'<rect x="{_x(stop):.0f}" y="30" width="{_x(tp if tp is not None else 2) - _x(stop):.0f}" height="14" fill="{GREEN}" opacity=".18"/>')
    s.append(f'<line x1="{_x(X_MIN + .05):.0f}" x2="{_x(X_MAX - .05):.0f}" y1="37" y2="37" stroke="{MUTED}"/>')
    for rv, lab, col in [(-1, "original stop -1R", RED), (0, "entry", FG), (0.75, "+0.75R", AMBER), (1, "+1R trail", AMBER),
                         (tp, f"target +{tp:.1f}R" if tp is not None else "", GREEN)]:
        if rv is None:
            continue
        dash = "3 3" if rv in (0.75, 1, -1) else "0"
        s.append(f'<line x1="{_x(rv):.0f}" x2="{_x(rv):.0f}" y1="26" y2="48" stroke="{col}" stroke-width="1.5" stroke-dasharray="{dash}"/>')
        s.append(f'<text x="{_x(rv):.0f}" y="{62 if rv == 0.75 else 18}" fill="{col}" font-size="10" text-anchor="middle">{lab}</text>')
    stop_lab = "stop (breakeven)" if abs(stop) < .02 else f"stop {stop:+.1f}R"
    s.append(f'<rect x="{_x(stop) - 3:.0f}" y="24" width="6" height="26" fill="{RED}"/>'
             f'<text x="{_x(stop):.0f}" y="62" fill="{RED}" font-size="10" text-anchor="middle">{stop_lab}</text>')
    s.append(f'<circle cx="{_x(max(X_MIN, min(X_MAX, now))):.0f}" cy="37" r="8" fill="{GREEN if now >= 0 else RED}" stroke="#fff" stroke-width="2"/></svg>')
    return "".join(s)


def equity_svg(points: list[tuple[str, float]]) -> str:
    """Profit and loss against the start equity. points = (label, equity)."""
    vals = [e - START_EQUITY for _, e in points]
    if len(vals) < 2:
        return '<div class=sub>Not enough days yet.</div>'
    W, H = 640, 150
    lo, hi = min(0, min(vals)) - 1, max(vals) + 1.5
    ex = lambda i: 40 + i * (W - 70) / (len(vals) - 1)
    ey = lambda v: 15 + (hi - v) / (hi - lo) * (H - 40)
    o = [f'<svg viewBox="0 0 {W} {H}" width="100%">',
         f'<line x1="40" x2="{W - 30}" y1="{ey(0):.0f}" y2="{ey(0):.0f}" stroke="{AMBER}" stroke-dasharray="4 3"/><text x="4" y="{ey(0) + 4:.0f}" fill="{AMBER}" font-size="10">£0</text>']
    for v in (5, 10, 20):
        if v < hi:
            o.append(f'<line x1="40" x2="{W - 30}" y1="{ey(v):.0f}" y2="{ey(v):.0f}" stroke="#2b313c"/><text x="4" y="{ey(v) + 4:.0f}" fill="{MUTED}" font-size="10">+£{v}</text>')
    o.append(f'<polyline fill="none" stroke="{BLUE}" stroke-width="2" points="{" ".join(f"{ex(i):.0f},{ey(v):.0f}" for i, v in enumerate(vals))}"/>')
    for i, v in enumerate(vals):
        o.append(f'<circle cx="{ex(i):.0f}" cy="{ey(v):.0f}" r="4" fill="{BLUE}"/><text x="{ex(i):.0f}" y="{ey(v) - 9:.0f}" fill="{FG}" font-size="10" text-anchor="middle">{v:+.2f}</text>'
                 f'<text x="{ex(i):.0f}" y="{H - 6}" fill="{MUTED}" font-size="10" text-anchor="middle">{html.escape(points[i][0])}</text>')
    return "".join(o + ["</svg>"])


def text_sections(text: str) -> str:
    out = []
    for blk in text.split("## ")[1:]:
        head, _, body = blk.partition("\n")
        out.append(f"<h2>{html.escape(head.strip())}</h2><p class=w>{html.escape(body.strip())}</p>")
    return "".join(out)


AFFECTS = {"AUD": "AUD/USD short", "USD": "all four (dollar and yields)", "EUR": "EUR/USD short"}


def release_rows(events: list) -> str:
    rows = []
    for ev in events:
        m = re.search(r"\((\w{3})\)", ev.label)
        aff = AFFECTS.get(m.group(1), "") if m else ""
        if "Crude" in ev.label or "EIA" in ev.label:
            aff = "US Crude"
        rows.append(f"<tr><td>{ev.time_utc:%a %H:%M} UTC</td><td>{html.escape(ev.label)}</td><td>{html.escape(ev.forecast or '')}</td><td>{html.escape(aff or 'none held')}</td></tr>")
    return "".join(rows) or "<tr><td colspan=4>No high-impact releases in the next 26 hours.</td></tr>"


CSS = (f"body{{background:#14181f;color:{FG};font:15px system-ui;max-width:700px;margin:20px auto;padding:0 14px}}h1{{margin:0 0 4px}}h2{{margin:26px 0 8px;font-size:16px}}"
       f".sub{{color:{MUTED};font-size:13px}}.card{{background:{CARD};border-radius:8px;padding:10px 12px;margin:10px 0}}.hd{{display:flex;justify-content:space-between;margin-bottom:2px}}"
       f".v{{background:{CARD};border-left:4px solid {GREEN};padding:10px 14px;border-radius:6px;margin:12px 0}}.k{{display:flex;gap:10px;margin:10px 0}}.k div{{background:{CARD};border-radius:8px;padding:8px 12px;flex:1}}"
       f".k b{{display:block;font-size:20px}}p.w{{font-size:14px;line-height:1.55;margin:4px 0}}table{{border-collapse:collapse;width:100%;font-size:13px}}td,th{{border-bottom:1px solid #2b313c;padding:5px;text-align:left}}")


def build(positions: list, trades: dict, equity: float, equity_days: list[tuple[str, float]], checks: dict, events: list,
          verdict: str, text: str, now: datetime) -> str:
    from .manage import initial_risk_points, move_in_r
    cards, tot_pl, tot_risk = [], 0.0, 0.0
    for p in positions:
        rp = initial_risk_points(p, trades.get(p.deal_id))
        now_px = p.bid if p.direction == "SELL" else p.offer
        if not rp or p.stop_level is None or now_px is None:
            cards.append(f'<div class=card><b>{p.direction} {html.escape(p.instrument_name)}</b> <span class=sub>no journalled risk or no stop: not drawn</span></div>')
            continue
        lv = ladder_levels(p.direction, p.open_level, p.stop_level, p.limit_level, now_px, rp)
        m = move_in_r(p, rp) or 0.0
        pl = p.unrealised_pnl or 0.0
        tot_pl += pl
        tot_risk += min(0.0, lv["stop"] * rp * p.size)
        prev = [r for ts, r in checks.get(p.deal_id, []) if ts < now - timedelta(minutes=45)]
        dtxt = f' · <span style="color:{GREEN if m - prev[-1] >= 0 else RED}">{m - prev[-1]:+.2f}R since the last check</span>' if prev else ""
        facts = [f"{lv['from_entry_pts']:.0f} pts {'in profit' if m >= 0 else 'against us'}", f"{lv['to_stop_pts']:.0f} pts to stop",
                 f"{lv['to_plus1r_pts']:.0f} pts to +1R" if m < 1 else "past +1R: trail it"]
        if lv["to_target_pts"] is not None:
            facts.append(f"{lv['to_target_pts']:.0f} pts to target")
        name = p.instrument_name.replace("Oil - US Crude", "US Crude")
        cards.append(f'<div class=card><div class=hd><b>{p.direction} {html.escape(name)}</b><span style="color:{GREEN if pl >= 0 else RED}">£{pl:+.2f} · {m:+.2f}R</span></div>'
                     f'{ladder_svg(lv)}<div class=sub>{" · ".join(facts)}{dtxt}</div></div>')
    eq_pts = [(d[5:], e) for d, e in equity_days] + [("now", equity)]
    return (f'<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>Health check</title><style>{CSS}</style>'
            f'<h1>Health check</h1><div class=sub>{now:%a %d %b %H:%M} UTC</div><div class=v>{html.escape(verdict)}</div>'
            f'<div class=k><div><span class=sub>Equity</span><b>£{equity:.2f}</b><span class=sub>{equity - START_EQUITY:+.2f} vs £{START_EQUITY:.0f} start</span></div>'
            f'<div><span class=sub>Open profit</span><b style="color:{GREEN if tot_pl >= 0 else RED}">£{tot_pl:+.2f}</b><span class=sub>{len(positions)} positions</span></div>'
            f'<div><span class=sub>Worst case, all stops hit</span><b style="color:{RED if tot_risk < 0 else GREEN}">£{tot_risk:+.2f}</b><span class=sub>{abs(tot_risk) / equity * 100:.0f}% of equity (limit 40%)</span></div></div>'
            f'<h2>Where each trade is</h2><div class=sub>Distance in R (1R = the risk taken at entry). The dot is now; right is profit. Red is loss, green is the way to target.</div>{"".join(cards)}'
            f'<h2>Profit and loss against the £{START_EQUITY:.0f} start</h2><div class=sub>The £15 test deposit before 12 Nov is left out.</div>{equity_svg(eq_pts)}'
            f'<h2>Next 26 hours: high-impact releases</h2><table><tr><th>When</th><th>Release</th><th>Forecast</th><th>Affects</th></tr>{release_rows(events)}</table>'
            f'{text_sections(text)}<p class=sub>No edge is shown. Results this small are noise.</p>')


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ig_swing.report_html", description=__doc__.splitlines()[0])
    ap.add_argument("--verdict", required=True, help="one plain sentence: is anything needing doing")
    ap.add_argument("--text-file", required=True, help="the written report: sections start with '## '")
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args(argv)

    import requests
    from .config import base_url, ig_env, load_credentials, optional_key
    from .events import events_within, load_events
    from .ig_client import IGClient
    from .journal import Journal

    now = datetime.now(timezone.utc)
    env = ig_env()
    creds = load_credentials(env)
    client = IGClient(creds, base_url=base_url(env), allow_live=(env == "live"), live_orders_enabled=False)
    positions, equity = client.get_positions(), client.get_account(creds.account_id).equity
    journal = Journal()
    trades = {t["deal_id"]: t for t in journal.open_trades()}
    db = sqlite3.connect(str(ROOT / "data" / "journal.db"))
    days = [(d, e) for d, e in db.execute("select day, equity from equity_daily where day >= ? order by day", (START_DAY,))]
    checks: dict = {}
    for ts, detail in db.execute("select ts, detail from events where kind='position_check' order by ts"):
        d = json.loads(detail)
        if d.get("r") is not None:
            checks.setdefault(d["deal_id"], []).append((datetime.fromisoformat(ts), d["r"]))
    evs, _ = load_events(requests.Session(), now.date(), now.date() + timedelta(days=2), optional_key("FRED_API_KEY"), world=True)
    upcoming = [e for e in events_within(evs, now, 26) if e.time_utc]
    page = build(positions, trades, equity, days, checks, upcoming, args.verdict, Path(args.text_file).read_text(), now)
    REPORTS.mkdir(exist_ok=True)
    path = REPORTS / f"{now:%Y-%m-%d_%H%M}_health.html"
    path.write_text(page)
    (REPORTS / "latest.html").write_text(page)
    print(f"REPORT WRITTEN: {path}")
    if not args.no_open:
        webbrowser.open(path.as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
