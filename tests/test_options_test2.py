from datetime import date

from ig_swing import options_test2 as o2


def test_quarterly_expiry_picks_next_third_friday_of_a_quarter_month():
    assert o2.quarterly_expiry(date(2026, 11, 19)) == date(2026, 12, 18)
    assert o2.quarterly_expiry(date(2026, 12, 18)) == date(2027, 3, 19)
    assert o2.quarterly_expiry(date(2026, 12, 17)) == date(2026, 12, 18)
