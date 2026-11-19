"""Pre-registered test: currency carry on IG's FX futures (forwards).

Written 2026-11-19, BEFORE any of the data below was fetched (series availability only was checked) or any result
seen. Run once: `python3 -m ig_swing.carry_test`. Nothing below may change after the run.

Question (the user's): is there anything different from holding the S&P that could match it? Currency carry earns
the interest-rate gap between currencies; it is a different return source from shares (though it has crashed with
them, as in 2008).

Data (free): FRED month-end values of the daily dollar rates DEXUSEU, DEXUSUK, DEXJPUS, DEXUSAL, DEXCAUS, DEXSZUS,
DEXUSNZ; FRED 3-month interbank rates IR3TIB01{AU,CA,CH,GB,JP,NZ,EZ,US}M156N, falling back to IRSTCI01 (the
immediate rate) for a month the 3-month series lacks, the last value carried forward up to 12 months (the euro area
and UK 3-month series end in January 2026). Benchmark: Yahoo ^SP500TR less the 3-month T-bill (FRED DTB3).
Period: 2000-01 to 2026-10 (monthly).

Strategy (the textbook one): at each month end rank the eight currencies (USD, EUR, GBP, JPY, AUD, CAD, CHF, NZD) by
their 3-month rate; hold the top three long and the bottom three short against the dollar for the next month, one
third each side (the dollar's own slot is cash). A long in currency i earns its spot change against the dollar plus
(rate_i - rate_USD) / 12, which is what an IG FX forward's price carries; a short earns the negative.

Costs (IG, observed 11 Nov 2026 on the FX forwards): EUR 0.098%, GBP 0.079%, JPY 0.063%, AUD, CAD, CHF and NZD 0.10%
(not observed, set at the widest seen). Half the spread on each change of position at a rebalance, and one full
spread on each open position at every quarterly roll (March, June, September, December month ends). No funding
(forwards). A run at 2x the spreads is used in bar 2.

Primary version (V2): the monthly return scaled to a 10% a year volatility target with the trailing 12 to 36 months
of the unscaled return, leverage between 1 and 3 (1 until 12 months exist), costs scaled with it. Comparison (V1):
unscaled. Both reported; the verdict is V2.

Bars (both needed for V2):
  1. The user's own bar (set 2026-11-11 for the trend sleeve): net return over cash of at least 4% a year in EVERY
     rolling 120-month window.
  2. Return per unit of risk (annualised mean / volatility of monthly returns over cash) at least that of the
     S&P 500 with dividends over cash, over the same months, at 1x and at 2x the spreads.
Also reported: net return a year, volatility, worst drawdown, 2008, 2020 and 2022, the 2000s, 2010s and 2020s, the
correlation with the S&P, turnover and cost a year. No significance claimed. One run; any variant needs a new
pre-registration.
"""
from __future__ import annotations

import json
import math
import sys
from datetime import date

from . import options_test as ot

CCY = ["EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD"]
FX = {"EUR": ("DEXUSEU", False), "GBP": ("DEXUSUK", False), "JPY": ("DEXJPUS", True), "AUD": ("DEXUSAL", False),
      "CAD": ("DEXCAUS", True), "CHF": ("DEXSZUS", True), "NZD": ("DEXUSNZ", False)}   # True: quoted as units per USD
RATE = {"USD": "US", "EUR": "EZ", "GBP": "GB", "JPY": "JP", "AUD": "AU", "CAD": "CA", "CHF": "CH", "NZD": "NZ"}
SPREAD = {"EUR": 0.00098, "GBP": 0.00079, "JPY": 0.00063, "AUD": 0.0010, "CAD": 0.0010, "CHF": 0.0010, "NZD": 0.0010}
START, END = "2000-01", "2026-10"


def month_end(daily: dict[str, float]) -> dict[str, float]:
    out: dict[str, float] = {}
    for d in sorted(daily):
        out[d[:7]] = daily[d]
    return out


