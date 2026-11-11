"""News and cross-asset context: deterministic rules that sit on top of the technical signal.

Role: the context layer can VETO an entry, HALVE its size, or PROPOSE trimming an
open position. It never creates a trade. No LLM, no scoring model: fixed
thresholds on IG's own VIX, oil and gold daily moves, plus a count of stress
words in fresh market headlines.

Rules (all untested hypotheses, pre-registered here, and logged as the trade
"driver" so the report can later split results by them):
- BUY is vetoed when the VIX is up by the spike threshold or more (risk-off).
- SELL is vetoed when the VIX is down by the spike threshold or more (vol collapse, rally).
- BUY is vetoed when oil is up by the oil threshold or more (the "oil shock" idea).
- News view (see ig_swing.news_view), when a valid one exists. It is adverse to
  a BUY if BEARISH and adverse to a SELL if BULLISH. HIGH confidence adverse:
  veto. MEDIUM adverse: size halved. LOW, NEUTRAL or supportive: a note only.
  A view never starts a trade and never loosens any other rule.
- Fallback when there is no valid view: BUY size is halved when several fresh
  headlines carry stress words. With a view, stress words only add a note.
- Gold only adds a note. Its meaning is ambiguous on its own.
- Missing VIX or oil data vetoes every entry: we cannot confirm the backdrop.

Thresholds are starting points, not tuned values. Changing them after seeing
results moves the goalposts.

Limits: headline matching is keyword counting, so it is noisy, and it only sees
headlines that already passed the relevance filter in ig_swing.news.
At the smallest stake a halving cannot reduce size below the market minimum.
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
    halve_factor: float = 0.5


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

    factor = 1.0
    stress = stress_headlines(headlines)
    if view is not None:
        why = "; ".join(view.reasons)
        adverse = (direction == "BUY" and view.direction == "BEARISH") or (direction == "SELL" and view.direction == "BULLISH")
        if adverse and view.confidence == "HIGH":
            vetoes.append(f"news view {view.direction} (high confidence): {why}")
            drivers.append("news_view")
        elif adverse and view.confidence == "MEDIUM":
            factor = rules.halve_factor
            notes.append(f"news view {view.direction} (medium confidence), size halved: {why}")
            drivers.append("news_view")
        else:
            notes.append(f"news view {view.direction} ({view.confidence}): {why}")
        if stress:
            notes.append(f"{len(stress)} stress headline(s) in the window (note only, a news view is in use)")
    elif direction == "BUY" and len(stress) >= rules.stress_halve_count:
        factor = rules.halve_factor
        notes.append(f"{len(stress)} stress headlines in the window: long size halved")
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
    """Propose halving positions the current backdrop argues against. Proposals only.

    Missing data proposes nothing: absence of data is not evidence of risk, and
    a position already has its stop.
    """
    if inputs is None or headlines is None:
        return []
    out = []
    for p in positions:
        v = assess(p.direction, inputs, headlines, rules, view)
        if not v.drivers:  # only genuine signals, not the missing-data veto
            continue
        close = _floor_to_step(p.size / 2, step)
        if close < min_size or p.size - close < min_size:
            continue
        out.append(ReductionProposal(p.deal_id, p.epic, close, "context turned against this position: " + "; ".join(v.vetoes or v.notes)))
    return out
