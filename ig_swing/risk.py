"""Risk layer: sizing, loss limits, kill switch, exposure caps, event blackout.

Pure logic. Nothing here places an order or calls IG. The order flow must ask
``RiskEngine.evaluate_entry`` first and obey the answer.

Rules enforced (limits come from ig_swing.config, profile chosen by IG_ENV):
- Size from the stop distance, never above the per-trade cap. If even the
  smallest stake would risk more than the cap, the trade is refused. The cap is
  never loosened to fit a small account.
- Daily and weekly loss limits trip a persistent kill switch.
- Maximum open positions and maximum total open risk. A position with no stop
  counts as unbounded risk and blocks new entries.
- One position per market: no adding to, or averaging down on, a position.
- Event blackout: no new entry shortly before or after a high-impact release.
- A stop may be tightened, never moved further away.

Risk is stake per point times stop distance. It excludes slippage: stops are
not guaranteed, so a gap can lose more than the stop distance.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import RiskLimits
from .events import HIGH, Event
from .ig_client import Position

UTC = timezone.utc
FOMC_LABEL = "FOMC rate decision"


class RiskViolation(Exception):
    """A proposed action breaks a risk rule."""


def _positive(value) -> bool:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(v) and v > 0


def _floor_to_step(value: float, step: float) -> float:
    return round(math.floor(value / step + 1e-9) * step, 6)


# ---- sizing ---------------------------------------------------------------


@dataclass(frozen=True)
class SizeResult:
    allowed: bool
    size: float
    risk_gbp: float
    risk_pct: float
    reason: str = ""


def size_position(
    balance: float,
    stop_distance: float,
    limits: RiskLimits,
    min_size: float,
    step: float = 0.01,
    risk_pct: float | None = None,
) -> SizeResult:
    """Stake in pounds per point, sized so the stop risks the target percentage."""
    if not _positive(balance):
        return SizeResult(False, 0.0, 0.0, 0.0, "balance must be above zero")
    if not _positive(stop_distance):
        return SizeResult(False, 0.0, 0.0, 0.0, "a stop distance above zero is mandatory")
    if not _positive(min_size) or not _positive(step):
        return SizeResult(False, 0.0, 0.0, 0.0, "minimum size and step must be above zero")

    pct = min(risk_pct if risk_pct is not None else limits.risk_per_trade_pct, limits.max_risk_per_trade_pct)
    cap_gbp = balance * limits.max_risk_per_trade_pct / 100
    size = _floor_to_step(balance * pct / 100 / stop_distance, step)
    if size < min_size:
        size = min_size
    risk = size * stop_distance
    if risk > cap_gbp + 1e-9:
        return SizeResult(
            False, 0.0, risk, risk / balance * 100,
            f"smallest allowed stake {min_size} would risk £{risk:.2f} ({risk / balance * 100:.1f}% of balance), "
            f"over the {limits.max_risk_per_trade_pct}% cap",
        )
    return SizeResult(True, size, risk, risk / balance * 100)


# ---- open risk ------------------------------------------------------------


def open_risk(positions: list[Position]) -> tuple[float, list[str]]:
    """Total £ at risk across open positions, plus deal ids that have no stop."""
    total = 0.0
    unprotected: list[str] = []
    for p in positions:
        if p.stop_level is None:
            unprotected.append(p.deal_id)
            continue
        distance = p.open_level - p.stop_level if p.direction == "BUY" else p.stop_level - p.open_level
        total += p.size * max(0.0, distance)  # a stop beyond breakeven risks nothing
    return total, unprotected


def validate_stop_move(direction: str, current_stop: float, new_stop: float) -> None:
    """Raise unless the new stop is the same or tighter. Never widen a stop."""
    if direction == "BUY":
        widened = new_stop < current_stop
    elif direction == "SELL":
        widened = new_stop > current_stop
    else:
        raise ValueError("direction must be BUY or SELL")
    if widened:
        raise RiskViolation(f"stop move refused: {current_stop} to {new_stop} widens the stop on a {direction}")


# ---- kill switch ----------------------------------------------------------


def next_day_start(now: datetime) -> datetime:
    return datetime(now.year, now.month, now.day, tzinfo=UTC) + timedelta(days=1)


def next_week_start(now: datetime) -> datetime:
    monday = datetime(now.year, now.month, now.day, tzinfo=UTC) - timedelta(days=now.weekday())
    return monday + timedelta(days=7)


class KillSwitch:
    """Persistent halt on new orders. Unreadable state fails safe (halted)."""

    def __init__(self, path: str | Path = "data/kill_switch.json"):
        self.path = Path(path)

    def status(self, now: datetime) -> tuple[bool, str]:
        if not self.path.exists():
            return False, ""
        try:
            data = json.loads(self.path.read_text())
            until = data.get("until")
            reason = str(data.get("reason", ""))
            if until and now >= datetime.fromisoformat(until):
                return False, ""
            return True, reason
        except Exception:
            return True, "kill switch state unreadable, failing safe"

    def trip(self, reason: str, until: datetime | None = None, now: datetime | None = None) -> None:
        """Halt new orders. A new trip never shortens an existing, longer halt."""
        now = now or datetime.now(UTC)
        active, _ = self.status(now)
        if active:
            try:
                current = json.loads(self.path.read_text()).get("until")
            except Exception:
                current = None
            current_dt = datetime.fromisoformat(current) if current else None
            if current_dt is None and self.path.exists():
                return  # indefinite halt already in place
            if until is not None and current_dt is not None and current_dt >= until:
                return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"reason": reason, "tripped_at": now.isoformat(), "until": until.isoformat() if until else None})
        )

    def reset(self) -> None:
        """Manual reset only. Nothing in the engine calls this."""
        if self.path.exists():
            self.path.unlink()


# ---- loss limits ----------------------------------------------------------


@dataclass(frozen=True)
class AccountState:
    equity: float
    start_of_day_equity: float
    start_of_week_equity: float


def check_loss_limits(account: AccountState, limits: RiskLimits) -> list[str]:
    """Reasons to halt new orders because a loss limit is reached. Empty if fine."""
    reasons = []
    if _positive(account.start_of_day_equity):
        loss = (account.start_of_day_equity - account.equity) / account.start_of_day_equity * 100
        if loss >= limits.daily_loss_limit_pct:
            reasons.append(f"daily loss limit reached ({loss:.1f}% down, limit {limits.daily_loss_limit_pct}%)")
    if _positive(account.start_of_week_equity):
        loss = (account.start_of_week_equity - account.equity) / account.start_of_week_equity * 100
        if loss >= limits.weekly_loss_limit_pct:
            reasons.append(f"weekly loss limit reached ({loss:.1f}% down, limit {limits.weekly_loss_limit_pct}%)")
    return reasons


# ---- events ---------------------------------------------------------------


@dataclass(frozen=True)
class EventRules:
    pre_hours: float = 12.0        # no new entry this long before a high-impact release
    fomc_pre_hours: float = 24.0   # and this long before an FOMC decision
    post_hours: float = 2.0        # and this long after
    reduce_hours: float = 4.0      # propose halving open positions this long before


def blocking_events(events: list[Event], now: datetime, rules: EventRules = EventRules()) -> list[Event]:
    """High-impact events whose blackout window includes ``now``.

    Date-only events (FRED gives no time) block that whole UTC day only.
    """
    out = []
    for e in events:
        if e.tier != HIGH:
            continue
        if e.time_utc is None:
            if e.day == now.date():
                out.append(e)
            continue
        pre = rules.fomc_pre_hours if e.label == FOMC_LABEL else rules.pre_hours
        if e.time_utc - timedelta(hours=pre) <= now <= e.time_utc + timedelta(hours=rules.post_hours):
            out.append(e)
    return out


@dataclass(frozen=True)
class ReductionProposal:
    deal_id: str
    epic: str
    close_size: float
    reason: str


def plan_event_reductions(
    positions: list[Position],
    events: list[Event],
    now: datetime,
    min_size: float,
    rules: EventRules = EventRules(),
    step: float = 0.01,
) -> list[ReductionProposal]:
    """Propose closing half of each open position shortly before a timed high-impact event.

    A proposal only. Placing the close is the order layer's job and needs the
    usual confirmation. Positions too small to halve are left alone.
    """
    soon = [
        e for e in events
        if e.tier == HIGH and e.time_utc is not None and now <= e.time_utc <= now + timedelta(hours=rules.reduce_hours)
    ]
    if not soon:
        return []
    event = min(soon, key=lambda e: e.time_utc)
    out = []
    for p in positions:
        close = _floor_to_step(p.size / 2, step)
        if close < min_size or p.size - close < min_size:
            continue
        out.append(ReductionProposal(p.deal_id, p.epic, close, f"{event.label} in under {rules.reduce_hours:g}h: halve the position"))
    return out


# ---- the gate -------------------------------------------------------------


@dataclass(frozen=True)
class EntryProposal:
    epic: str
    direction: str
    stop_distance: float
    min_deal_size: float
    min_stop_distance: float
    size_factor: float = 1.0          # context layer: 0.5 halves the target risk
    vetoes: tuple[str, ...] = ()      # context layer: each is a reason to refuse


@dataclass(frozen=True)
class Decision:
    allowed: bool
    size: float
    risk_gbp: float
    risk_pct: float
    reasons: tuple[str, ...]


class RiskEngine:
    def __init__(self, limits: RiskLimits, kill_switch: KillSwitch, event_rules: EventRules = EventRules()):
        self.limits = limits
        self.kill_switch = kill_switch
        self.event_rules = event_rules

    def refresh_halts(self, account: AccountState, now: datetime) -> list[str]:
        """Trip the kill switch if a loss limit is reached. Returns the reasons."""
        reasons = check_loss_limits(account, self.limits)
        for r in reasons:
            until = next_week_start(now) if r.startswith("weekly") else next_day_start(now)
            self.kill_switch.trip(r, until=until, now=now)
        return reasons

    def evaluate_entry(
        self,
        proposal: EntryProposal,
        account: AccountState,
        positions: list[Position],
        events: list[Event],
        now: datetime,
    ) -> Decision:
        """Every reason to refuse, not just the first. Allowed only if there are none."""
        reasons: list[str] = []

        active, why = self.kill_switch.status(now)
        if active:
            reasons.append(f"kill switch active: {why}")
        reasons += self.refresh_halts(account, now)

        if proposal.direction not in ("BUY", "SELL"):
            reasons.append("direction must be BUY or SELL")
        if not _positive(proposal.stop_distance):
            reasons.append("a stop distance above zero is mandatory")
        elif proposal.stop_distance < proposal.min_stop_distance:
            reasons.append(f"stop distance {proposal.stop_distance} is below the market minimum {proposal.min_stop_distance}")

        if len(positions) >= self.limits.max_open_positions:
            reasons.append(f"already {len(positions)} open position(s), maximum is {self.limits.max_open_positions}")
        if any(p.epic == proposal.epic for p in positions):
            reasons.append("already holding this market: no adding to or averaging down on a position")

        open_gbp, unprotected = open_risk(positions)
        if unprotected:
            reasons.append(f"{len(unprotected)} open position(s) have no stop, so total risk is unbounded")

        reasons += [f"context veto: {v}" for v in proposal.vetoes]
        factor = proposal.size_factor if 0 < proposal.size_factor <= 1 else 1.0  # context may only shrink size
        size = size_position(account.equity, proposal.stop_distance, self.limits, proposal.min_deal_size,
                             risk_pct=self.limits.risk_per_trade_pct * factor)
        if not size.allowed:
            reasons.append(size.reason)
        else:
            total_cap = account.equity * self.limits.max_total_open_risk_pct / 100
            if open_gbp + size.risk_gbp > total_cap + 1e-9:
                reasons.append(
                    f"total open risk £{open_gbp + size.risk_gbp:.2f} would exceed the {self.limits.max_total_open_risk_pct}% cap (£{total_cap:.2f})"
                )

        for e in blocking_events(events, now, self.event_rules):
            when = f"{e.time_utc:%a %d %b %H:%M}Z" if e.time_utc else f"{e.day:%a %d %b} (time n/a)"
            reasons.append(f"event blackout: {e.label}, {when}")

        ok = not reasons
        return Decision(ok, size.size if ok else 0.0, size.risk_gbp if ok else 0.0, size.risk_pct if ok else 0.0, tuple(reasons))
