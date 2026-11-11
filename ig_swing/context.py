"""News and cross-asset context: deterministic rules that act as a second vote beside the technical signal.

The technical engine finds the setup. This layer decides how much conviction the
backdrop adds or removes, and what to do with positions already open. It cannot
originate a live trade (news-only ideas are only recorded, see ig_swing.shadow),
and it can never push size above the agreed per-trade target.

Conviction sizing for an entry (factor x the profile's target risk, capped at 1.0):
- news view aligned with the trade (BULLISH for BUY, BEARISH for SELL), MEDIUM or
  HIGH confidence: full target size.
- no view, a NEUTRAL or LOW-confidence view: half size (the news has not backed the trade).
- news view adverse, MEDIUM confidence: quarter size.
- news view adverse, HIGH confidence: no trade.
- without a view, BUY with several fresh stress headlines: quarter size.
Open positions: an adverse HIGH view proposes closing the whole position, an
adverse MEDIUM view proposes halving it. A technical flip that the news backs
proposes closing the old position so the new direction can be entered.

Price rules from IG's own VIX, oil and gold daily moves (all untested hypotheses,
pre-registered here, and logged as the trade "driver" so the report can later
split results by them):
- BUY is vetoed when the VIX is up by the spike threshold or more (risk-off).
- SELL is vetoed when the VIX is down by the spike threshold or more (vol collapse, rally).
- BUY is vetoed when oil is up by the oil threshold or more (the "oil shock" idea).
- Gold only adds a note. Its meaning is ambiguous on its own.
- Missing VIX or oil data vetoes every entry: we cannot confirm the backdrop.

Thresholds are starting points, not tuned values. Changing them after seeing
results moves the goalposts. Tested 2026-11-11 on FRED history (see CLAUDE.md and
ig_swing/rules_test.py): the three price rules are NOT validated, and none is
contradicted. They stay in as unvalidated judgements.

Limits: the stress-word count is keyword matching, so it is noisy. The news view is
a judgement by the assistant in a session, not a measured signal, and its value is
unproven: the journal archive exists so it can be tested. At the smallest stake a
reduction cannot take size below the market minimum.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .ig_client import Position
from .inputs import CrossAssetInput
from .news import Headline
from .news_view import NewsView
from .risk import ReductionProposal, _floor_to_step

VIX, OIL, GOLD = "VIX", "Oil (US crude)", "Gold"
REQUIRED = (VIX, OIL)

STRESS_WORDS = (
    "crash", "plunge", "tumble", "selloff", "sell-off", "slump", "panic", "turmoil",
    "war", "invasion", "attack", "downgrade", "bank run", "shutdown", "default",
)
_STRESS_RE = re.compile(r"(?<![-\w])(?:" + "|".join(re.escape(w) for w in STRESS_WORDS) + r")(?:s|es|ed)?\b", re.I)


@dataclass(frozen=True)
class ContextRules:
    vix_spike_pct: float = 10.0
    oil_spike_pct: float = 3.0
    gold_note_pct: float = 2.0
    stress_halve_count: int = 3
    stress_note_count: int = 1
    unsupported_factor: float = 0.5   # no view, or a neutral/low-confidence one
    adverse_factor: float = 0.25      # adverse medium-confidence view, or stress headlines with no view


@dataclass(frozen=True)
class ContextVerdict:
    direction: str
    vetoes: tuple[str, ...] = ()
    size_factor: float = 1.0
    notes: tuple[str, ...] = ()
    drivers: tuple[str, ...] = ()

    @property
    def risk_off_for_position(self) -> bool:
        """True when this backdrop argues for trimming a position held in ``direction``."""
        return bool(self.vetoes) or self.size_factor < 1.0


def stress_headlines(headlines: list[Headline]) -> list[Headline]:
    return [h for h in headlines if _STRESS_RE.search(h.title)]


def _by_name(inputs: list[CrossAssetInput]) -> dict[str, CrossAssetInput]:
    return {i.name: i for i in inputs if i.pct_change is not None}


def view_stance(direction: str, view: NewsView) -> tuple[bool, bool]:
    """(adverse, aligned) of a view relative to a trade direction. Aligned needs MEDIUM or HIGH confidence."""
    adverse = (direction == "BUY" and view.direction == "BEARISH") or (direction == "SELL" and view.direction == "BULLISH")
    backs = (direction == "BUY" and view.direction == "BULLISH") or (direction == "SELL" and view.direction == "BEARISH")
    return adverse, backs and view.confidence in ("MEDIUM", "HIGH")


def assess(direction: str, inputs: list[CrossAssetInput] | None, headlines: list[Headline] | None, rules: ContextRules = ContextRules(),
           view: NewsView | None = None) -> ContextVerdict:
    """Context verdict for a BUY or SELL. Pure: same inputs, same answer.

    ``view`` must already be unexpired: ig_swing.news_view.load_view checks that.
    """
    if inputs is None or headlines is None:
        return ContextVerdict(direction, vetoes=("context not loaded: cross-asset data and headlines are required",))
    have = _by_name(inputs)
    vetoes: list[str] = []
    notes: list[str] = []
    drivers: list[str] = []

    missing = [n for n in REQUIRED if n not in have]
    if missing:
        vetoes.append(f"cross-asset data unavailable ({', '.join(missing)}): cannot confirm the backdrop")

    vix, oil, gold = have.get(VIX), have.get(OIL), have.get(GOLD)
    if vix is not None:
        if direction == "BUY" and vix.pct_change >= rules.vix_spike_pct:
            vetoes.append(f"VIX up {vix.pct_change:+.1f}% today (limit {rules.vix_spike_pct:g}%): risk-off, no new long")
            drivers.append("vix_spike")
        elif direction == "SELL" and vix.pct_change <= -rules.vix_spike_pct:
            vetoes.append(f"VIX down {vix.pct_change:+.1f}% today (limit {rules.vix_spike_pct:g}%): volatility collapsing, no new short")
            drivers.append("vix_collapse")
    if oil is not None and direction == "BUY" and oil.pct_change >= rules.oil_spike_pct:
        vetoes.append(f"oil up {oil.pct_change:+.1f}% today (limit {rules.oil_spike_pct:g}%): oil shock, no new long")
        drivers.append("oil_spike")
    if gold is not None and vix is not None and gold.pct_change >= rules.gold_note_pct and vix.pct_change > 0:
        notes.append(f"gold {gold.pct_change:+.1f}% with the VIX up: flight to safety")

    stress = stress_headlines(headlines)
    factor = rules.unsupported_factor
    if view is not None:
        why = "; ".join(view.reasons)
        adverse, aligned = view_stance(direction, view)
        if adverse and view.confidence == "HIGH":
            vetoes.append(f"news view {view.direction} (high confidence): {why}")
            drivers.append("news_view")
        elif adverse and view.confidence == "MEDIUM":
            factor = rules.adverse_factor
            notes.append(f"news view {view.direction} (medium confidence) against this trade, quarter size: {why}")
            drivers.append("news_view")
        elif aligned:
            factor = 1.0
            notes.append(f"news view {view.direction} ({view.confidence}) backs this trade, full target size: {why}")
            drivers.append("news_aligned")
        else:
            notes.append(f"news view {view.direction} ({view.confidence}) does not back this trade, half size: {why}")
        if stress:
            notes.append(f"{len(stress)} stress headline(s) in the window (note only, a news view is in use)")
    else:
        notes.append("no valid news view: half size")
        if direction == "BUY" and len(stress) >= rules.stress_halve_count:
            factor = rules.adverse_factor
            notes.append(f"{len(stress)} stress headlines in the window: long size cut to a quarter")
            drivers.append("headline_stress")
        elif stress and len(stress) >= rules.stress_note_count:
            notes.append(f"{len(stress)} stress headline(s) in the window")

    return ContextVerdict(direction, tuple(vetoes), factor, tuple(notes), tuple(drivers))


def plan_context_reductions(
    positions: list[Position],
    inputs: list[CrossAssetInput] | None,
    headlines: list[Headline] | None,
    min_size: float,
    rules: ContextRules = ContextRules(),
    step: float = 0.01,
    view: NewsView | None = None,
) -> list[ReductionProposal]:
    """Propose trimming or closing positions the current backdrop argues against. Proposals only.

    An adverse HIGH-confidence news view proposes closing the whole position; any
    other genuine signal proposes halving it. Missing data proposes nothing:
    absence of data is not evidence of risk, and a position already has its stop.
    """
    if inputs is None or headlines is None:
        return []
    out = []
    for p in positions:
        v = assess(p.direction, inputs, headlines, rules, view)
        if not [d for d in v.drivers if d != "news_aligned"]:  # genuine signals only, not the missing-data veto
            continue
        adverse, _ = view_stance(p.direction, view) if view is not None else (False, False)
        if adverse and view.confidence == "HIGH":
            out.append(ReductionProposal(p.deal_id, p.epic, p.size, "news view turned strongly against this position, close it: " + "; ".join(view.reasons)))
            continue
        close = _floor_to_step(p.size / 2, step)
        if close < min_size or p.size - close < min_size:
            continue
        out.append(ReductionProposal(p.deal_id, p.epic, close, "context turned against this position: " + "; ".join(v.vetoes or v.notes)))
    return out
