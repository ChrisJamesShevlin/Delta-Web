"""Pre-registered test: pairs trading on US indices and US share pairs (IG spread bets / futures).

Written 2026-11-19, BEFORE any price data for it was fetched (availability only was checked) or any result seen.
Run once: `python3 -m ig_swing.pairs_test`. Nothing below may change after the run.

Question (the user's): could pairs trading (long one market, short a related one, betting the gap between them
closes) match the S&P while being different from holding it?

Pairs (fixed): indices US 500 / US Tech 100 (^GSPC, ^NDX), Wall Street / US 500 (^DJI, ^GSPC), Russell 2000 / US 500
(^RUT, ^GSPC); shares Apple / Microsoft (the user's example), and three classic same-industry pairs: Coca-Cola /
PepsiCo, Exxon / Chevron, Visa / Mastercard (Visa from 2008). Yahoo daily closes (split-adjusted, dividends left
out: in a pair they largely cancel). The shares are today's large caps, so survivorship flatters the share results.
Period: 2005-01-03 to 2026-10-30. Before 2017 in-sample, 2017 on the holdout.

Rule (the textbook distance rule): gap = ln(A) - ln(B); z = (gap - its 60-day mean) / its 60-day standard deviation.
When 2 <= |z| < 4 at a close (a gap already past the stop is not entered), at the NEXT close short the rich leg and buy the cheap one, equal pounds in each leg (N).
Exit at the next close after z crosses zero, or |z| reaches 4 (stop), or 20 trading days have passed. One position
per pair at a time; a new entry needs a fresh |z| >= 2 after the exit day.

Costs: indices on IG quarterly futures, no funding: a full spread per leg per trade (US 500 0.013%, US Tech 100
0.011%, Russell 2000 0.019%, Wall Street 0.013% assumed as the US 500), plus one more spread per leg when a quarterly
expiry falls inside the hold. Shares on IG share spread bets (no share futures used): 0.125% per leg per trade (NVDA
cost panel, 11 Nov), plus net funding of 5% a year on N for the days held (the long leg pays about base + 2.5%, the
short leg receives about base - 2.5%).

Measured: per trade net return on N, pooled and by pair; n; mean, clustered standard error by calendar month, z;
win rate; in-sample and holdout. Monthly P&L series (trades booked in their exit month) as a return on capital
C = 2 x N x 7 (all pairs open at once, no leverage); its return a year, volatility and return per unit of risk
against the S&P 500 with dividends over cash (Yahoo ^SP500TR less FRED DTB3) over the same months.

Bars (both needed): (1) pooled net mean per trade above zero with a clustered z of 2.5 or more over the whole period
(the raised bar: many variants have been tried in this project), AND positive in the holdout; (2) return per unit of
risk of the monthly series at least the S&P's over cash (so leverage could in principle match the S&P). No tuning;
one run; any variant needs a new pre-registration.
"""
from __future__ import annotations

import json
import math
from datetime import date

from . import options_test as ot

PAIRS = [("^GSPC", "^NDX"), ("^DJI", "^GSPC"), ("^RUT", "^GSPC"), ("AAPL", "MSFT"), ("KO", "PEP"), ("XOM", "CVX"), ("V", "MA")]
FUT_SPREAD = {"^GSPC": 0.00013, "^NDX": 0.00011, "^RUT": 0.00019, "^DJI": 0.00013}
SHARE_COST = 0.00125
FUNDING = 0.05
START, END, HOLDOUT = "2005-01-03", "2026-10-30", "2017-01-01"
LOOK, ENTRY, STOP, MAXDAYS = 60, 2.0, 4.0, 20


def quarterly_in(d0: date, d1: date) -> bool:
    for y in range(d0.year, d1.year + 1):
        for m in (3, 6, 9, 12):
            tf = ot.third_friday(y, m)
            if d0 < tf <= d1:
                return True
    return False


def trades_for(a: str, b: str, px: dict[str, dict[str, tuple]]) -> list[dict]:
    days = sorted(d for d in px[a] if d in px[b] and START <= d <= END)
    gap = [math.log(px[a][d][1]) - math.log(px[b][d][1]) for d in days]
    out = []
    pos = None   # dict with side (+1 long A short B, -1 short A long B), entry index
    pending_entry = pending_exit = None
    last_exit = -1
    for i, d in enumerate(days):
        # execute pending orders at this close
        if pending_exit is not None and pos is not None:
            out.append(close_trade(a, b, px, days, pos, i, pending_exit))
            pos, pending_exit, last_exit = None, None, i
        if pending_entry is not None and pos is None:
            pos = {"side": pending_entry, "i0": i}
            pending_entry = None
        if i < LOOK:
            continue
        w = gap[i - LOOK + 1:i + 1]
        mu = sum(w) / LOOK
        sd = math.sqrt(sum((x - mu) ** 2 for x in w) / (LOOK - 1))
        if sd <= 0:
            continue
        z = (gap[i] - mu) / sd
        if pos is None:
            if pending_entry is None and i > last_exit and ENTRY <= abs(z) < STOP:
                pending_entry = -1 if z > 0 else 1   # A rich -> short A, long B
        else:
            held = i - pos["i0"]
            crossed = (pos["side"] == -1 and z <= 0) or (pos["side"] == 1 and z >= 0)
            if crossed:
                pending_exit = "target"
            elif abs(z) >= STOP:
                pending_exit = "stop"
            elif held >= MAXDAYS:
                pending_exit = "time"
    return out


