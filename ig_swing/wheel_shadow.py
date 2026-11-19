"""Shadow wheel on IG's US 500 and US Russell 2000 options: journal only, nothing is ever ordered.

Agreed with the user 2026-11-19 after tests 2 and 3 (CLAUDE.md): the wheel passed on both markets on model prices.
This records, on IG's live quotes, what the wheel would do, for 8 to 12 weeks, to check the real premiums against
the model before any money. Same rules as the tests, at £1 a point:
- Sell the 30-delta put on the monthly series nearest 35 days out (at least 21), at IG's bid. Held to settlement.
- A put that settles below its strike is paid out in cash, and the long US 500 (or Russell) quarterly future is
  bought at IG's offer at that check, with the put strike as the cost basis. While long, sell the call at the higher
  of the 30-delta strike and the cost basis. A call that settles above its strike is paid out and the long is
  closed at IG's bid. Then sell puts again.
- Benchmark L0: the long quarterly future from the first check, held throughout.
- Futures roll at IG's bid and offer when the held contract is within 2 days of its last dealing date.
- Delta is not given by IG: it is worked out from IG's own prices (the forward from put-call parity at the strike
  nearest the money, then each strike's implied volatility from its mid price).
- Settlement value: the index's opening price on the third Friday from Yahoo, a stand-in for the official opening
  settlement (SET), so it is approximate.
- Each check also prices the held option with the test's model and records IG mid / model mid: the question this
  shadow phase exists to answer.

Commands (run from the project root): `check` (journal: settle, roll, sell, mark), `status` (read only).
Uses market search and quote calls only: no price-history allowance.
"""
from __future__ import annotations

import argparse
import json
import math
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

import requests

from . import options_test as ot
from .journal import Journal

MARKETS = {
    "US500": {"label": "US 500", "search": "US 500", "name_re": r"^US 500 (\d+) (PUT|CALL)$", "step": 25.0,
              "futures": ["IX.D.SPTRD.MONTH1.IP", "IX.D.SPTRD.MONTH2.IP"], "yahoo": "^GSPC", "skew": -0.178, "vol": "vix_term"},
    "RUT": {"label": "US Russell 2000", "search": "US Russell 2000", "name_re": r"^US Russell 2000 (\d+) (PUT|CALL|Put|Call)$", "step": 10.0,
            "futures": ["IX.D.RUSSELL.MONTH1.IP", "IX.D.RUSSELL.MONTH2.IP"], "yahoo": "^RUT", "skew": -0.174, "vol": "RVXCLS"},
}
TARGET_DELTA = 0.30
KIND = "wheel_shadow"
MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


# ---------- pure helpers ----------

def monthly_target(today: date) -> tuple[date, str]:
    """The third-Friday monthly nearest 35 days out with at least 21 days. Returns (settle day, IG code like DEC-26)."""
    cands = []
    for k in range(0, 4):
        y, m = today.year + (today.month - 1 + k) // 12, (today.month - 1 + k) % 12 + 1
        tf = ot.third_friday(y, m)
        dd = (tf - today).days
        if dd >= 21:
            cands.append((abs(dd - 35), tf))
    tf = min(cands)[1]
    return tf, f"{MONTHS[tf.month - 1]}-{tf.year % 100:02d}"


def implied_vol(kind: str, f: float, k: float, t: float, price: float) -> float | None:
    intrinsic = max(0.0, f - k) if kind == "C" else max(0.0, k - f)
    if price <= intrinsic + 1e-9:
        return None
    lo, hi = 0.005, 4.0
    for _ in range(100):
        mid = (lo + hi) / 2
        if ot.black(kind, f, k, t, mid)[0] > price:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def pick_strike(quotes: dict[float, tuple[float, float]], kind: str, f: float, t: float, target: float = TARGET_DELTA,
                floor_k: float | None = None) -> dict | None:
    """quotes: strike -> (bid, offer) for one kind. Returns the strike whose delta (from IG's own mid) is nearest target."""
    best = None
    for k, (bid, offer) in quotes.items():
        if bid is None or offer is None or bid <= 0 or offer <= bid:
            continue
        if floor_k is not None and k < floor_k:
            continue
        mid = (bid + offer) / 2
        iv = implied_vol(kind, f, k, t, mid)
        if iv is None:
            continue
        delta = ot.black(kind, f, k, t, iv)[1]
        err = abs(abs(delta) - target)
        if best is None or err < best["err"]:
            best = {"k": k, "bid": bid, "offer": offer, "mid": mid, "iv": iv, "delta": delta, "err": err}
    return best


