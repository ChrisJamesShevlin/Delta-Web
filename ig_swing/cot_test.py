"""Pre-registered test: betting against small traders at extremes (CFTC Commitments of Traders).

Written 2026-11-19, BEFORE the positioning data was loaded for any market or any result seen (only the files'
availability and column names were checked). Run once: `python3 -m ig_swing.cot_test`. Nothing may change after.

Question (the user's): IG's client sentiment (for example silver 90% long while it falls) looks contrarian. IG keeps
no history of it, so the test uses the closest public record: the CFTC's weekly "non-reportable" positions, the
small speculators in US futures, back to 1986.

Data: CFTC Legacy futures-only reports (deacot1986_2016.zip and the yearly files from 2017), by contract code:
EUR 099741, GBP 096742, JPY 097741, AUD 232741, CAD 090741, CHF 092741, NZD 112741, gold 088691, silver 084691,
WTI crude 067651, natural gas 023651, E-mini S&P 500 13874A, E-mini Nasdaq-100 209742, mini Dow 124603. (The
Russell 2000 is left out: its futures moved exchange and code between 2008 and 2017.) Prices: FRED daily dollar
rates for the currencies (as the currency's price in dollars, which is what the CME contract tracks), Yahoo GC=F,
SI=F, CL=F, NG=F (continuous front month, which jumps at its own rolls: commodity results are data-limited), ^GSPC,
^NDX, ^DJI. Trades from 2001-01-01 to 2026-10-30; in-sample to 2015, holdout from 2016-01-01.

Signal: small-trader net = (non-reportable long - non-reportable short) / open interest. Its percentile among the
previous 156 weekly reports (three years, the current one excluded). Extreme long: 90th percentile or above, so go
SHORT; extreme short: 10th or below, so go LONG. A trade starts only on a fresh extreme (the previous week's reading
was not in the same zone), and only when no trade is open in that market.

Timing: the report is as of a Tuesday and published the Friday; entry at the close of the first trading day after
that Friday (no look-ahead). Hold 20 trading days, exit at the close. 5- and 10-day holds reported, descriptive only.

Returns, as traded on IG futures or forwards (no funding): direction x price change, plus carry: currencies add
direction x (3-month rate of the currency - US 3-month rate) x days / 365 (the rates of `carry_test`); index
futures subtract (US 3-month rate - 1.8% dividends) x days / 365 from a long. Costs: one full spread per trade, plus
one more when a roll falls inside the hold (quarterly third Friday for currencies and indices; always for the
commodities, which roll monthly or every other month). Spreads (IG, 11 Nov 2026): EUR 0.098%, GBP 0.079%, JPY
0.063%, AUD, CAD, CHF, NZD 0.10% (assumed), gold 0.020%, silver 0.193%, crude 0.070%, natural gas 0.310%, S&P 500
0.013%, Nasdaq-100 0.011%, Dow 0.013% (assumed).

Measured: per trade net return; n; mean; standard error clustered by calendar month; z; win rate; pooled, by side
(long signals, short signals), by market, in-sample and holdout; against each market's unconditional 20-day return
(its drift) so that a result that only rides the drift is visible.

Bars (both needed): (1) pooled net mean per trade above zero with a clustered z of 2.5 or more (the project's raised
bar); (2) the holdout mean above zero. A pass justifies only a forward test on IG's own sentiment (archived by every
briefing since 12 Nov 2026), never money. No tuning; any variant needs a new pre-registration.
"""
from __future__ import annotations

import csv
import io
import json
import math
import zipfile
from datetime import date, timedelta
from pathlib import Path

from . import carry_test as ct
from . import options_test as ot

