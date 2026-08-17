from c5_matrix_safety import audit_c5_matrix_safety


def _trade(side="LONG", qty=10, price=100):
    return {
        "position_side": side,
        "action_events": [
            {
                "timestamp": 1,
                "action": "OPEN",
                "requested_qty": qty,
                "executed_qty": qty,
                "price": price,
            },
            {
                "timestamp": 2,
                "action": "CLOSE",
                "requested_qty": qty,
                "executed_qty": qty,
                "price": price,
            },
        ],
    }


def test_c5_safety_passes_clean_side_capacity_and_reentry():
    audit = audit_c5_matrix_safety(
        [_trade()],
        {
            "max_open_notional": 1000,
            "reentry_violations": 0,
            "reentry_pending": 0,
            "reclaim_pending": 0,
        },
        intended_side="LONG",
    )
    assert audit["pass"]


def test_c5_safety_rejects_fold3_failure_classes():
    audit = audit_c5_matrix_safety(
        [_trade(), _trade(side="SHORT", qty=200, price=100)],
        {
            "max_open_notional": 35619,
            "reentry_violations": 1,
            "reentry_pending": 2,
            "reclaim_pending": 1,
        },
        intended_side="LONG",
    )
    assert not audit["pass"]
    assert audit["opposite_side_pnl_rows"] == 1
    assert audit["peak_requested_notional"] == 20000
    assert len(audit["reasons"]) == 7


def test_c5_safety_accepts_terminal_right_censoring_without_overshoot():
    audit = audit_c5_matrix_safety(
        [_trade()],
        {
            "max_open_notional": 1000,
            "reentry_violations": 0,
            "reentry_pending": 1,
            "reclaim_pending": 1,
        },
        intended_side="LONG",
        allow_terminal_pending=True,
    )
    assert audit["pass"]
    assert audit["terminal_right_censored"] is True
    assert audit["reentry_pending"] == 1
    assert audit["reclaim_pending"] == 1