def replay(events: list[dict]) -> dict[str, dict]:
    """Rebuild each market's shadow state from the journal."""
    st = {m: {"phase": "PUT", "option": None, "long": None, "basis": None, "l0": None, "wheel_cash": 0.0, "l0_cash": 0.0,
              "premium": 0.0, "sales": 0, "ratios": [], "settled": [], "start": None, "last_mark": None} for m in MARKETS}
    for e in events:
        if e["kind"] != KIND:
            continue
        d = json.loads(e["detail"])
        s = st.get(d.get("market"))
        if s is None:
            continue
        a = d["action"]
        if a == "start":
            s["l0"] = {"epic": d["epic"], "entry": d["price"], "last": d["last_dealing"]}
            s["start"] = {"ts": e["ts"], "index": d.get("index")}
        elif a == "sell":
            s["option"] = d
            s["wheel_cash"] += d["bid"]
            s["premium"] += d["bid"]
            s["sales"] += 1
            if d.get("model_mid"):
                s["ratios"].append(("sale", d["mid"] / d["model_mid"]))
        elif a == "mark":
            s["last_mark"] = d
            if d.get("model_mid") and d.get("mid"):
                s["ratios"].append(("mark", d["mid"] / d["model_mid"]))
        elif a == "settle":
            s["wheel_cash"] -= d["intrinsic"]
            s["settled"].append(d)
            s["option"] = None
        elif a == "long_open":
            s["long"] = {"epic": d["epic"], "entry": d["price"], "last": d["last_dealing"]}
            s["basis"] = d.get("basis", s["basis"])
            s["phase"] = "LONG"
        elif a == "long_close":
            s["wheel_cash"] += d["price"] - s["long"]["entry"]
            s["long"] = None
            s["phase"] = "PUT"
        elif a == "roll":
            book = s["long"] if d["which"] == "wheel" else s["l0"]
            gain = d["close_price"] - book["entry"]
            if d["which"] == "wheel":
                s["wheel_cash"] += gain
                s["long"] = {"epic": d["epic"], "entry": d["open_price"], "last": d["last_dealing"]}
            else:
                s["l0_cash"] += gain
                s["l0"] = {"epic": d["epic"], "entry": d["open_price"], "last": d["last_dealing"]}
    return st


# ---------- IG and market data ----------

def futures_front(client: Any, epics: list[str], today: date) -> dict | None:
    d = client._call("GET", "/markets", "2", params={"epics": ",".join(epics), "filter": "ALL"}, context="wheel futures")
    best = None
    for m in d.get("marketDetails", []):
        i, s = m["instrument"], m["snapshot"]
        last = (i.get("expiryDetails") or {}).get("lastDealingDate")
        if not last or s.get("bid") is None or s.get("offer") is None:
            continue
        ld = date.fromisoformat(last[:10])
        if (ld - today).days <= 2:
            continue
        if best is None or ld < best["last"]:
            best = {"epic": i["epic"], "bid": float(s["bid"]), "offer": float(s["offer"]), "last": ld, "mid": (float(s["bid"]) + float(s["offer"])) / 2}
    return best


def quote_epic(client: Any, epic: str) -> dict | None:
    d = client._call("GET", "/markets", "2", params={"epics": epic, "filter": "ALL"}, context="wheel quote")
    for m in d.get("marketDetails", []):
        s, i = m["snapshot"], m["instrument"]
        last = (i.get("expiryDetails") or {}).get("lastDealingDate")
        return {"bid": s.get("bid"), "offer": s.get("offer"), "last": last[:10] if last else None}
    return None


def chain(client: Any, cfg: dict, code: str, strikes: list[float], kinds: tuple[str, ...]) -> dict[str, dict[float, tuple]]:
    """kind -> strike -> (bid, offer, epic) for the monthly series `code`."""
    out: dict[str, dict[float, tuple]] = {"P": {}, "C": {}}
    rx = re.compile(cfg["name_re"])
    for k in strikes:
        for kind in kinds:
            word = "PUT" if kind == "P" else "CALL"
            for h in client.search_markets(f"{cfg['search']} {int(k)} {word}"):
                m = rx.match(h.name)
                if not m or h.expiry != code or float(m.group(1)) != k or m.group(2).upper() != word:
                    continue
                out[kind][k] = (h.bid, h.offer, h.epic)
    return out


