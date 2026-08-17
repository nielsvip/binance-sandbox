import pytest

from bottom_b_delayed_contract import BottomBDelayedLowerTop, params_from_recipe
from vec_paths.structural_wt_retest_exit import CompletedBar


def recipe(**changes):
    params = {
        "arm_tf": "1h", "confirm_tf": "5m", "rebound_atr": .25,
        "prebreak_lookback": 2, "max_wait_1h": 3, "max_wait_hours": .25,
        "confirmation_mode": "PRICE_ONLY", "confirmation_bars": 1,
        "emergency_modes": [], "emergency_adverse_atr": 0.,
        "emergency_adverse_stdev": 0., "emergency_continued_bars": 0,
        "arm_break_mode": "PREV_BAR", "arm_break_threshold": 0.,
    }
    params.update(changes)
    return {"family": "BOTTOM_B_DELAYED_LOWER_TOP_EXTENDED", "params": params}


def bar(tf, ts, high, low, close, wt, atr=1.):
    return CompletedBar(tf, ts, ts, high, low, close, wt, atr)


def test_bottom_b_arms_then_exits_only_after_later_rebound_lower_top_confirmation():
    route = BottomBDelayedLowerTop(recipe())
    # Two completed arm parents establish anchors; the third is the adverse
    # break.  It is explicitly not an exit.
    for item in (bar("1h", 100, 10, 8, 9, 5), bar("1h", 200, 11, 9, 10, 6), bar("1h", 300, 10, 7, 7.5, 4)):
        assert route.update(symbol="ABC", position_side="LONG", active=True, bar=item, role="ARM") is None
    assert route.update(symbol="ABC", position_side="LONG", active=True, bar=bar("5m", 310, 8, 7, 7.5, 4), role="CONFIRM") is None
    # Lower rebound top moves the state to WAIT_CONFIRM, still no exit.
    assert route.update(symbol="ABC", position_side="LONG", active=True, bar=bar("5m", 320, 9, 7.5, 8, 5), role="CONFIRM") is None
    signal = route.update(symbol="ABC", position_side="LONG", active=True, bar=bar("5m", 330, 8.5, 7, 7.4, 4), role="CONFIRM")
    assert signal is not None
    assert signal.reason.startswith("BOTTOM_B_DELAYED_LOWER_TOP__")
    assert signal.arm_source_ts == 300
    assert signal.source_ts == 330


def test_bottom_b_rejects_emergency_or_inconsistent_frozen_recipe():
    with pytest.raises(ValueError, match="Bottom-B must not include"):
        params_from_recipe(recipe(emergency_modes=["MAX_WAIT"]))
    with pytest.raises(ValueError, match="max_wait_1h"):
        params_from_recipe(recipe(max_wait_1h=4))
