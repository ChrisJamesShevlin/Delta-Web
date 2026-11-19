"""Stop-rule comparison (run once on 2026-11-19; results in CLAUDE.md). Usage: python3 -m ig_swing.stop_rule_test

Stop-rule comparison on the trend playbook. PRE-REGISTERED here, written 2026-11-19 BEFORE any data was fetched or result seen.

Question: which stop rule gives the best trade-off between money made (net R per trade) and the user's caveat, the share of trades that
were clearly in profit (+0.75R or more) and then ended at zero or worse ("gave a good gain back to nothing")?

Data: Yahoo daily bars (free, no IG allowance), 2016-01-01 to today, 23 markets standing in for IG's daily bets (cash indices, spot FX,
front futures). Yahoo is not IG's instrument: levels and gaps differ.
Entries (fixed, the trend playbook as traded): close beyond the prior 20 sessions' high (long) or low (short); enter at the next open;
stop distance 1R = 1.3 x ATR(14, Wilder) at the signal close. One trade per market at a time. Cost 0.05R per trade.
Bars: the stop in force at the start of a day is checked first against that day's adverse extreme (a gap through it fills at the open),
then the target; only then does the day's best price update the stop for the NEXT day (we cannot trail inside a day on daily bars, so
this is conservative compared with several checks a day).
Variants (7, fixed now; no others will be added after seeing results):
 V0 fixed: stop -1R, target +2R, exit at the 5th session's close
 V1 breakeven at +1R (to 18 Nov)
 V2 breakeven at +0.75R (18 to 19 Nov)
 V3 current rule (19 Nov): breakeven at +0.5R; from +0.75R lock max(+0.25R, best - 0.75R)
 V4 lock half: breakeven at +0.5R; from +0.5R lock half of the best
 V5 wide trail: breakeven at +1R; from +1R trail 1R behind the best
 V6 current rule, no target, 10 sessions (let winners run)
Measures: n, net mean R with SE (and SE clustered by month, since breakouts cluster), win rate, share that reached +0.75R and ended <= 0
("giveback to nothing"), mean R given back from the best among trades that reached +1R, and the two halves 2016-2020 / 2021-2026.
Reading: no variant is "proven" unless its difference from V0 exceeds 2 clustered SE; the choice between them is a preference trade-off
the user makes with these numbers, and that is said plainly.
"""
from __future__ import annotations

import json
import math
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

import requests

CACHE = Path(__file__).resolve().parent.parent / "data" / "history" / "yahoo_daily"   # gitignored
CACHE.mkdir(exist_ok=True)
SYMBOLS = ["^GSPC", "^NDX", "^DJI", "^RUT", "^GDAXI", "^FTSE", "^FCHI", "^N225", "EURUSD=X", "GBPUSD=X", "USDJPY=X", "AUDUSD=X", "USDCAD=X",
           "USDCHF=X", "NZDUSD=X", "EURGBP=X", "EURJPY=X", "GBPJPY=X", "GC=F", "SI=F", "CL=F", "BZ=F", "NG=F"]
COST = 0.05


def fetch(sym: str) -> list[dict]:
    f = CACHE / (sym.replace("^", "_").replace("=", "_") + ".json")
    if f.exists():
        return json.loads(f.read_text())
    p1 = int(datetime(2015, 6, 1, tzinfo=timezone.utc).timestamp())
    r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}", params={"period1": p1, "period2": int(time.time()), "interval": "1d"},
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    rows = []
    for i, ts in enumerate(res["timestamp"]):
        o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
        if None in (o, h, l, c) or h < l or o <= 0:
            continue
        rows.append({"d": datetime.fromtimestamp(ts, timezone.utc).date().isoformat(), "o": o, "h": h, "l": l, "c": c})
    today = date.today().isoformat()
    rows = [r_ for r_ in rows if r_["d"] < today]
    f.write_text(json.dumps(rows))
    time.sleep(0.4)
    return rows


def atr(rows: list[dict], n: int = 14) -> list[float | None]:
    out, a = [None] * len(rows), None
    for i in range(1, len(rows)):
        tr = max(rows[i]["h"] - rows[i]["l"], abs(rows[i]["h"] - rows[i - 1]["c"]), abs(rows[i]["l"] - rows[i - 1]["c"]))
        a = tr if a is None else (a * (n - 1) + tr) / n
        out[i] = a if i >= n else None
    return out


def stop_rule(v: str, best: float) -> float | None:
    """The stop, in R from entry, implied by the best R reached so far (None = keep the initial -1R)."""
    if v == "V0":
        return None
    if v == "V1":
        return 0.0 if best >= 1 else None
    if v == "V2":
        return 0.0 if best >= 0.75 else None
    if v in ("V3", "V6"):
        if best >= 0.75:
            return max(0.25, best - 0.75)
        return 0.0 if best >= 0.5 else None
    if v == "V4":
        return best / 2 if best >= 0.5 else None
    if v == "V5":
        return best - 1.0 if best >= 1 else None
    raise ValueError(v)


