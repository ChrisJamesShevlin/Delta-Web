"""Pre-registered test: selling US 500 options on IG spread-bet terms.

Written 2026-11-19, BEFORE any of the data below was fetched or any result seen. Run once with
`python3 -m ig_swing.options_test`. Nothing in FIXED, the strategy rules or the bars may change after the run;
any variant needs a new pre-registration with the variant count stated (seven strategies here, plus the
sensitivity runs listed below, which are descriptive only).

Question (the user's, 2026-11-19): with an account big enough, would short strangles or similar premium-selling
structures on IG's US 500 options have made money after IG's costs, and how big would the account need to be to
have survived every bad period?

Data (free, no IG allowance): Yahoo daily ^GSPC (open and close), ^VIX9D, ^VIX, ^VIX3M, ^VIX6M, ^SP500TR; FRED DTB3
(3-month T-bill). Main period 2008-01-02 to 2026-10-30. A longer descriptive run of S1 to S5 from 1990-01-02 uses
^VIX only.

Prices are model estimates, not real quotes:
- Black-76 on the forward F = S exp((r - q) T), r = DTB3, q = 1.5% a year fixed, no discounting of the premium.
  T = calendar days to the settlement day / 365.
- At-the-money implied volatility for T: 0.90 x the volatility index interpolated linearly in total variance
  (vol^2 x T) through the points 9 days (VIX9D), 30 (VIX), 93 (VIX3M) and 182 (VIX6M) that exist that day, flat
  beyond the ends. A missing value carries the last one forward.
- Skew, from IG's own ticket on 19 Nov (Weekly US 500, 4 days out: 7000 put 29.6%, 7100 put 27.0%, 7175 put 25.2%,
  at the money 23.5%, 7330 call 21.6%): vol(K) = ATM x clip(1 + B x, 0.6, 2.0), x = ln(K/F) / (ATM sqrt(T)),
  B = -0.178 (fits the four ticket points within about 0.2 vol points). Sensitivity runs at B x 0.5 and B x 2.
- Delta from the same model. Strikes on a grid of 25 points (5 points when the index is under 2,000), the strike
  whose delta is nearest the target.
- Expiries: monthly = the third Friday, settled on that day's OPENING price (IG's SET), moved to the last trading
  day on or before it; weekly (S6 short leg only) = a Friday, settled on its close.

Costs, IG's spread-bet terms: half the spread on every leg opened and every leg closed, nothing on a leg settled.
Spread (points) = min(S_T, max(0.4, 0.25 x option price)) with S_T by days to expiry: up to 7 days 1.12, 8 to 20
1.96, 21 to 45 2.52, 46 to 75 2.60, 76 to 120 3.64, over 120 4.20 (IG US 500 quotes seen on 19 Nov). Sensitivity:
spread x 1.5 (used in bar 1). No funding charge. Stake £1 a point throughout.

Margin: 5% of the index x stake for every short leg (confirmed on IG's ticket, 19 Nov), never reduced for a bought
leg (IG's netting is unknown). A bought leg's price is paid from the account. Positions are marked at the model's
mid price each close. Required account C = the largest value over the period of (margin needed - cumulative profit
and loss), so the account never falls below its margin at a close. Daily closes only: moves within a day are not
seen, so the true worst case is worse.

Strategies (one position cycle at a time per strategy; a new cycle opens at the close of the first trading day of
each month when flat; decisions at the close):
  S1 short strangle: monthly nearest 45 days; sell the 16-delta put and the 16-delta call. Close all at +50% of the
     credit taken in, or when 21 days are left. No rolling.
  S2 S1 plus rolling: when a short leg reaches 30 delta, buy back the other short leg and sell a new 16-delta one at
     the same expiry, and buy back the tested leg and sell a new 16-delta one at the next monthly expiry, only if
     the whole roll is for a net credit. Close all at +50% of all credits taken in, at 21 days left on the nearest
     leg, or when the cycle's loss reaches 3 times all credits taken in.
  S3 iron condor: S1 plus buy the 5-delta put and the 5-delta call, same expiry. Exits as S1 on the net credit.
  S4 put credit spread: sell the 25-delta put, buy the 10-delta put, monthly nearest 45 days. Exits as S1.
  S5 short put: sell the 30-delta put, monthly nearest 45 days. Exits as S1.
  S6 put calendar: sell the at-the-money put on the Friday weekly nearest 14 days (at least 7), buy the same strike
     on the monthly nearest 45 days (at least 30, after the weekly). Close all at +25% or -50% of the debit paid, or
     at the close before the short leg settles.
  S7 bullish call diagonal: buy the 70-delta call on the monthly nearest 90 days (at least 60), sell the 30-delta
     call on the monthly nearest 30 days (at least 14). When the short leg settles, sell a new 30-delta call on the
     monthly nearest 30 days that settles before the long leg. Close all at -50% of the first debit or when the
     long leg has 30 days left. Directional by design.

Measured per strategy (main period): profit and loss in £ a year at £1 a point, account needed C, return a year on
C, volatility and return per unit of risk of the monthly return on C, worst month, worst drawdown of the account
(C + profit and loss) and months to recover, 2008, Feb 2018, Mar 2020 and 2022 on their own, share of months
positive, share of rolling 36-month windows positive, and for S2 how often a roll was followed by a better or worse
end to the cycle than its loss at the first roll. Benchmark: ^SP500TR (US 500 with dividends) monthly.

Pass bars (set by the user 2026-11-19, all four needed to go to phase 2, the forward shadow book):
  1. Net profit over the main period after IG's costs, also at 1.5 x the spread.
  2. Worst drawdown of the account no more than 50% of its peak.
  3. Higher return per unit of risk (annualised mean / volatility of monthly returns) than the ^SP500TR benchmark.
  4. Profit in at least two of 2008 to 2012, 2013 to 2019 and 2020 to Oct 2026.
A pass that turns into a loss at half or double the skew is flagged as fragile. S6 and S7 depend most on the term
structure model and are labelled model-sensitive whatever they show. No statistical significance is claimed: option
selling returns are heavily skewed and 19 years is a small sample. The 1990 run is descriptive only.
"""
from __future__ import annotations

