from ig_swing import cot_test as cot


def test_signals_fire_on_fresh_extremes_only():
    base = [(None, 0.01 * ((i * 37) % 101) / 101) for i in range(1, 200)]   # spread-out values, no ties
    series = [(f"d{i:04d}", v) for i, (_, v) in enumerate(base)]
    series[170] = ("d0170", 0.5)    # crowd very long -> short
    series[171] = ("d0171", 0.5)    # still extreme, not fresh
    series[180] = ("d0180", -0.5)   # crowd very short -> long
    sig = cot.signals(series)
    assert ("d0170", -1) in sig
    assert ("d0171", -1) not in sig
    assert ("d0180", 1) in sig


def test_percentile():
    assert cot.percentile([1, 2, 3, 4], 5) == 1.0
    assert cot.percentile([1, 2, 3, 4], 0) == 0.0
