"""Pre-registered test 3: the wheel on US Tech 100, US Russell 2000, gold and crude (IG spread bets).

Written 2026-11-19, BEFORE this test was run or any of its results seen. Calibration (below) was taken from IG's live
option quotes the same day, before the run. Variants 11 to 14 of this options work (the wheel on four more markets).
Run once: `python3 -m ig_swing.options_test3`. Nothing may change after the run.

Question (the user's): does the wheel, which passed on the US 500 in test 2, also improve on simply holding the long
in other markets IG offers options on?

Same rules as test 2 (`options_test2.py`): the wheel sells the 30-delta put on the monthly series nearest 35 days out
(at least 21), held to settlement; when a put settles in the money it is cash-settled and the long future is bought
at that day's close, with the put strike as the cost basis; while long, each month it sells the call at the higher
of the 30-delta strike and the cost basis; when a call settles in the money it is cash-settled and the long is
closed at that day's close. Benchmark L0: the long future held throughout. Stake 1 a point; results are each month's
profit divided by the price at the previous month end, so they are returns over cash on a position the size of the
market.

Per market, from IG's quotes on 19 Nov 2026 (monthly options 27 to 39 days out):
  market  data (Yahoo / FRED vol)   skew B   option spread factor  future spread  carry       settles   strike step
  NDX     ^NDX / VXNCLS            -0.193   6.67                  0.011%         q 0.8%      open      about 0.4% of price
  RUT     ^RUT / RVXCLS            -0.174   1.11                  0.019%         q 1.3%      open      about 0.4% of price
  GOLD    GC=F / GVZCLS            -0.079   1.39                  0.020%         none        close     about 0.4% of price
  CRUDE   CL=F / OVXCLS            +0.031   4.44                  0.070%         none        close     about 0.4% of price
- At-the-money implied volatility = 0.90 x the market's 30-day volatility index (the US 500 value; IG's US Tech 100
  chain gave 0.898 against the same-day VXN; the others gave 0.88 to 0.93 against the previous day's index).
  Sensitivity runs at 0.85 and 0.95, and at half and double the skew (descriptive, used for the fragile flag).
- Option spread in points = factor x the US 500 table by days to expiry (1.12 up to 7 days, 1.96 to 20, 2.52 to 45,
  2.60 to 75, 3.64 to 120, 4.20 beyond), never above 0.25 x the option's price. Half on each leg opened or closed, none at settlement. Sensitivity: all spreads x 1.5 (bar 1).
- Long leg: indices as test 2 (quarterly future priced from the index, rolled at the close before the third Friday of
  Mar, Jun, Sep, Dec). Gold and crude: Yahoo's front-month continuous futures series is used as the price, with no
  carry formula, and one full future spread is charged at each roll (crude every month, gold every second month,
  on the first trading day of the month). Yahoo's continuous series jumps at its own rolls and drops days with a
  zero or negative price (crude, 20 Apr 2020), so the commodity results are labelled data-limited.
- Options on all four use the third-Friday monthly cycle as an approximation (IG's gold and crude options expire on
  other days of the month), settled on the open for the indices and on the close for gold and crude.

Main period 2008-07-01 (gold's volatility index starts June 2008) to 2026-10-30 for all four. Descriptive longer run
for US Tech 100 from 2001-02 and Russell 2000 from 2004-02.

Pass bars per market, the wheel against that market's L0, both needed: (1) higher return per unit of risk
(annualised mean / volatility of the monthly returns) at 1x and 1.5x all spreads; (2) smaller worst cumulative fall.
Flagged fragile if the pass fails at half or double the skew or at 0.85 x the volatility index. No significance
claimed.
"""
from __future__ import annotations

import json
import math
import sys
from datetime import date, timedelta

from . import options_test as ot