import json
import math
import sys
import time
from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "history" / "options_test"   # gitignored
FRED_URL = "https://api.stlouisfed.org/fred/series/observations"

FIXED = {
    "q": 0.015, "atm_scale": 0.90, "skew_b": -0.178, "skew_clip": (0.6, 2.0), "margin": 0.05,
    "spread_by_days": [(7, 1.12), (20, 1.96), (45, 2.52), (75, 2.60), (120, 3.64), (10**6, 4.20)],
    "spread_floor": 0.4, "spread_share": 0.25,
    "main": ("2008-01-02", "2026-10-30"), "long": ("1990-01-02", "2026-10-30"),
}
YAHOO = ["^GSPC", "^VIX9D", "^VIX", "^VIX3M", "^VIX6M", "^SP500TR"]
VOL_POINTS = [(9, "^VIX9D"), (30, "^VIX"), (93, "^VIX3M"), (182, "^VIX6M")]
STRATEGIES = ["S1", "S2", "S3", "S4", "S5", "S6", "S7"]
NAMES = {"S1": "Short strangle", "S2": "Short strangle + rolling", "S3": "Iron condor", "S4": "Put credit spread",
         "S5": "Short put", "S6": "Put calendar", "S7": "Call diagonal"}


# ---------- data ----------

def fetch_yahoo(sym: str) -> dict[str, tuple[float, float]]:
    """date -> (open, close), from 1989-12-01, cached."""
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / (sym.replace("^", "_") + ".json")
    if f.exists():
        return {k: tuple(v) for k, v in json.loads(f.read_text()).items()}
    p1 = int(datetime(1989, 12, 1, tzinfo=timezone.utc).timestamp())
    r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}", params={"period1": p1, "period2": int(time.time()), "interval": "1d"},
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    out: dict[str, tuple[float, float]] = {}
    for i, ts in enumerate(res.get("timestamp") or []):
        o, c = q["open"][i], q["close"][i]
        if c is None or c <= 0:
            continue
        d = datetime.fromtimestamp(ts + 6 * 3600, timezone.utc).date().isoformat()   # US session dates
        out[d] = (float(o) if o else float(c), float(c))
    f.write_text(json.dumps(out))
    time.sleep(0.5)
    return out


