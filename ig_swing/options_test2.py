"""Pre-registered test 2: covered calls and the wheel on IG's US 500 (spread bets, futures for the long leg).

Written 2026-11-19, BEFORE this test was run or any of its results seen. It reuses the data, pricing, option costs and
margin rules of `ig_swing/options_test.py` (test 1, run once the same day: all seven selling strategies failed).
Variants 8, 9 and 10 of this options work. Run once: `python3 -m ig_swing.options_test2`. Nothing below may change
after the run; any further variant needs a new pre-registration.

Question (the user's): on the IG account, does (a) a covered call (long US 500 plus calls sold against it) or (b) the
wheel (sell puts; if "assigned", hold the index and sell calls until "called away"; repeat) improve on simply holding
the US 500 long, after IG's costs?

Long leg: the US 500 quarterly future spread bet (the user's standing rule: futures, no overnight funding). Modelled
as F = S exp((r - q) tau) to the quarterly expiry (third Friday of Mar, Jun, Sep, Dec; r = DTB3, q = 1.5%), so it
earns the index's return over cash. Spread 0.013% of the index (IG, 11 Nov), half on each open and close, and rolled
at the close of the last trading day before each quarterly expiry (one close and one open). Margin 5% of the index.
Cash interest outside IG is the same for every strategy and is left out, so all returns are over cash.

Options: as test 1 (model prices from the Cboe volatility term structure and the skew fitted to IG's ticket; IG's
option spread by days to expiry, half per leg opened or closed, none at settlement; margin 5% of the index per short
leg, no netting; monthly series settled on the opening print). All options are monthly, the series nearest 35 days
out with at least 21 days, held to settlement (no early close, no rolling), and the next one is sold at the close of
the settlement day. Stake £1 a point.

Strategies (main period 2008-01-02 to 2026-10-30):
  L0   benchmark: long the US 500 future throughout.
  CC30 L0 plus a 30-delta call sold each month.
  CC16 L0 plus a 16-delta call sold each month.
  WH   the wheel. Start flat and sell the 30-delta put. When a put settles below its strike ("assigned"), it is
       cash-settled and the long future is bought at that day's close, with the put strike as the cost basis. While
       long, sell each month the call at the higher of the 30-delta strike and the cost basis (rounded up to the
       grid). When a call settles above its strike ("called away"), it is cash-settled and the long future is closed
       at that day's close; then sell puts again. (Buying or selling at the close, not at the settlement print, is
       the one gap from a real assignment.)

Measured: each month's profit and loss divided by the index level at the end of the previous month (the profit on
a position the size of the index, so the fixed £1 a point does not let the later, higher-priced years dominate).
From that series: return a year, volatility, return per unit of risk (annualised mean / volatility), worst
cumulative fall, worst month, share of months positive, profit in 2008-12, 2013-19, 2020-26, and in 2008, Mar 2020
and 2022. Also the account needed at £1 a point (largest margin less cumulative profit), the share of time the wheel
held the long, and the number of assignments and call-aways.

Pass bars, each strategy against L0, both needed:
  1. Higher return per unit of risk than L0, at 1x and at 1.5x all spreads.
  2. A smaller worst cumulative fall than L0.
A pass that fails at half or double the skew is flagged fragile. Descriptive only: a VIX-only run from 1990, and the
US 500 with dividends for context. No statistical significance claimed.
"""
from __future__ import annotations

import json
import math
import sys
from datetime import date, timedelta

from . import options_test as ot

FUT_SPREAD = 0.00013
MAIN = ("2008-01-02", "2026-10-30")
LONG = ("1990-01-02", "2026-10-30")
STRATS = ["L0", "CC30", "CC16", "WH"]
NAMES = {"L0": "Long US 500 future", "CC30": "Covered call, 30 delta", "CC16": "Covered call, 16 delta", "WH": "The wheel"}


def quarterly_expiry(d: date) -> date:
    for k in range(0, 5):
        y, m = d.year + (d.month - 1 + k) // 12, (d.month - 1 + k) % 12 + 1
        if m % 3 == 0:
            tf = ot.third_friday(y, m)
            if tf > d:
                return tf
    raise ValueError(d)


def fut_price(mkt: ot.Market, d: date, expiry: date) -> float:
    s = mkt.px[d][1]
    return s * math.exp((mkt.rate[d] - ot.FIXED["q"]) * max((expiry - d).days, 0) / 365.0)