MAIN = ("2008-07-01", "2026-10-30")
MARKETS = {
    "NDX": {"name": "US Tech 100", "px": "^NDX", "vol": "VXNCLS", "b": -0.193, "sf": 6.67, "fs": 0.00011, "q": 0.008, "index": True, "long": "2001-02-01"},
    "RUT": {"name": "US Russell 2000", "px": "^RUT", "vol": "RVXCLS", "b": -0.174, "sf": 1.11, "fs": 0.00019, "q": 0.013, "index": True, "long": "2004-02-02"},
    "GOLD": {"name": "Gold", "px": "GC=F", "vol": "GVZCLS", "b": -0.079, "sf": 1.39, "fs": 0.00020, "roll_every": 2, "index": False},
    "CRUDE": {"name": "Crude oil", "px": "CL=F", "vol": "OVXCLS", "b": 0.031, "sf": 4.44, "fs": 0.00070, "roll_every": 1, "index": False},
}


def nice_step(x: float) -> float:
    e = 10 ** math.floor(math.log10(x))
    for m in (1, 2, 5, 10):
        if x <= m * e:
            return m * e
    return 10 * e


class Mkt:
    def __init__(self, cfg: dict, px: dict, vol: dict, rates: dict, b: float, scale: float, smult: float):
        self.cfg, self.b, self.scale, self.smult = cfg, b, scale, smult
        self.days = sorted(date.fromisoformat(d) for d in px)
        self.px = {date.fromisoformat(d): v for d, v in px.items()}
        vol_items = sorted((date.fromisoformat(d), v) for d, v in vol.items())
        rate_items = sorted((date.fromisoformat(d), v) for d, v in rates.items())
        self.vol, self.rate = {}, {}
        vi = ri = 0
        lv = lr = None
        for d in self.days:
            while vi < len(vol_items) and vol_items[vi][0] <= d:
                lv = vol_items[vi][1]
                vi += 1
            while ri < len(rate_items) and rate_items[ri][0] <= d:
                lr = rate_items[ri][1] / 100.0
                ri += 1
            self.vol[d], self.rate[d] = lv, lr or 0.0

    def fwd(self, d: date, t: float) -> float:
        s = self.px[d][1]
        return s * math.exp((self.rate[d] - self.cfg["q"]) * t) if self.cfg["index"] else s

    def quote(self, d: date, kind: str, k: float, settle: date) -> tuple[float, float, float]:
        days = max((settle - d).days, 0.5)
        t = days / 365.0
        f = self.fwd(d, t)
        atm = self.scale * self.vol[d] / 100.0
        p, dl = ot.black(kind, f, k, t, ot.strike_vol(atm, f, k, t, self.b))
        return max(p, 0.0), dl, days

    def spread(self, price: float, days: float) -> float:
        st = next(s for dd, s in ot.FIXED["spread_by_days"] if days <= dd)
        return self.smult * min(self.cfg["sf"] * st, 0.25 * price)

    def monthly(self, d: date, target: int, min_days: int) -> date | None:
        cands = []
        for k in range(0, 4):
            y, m = d.year + (d.month - 1 + k) // 12, (d.month - 1 + k) % 12 + 1
            tf = ot.third_friday(y, m)
            sd = ot.settle_day(tf, self.days) if tf <= self.days[-1] else tf
            if sd is None or (sd - d).days < min_days:
                continue
            cands.append((abs((sd - d).days - target), sd))
        return min(cands)[1] if cands else None

    def strike_for_delta(self, d: date, kind: str, settle: date, target: float) -> float:
        s = self.px[d][1]
        step = nice_step(0.004 * s)
        base = round(s / step) * step
        best, err = base, 9.0
        for i in range(-60, 61):
            k = base + i * step
            if k <= 0:
                continue
            e = abs(abs(self.quote(d, kind, k, settle)[1]) - target)
            if e < err:
                best, err = k, e
        return best


class Long:
    def __init__(self):
        self.on, self.ref, self.expiry = False, 0.0, None

    def price(self, m: Mkt, d: date) -> float:
        if m.cfg["index"]:
            return m.fwd(d, max((self.expiry - d).days, 0) / 365.0)
        return m.px[d][1]

    def open(self, cash: list, m: Mkt, d: date) -> None:
        if m.cfg["index"]:
            from .options_test2 import quarterly_expiry
            self.expiry = quarterly_expiry(d + timedelta(days=1))
        self.on, self.ref = True, self.price(m, d)
        cash[0] -= m.smult * m.cfg["fs"] * m.px[d][1] / 2

    def close(self, cash: list, m: Mkt, d: date) -> None:
        cash[0] += self.price(m, d) - self.ref - m.smult * m.cfg["fs"] * m.px[d][1] / 2
        self.on = False

    def mark(self, m: Mkt, d: date) -> float:
        return self.price(m, d) - self.ref if self.on else 0.0


