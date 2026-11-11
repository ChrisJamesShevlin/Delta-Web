"""Offline tests for ig_swing.ig_client. No network, no real credentials."""

from __future__ import annotations

import pytest

from ig_swing.config import DEMO_BASE_URL, Credentials
from ig_swing.ig_client import (
    AllowanceExhausted,
    IGClient,
    IGError,
    MarketDataError,
    OrderRejected,
    PriceCache,
)

FAKE_BODY_TEXT = "SENTINEL-BODY-TEXT-MUST-NOT-LEAK"


class FakeResponse:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.headers = headers or {}
        self.text = FAKE_BODY_TEXT

    def json(self):
        return self._body


class FakeSession:
    """Routes (METHOD, path) to a queue of responses and records every call."""

    def __init__(self, routes):
        self.routes = {k: list(v) for k, v in routes.items()}
        self.calls = []

    def request(self, method, url, headers=None, params=None, json=None, timeout=None):
        path = url[len(DEMO_BASE_URL):]
        self.calls.append({"method": method, "path": path, "headers": headers, "json": json})
        queue = self.routes[(method, path)]
        return queue.pop(0) if len(queue) > 1 else queue[0]

    def paths(self):
        return [(c["method"], c["path"]) for c in self.calls]


CREDS = Credentials(api_key="k", username="u", password="p", account_id="ACC1")
LOGIN_OK = FakeResponse(200, {"currentAccountId": "ACC1"}, {"CST": "c", "X-SECURITY-TOKEN": "x"})
EPIC = "IX.D.SPTRD.DAILY.IP"


def market(min_size=0.5, min_stop=10, unit="POINTS", bid=5000.0, offer=5001.0):
    return FakeResponse(
        200,
        {
            "instrument": {"name": "US 500", "expiry": "DFB"},
            "dealingRules": {
                "minDealSize": {"unit": "POINTS", "value": min_size},
                "minNormalStopOrLimitDistance": {"unit": unit, "value": min_stop},
            },
            "snapshot": {"bid": bid, "offer": offer},
        },
    )


def client(routes, tmp_path):
    routes = {("POST", "/session"): [LOGIN_OK], **routes}
    session = FakeSession(routes)
    return IGClient(CREDS, session=session, cache=PriceCache(tmp_path)), session


def order_kwargs(**over):
    kw = dict(epic=EPIC, expiry="DFB", direction="BUY", size=1.0, stop_distance=50.0, user_confirmed=True)
    kw.update(over)
    return kw


# ---- safety ---------------------------------------------------------------


def test_live_url_is_refused():
    with pytest.raises(ValueError):
        IGClient(CREDS, base_url="https://api.ig.com/gateway/deal", session=FakeSession({}))


def test_order_without_stop_keyword_is_a_type_error(tmp_path):
    c, _ = client({}, tmp_path)
    with pytest.raises(TypeError):
        c.place_market_order(epic=EPIC, expiry="DFB", direction="BUY", size=1.0, user_confirmed=True)


@pytest.mark.parametrize("bad", [0, -5, None, float("nan"), float("inf"), "abc"])
def test_order_with_bad_stop_is_refused_before_any_network_call(bad, tmp_path):
    c, s = client({}, tmp_path)
    with pytest.raises(ValueError):
        c.place_market_order(**order_kwargs(stop_distance=bad))
    assert s.calls == []


@pytest.mark.parametrize("flag", [False, None, 1, "yes"])
def test_order_needs_explicit_user_confirmation(flag, tmp_path):
    c, s = client({}, tmp_path)
    with pytest.raises(PermissionError):
        c.place_market_order(**order_kwargs(user_confirmed=flag))
    assert s.calls == []


def test_stop_below_market_minimum_is_refused(tmp_path):
    c, s = client({("GET", f"/markets/{EPIC}"): [market(min_stop=20)]}, tmp_path)
    with pytest.raises(ValueError, match="below the market minimum"):
        c.place_market_order(**order_kwargs(stop_distance=10))
    assert ("POST", "/positions/otc") not in s.paths()


