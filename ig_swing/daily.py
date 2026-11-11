"""Daily plan: what the system would do today. Read-only. It never places an order.

Run from the repo root:  python3 -m ig_swing.daily
Reads the daily bars, your account and open positions, the calendar, then
reports the technical signal and whether the risk rules would allow it. Every
run is written to the journal, including "no signal", so refusals are on record.
Uses up to 120 points of the weekly price allowance (cached for 6 hours).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

from .context import ContextVerdict, assess, plan_context_reductions
from .events import Event
from .ig_client import Position
from .inputs import CrossAssetInput
from .news import Headline
from .journal import Journal
from .risk import AccountState, Decision, EntryProposal, ReductionProposal, RiskEngine, plan_event_reductions
from .strategy import Signal, StrategyParams, generate_signal

UTC = timezone.utc
BARS = 120


@dataclass(frozen=True)
class Plan:
    signal: Signal | None
    decision: Decision | None
    account: AccountState
    positions: list[Position]
    notes: list[str] = field(default_factory=list)
    context: ContextVerdict | None = None
    reductions: list[ReductionProposal] = field(default_factory=list)


def build_plan(
    client: Any,
    journal: Journal,
    engine: RiskEngine,
    strat: StrategyParams,
    *,
    epic: str,
    account_id: str,
    env: str,
    events: list[Event],
    now: datetime,
    inputs: list[CrossAssetInput] | None,
    headlines: list[Headline] | None,
    bars: int = BARS,
) -> Plan:
    notes: list[str] = []
    ts = now.isoformat()
    today = now.date()
    monday = today - timedelta(days=today.weekday())

    equity = client.get_account(account_id).equity
    journal.record_equity(today.isoformat(), equity, env)
    sod = journal.equity_on_or_before(today.isoformat()) or equity
    sow = journal.equity_on_or_before(monday.isoformat()) or sod
    account = AccountState(equity, sod, sow)

    positions = client.get_positions()
    for p in positions:
        if p.stop_level is None:
            notes.append(f"WARNING: open position in {p.instrument_name} has no stop")

    rules = client.get_market_rules(epic)
    reductions = plan_event_reductions(positions, events, now, rules.min_deal_size)
    reductions += plan_context_reductions(positions, inputs, headlines, rules.min_deal_size)
    for r in reductions:
        journal.record_event(ts, "reduction_proposed", f"{r.deal_id}: close {r.close_size}: {r.reason}")

    df = client.get_prices(epic, "DAY", bars)
    signal = generate_signal(df, strat)
    if signal is None:
        journal.record_decision(ts, epic, None, None, False, 0.0, ["no signal"], {"equity": equity, "bars": len(df)})
        return Plan(None, None, account, positions, notes, None, reductions)

    context = assess(signal.direction, inputs, headlines)
    proposal = EntryProposal(epic, signal.direction, signal.stop_distance, rules.min_deal_size, rules.min_stop_distance,
                             size_factor=context.size_factor, vetoes=context.vetoes)
    decision = engine.evaluate_entry(proposal, account, positions, events, now)
    journal.record_decision(ts, epic, signal.direction, signal.setup, decision.allowed, decision.size, decision.reasons,
                            {"equity": equity, "stop_distance": signal.stop_distance, "target_distance": signal.target_distance,
                             "reason": signal.reason, "size_factor": context.size_factor, "drivers": list(context.drivers),
                             "context_notes": list(context.notes)})
    return Plan(signal, decision, account, positions, notes, context, reductions)


def format_plan(plan: Plan) -> str:
    a = plan.account
    lines = [f"Equity £{a.equity:.2f} (start of day £{a.start_of_day_equity:.2f}, start of week £{a.start_of_week_equity:.2f})",
             f"Open positions: {len(plan.positions)}"]
    lines += [f"  {n}" for n in plan.notes]
    if plan.signal is None:
        lines.append("Signal: none today.")
    else:
        s = plan.signal
        lines.append(f"Signal: {s.direction} ({s.setup}) on bar {s.bar_date}: {s.reason}")
        lines.append(f"  stop {s.stop_distance:.1f} pts, target {s.target_distance:.1f} pts, ATR {s.atr:.1f}")
        d = plan.decision
        if d is not None and d.allowed:
            lines.append(f"Risk: ALLOWED, stake £{d.size}/pt, risk £{d.risk_gbp:.2f} ({d.risk_pct:.1f}%)")
        elif d is not None:
            lines.append("Risk: REFUSED")
            lines += [f"  - {r}" for r in d.reasons]
    if plan.context is not None:
        lines += [f"  context: {n}" for n in plan.context.notes]
    for r in plan.reductions:
        lines.append(f"Proposed (not placed): close {r.close_size} of deal {r.deal_id}: {r.reason}")
    lines.append("Plan only. No order was placed.")
    return "\n".join(lines)


def main() -> int:
    import requests

    from .config import base_url, ig_env, load_credentials, optional_key, risk_limits
    from .events import load_events
    from .ig_client import IGClient
    from .inputs import load_inputs
    from .news import load_headlines
    from .risk import KillSwitch

    now = datetime.now(UTC)
    try:
        env = ig_env()
        creds = load_credentials(env)
        client = IGClient(creds, base_url=base_url(env), allow_live=(env == "live"))
        session = requests.Session()
        events, event_notes = load_events(session, now.date(), now.date() + timedelta(days=7), optional_key("FRED_API_KEY"))
        engine = RiskEngine(risk_limits(env), KillSwitch())
        epic = "IX.D.SPTRD.MONTH1.IP"
        inputs, input_notes = load_inputs(client)
        headlines, news_notes = load_headlines(session, optional_key("FINNHUB_API_KEY"), now)
        event_notes = event_notes + input_notes + news_notes
        plan = build_plan(client, Journal(), engine, StrategyParams(), epic=epic, account_id=creds.account_id,
                          env=env, events=events, now=now, inputs=inputs, headlines=headlines)
    except Exception as exc:  # type name only: messages can carry sensitive detail
        print(f"FAILED: {type(exc).__name__}")
        return 1
    print(f"Environment: {env.upper()}  market: {epic}")
    print(format_plan(plan))
    for n in event_notes:
        print(f"  note: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
