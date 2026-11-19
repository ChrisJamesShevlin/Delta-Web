from ig_swing import options_test3 as o3


def test_nice_step_rounds_up_to_1_2_5():
    assert o3.nice_step(107) == 200
    assert o3.nice_step(10.7) == 20
    assert o3.nice_step(14.8) == 20
    assert o3.nice_step(33) == 50
