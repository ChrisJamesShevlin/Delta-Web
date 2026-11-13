"""Offline tests for ig_swing.config and the live guards in ig_client."""

from __future__ import annotations

import os

import pytest

from ig_swing import config
from ig_swing.config import (
    AGREED,
    DEMO_BASE_URL,
    LIVE_BASE_URL,
    LIVE_TEST,
    Credentials,
    base_url,
    ig_env,
    load_credentials,
    risk_limits,
)
from ig_swing.ig_client import IGClient

CREDS = Credentials(api_key="k", username="u", password="p", account_id="A")


@pytest.fixture(autouse=True)
def no_dotenv_and_clean_env(monkeypatch):
    # A real .env on this machine must never change test outcomes.
    monkeypatch.setattr(config, "_load_dotenv", lambda: None)
    for name in [n for n in os.environ if n.startswith("IG_")]:
        monkeypatch.delenv(name, raising=False)


def test_default_environment_is_demo():
    assert ig_env() == "demo"
    assert base_url() == DEMO_BASE_URL
    assert risk_limits() is AGREED


def test_live_selected_by_env_uses_live_url_and_live_test_profile(monkeypatch):
    monkeypatch.setenv("IG_ENV", "live")
    assert ig_env() == "live"
    assert base_url() == LIVE_BASE_URL
    assert risk_limits() is LIVE_TEST


@pytest.mark.parametrize("bad", ["prod", "live2", "", "yes"])
def test_bad_env_value_is_an_error_not_a_guess(monkeypatch, bad):
    monkeypatch.setenv("IG_ENV", bad)
    with pytest.raises(RuntimeError):
        ig_env()


def test_agreed_limits_unchanged():
    assert (AGREED.risk_per_trade_pct, AGREED.max_risk_per_trade_pct) == (0.5, 1.0)
    assert (AGREED.daily_loss_limit_pct, AGREED.weekly_loss_limit_pct) == (2.0, 4.0)
    assert (AGREED.max_open_positions, AGREED.max_total_open_risk_pct) == (3, 2.0)


def test_live_test_profile_values():
    assert LIVE_TEST.name == "live_test"
    assert LIVE_TEST.max_risk_per_trade_pct == 5.0
    assert (LIVE_TEST.daily_loss_limit_pct, LIVE_TEST.weekly_loss_limit_pct) == (10.0, 20.0)
    assert (LIVE_TEST.max_open_positions, LIVE_TEST.max_total_open_risk_pct) == (1, 5.0)


def test_demo_and_live_credentials_use_separate_names(monkeypatch):
    for n in ("API_KEY", "USERNAME", "PASSWORD", "ACCOUNT_ID"):
        monkeypatch.setenv(f"IG_DEMO_{n}", f"demo-{n}")
    assert load_credentials("demo").api_key == "demo-API_KEY"
    with pytest.raises(RuntimeError, match="IG_LIVE_API_KEY"):
        load_credentials("live")  # demo values are never used for live


def test_missing_credentials_name_the_variables_not_values(monkeypatch):
    monkeypatch.setenv("IG_DEMO_API_KEY", "SECRET-VALUE")
    with pytest.raises(RuntimeError) as exc:
        load_credentials("demo")
    assert "IG_DEMO_USERNAME" in str(exc.value)
    assert "SECRET-VALUE" not in str(exc.value)


# ---- client live guards -----------------------------------------------------


def test_live_url_needs_allow_live():
    with pytest.raises(ValueError):
        IGClient(CREDS, base_url=LIVE_BASE_URL, session=object())
    IGClient(CREDS, base_url=LIVE_BASE_URL, allow_live=True, session=object())


def test_unknown_url_is_refused():
    with pytest.raises(ValueError):
        IGClient(CREDS, base_url="https://example.com", allow_live=True, session=object())


class _NoCalls:
    def request(self, *a, **k):
        raise AssertionError("no network call expected")


def test_live_orders_and_closes_refused_unless_explicitly_enabled():
    c = IGClient(CREDS, base_url=LIVE_BASE_URL, allow_live=True, session=_NoCalls())
    with pytest.raises(PermissionError, match="not enabled"):
        c.place_market_order(
            epic="E", expiry="DFB", direction="BUY", size=0.01, stop_distance=50, user_confirmed=True
        )
    with pytest.raises(PermissionError, match="not enabled"):
        c.close_position(deal_id="D", direction="BUY", size=0.01, user_confirmed=True)


def test_challenge_profile_is_only_chosen_on_live_with_the_explicit_switch(monkeypatch):
    from ig_swing.config import CHALLENGE, LIVE_TEST, AGREED, risk_limits

    monkeypatch.setattr("ig_swing.config._load_dotenv", lambda: None)
    monkeypatch.delenv("IG_RISK_PROFILE", raising=False)
    assert risk_limits("live") is LIVE_TEST
    monkeypatch.setenv("IG_RISK_PROFILE", "challenge")
    assert risk_limits("live") is CHALLENGE
    assert risk_limits("demo") is AGREED                              # never on demo
    monkeypatch.setenv("IG_RISK_PROFILE", "wild")
    assert risk_limits("live") is LIVE_TEST                           # an unknown value never loosens anything
    assert (CHALLENGE.max_risk_per_trade_pct, CHALLENGE.max_open_positions, CHALLENGE.min_equity_gbp, CHALLENGE.cooldown_losses) == (20.0, 2, 15.0, 2)
    assert (CHALLENGE.drawdown_brake_pct, CHALLENGE.drawdown_brake_factor, CHALLENGE.max_total_open_risk_pct) == (25.0, 0.5, 40.0)
