"""Acceptance fixture distilled from the failed exact MU_LONG c4 fold-3 run.

The source receipt is:
data/reports/exact_mu_wt_exit_interactions_20260730/fold_3/dc_1h_n5/receipt.json
on s1.  This deliberately keeps only the evidence needed to prove that c5
rejects all three structural failure classes; it is not a replacement receipt.
"""

from c5_execution_contract import (
    actual_filled_quantity,
    clamp_open_quantity,
    exact_side_allowed,
)
from c5_matrix_safety import audit_c5_matrix_safety
from reentry_contract import force_reentry_after_temporary_opposition


FOLD3_RESULT = {
    "max_open_notional": 35619.0,
    "reentry_violations": 1,
    "reentry_pending": 2,
    "reclaim_pending": 1,
}

FOLD3_TRADES = [
    {
        # One of 24 real opposite-side P&L rows in the forced MU_LONG run.
        "symbol": "MU",
        "position_side": "SHORT",
        "entry_reason": (
            "LR_BAND_LADDER_PARITY_TARGET_x8.000_usd16000_D_qty_130"
            "|NEXT_AVAIL_FILL"
        ),
        "action_events": [
            {
                "timestamp": 1755611100,
                "action": "OPEN",
                "requested_qty": 130,
                "executed_qty": 130,
                "price": 122.79000091552734,
                "position_key": "trb:MU_SHORT",
            },
            {
                "timestamp": 1755698400,
                "action": "CLOSE",
                "requested_qty": 130,
                "executed_qty": 130,
                "price": 114.91999816894531,
                "position_key": "trb:MU_SHORT",
            },
        ],
    },
    {
        # Exact ledger OPEN: one fill alone exceeded the declared $16k cap.
        "symbol": "MU",
        "position_side": "LONG",
        "action_events": [
            {
                "timestamp": 1754669400,
                "action": "OPEN",
                "requested_qty": 135.03629424935178,
                "executed_qty": 135.0,
                "price": 118.71833038330078,
                "position_key": "trb:MU_LONG",
            }
        ],
    },
]


def test_failed_fold3_is_rejected_for_side_capacity_and_reentry() -> None:
    audit = audit_c5_matrix_safety(
        FOLD3_TRADES,
        FOLD3_RESULT,
        intended_side="LONG",
        capacity_usd=16000,
    )

    assert audit["pass"] is False
    assert audit["opposite_side_pnl_rows"] == 1
    assert audit["peak_requested_notional"] > 16000
    assert audit["peak_executed_notional"] > 16000
    assert audit["engine_max_open_notional"] == 35619
    assert audit["reentry_violations"] == 1
    assert audit["reentry_pending"] == 2
    assert audit["reclaim_pending"] == 1
    assert any("opposite-side" in reason for reason in audit["reasons"])
    assert any("executed exposure" in reason for reason in audit["reasons"])
    assert any("reentry obligations pending" in reason for reason in audit["reasons"])


def test_c5_pure_contracts_block_the_fold3_execution_paths() -> None:
    assert not exact_side_allowed("SHORT", "LONG")

    accepted = clamp_open_quantity(
        requested_qty=135.03629424935178,
        price=118.71833038330078,
        existing_qty=0,
        capacity_usd=16000,
    )
    assert accepted < 135.0
    assert accepted * 118.71833038330078 <= 16000.000001

    assert (
        actual_filled_quantity(
            FOLD3_TRADES[1]["action_events"], "trb:MU_LONG"
        )
        == 135.0
    )


def test_fold3_opposition_cannot_postpone_reentry_forever() -> None:
    row = {
        "pending": True,
        "flat_bars": 807,
        "max_overshoot_pct": 12.2503,
        "last_opposed_tfs": ["1h", "4h"],
    }
    assert force_reentry_after_temporary_opposition(
        row,
        favorable=True,
        material_pct=0.5,
        max_opposed_bars=12,
    )
