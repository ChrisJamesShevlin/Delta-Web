"""Shared bits for the free data sources (calendar and news)."""

from __future__ import annotations

USER_AGENT = "ig-swing-briefing/0.1"
TIMEOUT_SECONDS = 20


class SourceError(Exception):
    """A data source failed. The message holds a status or type only, never a
    URL or response body, because URLs can carry API keys."""
