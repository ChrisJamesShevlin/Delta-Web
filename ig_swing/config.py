"""Agreed risk limits and environment settings.

Risk numbers were approved on 2026-11-11. Change them only with the user's
explicit agreement. Secrets come from environment variables or a gitignored
.env file, never from code.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

DEMO_BASE_URL = "https://demo-api.ig.com/gateway/deal"
LIVE_BASE_URL = "https://api.ig.com/gateway/deal"


@dataclass(frozen=True)
class RiskLimits:
    risk_per_trade_pct: float = 0.5        # starting risk per trade
    max_risk_per_trade_pct: float = 1.0    # hard cap per trade
    daily_loss_limit_pct: float = 2.0      # halts new orders
    weekly_loss_limit_pct: float = 4.0     # halts new orders
    max_open_positions: int = 3
    max_total_open_risk_pct: float = 2.0


RISK = RiskLimits()


@dataclass(frozen=True)
class Credentials:
    # repr=False keeps values out of logs and tracebacks.
    api_key: str = field(repr=False)
    username: str = field(repr=False)
    password: str = field(repr=False)
    account_id: str = field(repr=False)


def load_credentials() -> Credentials:
    """Read IG credentials from the environment (optionally via .env)."""
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    names = ("IG_API_KEY", "IG_USERNAME", "IG_PASSWORD", "IG_ACCOUNT_ID")
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        raise RuntimeError(f"Missing environment variables: {', '.join(missing)}")

    return Credentials(
        api_key=os.environ["IG_API_KEY"],
        username=os.environ["IG_USERNAME"],
        password=os.environ["IG_PASSWORD"],
        account_id=os.environ["IG_ACCOUNT_ID"],
    )


def base_url() -> str:
    """Demo only. A live switch is a deliberate, user-approved code change."""
    return DEMO_BASE_URL