COT_DIR = Path(__file__).resolve().parent.parent / "data" / "history" / "cot"   # gitignored
FILES = ["deacot1986_2016"] + [f"deacot{y}" for y in range(2017, 2027)]
MARKETS = {
    "EUR": ("099741", ("fred", "DEXUSEU", False), 0.00098, "q"), "GBP": ("096742", ("fred", "DEXUSUK", False), 0.00079, "q"),
    "JPY": ("097741", ("fred", "DEXJPUS", True), 0.00063, "q"), "AUD": ("232741", ("fred", "DEXUSAL", False), 0.0010, "q"),
    "CAD": ("090741", ("fred", "DEXCAUS", True), 0.0010, "q"), "CHF": ("092741", ("fred", "DEXSZUS", True), 0.0010, "q"),
    "NZD": ("112741", ("fred", "DEXUSNZ", False), 0.0010, "q"),
    "Gold": ("088691", ("yahoo", "GC=F"), 0.00020, "m"), "Silver": ("084691", ("yahoo", "SI=F"), 0.00193, "m"),
    "Crude": ("067651", ("yahoo", "CL=F"), 0.00070, "m"), "NatGas": ("023651", ("yahoo", "NG=F"), 0.00310, "m"),
    "S&P 500": ("13874A", ("yahoo", "^GSPC"), 0.00013, "q"), "Nasdaq-100": ("209742", ("yahoo", "^NDX"), 0.00011, "q"),
    "Dow": ("124603", ("yahoo", "^DJI"), 0.00013, "q"),
}
START, END, HOLDOUT = "2001-01-01", "2026-10-30", "2016-01-01"
LOOKBACK, HI, LO, HOLD = 156, 0.90, 0.10, 20


def load_cot() -> dict[str, list[tuple[str, float]]]:
    """contract code -> [(as-of date, small-trader net / open interest)], sorted."""
    COT_DIR.mkdir(parents=True, exist_ok=True)
    codes = {v[0] for v in MARKETS.values()}
    out: dict[str, dict[str, float]] = {c: {} for c in codes}
    import requests
    for f in FILES:
        p = COT_DIR / f"{f}.zip"
        if not p.exists():
            r = requests.get(f"https://www.cftc.gov/files/dea/history/{f}.zip", headers={"User-Agent": "Mozilla/5.0"}, timeout=120)
            r.raise_for_status()
            p.write_bytes(r.content)
        z = zipfile.ZipFile(p)
        for name in z.namelist():
            rd = csv.reader(io.TextIOWrapper(z.open(name), encoding="latin-1"))
            h = next(rd)
            ix = {c: i for i, c in enumerate(h)}
            for row in rd:
                code = row[ix["CFTC Contract Market Code"]].strip()
                if code not in codes:
                    continue
                try:
                    oi = float(row[ix["Open Interest (All)"]])
                    nl = float(row[ix["Nonreportable Positions-Long (All)"]])
                    ns = float(row[ix["Nonreportable Positions-Short (All)"]])
                except ValueError:
                    continue
                if oi > 0:
                    out[code][row[ix["As of Date in Form YYYY-MM-DD"]].strip()] = (nl - ns) / oi
    return {c: sorted(v.items()) for c, v in out.items()}


def prices(src: tuple) -> dict[str, float]:
    if src[0] == "fred":
        raw = ot.fetch_fred(src[1])
        return {d: (1.0 / v if src[2] else v) for d, v in raw.items() if v}
    return {d: v[1] for d, v in ot.fetch_yahoo(src[1]).items()}


def percentile(hist: list[float], x: float) -> float:
    return sum(1 for h in hist if h < x) / len(hist)


def signals(series: list[tuple[str, float]]) -> list[tuple[str, int]]:
    """(as-of date, direction) for fresh extremes: -1 short after an extreme long, +1 long after an extreme short."""
    out, prev_zone = [], 0
    for i in range(LOOKBACK, len(series)):
        hist = [v for _, v in series[i - LOOKBACK:i]]
        p = percentile(hist, series[i][1])
        zone = -1 if p >= HI else 1 if p <= LO else 0
        if zone != 0 and zone != prev_zone:
            out.append((series[i][0], zone))
        prev_zone = zone
    return out


def roll_inside(kind: str, d0: date, d1: date) -> bool:
    if kind == "m":
        return True
    for y in range(d0.year, d1.year + 1):
        for m in (3, 6, 9, 12):
            if d0 < ot.third_friday(y, m) <= d1:
                return True
    return False


