"""Cross-asset inputs: VIX, oil and gold, read from IG's own prices.

We only need the level and whether each is up or down today, so one market
lookup per instrument is enough (no history allowance used). The daily funded
bet epics give a continuous price with no contract rolls. Epics found by
read-only search on 2026-11-11.

The VIX has no bid or offer on IG, only the change, high and low, so its level
is not shown. Direction and percentage change are what the rules use.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

INPUT_EPICS = {
    "VIX": "CC.D.VIX.USS.IP",
    "Oil (US crude)": "CC.D.CL.USS.IP",
    "Gold": "CS.D.USCGC.TODAY.IP",
}
FLAT_BAND_PCT = 0.10  # moves smaller than this count as flat


@dataclass(frozen=True)
class CrossAssetInput:
    name: str
    epic: str
    status: str
    pct_change: float | None
    net_change: float | None
    level: float | None  # IG quote units, None when IG gives no bid/offer
    update_time: str | None

    @property
    def direction(self) -> str | None:
        if self.pct_change is None:
            return None
        if abs(self.pct_change) < FLAT_BAND_PCT:
            return "FLAT"
        return "UP" if self.pct_change > 0 else "DOWN"


def load_inputs(client: Any, epics: dict[str, str] = INPUT_EPICS) -> tuple[list[CrossAssetInput], list[str]]:
    """Fetch every input. One failing market never stops the others."""
    out: list[CrossAssetInput] = []
    notes: list[str] = []
    for name, epic in epics.items():
        try:
            s = client.get_snapshot(epic)
        except Exception as exc:  # type name only: never echo a message
            notes.append(f"{name}: unavailable ({type(exc).__name__})")
            continue
        level = (s.bid + s.offer) / 2 if s.bid is not None and s.offer is not None else None
        out.append(CrossAssetInput(name, epic, s.status, s.pct_change, s.net_change, level, s.update_time))
    return out, notes