def test_size_below_minimum_is_refused(tmp_path):
    c, s = client({("GET", f"/markets/{EPIC}"): [market(min_size=2)]}, tmp_path)
    with pytest.raises(ValueError, match="minimum deal size"):
        c.place_market_order(**order_kwargs(size=1.0))
    assert ("POST", "/positions/otc") not in s.paths()


def test_valid_order_payload_has_stop_and_no_guaranteed_stop(tmp_path):
    c, s = client(
        {
            ("GET", f"/markets/{EPIC}"): [market()],
            ("POST", "/positions/otc"): [FakeResponse(200, {"dealReference": "REF1"})],
        },
        tmp_path,
    )
    assert c.place_market_order(**order_kwargs(limit_distance=100.0)) == "REF1"
    body = [x for x in s.calls if x["path"] == "/positions/otc"][0]["json"]
    assert body["stopDistance"] == 50.0
    assert body["limitDistance"] == 100.0
    assert body["forceOpen"] is True and body["guaranteedStop"] is False
    assert body["orderType"] == "MARKET"


def test_close_needs_confirmation_and_uses_opposite_direction(tmp_path):
    c, s = client({("POST", "/positions/otc"): [FakeResponse(200, {"dealReference": "REF2"})]}, tmp_path)
    with pytest.raises(PermissionError):
        c.close_position(deal_id="D1", direction="BUY", size=1.0, user_confirmed=False)
    assert c.close_position(deal_id="D1", direction="BUY", size=1.0, user_confirmed=True) == "REF2"
    call = [x for x in s.calls if x["path"] == "/positions/otc"][0]
    assert call["json"]["direction"] == "SELL"
    assert call["headers"]["_method"] == "DELETE"


# ---- errors do not leak ---------------------------------------------------


def test_login_error_has_status_and_code_but_not_body(tmp_path):
    bad = FakeResponse(401, {"errorCode": "error.security.invalid-details"})
    session = FakeSession({("POST", "/session"): [bad]})
    c = IGClient(CREDS, session=session, cache=PriceCache(tmp_path))
    with pytest.raises(IGError) as exc:
        c.login()
    msg = str(exc.value)
    assert "401" in msg and "error.security.invalid-details" in msg
    assert FAKE_BODY_TEXT not in msg


def test_credentials_repr_hides_values():
    secret = Credentials(api_key="KEY-VALUE", username="USER-VALUE", password="PASS-VALUE", account_id="ACC-VALUE")
    text = repr(secret)
    assert not any(v in text for v in ("KEY-VALUE", "USER-VALUE", "PASS-VALUE", "ACC-VALUE"))


def test_relogin_once_on_invalid_token(tmp_path):
    stale = FakeResponse(401, {"errorCode": "error.security.client-token-invalid"})
    c, s = client(
        {
            ("GET", "/positions"): [stale, FakeResponse(200, {"positions": []})],
            ("POST", "/session"): [LOGIN_OK, LOGIN_OK],
        },
        tmp_path,
    )
    assert c.get_positions() == []
    assert s.paths().count(("POST", "/session")) == 2


# ---- market search --------------------------------------------------------


def test_search_markets_parses_results_and_skips_rows_without_epic(tmp_path):
    body = {
        "markets": [
            {"epic": "IX.D.SPTRD.DEC.IP", "instrumentName": "US 500 (DEC-26)", "expiry": "DEC-26", "instrumentType": "INDICES", "marketStatus": "TRADEABLE", "bid": 7617.08, "offer": 7618.08},
            {"instrumentName": "no epic here"},
        ]
    }
    c, s = client({("GET", "/markets"): [FakeResponse(200, body)]}, tmp_path)
    found = c.search_markets("US 500")
    assert len(found) == 1 and found[0].expiry == "DEC-26" and found[0].offer - found[0].bid == pytest.approx(1.0)
    assert all(call["method"] == "GET" for call in s.calls if call["path"] == "/markets")


# ---- market rules ---------------------------------------------------------


def test_market_rules_parse(tmp_path):
    c, _ = client({("GET", f"/markets/{EPIC}"): [market(min_size=0.5, min_stop=10)]}, tmp_path)
    r = c.get_market_rules(EPIC)
    assert (r.min_deal_size, r.min_stop_distance, r.spread) == (0.5, 10.0, 1.0)