class Fut:
    def __init__(self):
        self.expiry: date | None = None
        self.ref = 0.0

    def is_open(self) -> bool:
        return self.expiry is not None

    def open(self, book: ot.Book, mkt: ot.Market, d: date, sm: float) -> None:
        self.expiry = quarterly_expiry(d + timedelta(days=1))
        self.ref = fut_price(mkt, d, self.expiry)
        book.cash -= sm * FUT_SPREAD * mkt.px[d][1] / 2

    def close(self, book: ot.Book, mkt: ot.Market, d: date, sm: float) -> None:
        book.cash += fut_price(mkt, d, self.expiry) - self.ref - sm * FUT_SPREAD * mkt.px[d][1] / 2
        self.expiry = None

    def mark(self, mkt: ot.Market, d: date) -> float:
        return fut_price(mkt, d, self.expiry) - self.ref if self.expiry else 0.0


def sell(book: ot.Book, mkt: ot.Market, d: date, kind: str, target: float, b: float, sm: float, floor_k: float | None = None) -> None:
    ex = mkt.monthly(d, 35, 21)
    if ex is None:
        return
    k = mkt.strike_for_delta(d, kind, ex, target, b)
    if floor_k is not None:
        step = ot.step_for(mkt.px[d][1])
        k = max(k, math.ceil(floor_k / step) * step)
    ot.trade(book, mkt, d, ot.Leg(kind, k, ex, True, -1), True, b, sm)


def run(sid: str, mkt: ot.Market, start: str, end: str, b: float, sm: float) -> dict:
    book, fut = ot.Book(), Fut()
    days = [d for d in mkt.days if date.fromisoformat(start) <= d <= date.fromisoformat(end)]
    state, basis = "PUT", 0.0
    st = {"assigned": 0, "called": 0, "long_days": 0, "days": 0}
    series = []
    for i, d in enumerate(days):
        # options settle on the opening print
        s_open = mkt.px[d][0]
        settled = [l for l in book.legs if l.settle == d]
        for l in settled:
            intrinsic = max(0.0, s_open - l.k) if l.kind == "C" else max(0.0, l.k - s_open)
            book.cash += l.qty * intrinsic
            book.legs.remove(l)
        if i == 0 and sid != "WH":
            fut.open(book, mkt, d, sm)
        if sid == "WH":
            for l in settled:
                if l.kind == "P" and s_open < l.k and state == "PUT":
                    state, basis = "LONG", l.k
                    fut.open(book, mkt, d, sm)
                    st["assigned"] += 1
                elif l.kind == "C" and s_open > l.k and state == "LONG":
                    state = "PUT"
                    fut.close(book, mkt, d, sm)
                    st["called"] += 1
        # quarterly roll of the long future at the close before expiry
        if fut.is_open():
            nxt = days[i + 1] if i + 1 < len(days) else None
            if nxt is None or nxt >= fut.expiry:
                if nxt is not None:
                    fut.close(book, mkt, d, sm)
                    fut.open(book, mkt, d, sm)
        # sell the next option at the close when none is open
        if not book.legs and sid != "L0":
            if sid == "CC30":
                sell(book, mkt, d, "C", 0.30, b, sm)
            elif sid == "CC16":
                sell(book, mkt, d, "C", 0.16, b, sm)
            elif state == "PUT":
                sell(book, mkt, d, "P", 0.30, b, sm)
            else:
                sell(book, mkt, d, "C", 0.30, b, sm, floor_k=basis)
        eq = ot.equity(book, mkt, d, b) + fut.mark(mkt, d)
        margin = ot.FIXED["margin"] * mkt.px[d][1] * (sum(1 for l in book.legs if l.qty < 0) + (1 if fut.is_open() else 0))
        st["days"] += 1
        st["long_days"] += 1 if fut.is_open() else 0
        series.append((d, eq, margin))
    return {"series": series, "stats": st}


def summarise(res: dict, mkt: ot.Market) -> dict:
    series = res["series"]
    me = ot.month_ends(series)
    rets = [(b[1] - a[1]) / mkt.px[a[0]][1] for a, b in zip(me, me[1:])]
    months = [(b[0].year, b[0].month) for b in me[1:]]
    cum = peak = dd = 0.0
    for r in rets:
        cum += r
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    m = sum(rets) / len(rets)
    sd = math.sqrt(sum((r - m) ** 2 for r in rets) / (len(rets) - 1))

    def span(y0: int, y1: int) -> float:
        return sum(r for (y, _), r in zip(months, rets) if y0 <= y <= y1)

    return {"ret_year": m * 12, "vol": sd * math.sqrt(12), "ratio": ot.ratio(rets), "worst_fall": dd, "worst_month": min(rets),
            "months_pos": sum(1 for r in rets if r > 0) / len(rets), "p1": span(2008, 2012), "p2": span(2013, 2019), "p3": span(2020, 2026),
            "y2008": span(2008, 2008), "mar2020": dict(zip(months, rets)).get((2020, 3), 0.0), "y2022": span(2022, 2022),
            "account": max(1.0, max(mg - eq for _, eq, mg in series)), "stats": res["stats"]}