def simulate(rows: list[dict], i0: int, side: int, risk: float, v: str) -> tuple[float, float]:
    """Returns (R net of cost, best R reached). i0 = entry day index (enter at its open)."""
    entry = rows[i0]["o"]
    target = None if v == "V6" else 2.0
    horizon = 10 if v == "V6" else 5
    stop_r, best = -1.0, 0.0
    for k in range(horizon):
        i = i0 + k
        if i >= len(rows):
            return (rows[-1]["c"] - entry) * side / risk - COST, best
        b = rows[i]
        o_r = (b["o"] - entry) * side / risk
        adverse = ((b["l"] if side > 0 else b["h"]) - entry) * side / risk
        fav = ((b["h"] if side > 0 else b["l"]) - entry) * side / risk
        if k > 0 and o_r <= stop_r:
            return o_r - COST, best
        if adverse <= stop_r:
            return stop_r - COST, best
        if target is not None:
            if k > 0 and o_r >= target:
                return o_r - COST, max(best, o_r)
            if fav >= target:
                return target - COST, max(best, target)
        best = max(best, fav)
        s = stop_rule(v, best)
        if s is not None:
            stop_r = max(stop_r, s)
    return (rows[i0 + horizon - 1]["c"] - entry) * side / risk - COST, best


def trades_for(rows: list[dict]) -> list[tuple[int, int, float, str]]:
    a = atr(rows)
    out, busy_until = [], -1
    for t in range(21, len(rows) - 1):
        if t <= busy_until or a[t] is None:
            continue
        hi = max(r["h"] for r in rows[t - 20:t])
        lo = min(r["l"] for r in rows[t - 20:t])
        side = 1 if rows[t]["c"] > hi else -1 if rows[t]["c"] < lo else 0
        if side:
            out.append((t + 1, side, 1.3 * a[t], rows[t + 1]["d"]))
            busy_until = t + 10          # one trade per market at a time (longest variant holds 10 sessions)
    return out


def stats(res: list[tuple[float, float, str]]) -> dict:
    n = len(res)
    rs = [r for r, _, _ in res]
    m = sum(rs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in rs) / (n - 1))
    months: dict[str, list[float]] = {}
    for r, _, d in res:
        months.setdefault(d[:7], []).append(r - m)
    se_cl = math.sqrt(sum(sum(v) ** 2 for v in months.values())) / n
    good = [(r, b) for r, b, _ in res if b >= 0.75]
    big = [(r, b) for r, b, _ in res if b >= 1.0]
    return {"n": n, "mean": m, "se": sd / math.sqrt(n), "se_cl": se_cl, "win": sum(1 for x in rs if x > 0) / n,
            "giveback_to_nothing": (sum(1 for r, _ in good if r <= 0) / len(good)) if good else float("nan"), "n_good": len(good),
            "given_back_from_best": (sum(b - (r + COST) for r, b in big) / len(big)) if big else float("nan")}


def main() -> int:
    data = {}
    for s in SYMBOLS:
        try:
            data[s] = fetch(s)
        except Exception as exc:
            print(f"{s}: fetch failed ({type(exc).__name__})", file=sys.stderr)
    variants = ["V0", "V1", "V2", "V3", "V4", "V5", "V6"]
    res = {v: [] for v in variants}
    for s, rows in data.items():
        if len(rows) < 300:
            continue
        for i0, side, risk, d in trades_for(rows):
            if risk <= 0:
                continue
            for v in variants:
                r, b = simulate(rows, i0, side, risk, v)
                res[v].append((r, b, d))
    out = {}
    for v in variants:
        out[v] = {"all": stats(res[v]), "2016-2020": stats([x for x in res[v] if x[2] < "2021"]), "2021-2026": stats([x for x in res[v] if x[2] >= "2021"])}
    base = res["V0"]
    for v in variants:                      # paired difference from V0, clustered by month
        diffs = [(a[0] - b[0], a[2]) for a, b in zip(res[v], base)]
        n = len(diffs)
        md = sum(x for x, _ in diffs) / n
        months: dict[str, float] = {}
        for x, d in diffs:
            months[d[:7]] = months.get(d[:7], 0.0) + (x - md)
        out[v]["diff_vs_V0"] = {"mean": md, "se_cl": math.sqrt(sum(val ** 2 for val in months.values())) / n}
    out["_markets"] = {s: len(r) for s, r in data.items()}
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
