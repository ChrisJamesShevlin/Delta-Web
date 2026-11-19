from datetime import date

import pytest

from ig_swing import options_test as ot


def test_black_put_call_parity_and_delta():
    c, dc = ot.black("C", 100.0, 100.0, 0.25, 0.2)
    p, dp = ot.black("P", 100.0, 100.0, 0.25, 0.2)
    assert c == pytest.approx(p, abs=1e-9)          # at the money on the forward
    assert dc - dp == pytest.approx(1.0)
    assert 0.5 < dc < 0.53


def test_skew_matches_ig_ticket():
    atm, f, t = 0.235, 7247.5, 4 / 365
    assert ot.strike_vol(atm, f, 7100, t, ot.FIXED["skew_b"]) * 100 == pytest.approx(26.99, abs=0.2)
    assert ot.strike_vol(atm, f, 7330, t, ot.FIXED["skew_b"]) * 100 == pytest.approx(21.60, abs=0.2)


def test_spread_model_matches_observed_quotes():
    assert ot.spread_points(57.0, 4) == pytest.approx(1.12)
    assert ot.spread_points(0.9, 4) == pytest.approx(0.4)
    assert ot.spread_points(96.0, 11) == pytest.approx(1.96)
    assert ot.spread_points(88.0, 28) == pytest.approx(2.52)
    assert ot.spread_points(88.0, 28, 1.5) == pytest.approx(3.78)


def test_atm_vol_interpolates_in_variance():
    v = ot.atm_vol({9: 20.0, 30: 20.0, 93: 20.0}, 45, scale=1.0)
    assert v == pytest.approx(0.20)
    v2 = ot.atm_vol({30: 20.0, 93: 30.0}, 61.5, scale=1.0)
    assert 0.20 < v2 < 0.30
    assert ot.atm_vol({30: 20.0}, 5, scale=0.9) == pytest.approx(0.18)


def test_third_friday_and_settle_day():
    assert ot.third_friday(2026, 11) == date(2026, 11, 20)
    assert ot.third_friday(2008, 3) == date(2008, 3, 21)
    days = [date(2008, 3, 19), date(2008, 3, 20), date(2008, 3, 24)]   # Good Friday closed
    assert ot.settle_day(date(2008, 3, 21), days) == date(2008, 3, 20)