def yahoo_recent(sym: str) -> dict[str, tuple[float, float]]:
    r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}", params={"range": "1mo", "interval": "1d"},
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    out = {}
    for i, ts in enumerate(res.get("timestamp") or []):
        if q["close"][i] is None:
            continue
        out[datetime.fromtimestamp(ts + 6 * 3600, timezone.utc).date().isoformat()] = (q["open"][i] or q["close"][i], q["close"][i])
    return out


def model_atm(cfg: dict, days: float) -> float | None:
    """The test's at-the-money volatility for this market today (decimal), or None."""
    try:
        if cfg["vol"] == "vix_term":
            vols = {}
            for t, sym in ot.VOL_POINTS:
                rows = yahoo_recent(sym)
                if rows:
                    vols[t] = rows[max(rows)][1]
            return ot.atm_vol(vols, days)
        fr = requests.get(ot.FRED_URL, params={"series_id": cfg["vol"], "api_key": _fred_key(), "file_type": "json", "sort_order": "desc", "limit": 5}, timeout=30).json()
        v = next(float(o["value"]) for o in fr["observations"] if o["value"] not in (".", ""))
        return ot.FIXED["atm_scale"] * v / 100.0
    except Exception:
        return None


def _fred_key() -> str:
    from .config import optional_key
    return optional_key("FRED_API_KEY") or ""


def forward_from_chain(ch: dict[str, dict[float, tuple]]) -> float | None:
    both = [k for k in ch["P"] if k in ch["C"] and None not in ch["P"][k][:2] and None not in ch["C"][k][:2]]
    if not both:
        return None
    k = min(both, key=lambda k: abs((ch["C"][k][0] + ch["C"][k][1]) - (ch["P"][k][0] + ch["P"][k][1])))
    return k + (ch["C"][k][0] + ch["C"][k][1]) / 2 - (ch["P"][k][0] + ch["P"][k][1]) / 2


# ---------- the check ----------

