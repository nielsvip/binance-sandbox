from tools.run_bottom_structural_v2_campaign import (
    _compact_winner,
    _fold_failures,
    preregistration,
)


def _fold(**overrides):
    row = {
        "alpha_vs_bh_pp": 1.0,
        "alpha_vs_same_entry_e02_pp": 1.0,
        "weighted_tim_pct": 75.0,
        "exit_fills": 2,
        "insolvent": False,
        "entry_capacity_breach": False,
        "future_htf_source_count": 0,
        "bars_flat_beyond_reclaim": 0,
        "emergency_exit_share": 0.0,
    }
    row.update(overrides)
    return row


def test_preregistration_is_bounded_and_never_sells_break_bar():
    contract = preregistration()
    assert contract["families"]["B"]["candidate_count"] == 960
    assert contract["families"]["B"]["break_bar_can_exit"] is False
    assert contract["families"]["C"]["candidate_count"] == 12
    assert contract["families"]["C"]["emergency_share_max_each_fold"] == 0.10


def test_fold_gate_enforces_solvency_exposure_and_rare_emergency():
    assert not _fold_failures(_fold(), emergency=False)
    failures = _fold_failures(
        _fold(
            weighted_tim_pct=81.0,
            insolvent=True,
            emergency_exit_share=0.11,
        ),
        emergency=True,
    )
    assert failures == [
        "TIM_OUTSIDE_70_80",
        "INSOLVENT",
        "EMERGENCY_NOT_RARE",
    ]


def test_isolated_strict_winner_is_quarantined_from_exact_queue():
    winner = {
        "family": "BOTTOM_B_STRUCTURAL_V2",
        "params": {"arm_break_mode": "ATR"},
        "fold_evidence": [_fold(), _fold(), _fold()],
        "compiled_python_parity": {"status": "PASS"},
    }
    compact = _compact_winner(winner, isolated=True)
    assert compact["all_folds_strict"] is True
    assert compact["exact_engine_replay_ready"] is False
    assert compact["quarantine_reason"] == "ISOLATED_VERSIONED_DATA"