def fetch_fred(series: str) -> dict[str, float]:
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f"fred_{series}.json"
    if f.exists():
        return json.loads(f.read_text())
    from .config import optional_key
    key = optional_key("FRED_API_KEY")
    if not key:
        raise SystemExit("FRED_API_KEY is not set")
    r = requests.get(FRED_URL, params={"series_id": series, "api_key": key, "file_type": "json", "observation_start": "1989-12-01"}, timeout=60)
    r.raise_for_status()
    out = {}
    for o in r.json().get("observations", []):
        try:
            out[o["date"]] = float(o["value"])
        except (KeyError, ValueError):
            continue
    f.write_text(json.dumps(out))
    return out


# ---------- pricing ----------

def ncdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def atm_vol(vols: dict[int, float], t_days: float, scale: float = FIXED["atm_scale"]) -> float:
    """vols: days -> index level in percent. Linear in total variance, flat beyond the ends. Returns a decimal."""
    pts = sorted((d, v / 100.0) for d, v in vols.items() if v and v > 0)
    if not pts:
        raise ValueError("no volatility points")
    t = max(t_days, 0.5)
    if t <= pts[0][0]:
        v = pts[0][1]
    elif t >= pts[-1][0]:
        v = pts[-1][1]
    else:
        for (d0, v0), (d1, v1) in zip(pts, pts[1:]):
            if d0 <= t <= d1:
                w = v0 * v0 * d0 + (v1 * v1 * d1 - v0 * v0 * d0) * (t - d0) / (d1 - d0)
                v = math.sqrt(w / t)
                break
    return scale * v


def strike_vol(atm: float, f: float, k: float, t: float, b: float) -> float:
    x = math.log(k / f) / (atm * math.sqrt(t))
    lo, hi = FIXED["skew_clip"]
    return atm * min(hi, max(lo, 1.0 + b * x))


def black(kind: str, f: float, k: float, t: float, vol: float) -> tuple[float, float]:
    """(price, delta) for a call 'C' or put 'P' on the forward, undiscounted."""
    t = max(t, 0.5 / 365)
    sd = vol * math.sqrt(t)
    d1 = (math.log(f / k) + 0.5 * sd * sd) / sd
    d2 = d1 - sd
    if kind == "C":
        return f * ncdf(d1) - k * ncdf(d2), ncdf(d1)
    return k * ncdf(-d2) - f * ncdf(-d1), ncdf(d1) - 1.0


def spread_points(price: float, days: float, mult: float = 1.0) -> float:
    st = next(s for d, s in FIXED["spread_by_days"] if days <= d)
    return mult * min(st, max(FIXED["spread_floor"], FIXED["spread_share"] * price))


def step_for(s: float) -> float:
    return 25.0 if s >= 2000 else 5.0


# ---------- calendar ----------

def third_friday(y: int, m: int) -> date:
    d = date(y, m, 1)
    first_fri = d + timedelta(days=(4 - d.weekday()) % 7)
    return first_fri + timedelta(days=14)


def settle_day(target: date, days: list[date]) -> date | None:
    """The last trading day on or before target (days sorted)."""
    lo, hi = 0, len(days) - 1
    best = None
    while lo <= hi:
        mid = (lo + hi) // 2
        if days[mid] <= target:
            best = days[mid]
            lo = mid + 1
        else:
            hi = mid - 1
    return best


# ---------- simulation ----------

@dataclass
class Leg:
    kind: str          # 'C' or 'P'
    k: float
    settle: date       # trading day it settles
    at_open: bool      # monthly (opening print) or weekly (close)
    qty: int           # +1 long, -1 short


@dataclass
class Book:
    cash: float = 0.0
    legs: list = field(default_factory=list)
    cycle_start_eq: float = 0.0
    credit: float = 0.0          # credits taken in this cycle (net, after costs); debit stored as negative
    first_debit: float = 0.0
    rolled_at: float | None = None
    open_cycle: bool = False


