import inspect
from types import SimpleNamespace

import pytest

import ez_manage


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_flat_reentry_negative_native_sizing_gets_finite_positive_floor(side):
    position = SimpleNamespace(positionAmt=0.0, position_side=side)
    result = ez_manage._flat_reentry_qty_floor(
        -11.861609135797694 / 0.004743,
        "OPEN",
        True,
        position,
        1.0,
        0.004743,
        0.001,
    )
    assert result == pytest.approx(6.0 / 0.004743)
    assert result > 0.0


def test_nonflat_negative_gain_augment_keeps_native_quantity_blocked():
    position = SimpleNamespace(positionAmt=0.02, gain=-4.53)
    original_quantity = -11.861609135797694 / 0.004743
    result = ez_manage._flat_reentry_qty_floor(
        original_quantity,
        "AUGMENT",
        False,
        position,
        1.0,
        0.004743,
        0.001,
    )
    assert result == original_quantity
    assert result <= 3.0 / 0.004743


def test_reentry_flag_cannot_floor_if_actual_position_is_nonflat():
    original_quantity = -11.861609135797694 / 0.004743
    result = ez_manage._flat_reentry_qty_floor(
        original_quantity,
        "OPEN",
        True,
        SimpleNamespace(positionAmt=-0.001),
        1.0,
        0.004743,
        0.001,
    )
    assert result == original_quantity


def test_flat_reentry_floor_is_wired_before_unchanged_final_quantity_refusal():
    source = inspect.getsource(ez_manage.MultiAccountTradeManager.execute_trade_action)
    floor_call = source.index("quantity = _flat_reentry_qty_floor(")
    refusal = source.index("order not_allowed no quantity left")
    assert floor_call < refusal
    assert "action != \"OPEN\"" in inspect.getsource(ez_manage._flat_reentry_qty_floor)
    assert "amount != 0.0" in inspect.getsource(ez_manage._flat_reentry_qty_floor)

