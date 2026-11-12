"""Order flow: risk check, human confirmation, placement with a stop, verification, journal.

Nothing here decides what to trade. It takes a proposal, asks the risk engine,
asks the confirmation callback, and only then calls the client. The client adds
its own guards: a mandatory stop, user_confirmed, and live orders disabled
unless explicitly enabled.

The confirmation callback is injected. The default asks you to type "yes". Any
automatic confirmation is a deliberate change to make later, only after paper
results justify it, and it must be recorded in CLAUDE.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable

from .context import ContextVerdict
from .events import Event
from .ig_client import OrderRejected, Position
from .journal import Journal
from .risk import AccountState, EntryProposal, RiskEngine

ConfirmFn = Callable[[str], bool]


def terminal_confirm(text: str) -> bool:
    print(text)
    return input('Type "yes" to place this order: ').strip().lower() == "yes"


@dataclass(frozen=True)
class OrderResult:
    placed: bool
    status: str
    reasons: tuple[str, ...] = ()
    deal_id: str | None = None
    size: float = 0.0
    stop_level: float | None = None
    trade_id: int | None = None


class OrderService:
    def __init__(self, client: Any, risk: RiskEngine, journal: Journal, confirm: ConfirmFn = terminal_confirm, env: str = "demo"):
        self.client, self.risk, self.journal, self.confirm, self.env = client, risk, journal, confirm, env

    def submit_entry(
        self,
        *,
        epic: str,
        expiry: str,
        market: str,
        direction: str,
        stop_distance: float,
        target_distance: float | None,
        setup: str,
        account: AccountState,
        events: list[Event],
        now: datetime,
        context: ContextVerdict,
        driver: str = "",
        thesis: str = "",
        invalidation: str = "",
        expect_size: float | None = None,
    ) -> OrderResult:
        ts = now.isoformat()
        rules = self.client.get_market_rules(epic)
        positions = self.client.get_positions()
        extra = tuple(v for v in (self._cooldown_veto(now), self._daily_trade_veto(now)) if v)
        proposal = EntryProposal(epic, direction, stop_distance, rules.min_deal_size, rules.min_stop_distance,
                                 size_factor=context.size_factor, vetoes=context.vetoes + extra)
        driver = driver or ",".join(context.drivers)
        decision = self.risk.evaluate_entry(proposal, account, positions, events, now)
        self.journal.record_decision(ts, epic, direction, setup, decision.allowed, decision.size, decision.reasons,
                                     {"stop_distance": stop_distance, "target_distance": target_distance, "equity": account.equity,
                                      "size_factor": context.size_factor, "context_notes": list(context.notes)})
        if not decision.allowed:
            return OrderResult(False, "refused by risk rules", decision.reasons)
        if expect_size is not None and abs(decision.size - expect_size) > 1e-9:
            reason = f"size {decision.size:g} differs from the confirmed plan {expect_size:g}: not placed"
            self.journal.record_event(ts, "size_mismatch", reason)
            return OrderResult(False, "size differs from the confirmed plan", (reason,))

        text = (
            f"{direction} {market} ({epic}, {expiry})\n"
            f"  stake £{decision.size}/pt, stop {stop_distance:.1f} pts, target "
            f"{'none' if target_distance is None else f'{target_distance:.1f} pts'}\n"
            f"  risk £{decision.risk_gbp:.2f} ({decision.risk_pct:.1f}% of £{account.equity:.2f}), setup {setup}, env {self.env}\n"
            f"  quote {rules.bid}/{rules.offer}"
            + "".join(f"\n  context: {n}" for n in context.notes)
            + "".join(f"\n  note: {n}" for n in decision.notes)
        )
        if not self.confirm(text):
            self.journal.record_event(ts, "declined", f"{direction} {epic} declined at confirmation")
            return OrderResult(False, "declined at confirmation")

        ref = self.client.place_market_order(
            epic=epic, expiry=expiry, direction=direction, size=decision.size,
            stop_distance=stop_distance, limit_distance=target_distance, user_confirmed=True,
        )
        try:
            conf = self.client.confirm_deal(ref)
        except OrderRejected as exc:
            self.journal.record_event(ts, "deal_rejected", f"{exc.deal_reference}: {exc.reason}")
            return OrderResult(False, f"deal rejected: {exc.reason}")

        if conf.stop_level is None:
            self.journal.record_event(ts, "CRITICAL_no_stop", f"deal {conf.deal_id} has no stop: closing it")
            if conf.deal_id:
                self.client.close_position(deal_id=conf.deal_id, direction=direction, size=decision.size, user_confirmed=True)
            return OrderResult(False, "no stop was attached: position closed", deal_id=conf.deal_id)

        target_level = None
        if target_distance is not None and conf.level is not None:
            target_level = conf.level + target_distance if direction == "BUY" else conf.level - target_distance
        trade_id = self.journal.open_trade(
            opened_at=ts, epic=epic, direction=direction, size=decision.size,
            entry=conf.level if conf.level is not None else 0.0, stop=conf.stop_level,
            target=target_level, risk_gbp=decision.risk_gbp, env=self.env, market=market, expiry=expiry,
            setup=setup, driver=driver, thesis=thesis, invalidation=invalidation,
            deal_ref=ref, deal_id=conf.deal_id,
        )
        return OrderResult(True, "placed", deal_id=conf.deal_id, size=decision.size, stop_level=conf.stop_level, trade_id=trade_id)

    def _daily_trade_veto(self, now: datetime) -> str | None:
        """A cap on new entries per UTC day, so unattended placement cannot run away."""
        cap = self.risk.limits.max_new_trades_per_day
        if cap <= 0:
            return None
        n = self.journal.trades_opened_on(now.date().isoformat())
        return f"daily limit: {n} new trade(s) already opened today, the maximum is {cap}" if n >= cap else None

    def _cooldown_veto(self, now: datetime) -> str | None:
        """Poker-style tilt rule: after N losing trades in a row, no new entry until the cooldown has passed."""
        lim = self.risk.limits
        if lim.cooldown_losses <= 0 or lim.cooldown_hours <= 0:
            return None
        closed = self.journal.closed_trades()
        last = closed[-lim.cooldown_losses:]
        if len(last) < lim.cooldown_losses or not all((t["net_pnl"] or 0.0) < 0 for t in last):
            return None
        closed_at = datetime.fromisoformat(last[-1]["closed_at"])
        if closed_at.tzinfo is None:
            closed_at = closed_at.replace(tzinfo=now.tzinfo)
        remaining = (closed_at + timedelta(hours=lim.cooldown_hours) - now).total_seconds() / 3600
        if remaining > 0:
            return f"cooldown: {lim.cooldown_losses} losing trades in a row, {remaining:.1f} hours left before the next entry"
        return None

    def submit_close(self, position: Position, reason: str, now: datetime, size: float | None = None) -> OrderResult:
        """Close all or part of a position. Closing reduces risk but still needs confirmation."""
        ts = now.isoformat()
        close_size = position.size if size is None else size
        if not self.confirm(f"CLOSE {close_size} of {position.direction} {position.instrument_name} ({position.epic}): {reason}"):
            self.journal.record_event(ts, "declined", f"close of {position.deal_id} declined")
            return OrderResult(False, "declined at confirmation")

        ref = self.client.close_position(deal_id=position.deal_id, direction=position.direction, size=close_size, user_confirmed=True)
        try:
            conf = self.client.confirm_deal(ref)
        except OrderRejected as exc:
            self.journal.record_event(ts, "deal_rejected", f"{exc.deal_reference}: {exc.reason}")
            return OrderResult(False, f"close rejected: {exc.reason}")

        trade = next((t for t in self.journal.open_trades() if t["deal_id"] == position.deal_id), None)
        if close_size >= position.size and trade is not None and conf.level is not None:
            self.journal.close_trade(trade["id"], ts, conf.level, reason)
        else:
            self.journal.record_event(ts, "partial_close", f"{position.deal_id}: closed {close_size} ({reason})")
            if trade is not None and conf.level is not None and 0 < close_size < trade["size"]:
                self.journal.partial_close(trade["id"], close_size, conf.level)
        return OrderResult(True, "closed" if close_size >= position.size else "partly closed", deal_id=position.deal_id, size=close_size)