class Market:
    def __init__(self, data: dict, rates: dict[str, float], vol_syms: list[tuple[int, str]]):
        self.days = sorted(date.fromisoformat(d) for d in data["^GSPC"])
        self.px = {date.fromisoformat(d): v for d, v in data["^GSPC"].items()}
        self.vol_syms = vol_syms
        self.vols: dict[date, dict[int, float]] = {}
        last: dict[int, float] = {}
        rate_items = sorted((date.fromisoformat(d), v) for d, v in rates.items())
        self.rate: dict[date, float] = {}
        ri, last_r = 0, 0.0
        for d in self.days:
            for t, sym in vol_syms:
                v = data.get(sym, {}).get(d.isoformat())
                if v:
                    last[t] = v[1]
            self.vols[d] = dict(last)
            while ri < len(rate_items) and rate_items[ri][0] <= d:
                last_r = rate_items[ri][1] / 100.0
                ri += 1
            self.rate[d] = last_r
        self.fridays = {}

    def monthly(self, d: date, target: int, min_days: int, before: date | None = None, after: date | None = None) -> date | None:
        cands = []
        for k in range(0, 9):
            y, m = d.year + (d.month - 1 + k) // 12, (d.month - 1 + k) % 12 + 1
            sd = settle_day(third_friday(y, m), self.days) if third_friday(y, m) <= self.days[-1] else third_friday(y, m)
            if sd is None:
                continue
            dd = (sd - d).days
            if dd < min_days or (before and sd >= before) or (after and sd <= after):
                continue
            cands.append((abs(dd - target), sd))
        return min(cands)[1] if cands else None

    def weekly(self, d: date, target: int, min_days: int) -> date | None:
        cands = []
        for k in range(1, 30):
            day = d + timedelta(days=k)
            if day.weekday() != 4:
                continue
            sd = settle_day(day, self.days) if day <= self.days[-1] else day
            dd = (sd - d).days
            if dd >= min_days:
                cands.append((abs(dd - target), sd))
        return min(cands)[1] if cands else None

    def quote(self, d: date, leg_kind: str, k: float, settle: date, b: float) -> tuple[float, float, float]:
        """(mid, delta, days) at the close of d."""
        s = self.px[d][1]
        days = max((settle - d).days, 0.5)
        t = days / 365.0
        f = s * math.exp((self.rate[d] - FIXED["q"]) * t)
        atm = atm_vol(self.vols[d], days)
        vol = strike_vol(atm, f, k, t, b)
        p, dl = black(leg_kind, f, k, t, vol)
        return max(p, 0.0), dl, days

    def strike_for_delta(self, d: date, kind: str, settle: date, target: float, b: float) -> float:
        s = self.px[d][1]
        step = step_for(s)
        base = round(s / step) * step
        best, best_err = base, 9.0
        for i in range(-int(0.45 * s / step), int(0.45 * s / step) + 1):
            k = base + i * step
            if k <= 0:
                continue
            _, dl, _ = self.quote(d, kind, k, settle, b)
            err = abs(abs(dl) - target)
            if err < best_err:
                best, best_err = k, err
        return best


def trade(book: Book, mkt: Market, d: date, leg: Leg, opening: bool, b: float, smult: float) -> float:
    """Open or close one leg at the model mid +/- half the spread. Returns the cash change."""
    mid, _, days = mkt.quote(d, leg.kind, leg.k, leg.settle, b)
    h = spread_points(mid, days, smult) / 2
    buying = (leg.qty > 0) == opening
    price = mid + h if buying else max(mid - h, 0.0)
    cash = -price if buying else price
    book.cash += cash
    if opening:
        book.legs.append(leg)
    else:
        book.legs.remove(leg)
    return cash


def equity(book: Book, mkt: Market, d: date, b: float) -> float:
    return book.cash + sum(l.qty * mkt.quote(d, l.kind, l.k, l.settle, b)[0] for l in book.legs)


def close_all(book: Book, mkt: Market, d: date, b: float, smult: float) -> None:
    for l in list(book.legs):
        trade(book, mkt, d, l, False, b, smult)
    book.open_cycle = False


