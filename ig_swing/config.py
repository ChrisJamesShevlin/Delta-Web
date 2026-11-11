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
    min_equity_gbp: float = 0.0            # new entries refused below this equity (0 = no floor)
    cooldown_losses: int = 0               # this many losing trades in a row ...
    cooldown_hours: float = 0.0            # ... blocks new entries for this long after the last one closed
    drawdown_brake_pct: float = 0.0        # when equity is this far below its peak, stakes are scaled down ...
    drawdown_brake_factor: float = 0.5     # ... by this factor (0 pct = no brake)
    max_new_trades_per_day: int = 0        # new entries allowed per UTC day (0 = no limit)


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


# The £45 play-money "double it" challenge, approved by the user on 2026-11-11 (see CLAUDE.md). Used only when
# IG_ENV=live AND IG_RISK_PROFILE=challenge. The stop, user_confirmed and the minimum checks are unchanged. Poker-style
# rules: a floor at £15 equity, two positions at most, and a 24 hour cooldown after two losses in a row.
CHALLENGE = RiskLimits(
    name="challenge",
    risk_per_trade_pct=10.0,               # the ceiling: the Kelly tool scales below it (5% until calibrated)
    max_risk_per_trade_pct=10.0,           # lowered from 20% by the user's staking decision on 2026-11-11
    daily_loss_limit_pct=25.0,
    weekly_loss_limit_pct=40.0,
    max_open_positions=2,
    max_total_open_risk_pct=20.0,
    min_equity_gbp=15.0,
    cooldown_losses=2,
    cooldown_hours=24.0,
    drawdown_brake_pct=25.0,
    drawdown_brake_factor=0.5,
    max_new_trades_per_day=2,              # added when the user authorised the assistant to place orders without asking each time
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


def optional_key(name: str) -> str | None:
    """A non-IG API key from the environment or .env, or None if unset."""
    _load_dotenv()
    return os.environ.get(name) or None


def ig_env() -> str:
    """'demo' (default) or 'live'. Anything else is an error, never a guess."""
    _load_dotenv()
    env = os.environ.get("IG_ENV", "demo").strip().lower()
    if env not in ("demo", "live"):
        raise RuntimeError("IG_ENV must be 'demo' or 'live'")
    return env


def risk_limits(env: str | None = None) -> RiskLimits:
    env = env or ig_env()
    if env != "live":
        return AGREED
    _load_dotenv()
    if os.environ.get("IG_RISK_PROFILE", "").strip().lower() == "challenge":
        return CHALLENGE
    return LIVE_TEST


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