def run(m: Mkt, sid: str, start: str, end: str) -> dict:
    cash = [0.0]
    legs: list[ot.Leg] = []
    lg = Long()
    days = [d for d in m.days if date.fromisoformat(start) <= d <= date.fromisoformat(end) and m.vol.get(d)]
    state, basis = "PUT", 0.0
    st = {"assigned": 0, "called": 0, "long_days": 0, "days": 0}
    series = []
    prev_month = None
    at_open = m.cfg["index"]
    for i, d in enumerate(days):
        s_set = m.px[d][0] if at_open else m.px[d][1]
        settled = [l for l in legs if l.settle == d]
        for l in settled:
            intrinsic = max(0.0, s_set - l.k) if l.kind == "C" else max(0.0, l.k - s_set)
            cash[0] += l.qty * intrinsic
            legs.remove(l)
        if i == 0 and sid == "L0":
            lg.open(cash, m, d)
        if sid == "WH":
            for l in settled:
                if l.kind == "P" and s_set < l.k and state == "PUT":
                    state, basis = "LONG", l.k
                    lg.open(cash, m, d)
                    st["assigned"] += 1
                elif l.kind == "C" and s_set > l.k and state == "LONG":
                    state = "PUT"
                    lg.close(cash, m, d)
                    st["called"] += 1
        # rolls of the long leg
        if lg.on:
            if m.cfg["index"]:
                nxt = days[i + 1] if i + 1 < len(days) else None
                if nxt is not None and nxt >= lg.expiry:
                    lg.close(cash, m, d)
                    lg.open(cash, m, d)
            elif (d.year, d.month) != prev_month and prev_month is not None and (d.month % m.cfg["roll_every"] == 0):
                cash[0] -= m.smult * m.cfg["fs"] * m.px[d][1]
        prev_month = (d.year, d.month)
        if sid == "WH" and not legs:
            ex = m.monthly(d, 35, 21)
            if ex is not None:
                kind = "P" if state == "PUT" else "C"
                k = m.strike_for_delta(d, kind, ex, 0.30)
                if kind == "C":
                    step = nice_step(0.004 * m.px[d][1])
                    k = max(k, math.ceil(basis / step) * step)
                mid, _, dd = m.quote(d, kind, k, ex)
                cash[0] += max(mid - m.spread(mid, dd) / 2, 0.0)
                legs.append(ot.Leg(kind, k, ex, at_open, -1))
        eq = cash[0] + lg.mark(m, d) - sum(m.quote(d, l.kind, l.k, l.settle)[0] for l in legs)
        st["days"] += 1
        st["long_days"] += lg.on
        series.append((d, eq, 0.0))
    return {"series": series, "stats": st}


def summarise(res: dict, m: Mkt) -> dict:
    me = ot.month_ends(res["series"])
    rets = [(b[1] - a[1]) / m.px[a[0]][1] for a, b in zip(me, me[1:])]
    cum = peak = dd = 0.0
    for r in rets:
        cum += r
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    months = [(b[0].year, b[0].month) for b in me[1:]]

    def span(y0, y1):
        return sum(r for (y, _), r in zip(months, rets) if y0 <= y <= y1)

    mu = sum(rets) / len(rets)
    sd = math.sqrt(sum((r - mu) ** 2 for r in rets) / (len(rets) - 1))
    return {"ret_year": mu * 12, "vol": sd * math.sqrt(12), "ratio": ot.ratio(rets), "worst_fall": dd, "worst_month": min(rets),
            "y2008": span(2008, 2008), "y2020": span(2020, 2020), "y2022": span(2022, 2022), "stats": res["stats"]}


