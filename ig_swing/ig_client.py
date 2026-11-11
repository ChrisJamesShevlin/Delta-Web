"""Low-level IG REST client (demo only).

Safety properties, enforced here:
- Demo base URL only. Passing the live URL raises.
- No order without a stop: ``stop_distance`` is a required keyword and must be
  a finite number above zero and at least the market's minimum stop distance.
- No order or close without ``user_confirmed=True`` from the caller.
- Errors carry the HTTP status and IG's ``errorCode`` only, never response
  bodies, so account data and tokens cannot leak into logs.

Endpoint paths, versions and response field names follow the old API_Trader
code and IG's public docs. They are UNTESTED against the live demo API until
the first read-only run.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from .config import DEMO_BASE_URL, Credentials

TIMEOUT_SECONDS = 30
INVALID_TOKEN_CODE = "error.security.client-token-invalid"


class IGError(Exception):
    """An IG call failed. Holds status and errorCode only, never the body."""

    def __init__(self, status: int, error_code: str | None = None, context: str = ""):
        self.status = status
        self.error_code = error_code
        self.context = context
        text = f"IG {context} failed: HTTP {status}"
        if error_code:
            text += f" ({error_code})"
        super().__init__(text)


class MarketDataError(Exception):
    """A response was missing a field we need. Never guess a default."""


class OrderRejected(Exception):
    """IG accepted the request but the deal was rejected."""

    def __init__(self, deal_reference: str, reason: str):
        self.deal_reference = deal_reference
        self.reason = reason
        super().__init__(f"Deal {deal_reference} rejected: {reason}")


class AllowanceExhausted(Exception):
    """Not enough historical price allowance left for the requested pull."""


@dataclass(frozen=True)
class MarketRules:
    epic: str
    instrument_name: str
    expiry: str
    min_deal_size: float
    min_stop_distance: float  # in points
    bid: float | None
    offer: float | None

    @property
    def spread(self) -> float | None:
        if self.bid is None or self.offer is None:
            return None
        return self.offer - self.bid


@dataclass(frozen=True)
class Position:
    deal_id: str
    epic: str
    instrument_name: str
    direction: str
    size: float
    open_level: float
    stop_level: float | None
    limit_level: float | None
    bid: float | None
    offer: float | None
    unrealised_pnl: float | None


@dataclass(frozen=True)
class Confirmation:
    deal_reference: str
    deal_id: str | None
    status: str
    reason: str
    epic: str | None
    direction: str | None
    size: float | None
    level: float | None
    stop_level: float | None


class PriceCache:
    """Small on-disk cache so repeat runs do not burn the price allowance."""

    def __init__(self, directory: str | Path = "data/cache", ttl_seconds: int = 6 * 3600):
        self.directory = Path(directory)
        self.ttl_seconds = ttl_seconds

    def _path(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def load(self, key: str) -> list[dict] | None:
        try:
            data = json.loads(self._path(key).read_text())
            if time.time() - float(data["fetched_at"]) > self.ttl_seconds:
                return None
            return list(data["rows"])
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def save(self, key: str, rows: list[dict]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        self._path(key).write_text(json.dumps({"fetched_at": time.time(), "rows": rows}))

    def load_allowance(self) -> int | None:
        try:
            return int(json.loads((self.directory / "allowance.json").read_text())["remaining"])
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def save_allowance(self, remaining: int) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / "allowance.json").write_text(json.dumps({"remaining": int(remaining)}))


def _error_code(resp: Any) -> str | None:
    try:
        code = resp.json().get("errorCode")
        return code if isinstance(code, str) else None
    except Exception:
        return None


def _num(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _rule_points(rule: Any, price: float | None, name: str) -> float:
    """Return a dealing-rule distance in points, converting percentages."""
    try:
        value = float(rule["value"])
        unit = str(rule.get("unit", "POINTS")).upper()
    except (KeyError, TypeError, ValueError, AttributeError):
        raise MarketDataError(f"Missing or invalid dealing rule: {name}") from None
    if unit == "PERCENTAGE":
        if price is None:
            raise MarketDataError(f"{name} is a percentage but no live price is available")
        return value / 100.0 * price
    return value


class IGClient:
    def __init__(
        self,
        credentials: Credentials,
        *,
        base_url: str = DEMO_BASE_URL,
        session: Any = None,
        cache: PriceCache | None = None,
    ):
        if base_url != DEMO_BASE_URL:
            raise ValueError("Live trading is disabled. Demo only until the user approves a change.")
        self._creds = credentials
        self._base = base_url
        self._session = session or requests.Session()
        self._cache = cache or PriceCache()
        self._cst: str | None = None
        self._xst: str | None = None

    # ---- transport -------------------------------------------------------

    def _headers(self, version: str, authed: bool, extra: dict | None = None) -> dict:
        headers = {
            "X-IG-API-KEY": self._creds.api_key,
            "Accept": "application/json; charset=UTF-8",
            "Content-Type": "application/json; charset=UTF-8",
            "VERSION": version,
        }
        if authed:
            if self._cst:
                headers["CST"] = self._cst
            if self._xst:
                headers["X-SECURITY-TOKEN"] = self._xst
        if extra:
            headers.update(extra)
        return headers

    def _send(self, method, path, version, params=None, body=None, extra=None, authed=True):
        return self._session.request(
            method,
            f"{self._base}{path}",
            headers=self._headers(version, authed, extra),
            params=params,
            json=body,
            timeout=TIMEOUT_SECONDS,
        )

    def _call(self, method, path, version, *, params=None, body=None, extra=None, context="request"):
        if not self._cst:
            self.login()
        resp = self._send(method, path, version, params, body, extra)
        if resp.status_code == 401 and _error_code(resp) == INVALID_TOKEN_CODE:
            self.login()
            resp = self._send(method, path, version, params, body, extra)
        if resp.status_code != 200:
            raise IGError(resp.status_code, _error_code(resp), context)
        return resp.json()

    # ---- session ---------------------------------------------------------

    def login(self) -> None:
        resp = self._send(
            "POST",
            "/session",
            "2",
            body={"identifier": self._creds.username, "password": self._creds.password},
            authed=False,
        )
        if resp.status_code != 200:
            raise IGError(resp.status_code, _error_code(resp), "login")
        self._cst = resp.headers["CST"]
        self._xst = resp.headers["X-SECURITY-TOKEN"]
        try:
            current = resp.json().get("currentAccountId")
        except Exception:
            current = None
        if current != self._creds.account_id:
            self._switch_account()

    def _switch_account(self) -> None:
        resp = self._send(
            "PUT", "/session", "1", body={"accountId": self._creds.account_id, "defaultAccount": False}
        )
        code = _error_code(resp)
        if resp.status_code != 200 and "accountId-must-be-different" not in (code or ""):
            raise IGError(resp.status_code, code, "account switch")
        self._cst = resp.headers.get("CST", self._cst)
        self._xst = resp.headers.get("X-SECURITY-TOKEN", self._xst)

    # ---- market data -----------------------------------------------------

    def get_market_rules(self, epic: str) -> MarketRules:
        data = self._call("GET", f"/markets/{epic}", "3", context="market rules")
        try:
            rules = data["dealingRules"]
            snap = data.get("snapshot") or {}
            inst = data["instrument"]
        except (KeyError, TypeError):
            raise MarketDataError("Market response missing dealingRules or instrument") from None

        bid, offer = _num(snap.get("bid")), _num(snap.get("offer"))
        mid = (bid + offer) / 2 if bid is not None and offer is not None else None
        try:
            min_size = float(rules["minDealSize"]["value"])
        except (KeyError, TypeError, ValueError):
            raise MarketDataError("Missing or invalid dealing rule: minDealSize") from None
        min_stop = _rule_points(rules.get("minNormalStopOrLimitDistance"), mid, "minNormalStopOrLimitDistance")

        return MarketRules(
            epic=epic,
            instrument_name=str(inst.get("name", "")),
            expiry=str(inst.get("expiry", "")),
            min_deal_size=min_size,
            min_stop_distance=min_stop,
            bid=bid,
            offer=offer,
        )

    def get_prices(self, epic: str, resolution: str = "DAY", num_points: int = 60) -> pd.DataFrame:
        """Fetch OHLC bars. Cached, and guarded by the historical price allowance."""
        key = f"{epic}_{resolution}_{num_points}"
        rows = self._cache.load(key)
        if rows is None:
            remaining = self._cache.load_allowance()
            if remaining is not None and remaining < num_points:
                raise AllowanceExhausted(f"{remaining} price points left, {num_points} requested")
            data = self._call(
                "GET", f"/prices/{epic}/{resolution}/{num_points}", "2", context="prices"
            )
            rows = self._parse_prices(data)
            self._cache.save(key, rows)
            try:
                self._cache.save_allowance(int(data["metadata"]["allowance"]["remainingAllowance"]))
            except (KeyError, TypeError, ValueError):
                pass
        if not rows:
            raise MarketDataError("No price data returned")
        df = pd.DataFrame(rows)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        return df.sort_values("timestamp").reset_index(drop=True)

    @staticmethod
    def _parse_prices(data: dict) -> list[dict]:
        rows = []
        for p in data.get("prices", []):
            try:
                o, h, l, c = p["openPrice"], p["highPrice"], p["lowPrice"], p["closePrice"]
                rows.append(
                    {
                        "timestamp": p.get("snapshotTimeUTC") or p["snapshotTime"],
                        "open": (o["bid"] + o["ask"]) / 2,
                        "high": (h["bid"] + h["ask"]) / 2,
                        "low": (l["bid"] + l["ask"]) / 2,
                        "close": (c["bid"] + c["ask"]) / 2,
                        "bid": c["bid"],
                        "ask": c["ask"],
                    }
                )
            except (KeyError, TypeError):
                continue
        return rows

    # ---- positions -------------------------------------------------------

    def get_positions(self) -> list[Position]:
        """All open positions, across every market."""
        data = self._call("GET", "/positions", "2", context="positions")
        out = []
        for entry in data.get("positions", []):
            p, m = entry.get("position", {}), entry.get("market", {})
            level, size = _num(p.get("level")), _num(p.get("size"))
            bid, offer = _num(m.get("bid")), _num(m.get("offer"))
            if level is None or size is None or "dealId" not in p:
                continue
            direction = p.get("direction")
            pnl = None
            if direction == "BUY" and bid is not None:
                pnl = (bid - level) * size
            elif direction == "SELL" and offer is not None:
                pnl = (level - offer) * size
            out.append(
                Position(
                    deal_id=p["dealId"],
                    epic=str(m.get("epic", "")),
                    instrument_name=str(m.get("instrumentName", "")),
                    direction=str(direction),
                    size=size,
                    open_level=level,
                    stop_level=_num(p.get("stopLevel")),
                    limit_level=_num(p.get("limitLevel")),
                    bid=bid,
                    offer=offer,
                    unrealised_pnl=None if pnl is None else round(pnl, 2),
                )
            )
        return out

    # ---- orders ----------------------------------------------------------

    def place_market_order(
        self,
        *,
        epic: str,
        expiry: str,
        direction: str,
        size: float,
        stop_distance: float,
        limit_distance: float | None = None,
        user_confirmed: bool,
        currency: str = "GBP",
    ) -> str:
        """Open a position with a mandatory stop. Returns the deal reference."""
        if user_confirmed is not True:
            raise PermissionError("Order refused: the user has not confirmed it.")
        if direction not in ("BUY", "SELL"):
            raise ValueError("direction must be BUY or SELL")
        if not _is_positive(stop_distance):
            raise ValueError("Order refused: a stop distance above zero is mandatory.")
        if not _is_positive(size):
            raise ValueError("size must be a finite number above zero")
        if limit_distance is not None and not _is_positive(limit_distance):
            raise ValueError("limit_distance must be above zero when given")

        rules = self.get_market_rules(epic)
        if size < rules.min_deal_size:
            raise ValueError(f"size {size} is below the minimum deal size {rules.min_deal_size}")
        if stop_distance < rules.min_stop_distance:
            raise ValueError(
                f"stop distance {stop_distance} is below the market minimum {rules.min_stop_distance}"
            )
        if limit_distance is not None and limit_distance < rules.min_stop_distance:
            raise ValueError(
                f"limit distance {limit_distance} is below the market minimum {rules.min_stop_distance}"
            )

        body: dict[str, Any] = {
            "epic": epic,
            "expiry": expiry,
            "direction": direction,
            "size": size,
            "orderType": "MARKET",
            "currencyCode": currency,
            "forceOpen": True,
            "guaranteedStop": False,
            "stopDistance": stop_distance,
        }
        if limit_distance is not None:
            body["limitDistance"] = limit_distance

        data = self._call("POST", "/positions/otc", "2", body=body, context="place order")
        ref = data.get("dealReference")
        if not ref:
            raise IGError(200, None, "place order (no dealReference returned)")
        return str(ref)

    def close_position(self, *, deal_id: str, direction: str, size: float, user_confirmed: bool) -> str:
        """Close one position by deal id. ``direction`` is the OPEN position's direction."""
        if user_confirmed is not True:
            raise PermissionError("Close refused: the user has not confirmed it.")
        if direction not in ("BUY", "SELL"):
            raise ValueError("direction must be BUY or SELL")
        if not _is_positive(size):
            raise ValueError("size must be a finite number above zero")
        body = {
            "dealId": deal_id,
            "direction": "SELL" if direction == "BUY" else "BUY",
            "size": size,
            "orderType": "MARKET",
        }
        data = self._call(
            "POST", "/positions/otc", "1", body=body, extra={"_method": "DELETE"}, context="close position"
        )
        ref = data.get("dealReference")
        if not ref:
            raise IGError(200, None, "close position (no dealReference returned)")
        return str(ref)

    def confirm_deal(self, deal_reference: str) -> Confirmation:
        """Fetch the deal outcome. Raises OrderRejected unless it was ACCEPTED."""
        data = self._call("GET", f"/confirms/{deal_reference}", "1", context="confirm deal")
        status = str(data.get("dealStatus", "UNKNOWN"))
        reason = str(data.get("reason", ""))
        if status != "ACCEPTED":
            raise OrderRejected(deal_reference, f"{status} {reason}".strip())
        return Confirmation(
            deal_reference=deal_reference,
            deal_id=data.get("dealId"),
            status=status,
            reason=reason,
            epic=data.get("epic"),
            direction=data.get("direction"),
            size=_num(data.get("size")),
            level=_num(data.get("level")),
            stop_level=_num(data.get("stopLevel")),
        )


def _is_positive(value: Any) -> bool:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(v) and v > 0
