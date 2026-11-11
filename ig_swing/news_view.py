"""The news view: a structured, expiring read of the news written by the assistant (Claude) in a session.

The headlines, IG cross-asset moves and market backdrop are shown by
``python3 -m ig_swing.briefing``. The assistant reads them and records a verdict
here. The verdict can only act as a BRAKE (see ig_swing.context): it never
starts a trade, flips a direction or changes stops or loss limits.

Safety:
- Strict schema: anything malformed is treated as no view (the plan falls back to
  the word-count rule). A bad or missing view never blocks trading by itself.
- Every view expires (default 12 hours, never more than 24).
- Headlines are untrusted text. The verdict is a fixed choice of words, so an
  instruction planted in a headline cannot do anything but make us more cautious.
- Every view is written to the journal with its reasons, so it can be audited.

Record one:
  python3 -m ig_swing.news_view set --direction BEARISH --confidence HIGH --reason "..." [--reason "..."] [--ttl 12]
Show the current one:
  python3 -m ig_swing.news_view show
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

UTC = timezone.utc
DIRECTIONS = ("BULLISH", "BEARISH", "NEUTRAL")
CONFIDENCES = ("LOW", "MEDIUM", "HIGH")
DEFAULT_TTL_HOURS = 12.0
MAX_TTL_HOURS = 24.0
MAX_REASONS = 5
MAX_REASON_LEN = 240
DEFAULT_PATH = Path("data/news_view.json")


class InvalidView(ValueError):
    """The view does not match the schema."""


@dataclass(frozen=True)
class NewsView:
    direction: str       # BULLISH / BEARISH / NEUTRAL, for the US 500
    confidence: str      # LOW / MEDIUM / HIGH
    reasons: tuple[str, ...]
    created_at: datetime
    expires_at: datetime
    author: str = "claude"

    def to_json(self) -> dict:
        return {
            "direction": self.direction, "confidence": self.confidence, "reasons": list(self.reasons),
            "created_at": self.created_at.isoformat(), "expires_at": self.expires_at.isoformat(), "author": self.author,
        }


def make_view(direction: str, confidence: str, reasons: list[str], now: datetime, ttl_hours: float = DEFAULT_TTL_HOURS,
              author: str = "claude") -> NewsView:
    direction, confidence = str(direction).upper(), str(confidence).upper()
    if direction not in DIRECTIONS:
        raise InvalidView(f"direction must be one of {DIRECTIONS}")
    if confidence not in CONFIDENCES:
        raise InvalidView(f"confidence must be one of {CONFIDENCES}")
    if not isinstance(reasons, (list, tuple)) or not 1 <= len(reasons) <= MAX_REASONS:
        raise InvalidView(f"give between 1 and {MAX_REASONS} reasons")
    clean = []
    for r in reasons:
        if not isinstance(r, str) or not r.strip() or len(r) > MAX_REASON_LEN:
            raise InvalidView(f"each reason must be non-empty text of at most {MAX_REASON_LEN} characters")
        clean.append(r.strip())
    try:
        ttl = float(ttl_hours)
    except (TypeError, ValueError):
        raise InvalidView("ttl must be a number of hours") from None
    if not 0 < ttl <= MAX_TTL_HOURS:
        raise InvalidView(f"ttl must be above 0 and at most {MAX_TTL_HOURS:g} hours")
    if now.tzinfo is None:
        raise InvalidView("time must be timezone-aware")
    return NewsView(direction, confidence, tuple(clean), now, now + timedelta(hours=ttl), author)


def save_view(view: NewsView, path: str | Path = DEFAULT_PATH) -> None:
    """Atomic write: a reader never sees half a file."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".news_view_")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(view.to_json(), f)
        os.replace(tmp, p)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def load_view(now: datetime, path: str | Path = DEFAULT_PATH) -> tuple[NewsView | None, str]:
    """The current view, or None plus a plain note saying why there is none."""
    p = Path(path)
    if not p.exists():
        return None, "news view: none recorded (word-count fallback in use)"
    try:
        d = json.loads(p.read_text())
        created = datetime.fromisoformat(d["created_at"])
        expires = datetime.fromisoformat(d["expires_at"])
        if created.tzinfo is None or expires.tzinfo is None:
            raise InvalidView("naive time")
        if not timedelta(0) < expires - created <= timedelta(hours=MAX_TTL_HOURS):
            raise InvalidView("expiry outside the allowed window")
        view = make_view(d["direction"], d["confidence"], d["reasons"], created,
                         ttl_hours=min((expires - created).total_seconds() / 3600, MAX_TTL_HOURS), author=str(d.get("author", "claude")))
        view = NewsView(view.direction, view.confidence, view.reasons, created, expires, view.author)
    except (OSError, ValueError, KeyError, TypeError):
        return None, "news view: file is malformed, ignored (word-count fallback in use)"
    if now >= view.expires_at:
        return None, f"news view: expired {view.expires_at:%a %d %b %H:%M}Z (word-count fallback in use)"
    if view.created_at > now + timedelta(minutes=5):
        return None, "news view: dated in the future, ignored (word-count fallback in use)"
    return view, ""


def format_view(view: NewsView) -> str:
    lines = [f"News view: {view.direction} ({view.confidence} confidence), valid until {view.expires_at:%a %d %b %H:%M}Z"]
    lines += [f"  - {r}" for r in view.reasons]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ig_swing.news_view")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("set")
    s.add_argument("--direction", required=True, choices=DIRECTIONS)
    s.add_argument("--confidence", required=True, choices=CONFIDENCES)
    s.add_argument("--reason", action="append", required=True)
    s.add_argument("--ttl", type=float, default=DEFAULT_TTL_HOURS)
    sub.add_parser("show")
    args = ap.parse_args(argv)
    now = datetime.now(UTC)

    if args.cmd == "show":
        view, note = load_view(now)
        print(format_view(view) if view else note)
        return 0

    from .journal import Journal  # local import: keeps the schema code free of storage

    try:
        view = make_view(args.direction, args.confidence, args.reason, now, args.ttl)
    except InvalidView as exc:
        print(f"REFUSED: {exc}")
        return 1
    save_view(view)
    Journal().record_event(now.isoformat(), "news_view", json.dumps(view.to_json()))
    print(format_view(view))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
