"""Market backdrop from FRED: VIX level, high-yield credit spread, financial stress, yield curve.

Objective, free, official data. It lags by a day (weekly for the stress index),
so it describes the backdrop and not today's move: IG supplies today's move.
Verified read-only on 2026-11-11 with the existing FRED key.

This is information for the news read and the briefing. It drives no rule.
Only the series id, date and value are ever printed: never the URL or key.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from .sources import TIMEOUT_SECONDS, USER_AGENT, SourceError

FRED_OBS_URL = "https://api.stlouisfed.org/fred/series/observations"

# key -> (FRED series id, label, how many observations back the change is measured over)
SERIES = {
    "vix": ("VIXCLS", "VIX close", 1),
    "hy_spread": ("BAMLH0A0HYM2", "High-yield credit spread (%)", 5),
    "stress": ("STLFSI4", "St. Louis financial stress index (weekly, 0 = average)", 1),
    "curve": ("T10Y2Y", "10y minus 2y Treasury yield (%)", 5),
}


@dataclass(frozen=True)
class Reading:
    label: str
    day: date
    value: float
    prior: float | None   # the value ``lookback`` observations earlier
    lookback: int

    @property
    def change(self) -> float | None:
        return None if self.prior is None else self.value - self.prior


Backdrop = dict[str, Reading]


def fetch_series(session: Any, api_key: str, series_id: str, limit: int = 10) -> list[tuple[date, float]]:
    """Newest first. Missing values (FRED uses ".") are skipped."""
    resp = session.get(
        FRED_OBS_URL,
        params={"series_id": series_id, "api_key": api_key, "file_type": "json", "sort_order": "desc", "limit": limit},
        headers={"User-Agent": USER_AGENT},
        timeout=TIMEOUT_SECONDS,
    )
    if resp.status_code != 200:
        raise SourceError(f"HTTP {resp.status_code}")
    out = []
    for o in resp.json().get("observations", []):
        try:
            out.append((date.fromisoformat(o["date"]), float(o["value"])))
        except (KeyError, ValueError, TypeError):
            continue
    return out


def load_backdrop(session: Any, api_key: str | None) -> tuple[Backdrop, list[str]]:
    """One failing series never stops the others."""
    if not api_key:
        return {}, ["Backdrop: no FRED_API_KEY set, skipped"]
    out: Backdrop = {}
    notes: list[str] = []
    for key, (sid, label, lookback) in SERIES.items():
        try:
            obs = fetch_series(session, api_key, sid, limit=lookback + 4)
        except SourceError as exc:
            notes.append(f"{sid}: unavailable ({exc})")
            continue
        except Exception as exc:  # type name only: messages can contain URLs with keys
            notes.append(f"{sid}: unavailable ({type(exc).__name__})")
            continue
        if not obs:
            notes.append(f"{sid}: no data")
            continue
        day, value = obs[0]
        prior = obs[lookback][1] if len(obs) > lookback else None
        out[key] = Reading(label, day, value, prior, lookback)
    return out, notes


def format_backdrop(b: Backdrop) -> list[str]:
    if not b:
        return []
    lines = ["Market backdrop (FRED, lags a day)"]
    for key in SERIES:
        r = b.get(key)
        if r is None:
            continue
        chg = "" if r.change is None else f"  ({r.change:+.2f} over {r.lookback} obs)"
        lines.append(f"  {r.label}: {r.value:.2f} on {r.day:%d %b}{chg}")
    return lines + [""]


def backdrop_snapshot(b: Backdrop) -> dict[str, Any]:
    """Plain data for the journal archive."""
    return {k: {"date": r.day.isoformat(), "value": r.value, "change": r.change, "lookback": r.lookback} for k, r in b.items()}