def check(client: Any, journal: Journal, now: datetime, out: list[str]) -> None:
    today = now.date()
    ts = now.isoformat(timespec="seconds")
    state = replay(journal.events())

    def rec(d: dict) -> None:
        journal.record_event(ts, KIND, json.dumps(d))

    for mk, cfg in MARKETS.items():
        s = state[mk]
        fut = futures_front(client, cfg["futures"], today)
        if fut is None:
            out.append(f"{cfg['label']}: no future quote, skipped")
            continue
        index_now = fut["mid"]
        if s["l0"] is None:
            rec({"market": mk, "action": "start", "epic": fut["epic"], "price": fut["offer"], "last_dealing": fut["last"].isoformat(), "index": index_now})
            s["l0"] = {"epic": fut["epic"], "entry": fut["offer"], "last": fut["last"].isoformat()}
            out.append(f"{cfg['label']}: shadow started, benchmark long bought at {fut['offer']}")
        # rolls
        for which, book in (("l0", s["l0"]), ("wheel", s["long"])):
            if book and (date.fromisoformat(book["last"]) - today).days <= 2 and book["epic"] != fut["epic"]:
                old = quote_epic(client, book["epic"])
                if old and old["bid"] is not None:
                    rec({"market": mk, "action": "roll", "which": which, "close_price": old["bid"], "epic": fut["epic"], "open_price": fut["offer"], "last_dealing": fut["last"].isoformat()})
                    out.append(f"{cfg['label']}: rolled the {which} long to {fut['epic']}")
                    state = replay(journal.events())
                    s = state[mk]
        # settlement
        opt = s["option"]
        if opt:
            settle_day = date.fromisoformat(opt["settle"])
            if today > settle_day or (today == settle_day and now.hour >= 15):
                rows = yahoo_recent(cfg["yahoo"])
                px = rows.get(settle_day.isoformat())
                if px is None:
                    out.append(f"{cfg['label']}: settlement price for {settle_day} not available yet")
                else:
                    sv = px[0]
                    intrinsic = max(0.0, sv - opt["k"]) if opt["kind"] == "C" else max(0.0, opt["k"] - sv)
                    rec({"market": mk, "action": "settle", "epic": opt["epic"], "kind": opt["kind"], "k": opt["k"], "settle_value": sv, "intrinsic": intrinsic, "itm": intrinsic > 0})
                    out.append(f"{cfg['label']}: {opt['kind']} {opt['k']:.0f} settled at about {sv:.1f} (Yahoo open), paid out {intrinsic:.1f} points")
                    if intrinsic > 0 and opt["kind"] == "P" and s["phase"] == "PUT":
                        rec({"market": mk, "action": "long_open", "epic": fut["epic"], "price": fut["offer"], "last_dealing": fut["last"].isoformat(), "basis": opt["k"]})
                        out.append(f"{cfg['label']}: put finished in the money, shadow long bought at {fut['offer']} (cost basis {opt['k']:.0f})")
                    elif intrinsic > 0 and opt["kind"] == "C" and s["phase"] == "LONG":
                        lq = quote_epic(client, s["long"]["epic"])
                        price = lq["bid"] if lq and lq["bid"] is not None else fut["bid"]
                        rec({"market": mk, "action": "long_close", "price": price})
                        out.append(f"{cfg['label']}: call finished in the money, shadow long closed at {price}")
                    state = replay(journal.events())
                    s = state[mk]
        # sell the next option, or mark the open one
        settle, code = monthly_target(today)
        t_days = (settle - today).days
        if s["option"] is None:
            kind = "P" if s["phase"] == "PUT" else "C"
            atm_k = round(index_now / cfg["step"]) * cfg["step"]
            atm = chain(client, cfg, code, [atm_k - cfg["step"], atm_k, atm_k + cfg["step"]], ("P", "C"))
            f = forward_from_chain(atm)
            if f is None:
                out.append(f"{cfg['label']}: no usable {code} quotes (market closed?), nothing sold")
                continue
            t = t_days / 365.0
            kk = min(atm["C"], key=lambda k: abs(k - f)) if atm["C"] else atm_k
            c_bid, c_off, _ = atm["C"].get(kk, (None, None, None))
            atm_iv = implied_vol("C", f, kk, t, (c_bid + c_off) / 2) if c_bid and c_off else 0.2
            sign = -1 if kind == "P" else 1
            k0 = f * math.exp(sign * 0.52 * (atm_iv or 0.2) * math.sqrt(t))
            base = round(k0 / cfg["step"]) * cfg["step"]
            strikes = [base + i * cfg["step"] for i in range(-4, 5)]
            ch = chain(client, cfg, code, strikes, (kind,))
            floor_k = s["basis"] if kind == "C" else None
            best = pick_strike({k: v[:2] for k, v in ch[kind].items()}, kind, f, t, floor_k=floor_k)
            if best is None:
                out.append(f"{cfg['label']}: no strike found near 30 delta for {code}, nothing sold")
                continue
            epic = ch[kind][best["k"]][2]
            matm = model_atm(cfg, t_days)
            model_mid = ot.black(kind, f, best["k"], t, ot.strike_vol(matm, f, best["k"], t, cfg["skew"]))[0] if matm else None
            rec({"market": mk, "action": "sell", "kind": kind, "k": best["k"], "code": code, "settle": settle.isoformat(), "epic": epic,
                 "bid": best["bid"], "offer": best["offer"], "mid": best["mid"], "iv": best["iv"], "delta": best["delta"], "forward": f,
                 "model_mid": model_mid, "index": index_now})
            ratio = f", IG mid / model {best['mid'] / model_mid:.2f}" if model_mid else ""
            out.append(f"{cfg['label']}: sold (shadow) the {code} {best['k']:.0f} {'put' if kind == 'P' else 'call'} at {best['bid']} "
                       f"(offer {best['offer']}, delta {best['delta']:+.2f}, implied vol {best['iv'] * 100:.1f}%, forward {f:.1f}{ratio})")
        else:
            opt = s["option"]
            q = quote_epic(client, opt["epic"])
            if q is None or q["bid"] is None:
                out.append(f"{cfg['label']}: no quote for the held option")
                continue
            mid = (q["bid"] + q["offer"]) / 2
            days_left = max((date.fromisoformat(opt["settle"]) - today).days, 0.5)
            t = days_left / 365.0
            # forward for marking: the quarterly future, carried back to the option's settlement at about 2.5% a year
            # (rates less dividends) when the future expires later than the option
            gap = max((fut["last"] - date.fromisoformat(opt["settle"])).days, 0)
            f = index_now * math.exp(-0.025 * gap / 365.0)
            iv = implied_vol(opt["kind"], f, opt["k"], t, mid)
            delta = ot.black(opt["kind"], f, opt["k"], t, iv)[1] if iv else None
            matm = model_atm(cfg, days_left)
            model_mid = ot.black(opt["kind"], f, opt["k"], t, ot.strike_vol(matm, f, opt["k"], t, cfg["skew"]))[0] if matm else None
            rec({"market": mk, "action": "mark", "epic": opt["epic"], "bid": q["bid"], "offer": q["offer"], "mid": mid, "delta": delta,
                 "fut_mid": index_now, "model_mid": model_mid, "long_mid": index_now if s["long"] else None})
            out.append(f"{cfg['label']}: holding the {opt['code']} {opt['k']:.0f} {'put' if opt['kind'] == 'P' else 'call'}, sold at {opt['bid']}, now {q['bid']}/{q['offer']}"
                       + (f", delta {delta:+.2f}" if delta is not None else ""))
        # record the futures mid for the P&L of both books
        rec({"market": mk, "action": "fut_mark", "epic": fut["epic"], "mid": fut["mid"]})