def rates_monthly(c: str) -> dict[str, float]:
    a = ot.fetch_fred(f"IR3TIB01{RATE[c]}M156N")
    b = ot.fetch_fred(f"IRSTCI01{RATE[c]}M156N")
    out = {}
    months = sorted({k[:7] for k in list(a) + list(b)})
    last, age = None, 99
    y, m = int(months[0][:4]), int(months[0][5:])
    while f"{y:04d}-{m:02d}" <= END:
        key = f"{y:04d}-{m:02d}"
        v = a.get(key + "-01", b.get(key + "-01"))
        if v is not None:
            last, age = v, 0
        else:
            age += 1
        if last is not None and age <= 12:
            out[key] = last
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def months_between(a: str, b: str) -> list[str]:
    out, y, m = [], int(a[:4]), int(a[5:])
    while f"{y:04d}-{m:02d}" <= b:
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def ratio(rets: list[float]) -> float:
    mu = sum(rets) / len(rets)
    sd = math.sqrt(sum((r - mu) ** 2 for r in rets) / (len(rets) - 1))
    return mu * 12 / (sd * math.sqrt(12))


def run(cmult: float = 1.0) -> dict:
    spot = {}
    for c, (sid, inv) in FX.items():
        me = month_end(ot.fetch_fred(sid))
        spot[c] = {k: (1.0 / v if inv else v) for k, v in me.items() if v}   # USD per unit of c
    rates = {c: rates_monthly(c) for c in ["USD"] + CCY}
    months = months_between("1999-12", END)
    raw, costs, weights_hist = [], [], []
    prev_w = {c: 0.0 for c in CCY}
    for i in range(len(months) - 1):
        m0, m1 = months[i], months[i + 1]
        have = [c for c in ["USD"] + CCY if m0 in rates[c] and (c == "USD" or (m0 in spot[c] and m1 in spot[c]))]
        if len(have) < 7:
            raw.append(None)
            costs.append(0.0)
            continue
        ranked = sorted(have, key=lambda c: rates[c][m0], reverse=True)
        w = {c: 0.0 for c in CCY}
        for c in ranked[:3]:
            if c != "USD":
                w[c] += 1 / 3
        for c in ranked[-3:]:
            if c != "USD":
                w[c] -= 1 / 3
        cost = sum(abs(w[c] - prev_w[c]) * SPREAD[c] / 2 for c in CCY) * cmult
        if m1[5:] in ("03", "06", "09", "12"):
            cost += sum(abs(w[c]) * SPREAD[c] for c in CCY) * cmult
        ret = sum(w[c] * (spot[c][m1] / spot[c][m0] - 1 + (rates[c][m0] - rates["USD"][m0]) / 1200) for c in CCY)
        raw.append(ret)
        costs.append(cost)
        weights_hist.append(sum(abs(w[c] - prev_w[c]) for c in CCY))
        prev_w = w
    keep = [(months[i + 1], raw[i], costs[i]) for i in range(len(raw)) if raw[i] is not None and START <= months[i + 1] <= END]
    v1 = [(m, r - c) for m, r, c in keep]
    v2, hist = [], []
    for m, r, c in keep:
        if len(hist) >= 12:
            h = hist[-36:]
            mu = sum(h) / len(h)
            sd = math.sqrt(sum((x - mu) ** 2 for x in h) / (len(h) - 1)) * math.sqrt(12)
            lev = min(3.0, max(1.0, 0.10 / sd)) if sd > 0 else 1.0
        else:
            lev = 1.0
        v2.append((m, lev * (r - c)))
        hist.append(r)
    return {"v1": v1, "v2": v2, "turnover_year": sum(weights_hist) / len(weights_hist) * 12, "cost_year": sum(c for _, _, c in keep) / len(keep) * 12}


