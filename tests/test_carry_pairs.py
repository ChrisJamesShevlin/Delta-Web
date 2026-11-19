import math

from ig_swing import carry_test as ct
from ig_swing import pairs_test as pt


def test_months_between():
    assert ct.months_between("2025-11", "2026-02") == ["2025-11", "2025-12", "2026-01", "2026-02"]


def test_pairs_rule_enters_on_divergence_and_exits_on_reversion():
    days = [f"2010-{1 + i // 28:02d}-{1 + i % 28:02d}" for i in range(200)]
    a, b = {}, {}
    for i, d in enumerate(days):
        wobble = 0.002 * math.sin(i)
        jump = 0.0035 if 100 <= i < 105 else 0.0      # A runs rich for five days, then comes back
        a[d] = (0, 100 * math.exp(wobble + jump))
        b[d] = (0, 100.0)
    trades = pt.trades_for("AAPL", "MSFT", {"AAPL": a, "MSFT": b})
    assert trades, "expected a trade"
    t = trades[0]
    assert t["side"] == -1                          # short the rich leg
    assert t["gross"] > 0                           # the gap closed
    assert t["net"] < t["gross"]


def test_clustered_standard_error_is_positive():
    ts = [{"net": 0.01, "exit": "2010-01-05"}, {"net": -0.005, "exit": "2010-02-05"}, {"net": 0.002, "exit": "2010-03-05"}]
    mu, se, z = pt.clustered(ts)
    assert se > 0 and abs(mu - 0.007 / 3) < 1e-12
