from tools import ingest_dino_phase3_joint_stability as ingest


def test_payload_is_discovery_only_gray_and_attributable():
    fold = {
        "strategy_return_pct": 20.0,
        "bh_return_pct": 10.0,
        "same_entry_e02_return_pct": 21.0,
        "weighted_tim_pct": 75.0,
        "actual_exit_fills": 2,
        "future_htf_source_count": 0,
    }
    result = {
        "campaign": "X",
        "source_artifact": "/source",
        "frozen_exit_label": "P3_COMBO_DIV_OR_STRUCTURE",
        "availability_clock": "CLOCK",
        "fill_timing": "NEXT",
        "capital_contract": {},
        "final_fold_evaluated": False,
        "frozen_discovery_winner": {
            "profile": {"label": "P"},
            "discovery_evidence": [fold, {**fold, "strategy_return_pct": 22.0}],
            "discovery_fold_gate_pass": [False, True],
            "discovery_strict": False,
        },
    }
    row = ingest.payload(result, "/campaign", 36)
    assert row["status"] == "GRAY_REJECTED"
    assert row["strategy_return_pct"] == 42.0
    assert row["same_entry_control_return_pct"] == 42.0
    assert row["path_id"] == "ENTRY_STOCH_HHHL"
    assert row["fixed_exit_path"] == "EXIT_STRUCTURAL_WT_LOWER_TOP"
    assert not row["untouched_oos"]
    assert not row["exact_replay"]
