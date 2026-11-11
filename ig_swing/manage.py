"""Manage open positions: list, close (in profit or in loss), tighten a stop, set a take-profit, trail a stop by rule, and review history.

  python3 -m ig_swing.manage list
  python3 -m ig_swing.manage close --deal-id D1 --reason-type thesis_invalidated --reason "..." [--size 0.01] [--execute]
  python3 -m ig_swing.manage stop  --deal-id D1 (--level L | --breakeven | --distance POINTS) [--execute]
  python3 -m ig_swing.manage limit --deal-id D1 --level L [--execute]
  python3 -m ig_swing.manage trailing --deal-id D1 [--execute]   up 1R or more: stop to breakeven and an IG trailing stop (manual steps are printed if IG refuses)
  python3 -m ig_swing.manage trail [--execute]          the mechanical rule: stop to breakeven at +1R, to +1R at +2R (skips positions IG is trailing)
  python3 -m ig_swing.manage history                    closed trades by exit reason: does cutting early help?
  python3 -m ig_swing.manage reconcile [--execute]      record trades IG closed on its own in the journal (journal only, no order)

Every changing command is a DRY RUN unless --execute is given AND IG_LIVE_ORDERS=yes is set AND the challenge risk profile is active.
The assistant runs these unprompted under the user's standing authorisation (2026-11-11): closing a position in profit or in loss,
tightening a stop, setting or moving a take-profit, and reversing (a close followed by a fully guarded new entry through ig_swing.place).
Never: widening a stop, removing a stop, adding to a position, averaging down. A stop can only tighten (enforced in the client).

Why early exits are allowed: cutting a loser before its stop, when the thesis is broken, is risk management and is where most traders fail.
It is also a discretionary decision that can hurt (cutting a trade that would have worked), so every early exit is tagged with a reason
type and `history` compares them with trades that simply hit the stop or the target. A reversal is a close plus a NEW entry with its own
idea, probability and stop, never "to win it back": the daily trade cap and the cooldown after two losing trades still apply.

R is the initial risk: the trade's risk in pounds over its size, in points. Between sessions only IG's server-side stop and limit protect a position.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from typing import Any

from .ig_client import IGError, OrderRejected, Position

UTC = timezone.utc
REASON_TYPES = ("thesis_invalidated", "new_information", "time", "risk_event", "reversal", "profit_taking", "other")


def initial_risk_points(pos: Position, trade: dict | None) -> float | None:
    """The initial stop distance in points: the journalled risk over size, else the current stop distance from the open level."""
    if trade and trade.get("risk_gbp") and trade.get("size"):
        return float(trade["risk_gbp"]) / float(trade["size"])
    if pos.stop_level is not None:
        return abs(pos.open_level - pos.stop_level)
    return None


def move_in_r(pos: Position, r_pts: float | None) -> float | None:
    """How far the position has moved in its favour, in multiples of the initial risk (negative = against)."""
    if not r_pts:
        return None
    if pos.direction == "BUY" and pos.bid is not None:
        return (pos.bid - pos.open_level) / r_pts
    if pos.direction == "SELL" and pos.offer is not None:
        return (pos.open_level - pos.offer) / r_pts
    return None


def trail_target(pos: Position, r_pts: float | None) -> float | None:
    """The mechanical stop: breakeven once up 1R, lock in 1R once up 2R. None unless it tightens the stop and is on the right side of price."""
    m = move_in_r(pos, r_pts)
    if m is None or pos.stop_level is None or not r_pts:
        return None
    buy = pos.direction == "BUY"
    if m >= 2:
        new = pos.open_level + r_pts if buy else pos.open_level - r_pts
    elif m >= 1:
        new = pos.open_level
    else:
        return None
    tighter = new > pos.stop_level if buy else new < pos.stop_level
    valid = (pos.bid is not None and new < pos.bid) if buy else (pos.offer is not None and new > pos.offer)
    return new if tighter and valid else None


def native_trailing_plan(pos: Position, r_pts: float | None, min_stop: float = 0.0) -> tuple[float, float, float] | None:
    """When a position is up 1R or more and IG is not yet trailing it: (stop level, trailing distance, step).

    The stop goes to breakeven if that tightens it, and the trailing distance is the gap from the price to that stop, so IG trailing at
    that distance can never put the stop further away than it is now. None if not up 1R, already trailing, or the gap is too small.
    """
    m = move_in_r(pos, r_pts)
    if m is None or m < 1 or pos.stop_level is None or pos.trailing_distance:
        return None
    buy = pos.direction == "BUY"
    price = pos.bid if buy else pos.offer
    if price is None:
        return None
    stop = max(pos.stop_level, pos.open_level) if buy else min(pos.stop_level, pos.open_level)
    gap = (price - stop) if buy else (stop - price)
    if gap <= 0 or gap < min_stop:
        return None
    return stop, round(gap, 2), max(round(gap / 5, 2), 1.0)


MANUAL_TRAILING = ("IG did not accept a trailing stop through the API. Set it by hand in the IG platform: open the position, edit the stop, choose a TRAILING stop, "
                   "distance {distance:g} points, step {step:g} points. Tell the assistant when it is done.")


def describe(pos: Position, trade: dict | None) -> str:
    r = initial_risk_points(pos, trade)
    m = move_in_r(pos, r)
    price = pos.bid if pos.direction == "BUY" else pos.offer
    stop = "NONE (unprotected!)" if pos.stop_level is None else f"{pos.stop_level:g}"
    limit = "none" if pos.limit_level is None else f"{pos.limit_level:g}"
    trailing = f", IG trailing {pos.trailing_distance:g} points" if pos.trailing_distance else ""
    risk = ""
    if pos.stop_level is not None:
        stopped = pos.size * (pos.stop_level - pos.open_level if pos.direction == "BUY" else pos.open_level - pos.stop_level)
        risk = f", if stopped out: £{stopped:+.2f}" + (" (profit locked in)" if stopped > 0.005 else " (breakeven)" if stopped > -0.005 else "")
    pnl = "n/a" if pos.unrealised_pnl is None else f"£{pos.unrealised_pnl:+.2f}"
    thesis = f"\n    thesis: {trade['thesis']}\n    invalidation: {trade.get('invalidation') or 'not recorded'}" if trade and trade.get("thesis") else ""
    return (f"{pos.direction} {pos.instrument_name} [{pos.deal_id}] £{pos.size:g}/pt, opened {pos.open_level:g}, now {price if price is not None else 'n/a'}, "
            f"P&L {pnl}" + (f", {m:+.2f}R" if m is not None else "") + f", stop {stop}{trailing}, take-profit {limit}{risk}{thesis}")


def history_by_reason(closed: list[dict]) -> dict[str, list[float]]:
    """Closed trades grouped by how they ended: early exits by their reason type, the rest as stop, target or other."""
    groups: dict[str, list[float]] = {}
    for t in closed:
        reason = (t.get("exit_reason") or "other").strip()
        key = reason.split(":")[0].strip() if ":" in reason else reason
        if key not in REASON_TYPES:
            key = {"stop": "stop hit", "target": "target hit"}.get(key.lower(), key or "other")
        if t.get("r_multiple") is not None:
            groups.setdefault(key, []).append(float(t["r_multiple"]))
    return groups


def format_history(closed: list[dict]) -> str:
    if not closed:
        return "No closed trades yet."
    groups = history_by_reason(closed)
    allr = [r for rs in groups.values() for r in rs]
    lines = [f"Closed trades: {len(closed)} (n={len(allr)} with a result), mean {sum(allr) / len(allr):+.2f}R" if allr else f"Closed trades: {len(closed)}"]
    for key, rs in sorted(groups.items()):
        lines.append(f"  {key}: n={len(rs)}, mean {sum(rs) / len(rs):+.2f}R, wins {sum(1 for r in rs if r > 0)}" + ("  (too few to say anything)" if len(rs) < 20 else ""))
    lines.append("Early exits (thesis_invalidated, new_information, risk_event, time, reversal) against stop and target hits is how we find out whether cutting early helps.")
    return "\n".join(lines)


def _utc(value: Any) -> Any:
    import pandas as pd

    try:
        ts = pd.Timestamp(value)
    except Exception:
        return None
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def match_transaction(trade: dict, txs: list[dict]) -> dict | None:
    """The closed deal that matches a journalled trade: same market and size, the open level within a small tolerance, nearest in time."""
    opened = _utc(trade.get("opened_at"))
    entry, size = trade["entry"], trade["size"]
    tol = max(abs(entry) * 0.0005, 1.0)
    best, best_gap = None, None
    for t in txs:
        if not t.get("instrument") or not (str(trade.get("market") or "").lower() in str(t["instrument"]).lower() or str(t["instrument"]).lower() in str(trade.get("market") or "").lower()):
            continue
        if t.get("size") is not None and abs(t["size"] - size) > 1e-9:
            continue
        if t.get("open_level") is None or abs(t["open_level"] - entry) > tol or t.get("close_level") is None:
            continue
        t_open = _utc(t.get("opened_utc"))
        gap = abs((t_open - opened).total_seconds()) if (t_open is not None and opened is not None) else 0.0
        if gap > 3600:
            continue
        if best is None or gap < best_gap:
            best, best_gap = t, gap
    return best


def infer_exit_reason(trade: dict, close_level: float) -> str:
    """Server-side closes: near the stop or the take-profit, else closed outside a session (by the user in the platform, say)."""
    r = abs(trade["entry"] - trade["stop"]) or 1.0
    tol = max(0.1 * r, 1.0)
    if abs(close_level - trade["stop"]) <= tol:
        return "stop (closed by IG)"
    if trade.get("target") is not None and abs(close_level - trade["target"]) <= tol:
        return "target (closed by IG)"
    return "closed outside a session"


def reconcile_plan(open_trades: list[dict], positions: list[Position], txs: list[dict]) -> dict[str, Any]:
    """Journal trades that IG no longer holds, matched to their closing deal where one can be found, and positions IG holds that the journal does not know."""
    held = {p.deal_id for p in positions}
    known = {t["deal_id"] for t in open_trades}
    gone = []
    for t in open_trades:
        if t["deal_id"] in held:
            continue
        tx = match_transaction(t, txs)
        gone.append({"trade": t, "tx": tx, "reason": infer_exit_reason(t, tx["close_level"]) if tx else None})
    return {"closed": gone, "unknown_positions": [p for p in positions if p.deal_id not in known]}


def excursion_note(checks: list[float]) -> str:
    if not checks:
        return "no position checks were recorded while it was open"
    return f"best {max(checks):+.2f}R and worst {min(checks):+.2f}R seen at {len(checks)} check(s) (coarse: only moments when a session looked)"


def apply_reconcile(journal: Any, plan: dict[str, Any], checks_by_deal: dict[str, list[float]], now: datetime) -> list[str]:
    done = []
    for item in plan["closed"]:
        t, tx = item["trade"], item["tx"]
        if tx is None:
            done.append(f"{t['market']} [{t['deal_id']}]: IG no longer holds it but no matching closed deal was found, check the IG platform")
            continue
        closed_at = _utc(tx.get("closed_utc"))
        when = (closed_at or _utc(now.isoformat())).isoformat()
        journal.close_trade(t["id"], when, tx["close_level"], item["reason"], notes=f"IG P&L {tx.get('pnl_text')}; " + excursion_note(checks_by_deal.get(t["deal_id"], [])))
        done.append(f"{t['market']} [{t['deal_id']}]: recorded as closed at {tx['close_level']:g}, {item['reason']}")
    return done


def _find(positions: list[Position], deal_id: str) -> Position:
    for p in positions:
        if p.deal_id == deal_id:
            return p
    raise LookupError(f"no open position with deal id {deal_id}")


def apply_stop(client: Any, journal: Any, pos: Position, new_stop: float, now: datetime, why: str) -> dict[str, Any]:
    ref = client.amend_position(position=pos, new_stop=new_stop, user_confirmed=True)
    conf = client.confirm_deal(ref)
    final = conf.stop_level if conf.stop_level is not None else new_stop
    journal.update_levels(pos.deal_id, stop=final)
    journal.record_event(now.isoformat(), "stop_moved", json.dumps({"deal_id": pos.deal_id, "from": pos.stop_level, "to": final, "why": why}))
    return {"deal_id": pos.deal_id, "from": pos.stop_level, "to": final}


def apply_trailing(client: Any, journal: Any, pos: Position, stop: float, distance: float, step: float, now: datetime) -> dict[str, Any]:
    ref = client.amend_position(position=pos, new_stop=stop if stop != pos.stop_level else None, trailing=(distance, step), user_confirmed=True)
    conf = client.confirm_deal(ref)
    final = conf.stop_level if conf.stop_level is not None else stop
    journal.update_levels(pos.deal_id, stop=final)
    journal.record_event(now.isoformat(), "trailing_set", json.dumps({"deal_id": pos.deal_id, "stop": final, "distance": distance, "step": step}))
    return {"deal_id": pos.deal_id, "stop": final, "distance": distance, "step": step}


def apply_limit(client: Any, journal: Any, pos: Position, new_limit: float, now: datetime) -> dict[str, Any]:
    ref = client.amend_position(position=pos, new_limit=new_limit, user_confirmed=True)
    client.confirm_deal(ref)
    journal.update_levels(pos.deal_id, target=new_limit)
    journal.record_event(now.isoformat(), "limit_set", json.dumps({"deal_id": pos.deal_id, "from": pos.limit_level, "to": new_limit}))
    return {"deal_id": pos.deal_id, "from": pos.limit_level, "to": new_limit}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ig_swing.manage", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("history")
    t = sub.add_parser("trail")
    t.add_argument("--execute", action="store_true")
    c = sub.add_parser("close")
    c.add_argument("--deal-id", required=True)
    c.add_argument("--reason-type", required=True, choices=REASON_TYPES)
    c.add_argument("--reason", required=True)
    c.add_argument("--size", type=float, default=None)
    c.add_argument("--execute", action="store_true")
    s = sub.add_parser("stop")
    s.add_argument("--deal-id", required=True)
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--level", type=float)
    g.add_argument("--breakeven", action="store_true")
    g.add_argument("--distance", type=float, help="points from the current price")
    s.add_argument("--execute", action="store_true")
    rc = sub.add_parser("reconcile", help="record trades IG closed on its own (stop or take-profit) in the journal; changes the journal only, places nothing")
    rc.add_argument("--execute", action="store_true")
    tr = sub.add_parser("trailing", help="switch a winning position (up 1R or more) to an IG trailing stop")
    tr.add_argument("--deal-id", required=True)
    tr.add_argument("--execute", action="store_true")
    lm = sub.add_parser("limit")
    lm.add_argument("--deal-id", required=True)
    lm.add_argument("--level", type=float, required=True)
    lm.add_argument("--execute", action="store_true")
    args = ap.parse_args(argv)

    from .config import base_url, ig_env, load_credentials, risk_limits
    from .ig_client import IGClient
    from .journal import Journal
    from .orders import OrderService
    from .risk import KillSwitch, RiskEngine, RiskViolation

    now = datetime.now(UTC)
    journal = Journal()
    try:
        if args.cmd == "history":
            print(format_history(journal.closed_trades()))
            return 0
        env = ig_env()
        creds = load_credentials(env)
        live_ok = env == "live" and os.environ.get("IG_LIVE_ORDERS", "").strip().lower() == "yes"
        client = IGClient(creds, base_url=base_url(env), allow_live=(env == "live"), live_orders_enabled=live_ok)
        engine = RiskEngine(risk_limits(env), KillSwitch())
        positions = client.get_positions()
        open_trades = {t["deal_id"]: t for t in journal.open_trades()}
        if args.cmd == "list":
            if not positions:
                print("No open positions.")
            for p in positions:
                tr_ = open_trades.get(p.deal_id)
                print(describe(p, tr_))
                m_ = move_in_r(p, initial_risk_points(p, tr_))
                if m_ is not None:                                                  # a coarse record of how far each trade has run, for later review
                    journal.record_event(now.isoformat(), "position_check", json.dumps({"deal_id": p.deal_id, "r": round(m_, 3)}))
            return 0
        if args.cmd == "reconcile":
            since = min((_utc(t["opened_at"]) for t in open_trades.values()), default=None)
            txs = client.get_transactions(since.date()) if (since is not None and any(t["deal_id"] not in {p.deal_id for p in positions} for t in open_trades.values())) else []
            plan = reconcile_plan(list(open_trades.values()), positions, txs)
            checks: dict[str, list[float]] = {}
            for e in journal.events():
                if e["kind"] == "position_check":
                    try:
                        d = json.loads(e["detail"])
                        checks.setdefault(d["deal_id"], []).append(float(d["r"]))
                    except (ValueError, KeyError, TypeError):
                        continue
            if not plan["closed"] and not plan["unknown_positions"]:
                print("The journal and IG agree: nothing to reconcile.")
                return 0
            for item in plan["closed"]:
                t, tx = item["trade"], item["tx"]
                print(f"{'would record' if not args.execute else 'recording'}: {t['market']} [{t['deal_id']}] closed by IG" + (f" at {tx['close_level']:g}, {item['reason']}" if tx else ", but no matching closed deal found"))
            for p in plan["unknown_positions"]:
                print(f"WARNING: IG holds {p.instrument_name} [{p.deal_id}] that the journal does not know about (opened outside this tool?)")
            if args.execute:
                for line in apply_reconcile(journal, plan, checks, now):
                    print(" ", line)
            else:
                print("Dry run: add --execute to write the closes to the journal (no order is placed either way).")
            return 0
        execute = getattr(args, "execute", False)
        if execute and not live_ok:
            print("REFUSED: live orders are off. Set IG_LIVE_ORDERS=yes for this one command.")
            return 1
        if execute and engine.limits.name != "challenge":
            print(f"REFUSED: the risk profile is {engine.limits.name}, not challenge. Set IG_RISK_PROFILE=challenge in .env.")
            return 1
        mode = "EXECUTING" if execute else "DRY RUN (nothing changed)"
        if args.cmd == "close":
            pos = _find(positions, args.deal_id)
            size = pos.size if args.size is None else args.size
            print(f"{mode}: close {size:g} of {describe(pos, open_trades.get(pos.deal_id))}\n  reason: {args.reason_type}: {args.reason}")
            if execute:
                res = OrderService(client, engine, journal, confirm=lambda text: True, env=env).submit_close(pos, f"{args.reason_type}: {args.reason}", now, args.size)
                print(("CLOSED: " if res.placed else "NOT CLOSED: ") + res.status)
                return 0 if res.placed else 1
            return 0
        if args.cmd == "stop":
            pos = _find(positions, args.deal_id)
            if args.breakeven:
                new = pos.open_level
            elif args.level is not None:
                new = args.level
            else:
                new = (pos.bid - args.distance) if pos.direction == "BUY" else (pos.offer + args.distance)
            print(f"{mode}: move the stop on {describe(pos, open_trades.get(pos.deal_id))}\n  from {pos.stop_level} to {new:g}")
            if execute:
                print("STOP MOVED:", apply_stop(client, journal, pos, new, now, "manual"))
            return 0
        if args.cmd == "trailing":
            pos = _find(positions, args.deal_id)
            tr_ = open_trades.get(pos.deal_id)
            min_stop = client.get_market_rules(pos.epic).min_stop_distance
            plan = native_trailing_plan(pos, initial_risk_points(pos, tr_), min_stop)
            if plan is None:
                print(f"No trailing stop to set on {describe(pos, tr_)}\n  (it needs to be up at least 1R, not already trailing, and have room above the market minimum)")
                return 0
            stop, distance, step = plan
            print(f"{mode}: trailing stop on {describe(pos, tr_)}\n  stop to {stop:g}, trailing distance {distance:g} points, step {step:g} points")
            if execute:
                try:
                    print("TRAILING SET:", apply_trailing(client, journal, pos, stop, distance, step, now))
                except (IGError, OrderRejected) as exc:
                    print(f"NOT SET: {exc}\n" + MANUAL_TRAILING.format(distance=distance, step=step))
                    return 1
            else:
                print("If IG refuses it through the API, the command prints the manual steps instead.")
            return 0
        if args.cmd == "limit":
            pos = _find(positions, args.deal_id)
            print(f"{mode}: set the take-profit on {describe(pos, open_trades.get(pos.deal_id))}\n  from {pos.limit_level} to {args.level:g}")
            if execute:
                print("TAKE-PROFIT SET:", apply_limit(client, journal, pos, args.level, now))
            return 0
        if args.cmd == "trail":
            done = 0
            for p in positions:
                tr = open_trades.get(p.deal_id)
                if p.trailing_distance:
                    print(f"{p.instrument_name} [{p.deal_id}]: IG is trailing the stop ({p.trailing_distance:g} points), left alone")
                    continue
                new = trail_target(p, initial_risk_points(p, tr))
                if new is None:
                    print(f"{p.instrument_name} [{p.deal_id}]: no change ({(lambda m: 'n/a' if m is None else f'{m:+.2f}R')(move_in_r(p, initial_risk_points(p, tr)))})")
                    continue
                print(f"{mode}: {p.instrument_name} [{p.deal_id}] stop {p.stop_level} -> {new:g}")
                if execute:
                    apply_stop(client, journal, p, new, now, "trail rule")
                done += 1
            print(f"{done} stop(s) {'moved' if execute else 'would move'}.")
            return 0
    except (RiskViolation, ValueError, LookupError, PermissionError) as exc:
        print(f"REFUSED: {exc}")
        return 1
    except Exception as exc:  # type name only: messages can carry sensitive detail
        print(f"FAILED: {type(exc).__name__}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