def pnl(state: dict, fut_mid: dict[str, float], opt_mid: dict[str, float | None]) -> dict[str, dict[str, float]]:
    out = {}
    for mk, s in state.items():
        if s["l0"] is None:
            continue
        w = s["wheel_cash"] - (opt_mid.get(mk) or 0.0)
        if s["long"]:
            w += fut_mid[mk] - s["long"]["entry"]
        l0 = s["l0_cash"] + fut_mid[mk] - s["l0"]["entry"]
        out[mk] = {"wheel": w, "l0": l0}
    return out


def status(journal: Journal) -> list[str]:
    evs = journal.events()
    state = replay(evs)
    fut_mid, opt_mid = {}, {}
    for e in evs:
        if e["kind"] != KIND:
            continue
        d = json.loads(e["detail"])
        if d["action"] == "fut_mark":
            fut_mid[d["market"]] = d["mid"]
        elif d["action"] == "mark":
            opt_mid[d["market"]] = d["mid"]
        elif d["action"] == "sell":
            opt_mid[d["market"]] = d["mid"]
        elif d["action"] == "settle":
            opt_mid[d["market"]] = None
    lines = ["Shadow wheel (journal only, £1 a point, nothing ordered):"]
    res = pnl(state, fut_mid, opt_mid)
    for mk, s in state.items():
        cfg = MARKETS[mk]
        if s["l0"] is None:
            lines.append(f"  {cfg['label']}: not started")
            continue
        o = s["option"]
        held = (f"short {o['code']} {o['k']:.0f} {'put' if o['kind'] == 'P' else 'call'} sold at {o['bid']}" if o else "no option open")
        lng = f", long from {s['long']['entry']}" if s["long"] else ""
        r = res.get(mk, {})
        ratios = [x for _, x in s["ratios"]]
        rtxt = f"IG mid / model: mean {sum(ratios) / len(ratios):.2f} over {len(ratios)} readings" if ratios else "IG mid / model: no readings yet"
        lines.append(f"  {cfg['label']}: phase {s['phase']}, {held}{lng}; {s['sales']} sold, premium {s['premium']:.1f} points; "
                     f"wheel {r.get('wheel', 0):+.1f} pts vs holding {r.get('l0', 0):+.1f} pts; {rtxt}")
    return lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ig_swing.wheel_shadow")
    ap.add_argument("cmd", choices=["check", "status"])
    a = ap.parse_args(argv)
    j = Journal()
    if a.cmd == "status":
        print("\n".join(status(j)))
        return 0
    from .config import base_url, ig_env, load_credentials
    from .ig_client import IGClient
    env = ig_env()
    client = IGClient(load_credentials(env), base_url=base_url(env), allow_live=(env == "live"))
    out: list[str] = []
    check(client, j, datetime.now(timezone.utc), out)
    print("\n".join(out))
    print("\n".join(status(j)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