def run(hold: int = HOLD) -> list[dict]:
    cot = load_cot()
    rates = {c: ct.rates_monthly(c) for c in ["USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD"]}
    trades = []
    for mk, (code, src, spread, kind) in MARKETS.items():
        px = prices(src)
        days = sorted(d for d in px if START <= d <= "2026-12-31")
        busy_until = ""
        for asof, direction in signals(cot.get(code, [])):
            release = (date.fromisoformat(asof) + timedelta(days=3)).isoformat()
            i0 = next((i for i, d in enumerate(days) if d > release), None)
            if i0 is None or i0 + hold >= len(days):
                continue
            d0, d1 = days[i0], days[i0 + hold]
            if d0 < START or d0 > END or d0 <= busy_until:
                continue
            busy_until = d1
            ret = direction * (px[d1] / px[d0] - 1)
            yrs = (date.fromisoformat(d1) - date.fromisoformat(d0)).days / 365
            m0 = d0[:7]
            if mk in rates and m0 in rates[mk] and m0 in rates["USD"]:
                ret += direction * (rates[mk][m0] - rates["USD"][m0]) / 100 * yrs
            elif kind == "q" and mk in ("S&P 500", "Nasdaq-100", "Dow") and m0 in rates["USD"]:
                ret -= direction * (rates["USD"][m0] / 100 - 0.018) * yrs
            cost = spread * (2 if roll_inside(kind, date.fromisoformat(d0), date.fromisoformat(d1)) else 1)
            trades.append({"market": mk, "entry": d0, "exit": d1, "dir": direction, "gross": ret, "net": ret - cost})
    return trades


def drift(mk: str, hold: int = HOLD) -> float:
    px = prices(MARKETS[mk][1])
    days = sorted(d for d in px if START <= d <= END)
    r = [px[days[i + hold]] / px[days[i]] - 1 for i in range(0, len(days) - hold, hold)]
    return sum(r) / len(r) if r else float("nan")


def clustered(ts: list[dict]) -> tuple[float, float, float]:
    n = len(ts)
    mu = sum(t["net"] for t in ts) / n
    g: dict[str, float] = {}
    for t in ts:
        g[t["entry"][:7]] = g.get(t["entry"][:7], 0.0) + (t["net"] - mu)
    k = len(g)
    se = math.sqrt(sum(v * v for v in g.values()) / (n * n) * (k / (k - 1) if k > 1 else 1))
    return mu, se, (mu / se if se > 0 else float("nan"))


def main(argv: list[str] | None = None) -> int:
    trades = run()
    print(f"Against small traders at extremes (CFTC non-reportable, 3-year percentile >= 90 short / <= 10 long), hold {HOLD} days, IG costs\n")
    print(f"{'subset':24}{'n':>5}{'mean net':>10}{'SE':>8}{'z':>7}{'gross':>9}{'win':>6}")
    res = {}

    def row(label, ts):
        if len(ts) < 3:
            print(f"{label:24}{len(ts):>5}")
            return None
        mu, se, z = clustered(ts)
        print(f"{label:24}{len(ts):>5}{mu*100:>9.2f}%{se*100:>7.2f}%{z:>7.2f}{sum(t['gross'] for t in ts)/len(ts)*100:>8.2f}%{sum(1 for t in ts if t['net']>0)/len(ts)*100:>5.0f}%")
        res[label] = (len(ts), mu, se, z)
        return mu, se, z

    allr = row("all", trades)
    row("in-sample (2001-2015)", [t for t in trades if t["entry"] < HOLDOUT])
    hold = row("holdout (2016 on)", [t for t in trades if t["entry"] >= HOLDOUT])
    row("long signals (crowd short)", [t for t in trades if t["dir"] == 1])
    row("short signals (crowd long)", [t for t in trades if t["dir"] == -1])
    print()
    for mk in MARKETS:
        ts = [t for t in trades if t["market"] == mk]
        r = row(f"  {mk}", ts)
        if r:
            print(f"{'':24}     (market's own 20-day drift {drift(mk)*100:+.2f}%; {sum(1 for t in ts if t['dir']==1)} long, {sum(1 for t in ts if t['dir']==-1)} short)")
    b1 = allr is not None and allr[0] > 0 and allr[2] >= 2.5
    b2 = hold is not None and hold[0] > 0
    print(f"\nBar 1 (pooled mean > 0, clustered z >= 2.5): {'PASS' if b1 else 'FAIL'}")
    print(f"Bar 2 (holdout mean > 0): {'PASS' if b2 else 'FAIL'}")
    print(f"VERDICT: {'PASS' if b1 and b2 else 'FAIL'}")
    print("\nDescriptive, other holds (pooled):")
    for h in (5, 10):
        ts = run(h)
        mu, se, z = clustered(ts)
        print(f"  {h:>2} days: n {len(ts)}, mean net {mu*100:+.2f}%, z {z:.2f}")
    ot.CACHE.mkdir(parents=True, exist_ok=True)
    (ot.CACHE / "cot_result.json").write_text(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