def open_cycle(sid: str, book: Book, mkt: Market, d: date, b: float, smult: float) -> None:
    eq0 = equity(book, mkt, d, b)
    legs: list[Leg] = []
    if sid in ("S1", "S2", "S3", "S4", "S5"):
        ex = mkt.monthly(d, 45, 22)
        if ex is None:
            return
        spec = {"S1": [("P", 0.16, -1), ("C", 0.16, -1)], "S2": [("P", 0.16, -1), ("C", 0.16, -1)],
                "S3": [("P", 0.16, -1), ("C", 0.16, -1), ("P", 0.05, 1), ("C", 0.05, 1)],
                "S4": [("P", 0.25, -1), ("P", 0.10, 1)], "S5": [("P", 0.30, -1)]}[sid]
        for kind, dl, q in spec:
            legs.append(Leg(kind, mkt.strike_for_delta(d, kind, ex, dl, b), ex, True, q))
    elif sid == "S6":
        wk = mkt.weekly(d, 14, 7)
        if wk is None:
            return
        mo = mkt.monthly(d, 45, 30, after=wk)
        if mo is None:
            return
        s = mkt.px[d][1]
        k = round(s / step_for(s)) * step_for(s)
        legs = [Leg("P", k, wk, False, -1), Leg("P", k, mo, True, 1)]
    elif sid == "S7":
        lg = mkt.monthly(d, 90, 60)
        if lg is None:
            return
        sh = mkt.monthly(d, 30, 14, before=lg)
        if sh is None:
            return
        legs = [Leg("C", mkt.strike_for_delta(d, "C", lg, 0.70, b), lg, True, 1),
                Leg("C", mkt.strike_for_delta(d, "C", sh, 0.30, b), sh, True, -1)]
    keys = [(l.kind, l.k, l.settle) for l in legs]
    if len(set(keys)) != len(keys):
        return
    cash = sum(trade(book, mkt, d, l, True, b, smult) for l in legs)
    book.cycle_start_eq = eq0
    book.credit = cash
    book.first_debit = -cash
    book.rolled_at = None
    book.open_cycle = True


def manage(sid: str, book: Book, mkt: Market, d: date, b: float, smult: float, stats: dict) -> None:
    if not book.legs:
        book.open_cycle = False
        return
    pnl = equity(book, mkt, d, b) - book.cycle_start_eq
    days_left = min((l.settle - d).days for l in book.legs)
    if sid in ("S1", "S2", "S3", "S4", "S5"):
        if pnl >= 0.5 * book.credit or days_left <= 21 or (sid == "S2" and pnl <= -3 * book.credit):
            if sid == "S2" and book.rolled_at is not None:
                stats["rolled_cycles"] += 1
                stats["roll_better" if pnl > book.rolled_at else "roll_worse"] += 1
            close_all(book, mkt, d, b, smult)
            return
        if sid == "S2":
            shorts = [l for l in book.legs if l.qty < 0]
            tested = [l for l in shorts if abs(mkt.quote(d, l.kind, l.k, l.settle, b)[1]) >= 0.30]
            if len(tested) == 1 and len(shorts) == 2:
                t = tested[0]
                u = next(l for l in shorts if l is not t)
                nxt = mkt.monthly(t.settle + timedelta(days=1), 30, 1)
                if nxt is None:
                    return
                new_u = Leg(u.kind, mkt.strike_for_delta(d, u.kind, u.settle, 0.16, b), u.settle, True, -1)
                new_t = Leg(t.kind, mkt.strike_for_delta(d, t.kind, nxt, 0.16, b), nxt, True, -1)
                # price the roll first; only do it for a net credit
                net = 0.0
                for leg, opening in ((u, False), (t, False), (new_u, True), (new_t, True)):
                    mid, _, days = mkt.quote(d, leg.kind, leg.k, leg.settle, b)
                    h = spread_points(mid, days, smult) / 2
                    net += (mid - h) if opening else -(mid + h)
                if net > 0 and (new_u.kind, new_u.k, new_u.settle) != (u.kind, u.k, u.settle):
                    if book.rolled_at is None:
                        book.rolled_at = pnl
                    got = sum(trade(book, mkt, d, leg, op, b, smult) for leg, op in ((u, False), (t, False), (new_u, True), (new_t, True)))
                    book.credit += got
                    stats["rolls"] += 1
    elif sid == "S6":
        short = next((l for l in book.legs if l.qty < 0), None)
        if pnl >= 0.25 * book.first_debit or pnl <= -0.5 * book.first_debit or short is None or (short.settle - d).days <= 1:
            close_all(book, mkt, d, b, smult)
    elif sid == "S7":
        long_ = next((l for l in book.legs if l.qty > 0), None)
        if long_ is None or pnl <= -0.5 * book.first_debit or (long_.settle - d).days <= 30:
            close_all(book, mkt, d, b, smult)
            return
        if not any(l.qty < 0 for l in book.legs):
            sh = mkt.monthly(d, 30, 14, before=long_.settle)
            if sh is not None:
                trade(book, mkt, d, Leg("C", mkt.strike_for_delta(d, "C", sh, 0.30, b), sh, True, -1), True, b, smult)


