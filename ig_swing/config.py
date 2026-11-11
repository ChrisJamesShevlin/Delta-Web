"""Risk profiles, environment switch and credentials.

Two risk profiles exist:
- ``agreed``: the limits approved on 2026-11-11 (default, used on demo).
- ``live_test``: a deliberate, user-approved loosening for the £15 live test
  account, approved 2026-11-11. Used only when IG_ENV=live. See CLAUDE.md.

Demo is the default. Live needs IG_ENV=live, live credentials, and the client
must be built with allow_live=True. Placing live orders needs a further
explicit flag. Secrets come from environment variables or a gitignored .env.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

DEMO_BASE_URL = "https://demo-api.ig.com/gateway/deal"
LIVE_BASE_URL = "https://api.ig.com/gateway/deal"


@dataclass(frozen=True)
class RiskLimits:
    name: str = "agreed"
    risk_per_trade_pct: float = 0.5        # target risk per trade
    max_risk_per_trade_pct: float = 1.0    # hard cap per trade
    daily_loss_limit_pct: float = 2.0      # halts new orders
    weekly_loss_limit_pct: float = 4.0     # halts new orders
    max_open_positions: int = 3
    max_total_open_risk_pct: float = 2.0


AGREED = RiskLimits()

# £15 test account at £0.01 per point: the agreed 1% cap allows a 15 point stop
# only, which is inside normal daily noise. Target equals the cap here.
LIVE_TEST = RiskLimits(
    name="live_test",
    risk_per_trade_pct=5.0,
    max_risk_per_trade_pct=5.0,
    daily_loss_limit_pct=10.0,
    weekly_loss_limit_pct=20.0,
    max_open_positions=1,
    max_total_open_risk_pct=5.0,
)


@dataclass(frozen=True)
class Credentials:
    # repr=False keeps values out of logs and tracebacks.
    api_key: str = field(repr=False)
    username: str = field(repr=False)
    password: str = field(repr=False)
    account_id: str = field(repr=False)


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass


def ig_env() -> str:
    """'demo' (default) or 'live'. Anything else is an error, never a guess."""
    _load_dotenv()
    env = os.environ.get("IG_ENV", "demo").strip().lower()
    if env not in ("demo", "live"):
        raise RuntimeError("IG_ENV must be 'demo' or 'live'")
    return env


def risk_limits(env: str | None = None) -> RiskLimits:
    return LIVE_TEST if (env or ig_env()) == "live" else AGREED


def base_url(env: str | None = None) -> str:
    return LIVE_BASE_URL if (env or ig_env()) == "live" else DEMO_BASE_URL


def load_credentials(env: str | None = None) -> Credentials:
    """Read IG credentials for the chosen environment.

    Demo and live use separate variable names (IG_DEMO_*, IG_LIVE_*) so a demo
    key can never be used against the live account by mistake.
    """
    env = env or ig_env()
    prefix = f"IG_{env.upper()}_"
    names = [prefix + n for n in ("API_KEY", "USERNAME", "PASSWORD", "ACCOUNT_ID")]
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        raise RuntimeError(f"Missing environment variables: {', '.join(missing)}")
    return Credentials(
        api_key=os.environ[names[0]],
        username=os.environ[names[1]],
        password=os.environ[names[2]],
        account_id=os.environ[names[3]],
    )