def main(argv: list[str] | None = None) -> int:
    data = {s: ot.fetch_yahoo(s) for s in ot.YAHOO}
    rates = ot.fetch_fred("DTB3")
    full = ot.Market(data, rates, ot.VOL_POINTS)
    vix_only = ot.Market(data, rates, [(30, "^VIX")])
    b0 = ot.FIXED["skew_b"]
    out: dict = {}
    for label, b, sm in [("base", b0, 1.0), ("spread_x1.5", b0, 1.5), ("skew_half", b0 * 0.5, 1.0), ("skew_double", b0 * 2, 1.0)]:
        out[label] = {s: summarise(run(s, full, *MAIN, b, sm), full) for s in STRATS}
        print(f"  ran {label}", file=sys.stderr)
    out["long_1990"] = {s: summarise(run(s, vix_only, *LONG, b0, 1.0), vix_only) for s in STRATS}
    bench = ot.benchmark(data, *MAIN)
    base = out["base"]
    print(f"\nCovered calls and the wheel on IG's US 500, {MAIN[0]} to {MAIN[1]}, returns over cash on a position the size of the index")
    print(f"(context: US 500 with dividends {bench['cagr']*100:+.1f}% a year before taking off cash, risk ratio {bench['ratio']:.2f})\n")
    print(f"{'':6}{'strategy':26}{'ret/yr':>8}{'vol':>7}{'ratio':>7}{'worst fall':>11}{'worst mo':>9}{'2008':>8}{'Mar20':>8}{'2022':>8}{'mo +':>6}{'acct £':>8}")
    for s in STRATS:
        r = base[s]
        print(f"{s:6}{NAMES[s]:26}{r['ret_year']*100:>7.1f}%{r['vol']*100:>6.1f}%{r['ratio']:>7.2f}{r['worst_fall']*100:>10.1f}%{r['worst_month']*100:>8.1f}%"
              f"{r['y2008']*100:>7.1f}%{r['mar2020']*100:>7.1f}%{r['y2022']*100:>7.1f}%{r['months_pos']*100:>5.0f}%{r['account']:>8.0f}")
    print("\nBars against L0 (both needed): 1 higher risk ratio at 1x and 1.5x spreads; 2 smaller worst cumulative fall")
    l0, l015 = base["L0"], out["spread_x1.5"]["L0"]
    for s in ("CC30", "CC16", "WH"):
        r, r15 = base[s], out["spread_x1.5"][s]
        b1 = r["ratio"] > l0["ratio"] and r15["ratio"] > l015["ratio"]
        b2 = r["worst_fall"] < l0["worst_fall"]
        ok = b1 and b2
        fragile = ok and (out["skew_half"][s]["ratio"] <= out["skew_half"]["L0"]["ratio"] or out["skew_double"][s]["ratio"] <= out["skew_double"]["L0"]["ratio"])
        tag = ("PASS" + (" (FRAGILE)" if fragile else "")) if ok else "FAIL"
        print(f"  {s:5} {NAMES[s]:26} bar1 {'Y' if b1 else 'N'} (ratio {r['ratio']:.2f} vs {l0['ratio']:.2f}; at 1.5x {r15['ratio']:.2f} vs {l015['ratio']:.2f})"
              f"  bar2 {'Y' if b2 else 'N'} (fall {r['worst_fall']*100:.1f}% vs {l0['worst_fall']*100:.1f}%)  -> {tag}")
        base[s]["verdict"] = tag
    print("\nPeriods (return over cash, % of index): 2008-12 / 2013-19 / 2020-26")
    for s in STRATS:
        r = base[s]
        print(f"  {s:5} {r['p1']*100:>6.1f}% {r['p2']*100:>6.1f}% {r['p3']*100:>6.1f}%")
    print("\nSkew sensitivity, risk ratio: half / base / double")
    for s in STRATS:
        print(f"  {s:5} {out['skew_half'][s]['ratio']:.2f} {base[s]['ratio']:.2f} {out['skew_double'][s]['ratio']:.2f}")
    w = base["WH"]["stats"]
    print(f"\nWheel: assigned {w['assigned']} times, called away {w['called']} times, long {w['long_days']/w['days']*100:.0f}% of days")
    print("\nLonger look from 1990, VIX only (descriptive): ret/yr, ratio, worst fall")
    for s in STRATS:
        r = out["long_1990"][s]
        print(f"  {s:5} {r['ret_year']*100:>6.1f}% {r['ratio']:>6.2f} {r['worst_fall']*100:>6.1f}%")
    ot.CACHE.mkdir(parents=True, exist_ok=True)
    (ot.CACHE / "result2.json").write_text(json.dumps(out, default=str, indent=1))
    print("\nModel option prices, daily closes, no margin netting. Not evidence of an edge on its own.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