def settle_legs(book: Book, mkt: Market, d: date, at_open: bool) -> None:
    s = mkt.px[d][0] if at_open else mkt.px[d][1]
    for l in list(book.legs):
        if l.settle == d and l.at_open == at_open:
            intrinsic = max(0.0, s - l.k) if l.kind == "C" else max(0.0, l.k - s)
            book.cash += l.qty * intrinsic
            book.legs.remove(l)


def run(sid: str, mkt: Market, start: str, end: str, b: float, smult: float) -> dict:
    book = Book()
    stats = {"rolls": 0, "rolled_cycles": 0, "roll_better": 0, "roll_worse": 0, "cycles": 0}
    d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
    days = [d for d in mkt.days if d0 <= d <= d1]
    series: list[tuple[date, float, float]] = []   # date, equity, margin
    prev_month = None
    for d in days:
        settle_legs(book, mkt, d, True)
        settle_legs(book, mkt, d, False)
        if book.open_cycle:
            manage(sid, book, mkt, d, b, smult, stats)
        if (d.year, d.month) != prev_month:
            prev_month = (d.year, d.month)
            if not book.legs:
                open_cycle(sid, book, mkt, d, b, smult)
                stats["cycles"] += book.open_cycle
        if not book.legs:
            book.open_cycle = False
        eq = equity(book, mkt, d, b)
        margin = sum(FIXED["margin"] * mkt.px[d][1] for l in book.legs if l.qty < 0)
        series.append((d, eq, margin))
    if book.legs:
        close_all(book, mkt, days[-1], b, smult)
        series[-1] = (days[-1], book.cash, 0.0)
    return {"series": series, "stats": stats}


# ---------- reporting ----------

def month_ends(series: list[tuple[date, float, float]]) -> list[tuple[date, float]]:
    out: dict[tuple[int, int], tuple[date, float]] = {}
    for d, eq, _ in series:
        out[(d.year, d.month)] = (d, eq)
    return [out[k] for k in sorted(out)]


def ratio(rets: list[float]) -> float:
    if len(rets) < 3:
        return float("nan")
    m = sum(rets) / len(rets)
    sd = math.sqrt(sum((r - m) ** 2 for r in rets) / (len(rets) - 1))
    return (m * 12) / (sd * math.sqrt(12)) if sd > 0 else float("nan")