def test_percentage_stop_rule_is_converted_to_points(tmp_path):
    c, _ = client({("GET", f"/markets/{EPIC}"): [market(min_stop=1, unit="PERCENTAGE", bid=4999, offer=5001)]}, tmp_path)
    assert c.get_market_rules(EPIC).min_stop_distance == pytest.approx(50.0)


def test_missing_stop_rule_raises_instead_of_defaulting(tmp_path):
    body = market().json()
    del body["dealingRules"]["minNormalStopOrLimitDistance"]
    c, _ = client({("GET", f"/markets/{EPIC}"): [FakeResponse(200, body)]}, tmp_path)
    with pytest.raises(MarketDataError):
        c.get_market_rules(EPIC)


# ---- prices and cache -----------------------------------------------------


def price_row(ts, o, h, l, c):
    def side(v):
        return {"bid": v - 0.5, "ask": v + 0.5}

    return {
        "snapshotTimeUTC": ts,
        "openPrice": side(o),
        "highPrice": side(h),
        "lowPrice": side(l),
        "closePrice": side(c),
    }


def prices_response(remaining=9000):
    return FakeResponse(
        200,
        {
            "prices": [price_row("2026-11-10T00:00:00", 100, 110, 90, 105), price_row("2026-11-09T00:00:00", 95, 101, 92, 100)],
            "metadata": {"allowance": {"remainingAllowance": remaining}},
        },
    )


def test_prices_parse_sort_and_second_call_uses_cache(tmp_path):
    path = f"/prices/{EPIC}/DAY/2"
    c, s = client({("GET", path): [prices_response()]}, tmp_path)
    df = c.get_prices(EPIC, "DAY", 2)
    assert list(df["close"]) == [100.0, 105.0]  # sorted oldest first, mid of bid/ask
    c.get_prices(EPIC, "DAY", 2)
    assert s.paths().count(("GET", path)) == 1


def test_allowance_guard_blocks_oversized_pull(tmp_path):
    cache = PriceCache(tmp_path)
    cache.save_allowance(5)
    session = FakeSession({("POST", "/session"): [LOGIN_OK]})
    c = IGClient(CREDS, session=session, cache=cache)
    with pytest.raises(AllowanceExhausted):
        c.get_prices(EPIC, "DAY", 60)
    assert session.calls == []


# ---- positions and confirms -----------------------------------------------


def test_positions_returns_every_position_with_pnl(tmp_path):
    def entry(deal, direction, level):
        return {
            "position": {"dealId": deal, "direction": direction, "size": 2, "level": level, "stopLevel": level - 50},
            "market": {"epic": EPIC, "instrumentName": "US 500", "bid": 5000.0, "offer": 5001.0},
        }

    c, _ = client({("GET", "/positions"): [FakeResponse(200, {"positions": [entry("A", "BUY", 4990.0), entry("B", "SELL", 5010.0)]})]}, tmp_path)
    ps = c.get_positions()
    assert [p.deal_id for p in ps] == ["A", "B"]
    assert ps[0].unrealised_pnl == 20.0   # (5000 - 4990) * 2
    assert ps[1].unrealised_pnl == 18.0   # (5010 - 5001) * 2


def test_confirm_rejected_raises(tmp_path):
    c, _ = client({("GET", "/confirms/R1"): [FakeResponse(200, {"dealStatus": "REJECTED", "reason": "MARKET_CLOSED"})]}, tmp_path)
    with pytest.raises(OrderRejected, match="MARKET_CLOSED"):
        c.confirm_deal("R1")


def test_confirm_accepted_returns_details(tmp_path):
    body = {"dealStatus": "ACCEPTED", "reason": "SUCCESS", "dealId": "D9", "epic": EPIC, "direction": "BUY", "size": 1, "level": 5001.0, "stopLevel": 4951.0}
    c, _ = client({("GET", "/confirms/R2"): [FakeResponse(200, body)]}, tmp_path)
    conf = c.confirm_deal("R2")
    assert conf.deal_id == "D9" and conf.stop_level == 4951.0
