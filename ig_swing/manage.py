"""Manage open positions: list, close (in profit or in loss), tighten a stop, set a take-profit, trail a stop by rule, and review history.

  python3 -m ig_swing.manage list
  python3 -m ig_swing.manage close --deal-id D1 --reason-type thesis_invalidated --reason "..." [--size 0.01] [--execute]
  python3 -m ig_swing.manage stop  --deal-id D1 (--level L | --breakeven | --distance POINTS) [--execute]
  python3 -m ig_swing.manage limit --deal-id D1 --level L [--execute]
  python3 -m ig_swing.manage trail [--execute]          the mechanical rule: stop to breakeven at +1R, to +1R at +2R
  python3 -m ig_swing.manage history                    closed trades by exit reason: does cutting early help?

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

from .ig_client import Position

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


def describe(pos: Position, trade: dict | None) -> str:
    r = initial_risk_points(pos, trade)
    m = move_in_r(pos, r)
    price = pos.bid if pos.direction == "BUY" else pos.offer
    stop = "NONE (unprotected!)" if pos.stop_level is None else f"{pos.stop_level:g}"
    limit = "none" if pos.limit_level is None else f"{pos.limit_level:g}"
    risk = ""
    if pos.stop_level is not None:
        stopped = pos.size * (pos.stop_level - pos.open_level if pos.direction == "BUY" else pos.open_level - pos.stop_level)
        risk = f", if stopped out: £{stopped:+.2f}" + (" (profit locked in)" if stopped > 0.005 else " (breakeven)" if stopped > -0.005 else "")
    pnl = "n/a" if pos.unrealised_pnl is None else f"£{pos.unrealised_pnl:+.2f}"
    thesis = f"\n    thesis: {trade['thesis']}\n    invalidation: {trade.get('invalidation') or 'not recorded'}" if trade and trade.get("thesis") else ""
    return (f"{pos.direction} {pos.instrument_name} [{pos.deal_id}] £{pos.size:g}/pt, opened {pos.open_level:g}, now {price if price is not None else 'n/a'}, "
            f"P&L {pnl}" + (f", {m:+.2f}R" if m is not None else "") + f", stop {stop}, take-profit {limit}{risk}{thesis}")


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
                print(describe(p, open_trades.get(p.deal_id)))
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