def summarise(res: dict, years: float) -> dict:
    series = res["series"]
    c = max(1.0, max(m - eq for _, eq, m in series))
    total = series[-1][1]
    acct = [c + eq for _, eq, _ in series]
    peak, dd, dd_start, worst_rec = acct[0], 0.0, 0, 0
    peak_i, under_since = 0, None
    longest = 0
    for i, a in enumerate(acct):
        if a >= peak:
            if under_since is not None:
                longest = max(longest, i - under_since)
            peak, peak_i, under_since = a, i, None
        else:
            under_since = under_since if under_since is not None else peak_i
            dd = max(dd, (peak - a) / peak)
    if under_since is not None:
        longest = max(longest, len(acct) - under_since)
    me = month_ends(series)
    mpnl = [me[0][1]] + [b[1] - a[1] for a, b in zip(me, me[1:])]
    mrets = [x / c for x in mpnl]
    by_month = {(d.year, d.month): x for (d, _), x in zip(me, mpnl)}

    def span(y0: int, y1: int) -> float:
        return sum(x for (y, _m), x in by_month.items() if y0 <= y <= y1)

    roll36 = [sum(mpnl[i:i + 36]) for i in range(0, len(mpnl) - 35)]
    return {
        "pnl_total": total, "pnl_year": total / years, "account": c, "ret_year": total / years / c,
        "vol": (math.sqrt(sum((r - sum(mrets) / len(mrets)) ** 2 for r in mrets) / (len(mrets) - 1)) * math.sqrt(12)) if len(mrets) > 2 else float("nan"),
        "ratio": ratio(mrets), "worst_month": min(mpnl), "max_dd": dd, "longest_under_days": longest,
        "y2008": span(2008, 2008), "feb2018": by_month.get((2018, 2), 0.0), "mar2020": by_month.get((2020, 3), 0.0), "y2022": span(2022, 2022),
        "p1": span(2008, 2012), "p2": span(2013, 2019), "p3": span(2020, 2026),
        "months_pos": sum(1 for x in mpnl if x > 0) / len(mpnl), "roll36_pos": (sum(1 for x in roll36 if x > 0) / len(roll36)) if roll36 else float("nan"),
        "stats": res["stats"],
    }


def benchmark(data: dict, start: str, end: str) -> dict:
    tr = sorted((d, v[1]) for d, v in data["^SP500TR"].items() if start <= d <= end)
    me: dict[str, float] = {}
    for d, v in tr:
        me[d[:7]] = v
    vals = [me[k] for k in sorted(me)]
    rets = [b / a - 1 for a, b in zip(vals, vals[1:])]
    peak, dd = tr[0][1], 0.0
    for _, v in tr:
        peak = max(peak, v)
        dd = max(dd, (peak - v) / peak)
    years = (date.fromisoformat(tr[-1][0]) - date.fromisoformat(tr[0][0])).days / 365.25
    return {"ratio": ratio(rets), "cagr": (tr[-1][1] / tr[0][1]) ** (1 / years) - 1, "max_dd": dd}


