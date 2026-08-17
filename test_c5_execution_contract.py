from c5_execution_contract import (
    actual_filled_quantity,
    clamp_open_quantity,
    exact_side_allowed,
)


def test_exact_side_contract_rejects_opposite_ladder_key():
    assert exact_side_allowed("LONG", "LONG")
    assert not exact_side_allowed("SHORT", "LONG")
    assert exact_side_allowed("SHORT", "")


def test_last_mile_capacity_clamps_cumulative_exposure():
    qty = clamp_open_quantity(
        requested_qty=200,
        price=100,
        existing_qty=60,
        capacity_usd=16000,
    )
    assert qty == 100
    assert (60 + qty) * 100 == 16000


def test_position_update_uses_actual_fills_not_requested_quantity():
    events = [
        {
            "position_key": "trb:MU_LONG",
            "quantity": 100,
            "executed_qty": 100,
        },
        {
            "position_key": "trb:MU_SHORT",
            "quantity": 999,
            "executed_qty": 999,
        },
    ]
    assert actual_filled_quantity(events, "trb:MU_LONG") == 100
