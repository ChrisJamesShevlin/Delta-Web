"""Place a manual order through the risk layer and the order flow. A DRY RUN unless two things are both true.

  python3 -m ig_swing.place --market "EUR/USD" --direction SELL --stop 100 --target 200 --p-win 0.40 \
         --playbook news_thesis --thesis "..." [--idea-id N]

The dry run prints the exact order (market, direction, stake, stop, target, risk, margin) and the risk verdict, and places
nothing. It also prints a short confirmation token that is tied to those exact details. To place the order BOTH must hold:
the environment variable IG_LIVE_ORDERS=yes is set, and the token is passed back with --confirm. The assistant does this under the user's standing
authorisation of 2026-11-11 (no per-order yes) for the challenge account only, after its own dry run passes, and only with the
challenge risk profile active. Changing the stake, stop, target, market or direction changes
the token, so a different order can never go through on an old confirmation.

Stake: the minimum stake until calibrated (Kelly only filters), then 1/8 Kelly from --p-win and the stop and target distances (ig_swing.challenge), scaled below the profile's ceiling,
rounded down to the minimum stake step but never below the market's minimum, and checked by the risk engine (floor, cooldown,
drawdown brake, loss limits, position limits, mandatory stop). If Kelly says do not trade, the command refuses unless
--ignore-kelly is given, which is logged. News and event blackouts are not applied here: the challenge allows event plays.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any

from .challenge import ALL_MARKETS, KELLY_FRACTION, PLAYBOOKS, calibration, kelly_fraction, load_ideas, looks_like_epic, measure, resolve_market, scored_ideas, suggested_risk_pct
from .context import ContextVerdict
from .orders import OrderService
from .risk import AccountState, EntryProposal, RiskEngine

UTC = timezone.utc


def token_for(market: str, direction: str, stake: float, stop: float, target: float) -> str:
    raw = f"{market}|{direction.upper()}|{stake:.2f}|{stop:.1f}|{target:.1f}"
    return hashlib.sha256(raw.encode()).hexdigest()[:8]


def plan_order(client: Any, journal: Any, engine: RiskEngine, market: str, direction: str, stop: float, target: float, p_win: float,
               account: AccountState, now: datetime, calibrated: bool = False, ignore_kelly: bool = False, info: dict[str, Any] | None = None) -> dict[str, Any]:
    """Everything needed to show, and later place, one order. Pure apart from reading the client."""
    epic, margin_pct = (info["epic"], info["margin"]) if info else (ALL_MARKETS[market][0], ALL_MARKETS[market][2])
    rules = client.get_market_rules(epic)
    rr = target / stop
    kelly = kelly_fraction(p_win, rr)
    risk_pct = suggested_risk_pct(p_win, rr, calibrated=calibrated)
    reasons: list[str] = []
    if risk_pct <= 0 and not ignore_kelly:
        reasons.append(f"Kelly says do not trade this: a {p_win:.0%} chance at {rr:.1f} to 1 has no positive stake after costs (use --ignore-kelly to override, logged)")
    ceiling = engine.limits.risk_per_trade_pct
    # Staking rule of 2026-11-12: minimum stake until calibrated; Kelly is only a skip filter until then.
    factor = min(1.0, max(risk_pct, 0.0) / ceiling) if (calibrated and risk_pct > 0) else 0.01
    positions = client.get_positions()
    proposal = EntryProposal(epic, direction.upper(), stop, rules.min_deal_size, rules.min_stop_distance, size_factor=max(factor, 0.01))
    svc = OrderService(client, engine, journal)
    vetoes = tuple(v for v in (svc._cooldown_veto(now), svc._daily_trade_veto(now)) if v)
    if vetoes:
        proposal = EntryProposal(epic, direction.upper(), stop, rules.min_deal_size, rules.min_stop_distance, size_factor=max(factor, 0.01), vetoes=vetoes)
    decision = engine.evaluate_entry(proposal, account, positions, [], now)
    reasons += list(decision.reasons)
    price = (rules.bid + rules.offer) / 2
    entry = rules.offer if direction.upper() == "BUY" else rules.bid
    stake = decision.size
    return {
        "market": market, "epic": epic, "direction": direction.upper(), "stop": stop, "target": target, "reward_risk": rr, "p_win": p_win,
        "kelly": kelly, "kelly_eighth_pct": 100 * KELLY_FRACTION * kelly, "risk_pct_target": risk_pct, "calibrated": calibrated,
        "decision": decision, "reasons": reasons, "allowed": decision.allowed and not (risk_pct <= 0 and not ignore_kelly),
        "stake": stake, "entry": entry, "price": price, "spread": rules.offer - rules.bid, "margin": stake * price * margin_pct / 100,
        "token": token_for(market, direction, stake, stop, target) if stake else "",
    }


def describe(plan: dict[str, Any], equity: float) -> str:
    d = plan["decision"]
    lines = [
        f"ORDER: {plan['direction']} {plan['market']} at about {plan['entry']:.1f}",
        f"  stake £{plan['stake']:.2f} a point, stop {plan['stop']:g} points, target {plan['target']:g} points (reward to risk {plan['reward_risk']:.1f})",
        f"  risk £{d.risk_gbp:.2f} ({d.risk_pct:.1f}% of £{equity:.2f}), margin about £{plan['margin']:.2f}, spread {plan['spread']:.1f} points",
        f"  your stated chance {plan['p_win']:.0%}: full Kelly {plan['kelly'] * 100:.1f}%, 1/8 Kelly {plan['kelly_eighth_pct']:.1f}%; "
        + ("stake follows 1/8 Kelly (calibrated)" if plan["calibrated"] else "stake is the minimum until calibrated (Kelly is a skip filter only)"),
    ]
    lines += [f"  note: {n}" for n in d.notes]
    if plan["reasons"]:
        lines.append("  REFUSED: " + "; ".join(plan["reasons"]))
    return "\n".join(lines)


def execute(client: Any, journal: Any, engine: RiskEngine, plan: dict[str, Any], account: AccountState, now: datetime, token: str | None,
            thesis: str, playbook: str, env: str = "live") -> dict[str, Any]:
    """Place the order only if the token matches the exact plan. The confirmation callback is the token check."""
    if not plan["allowed"] or not plan["token"]:
        return {"placed": False, "status": "not allowed by the risk rules"}
    if not token or token != plan["token"]:
        return {"placed": False, "status": "confirmation token missing or does not match this exact order"}
    shown: list[str] = []
    svc = OrderService(client, engine, journal, confirm=lambda text: shown.append(text) or True, env=env)
    res = svc.submit_entry(
        epic=plan["epic"], expiry="DFB", market=plan["market"], direction=plan["direction"], stop_distance=plan["stop"], target_distance=plan["target"],
        setup=playbook, account=account, events=[], now=now, context=ContextVerdict(plan["direction"], size_factor=1.0),
        driver="challenge", thesis=thesis, invalidation=f"stop {plan['stop']:g} points",
    )
    return {"placed": res.placed, "status": res.status, "reasons": res.reasons, "deal_id": res.deal_id, "size": res.size, "stop_level": res.stop_level}


def build_account(client: Any, journal: Any, account_id: str, now: datetime) -> AccountState:
    from datetime import timedelta

    equity = client.get_account(account_id).equity
    today = now.date()
    journal.record_equity(today.isoformat(), equity, "live")
    monday = today - timedelta(days=today.weekday())
    sod = journal.equity_on_or_before(today.isoformat()) or equity
    sow = journal.equity_on_or_before(monday.isoformat()) or sod
    return AccountState(equity, sod, sow, journal.peak_equity())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ig_swing.place", description=__doc__.splitlines()[0])
    ap.add_argument("--market", required=True, help="any IG market: a name from the briefing, an epic, or search words")
    ap.add_argument("--direction", required=True, choices=["BUY", "SELL", "buy", "sell"])
    ap.add_argument("--stop", type=float, required=True)
    ap.add_argument("--target", type=float, required=True)
    ap.add_argument("--p-win", type=float, required=True)
    ap.add_argument("--playbook", required=True, choices=PLAYBOOKS)
    ap.add_argument("--thesis", required=True)
    ap.add_argument("--idea-id", type=int, default=None)
    ap.add_argument("--confirm", default=None, help="the token printed by the dry run, passed only after the user says yes to that exact order")
    ap.add_argument("--ignore-kelly", action="store_true")
    args = ap.parse_args(argv)

    from .bars import clean_daily
    from .config import base_url, ig_env, load_credentials, risk_limits
    from .ig_client import IGClient
    from .journal import Journal
    from .risk import KillSwitch

    now = datetime.now(UTC)
    try:
        env = ig_env()
        creds = load_credentials(env)
        live_ok = env == "live" and os.environ.get("IG_LIVE_ORDERS", "").strip().lower() == "yes"
        client = IGClient(creds, base_url=base_url(env), allow_live=(env == "live"), live_orders_enabled=live_ok)
        journal = Journal()
        engine = RiskEngine(risk_limits(env), KillSwitch())
        account = build_account(client, journal, creds.account_id, now)
        ideas = scored_ideas(journal.events())
        calibrated = False
        if len(ideas) >= 30:
            calibrated = calibration(measure(ideas, lambda key: clean_daily(client.get_prices(key if looks_like_epic(key) else ALL_MARKETS[key][0], "DAY", 60), now))["finished"])["calibrated"]
        info = resolve_market(client, args.market)
        plan = plan_order(client, journal, engine, info["name"], args.direction, args.stop, args.target, args.p_win, account, now, calibrated, args.ignore_kelly, info)
        print(f"Environment {env.upper()}, risk profile {engine.limits.name}, live orders {'ENABLED' if live_ok else 'off'}")
        print(describe(plan, account.equity))
        if args.confirm is None:
            if plan["allowed"]:
                print(f"DRY RUN: nothing was placed. To place this exact order the user must say yes, then re-run with IG_LIVE_ORDERS=yes and --confirm {plan['token']}")
            return 0 if plan["allowed"] else 1
        if not live_ok:
            print("REFUSED: live orders are off. Set IG_LIVE_ORDERS=yes for this one command.")
            return 1
        if engine.limits.name != "challenge":
            print(f"REFUSED: the risk profile is {engine.limits.name}, not challenge. The floor, cooldown, drawdown brake and daily limit only apply with "
                  "IG_RISK_PROFILE=challenge in .env, so nothing is placed without it.")
            return 1
        if args.ignore_kelly:
            journal.record_event(now.isoformat(), "kelly_override", json.dumps({"market": info["name"], "direction": args.direction, "p_win": args.p_win}))
        res = execute(client, journal, engine, plan, account, now, args.confirm, args.thesis, args.playbook, env)
        if res["placed"] and args.idea_id:
            journal.record_event(now.isoformat(), "challenge_live", json.dumps({"idea_id": args.idea_id, "deal_id": res["deal_id"]}))
        print(("PLACED: " if res["placed"] else "NOT PLACED: ") + res["status"] + (f", deal {res['deal_id']}, stake £{res['size']}, stop level {res['stop_level']}" if res["placed"] else ""))
        return 0 if res["placed"] else 1
    except Exception as exc:  # type name only: messages can carry sensitive detail
        print(f"FAILED: {type(exc).__name__}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