def main(argv: list[str] | None = None) -> int:
    data = {s: fetch_yahoo(s) for s in YAHOO}
    rates = fetch_fred("DTB3")
    for s in YAHOO:
        ds = sorted(data[s])
        print(f"  data {s}: {len(ds)} days, {ds[0] if ds else '-'} to {ds[-1] if ds else '-'}")
    full = Market(data, rates, VOL_POINTS)
    vix_only = Market(data, rates, [(30, "^VIX")])
    out: dict = {"fixed": FIXED, "runs": {}}
    start, end = FIXED["main"]
    years = (date.fromisoformat(end) - date.fromisoformat(start)).days / 365.25
    bench = benchmark(data, start, end)
    out["benchmark"] = bench
    b0 = FIXED["skew_b"]
    runs = [("base", b0, 1.0), ("spread_x1.5", b0, 1.5), ("skew_half", b0 * 0.5, 1.0), ("skew_double", b0 * 2, 1.0)]
    for label, b, sm in runs:
        out["runs"][label] = {}
        for sid in STRATEGIES:
            out["runs"][label][sid] = summarise(run(sid, full, start, end, b, sm), years)
            print(f"  ran {label} {sid}", file=sys.stderr)
    ls, le = FIXED["long"]
    lyears = (date.fromisoformat(le) - date.fromisoformat(ls)).days / 365.25
    out["runs"]["long_1990_vix_only"] = {sid: summarise(run(sid, vix_only, ls, le, b0, 1.0), lyears) for sid in STRATEGIES[:5]}
    out["benchmark_long"] = benchmark(data, ls, le)

    base = out["runs"]["base"]
    print(f"\nUS 500 option selling on IG spread-bet terms, {start} to {end}, £1 a point, model prices (not real quotes)")
    print(f"Benchmark ^SP500TR: {bench['cagr']*100:+.1f}% a year, return per unit of risk {bench['ratio']:.2f}, worst drawdown {bench['max_dd']*100:.0f}%\n")
    hdr = f"{'':4}{'strategy':26}{'£/yr':>8}{'account':>9}{'ret/yr':>8}{'risk ratio':>11}{'worst DD':>9}{'worst mo':>9}{'2008':>8}{'Feb18':>8}{'Mar20':>8}{'2022':>8}{'mo +':>6}"
    print(hdr)
    for sid in STRATEGIES:
        r = base[sid]
        print(f"{sid:4}{NAMES[sid]:26}{r['pnl_year']:>8.0f}{r['account']:>9.0f}{r['ret_year']*100:>7.1f}%{r['ratio']:>11.2f}{r['max_dd']*100:>8.0f}%{r['worst_month']:>9.0f}"
              f"{r['y2008']:>8.0f}{r['feb2018']:>8.0f}{r['mar2020']:>8.0f}{r['y2022']:>8.0f}{r['months_pos']*100:>5.0f}%")
    print("\nBars (all four needed): 1 profit at 1x and 1.5x spread; 2 worst drawdown <= 50%; 3 risk ratio above the benchmark; 4 profit in 2 of 3 periods")
    for sid in STRATEGIES:
        r, r15 = base[sid], out["runs"]["spread_x1.5"][sid]
        b1 = r["pnl_total"] > 0 and r15["pnl_total"] > 0
        b2 = r["max_dd"] <= 0.5
        b3 = r["ratio"] > bench["ratio"]
        b4 = sum(1 for p in (r["p1"], r["p2"], r["p3"]) if p > 0) >= 2
        ok = b1 and b2 and b3 and b4
        fragile = ok and (out["runs"]["skew_half"][sid]["pnl_total"] <= 0 or out["runs"]["skew_double"][sid]["pnl_total"] <= 0)
        tag = "PASS" + (" (FRAGILE: loses at half or double skew)" if fragile else "") if ok else "FAIL"
        if sid in ("S6", "S7"):
            tag += " [model-sensitive]"
        print(f"  {sid} {NAMES[sid]:26} bar1 {'Y' if b1 else 'N'} bar2 {'Y' if b2 else 'N'} bar3 {'Y' if b3 else 'N'} bar4 {'Y' if b4 else 'N'}  -> {tag}"
              f"   (periods £{r['p1']:.0f} / £{r['p2']:.0f} / £{r['p3']:.0f}; at 1.5x spread £{r15['pnl_year']:.0f}/yr)")
        out["runs"]["base"][sid]["verdict"] = tag
    print("\nSensitivity (descriptive), £ a year: skew half / base / double")
    for sid in STRATEGIES:
        print(f"  {sid} {out['runs']['skew_half'][sid]['pnl_year']:>7.0f} {base[sid]['pnl_year']:>7.0f} {out['runs']['skew_double'][sid]['pnl_year']:>7.0f}")
    s2 = base["S2"]["stats"]
    print(f"\nS2 rolling: {s2['rolls']} rolls in {s2['rolled_cycles']} rolled cycles; cycle ended better than its loss at the first roll {s2['roll_better']}, worse {s2['roll_worse']}")
    bl = out["benchmark_long"]
    print(f"\nLonger look, 1990 to {le}, VIX only, descriptive (benchmark {bl['cagr']*100:+.1f}% a year, risk ratio {bl['ratio']:.2f}, worst DD {bl['max_dd']*100:.0f}%):")
    for sid, r in out["runs"]["long_1990_vix_only"].items():
        print(f"  {sid} {NAMES[sid]:26} £{r['pnl_year']:.0f}/yr, account £{r['account']:.0f}, {r['ret_year']*100:+.1f}% a year, risk ratio {r['ratio']:.2f}, worst DD {r['max_dd']*100:.0f}%, worst month £{r['worst_month']:.0f}")
    CACHE.mkdir(parents=True, exist_ok=True)
    (CACHE / "result.json").write_text(json.dumps(out, default=str, indent=1))
    print("\nModel prices, daily closes only, margin with no netting. Not evidence of an edge on its own; survivors go to the forward shadow test.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