def close_trade(a, b, px, days, pos, i, reason) -> dict:
    d0, d1 = days[pos["i0"]], days[i]
    ra = px[a][d1][1] / px[a][d0][1] - 1
    rb = px[b][d1][1] / px[b][d0][1] - 1
    gross = pos["side"] * ra - pos["side"] * rb
    dd0, dd1 = date.fromisoformat(d0), date.fromisoformat(d1)
    if a in FUT_SPREAD:
        roll = quarterly_in(dd0, dd1)
        cost = (FUT_SPREAD[a] + FUT_SPREAD[b]) * (2 if roll else 1)
    else:
        cost = 2 * SHARE_COST + FUNDING * (dd1 - dd0).days / 365
    return {"pair": f"{a}/{b}", "entry": d0, "exit": d1, "side": pos["side"], "gross": gross, "net": gross - cost, "reason": reason}


def clustered(trades: list[dict]) -> tuple[float, float, float]:
    n = len(trades)
    mu = sum(t["net"] for t in trades) / n
    groups: dict[str, float] = {}
    for t in trades:
        groups[t["exit"][:7]] = groups.get(t["exit"][:7], 0.0) + (t["net"] - mu)
    var = sum(v * v for v in groups.values()) / (n * n)
    g = len(groups)
    var *= g / (g - 1) if g > 1 else 1
    se = math.sqrt(var)
    return mu, se, mu / se if se > 0 else float("nan")


def main(argv: list[str] | None = None) -> int:
    syms = sorted({s for p in PAIRS for s in p})
    px = {s: ot.fetch_yahoo(s) for s in syms}
    trades = []
    for a, b in PAIRS:
        trades += trades_for(a, b, px)
    ins = [t for t in trades if t["entry"] < HOLDOUT]
    hold = [t for t in trades if t["entry"] >= HOLDOUT]
    print(f"Pairs trading, {START} to {END}, |z| 2 entry, exit at 0, stop 4, 20 days; IG costs\n")
    print(f"{'subset':26}{'n':>6}{'mean net':>10}{'SE':>8}{'z':>7}{'gross':>9}{'win':>6}")

    def row(label, ts):
        if len(ts) < 3:
            print(f"{label:26}{len(ts):>6}")
            return None
        mu, se, z = clustered(ts)
        g = sum(t["gross"] for t in ts) / len(ts)
        w = sum(1 for t in ts if t["net"] > 0) / len(ts)
        print(f"{label:26}{len(ts):>6}{mu*100:>9.3f}%{se*100:>7.3f}%{z:>7.2f}{g*100:>8.3f}%{w*100:>5.0f}%")
        return mu, se, z

    allr = row("all pairs", trades)
    row("in-sample (to 2016)", ins)
    holdr = row("holdout (2017 on)", hold)
    row("indices only", [t for t in trades if t["pair"].startswith("^")])
    row("shares only", [t for t in trades if not t["pair"].startswith("^")])
    for a, b in PAIRS:
        row(f"  {a}/{b}", [t for t in trades if t["pair"] == f"{a}/{b}"])
    # monthly P&L on capital 2 x N x 7
    months: dict[str, float] = {}
    for t in trades:
        months[t["exit"][:7]] = months.get(t["exit"][:7], 0.0) + t["net"] / (2 * len(PAIRS))
    tr = {}
    for d, v in sorted(ot.fetch_yahoo("^SP500TR").items()):
        tr[d[:7]] = v[1]
    tb = {}
    for d, v in sorted(ot.fetch_fred("DTB3").items()):
        tb[d[:7]] = v
    ms = [m for m in sorted(tr) if START[:7] <= m <= END[:7]]
    bench = [tr[m1] / tr[m0] - 1 - tb.get(m0, 0) / 1200 for m0, m1 in zip(ms, ms[1:])]
    strat = [months.get(m1, 0.0) for m1 in ms[1:]]

    def ratio(r):
        mu = sum(r) / len(r)
        sd = math.sqrt(sum((x - mu) ** 2 for x in r) / (len(r) - 1))
        return mu * 12 / (sd * math.sqrt(12)) if sd > 0 else float("nan")

    sr, br = ratio(strat), ratio(bench)
    ann = sum(strat) / len(strat) * 12
    print(f"\nMonthly, on capital 2 x N x 7 (no leverage): {ann*100:.2f}% a year, return per unit of risk {sr:.2f}; S&P with dividends over cash {br:.2f}")
    b1 = allr is not None and allr[2] >= 2.5 and holdr is not None and holdr[0] > 0
    b2 = sr >= br
    print(f"Bar 1 (pooled z >= 2.5 and holdout positive): {'PASS' if b1 else 'FAIL'}")
    print(f"Bar 2 (return per unit of risk >= the S&P's): {'PASS' if b2 else 'FAIL'}")
    print(f"VERDICT: {'PASS' if b1 and b2 else 'FAIL'}")
    reasons = {}
    for t in trades:
        reasons[t["reason"]] = reasons.get(t["reason"], 0) + 1
    print(f"Exits: {reasons}")
    ot.CACHE.mkdir(parents=True, exist_ok=True)
    (ot.CACHE / "pairs_result.json").write_text(json.dumps({"n": len(trades), "ann": ann, "ratio": sr, "bench": br}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