def main(argv: list[str] | None = None) -> int:
    rates = ot.fetch_fred("DTB3")
    out: dict = {}
    print(f"The wheel against holding the long, {MAIN[0]} to {MAIN[1]}, returns over cash on a position the size of the market\n")
    print(f"{'market':16}{'strategy':8}{'ret/yr':>8}{'vol':>7}{'ratio':>7}{'worst fall':>11}{'2008*':>8}{'2020':>8}{'2022':>8}")
    for key, cfg in MARKETS.items():
        px, vol = ot.fetch_yahoo(cfg["px"]), ot.fetch_fred(cfg["vol"])
        res = {}
        for label, bm, sc, sm in [("base", 1, 0.90, 1.0), ("x1.5", 1, 0.90, 1.5), ("skew_half", 0.5, 0.90, 1.0), ("skew_double", 2, 0.90, 1.0), ("scale_085", 1, 0.85, 1.0), ("scale_095", 1, 0.95, 1.0)]:
            m = Mkt(cfg, px, vol, rates, cfg["b"] * bm, sc, sm)
            res[label] = {s: summarise(run(m, s, *MAIN), m) for s in ("L0", "WH")}
        if cfg.get("long"):
            m = Mkt(cfg, px, vol, rates, cfg["b"], 0.90, 1.0)
            res["long"] = {s: summarise(run(m, s, cfg["long"], MAIN[1]), m) for s in ("L0", "WH")}
        out[key] = res
        for s in ("L0", "WH"):
            r = res["base"][s]
            print(f"{cfg['name']:16}{s:8}{r['ret_year']*100:>7.1f}%{r['vol']*100:>6.1f}%{r['ratio']:>7.2f}{r['worst_fall']*100:>10.1f}%{r['y2008']*100:>7.1f}%{r['y2020']*100:>7.1f}%{r['y2022']*100:>7.1f}%")
        print(f"  ran {key}", file=sys.stderr)
    print("\n* 2008 from July only.\nBars, the wheel against its own L0 (both needed): 1 higher ratio at 1x and 1.5x spreads; 2 smaller worst cumulative fall")
    for key, res in out.items():
        b, x = res["base"], res["x1.5"]
        b1 = b["WH"]["ratio"] > b["L0"]["ratio"] and x["WH"]["ratio"] > x["L0"]["ratio"]
        b2 = b["WH"]["worst_fall"] < b["L0"]["worst_fall"]
        ok = b1 and b2
        frag = ok and any(res[v]["WH"]["ratio"] <= res[v]["L0"]["ratio"] for v in ("skew_half", "skew_double", "scale_085"))
        tag = ("PASS" + (" (FRAGILE)" if frag else "")) if ok else "FAIL"
        if key in ("GOLD", "CRUDE"):
            tag += " [data-limited]"
        w = b["WH"]["stats"]
        print(f"  {MARKETS[key]['name']:16} bar1 {'Y' if b1 else 'N'} ({b['WH']['ratio']:.2f} vs {b['L0']['ratio']:.2f}; 1.5x {x['WH']['ratio']:.2f} vs {x['L0']['ratio']:.2f})"
              f"  bar2 {'Y' if b2 else 'N'} ({b['WH']['worst_fall']*100:.1f}% vs {b['L0']['worst_fall']*100:.1f}%) -> {tag}"
              f"   [put settled in the money {w['assigned']}x, long {w['long_days']/w['days']*100:.0f}% of days]")
        res["verdict"] = tag
    print("\nSensitivity, wheel ratio minus L0 ratio: skew half / double, vol scale 0.85 / 0.95")
    for key, res in out.items():
        print(f"  {MARKETS[key]['name']:16} " + "  ".join(f"{res[v]['WH']['ratio']-res[v]['L0']['ratio']:+.2f}" for v in ("skew_half", "skew_double", "scale_085", "scale_095")))
    print("\nLonger look (descriptive): ratio and worst fall, L0 vs the wheel")
    for key, res in out.items():
        if "long" in res:
            l = res["long"]
            print(f"  {MARKETS[key]['name']:16} from {MARKETS[key]['long']}: L0 {l['L0']['ratio']:.2f} / {l['L0']['worst_fall']*100:.0f}%, wheel {l['WH']['ratio']:.2f} / {l['WH']['worst_fall']*100:.0f}%")
    ot.CACHE.mkdir(parents=True, exist_ok=True)
    (ot.CACHE / "result3.json").write_text(json.dumps(out, default=str, indent=1))
    print("\nModel option prices calibrated to IG's quotes on one day; daily closes; no margin netting. Not evidence of an edge on its own.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