def stats(series: list[tuple[str, float]], bench: dict[str, float]) -> dict:
    rets = [r for _, r in series]
    eq, peak, dd = 1.0, 1.0, 0.0
    for r in rets:
        eq *= 1 + r
        peak = max(peak, eq)
        dd = max(dd, 1 - eq / peak)
    years = len(rets) / 12
    roll = []
    for i in range(0, len(rets) - 119):
        g = 1.0
        for r in rets[i:i + 120]:
            g *= 1 + r
        roll.append(g ** (1 / 10) - 1)
    by = {m: r for m, r in series}

    def span(y0, y1):
        g = 1.0
        for m, r in series:
            if y0 <= int(m[:4]) <= y1:
                g *= 1 + r
        n = sum(1 for m, _ in series if y0 <= int(m[:4]) <= y1)
        return g ** (12 / n) - 1 if n else float("nan")

    common = [m for m, _ in series if m in bench]
    a = [by[m] for m in common]
    b = [bench[m] for m in common]
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    corr = cov / math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b))
    return {"cagr": eq ** (1 / years) - 1, "vol": math.sqrt(sum((r - sum(rets) / len(rets)) ** 2 for r in rets) / (len(rets) - 1)) * math.sqrt(12),
            "ratio": ratio(rets), "max_dd": dd, "worst_10y": min(roll) if roll else float("nan"),
            "share_10y_4pc": (sum(1 for x in roll if x >= 0.04) / len(roll)) if roll else float("nan"),
            "y2008": span(2008, 2008), "y2020": span(2020, 2020), "y2022": span(2022, 2022),
            "d2000s": span(2000, 2009), "d2010s": span(2010, 2019), "d2020s": span(2020, 2026), "corr_sp": corr}


def benchmark() -> dict[str, float]:
    tr = month_end({k: v[1] for k, v in ot.fetch_yahoo("^SP500TR").items()})
    tb = month_end(ot.fetch_fred("DTB3"))
    ms = sorted(tr)
    return {m1: tr[m1] / tr[m0] - 1 - tb.get(m0, 0.0) / 1200 for m0, m1 in zip(ms, ms[1:]) if START <= m1 <= END}


def main(argv: list[str] | None = None) -> int:
    bench = benchmark()
    base, x2 = run(1.0), run(2.0)
    bstats = {"ratio": ratio(list(bench.values()))}
    out = {}
    print(f"Currency carry on IG FX forwards, {START} to {END}, monthly, returns over cash\n")
    print(f"S&P 500 with dividends, over cash: return per unit of risk {bstats['ratio']:.2f}\n")
    print(f"{'':4}{'net/yr':>8}{'vol':>7}{'ratio':>7}{'worst DD':>9}{'worst 10y':>10}{'10y>=4%':>8}{'2008':>8}{'2020':>8}{'2022':>8}{'2000s':>8}{'2010s':>8}{'2020s':>8}{'corr S&P':>9}")
    for v in ("v2", "v1"):
        s = stats(base[v], bench)
        out[v] = s
        print(f"{v.upper():4}{s['cagr']*100:>7.1f}%{s['vol']*100:>6.1f}%{s['ratio']:>7.2f}{s['max_dd']*100:>8.0f}%{s['worst_10y']*100:>9.1f}%{s['share_10y_4pc']*100:>7.0f}%"
              f"{s['y2008']*100:>7.1f}%{s['y2020']*100:>7.1f}%{s['y2022']*100:>7.1f}%{s['d2000s']*100:>7.1f}%{s['d2010s']*100:>7.1f}%{s['d2020s']*100:>7.1f}%{s['corr_sp']:>9.2f}")
    s2x = stats(x2["v2"], bench)
    b1 = out["v2"]["worst_10y"] >= 0.04
    b2 = out["v2"]["ratio"] >= bstats["ratio"] and s2x["ratio"] >= bstats["ratio"]
    print(f"\nBar 1 (V2 at least 4% a year over cash in every 10-year window): {'PASS' if b1 else 'FAIL'} (worst window {out['v2']['worst_10y']*100:.1f}%)")
    print(f"Bar 2 (V2 return per unit of risk at least the S&P's, at 1x and 2x spreads): {'PASS' if b2 else 'FAIL'} ({out['v2']['ratio']:.2f} and {s2x['ratio']:.2f} vs {bstats['ratio']:.2f})")
    print(f"VERDICT: {'PASS' if b1 and b2 else 'FAIL'}")
    print(f"Turnover {base['turnover_year']:.1f} x a year (unscaled), cost {base['cost_year']*100:.2f}% a year (unscaled)")
    ot.CACHE.mkdir(parents=True, exist_ok=True)
    (ot.CACHE / "carry_result.json").write_text(json.dumps({"stats": out, "x2": s2x, "bench": bstats}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
