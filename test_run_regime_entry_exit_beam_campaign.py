from tools import run_regime_entry_exit_beam_campaign as campaign


def test_public_keeps_policy_and_redacts_final_when_not_selected():
    row = {
        "entry_family": "ENTRY_X",
        "entry_artifact": "a",
        "exit_family": "EXIT_E02_DONCHIAN",
        "exit_params": {},
        "adapter": "generic",
        "regime_policy": "ternary_balanced",
        "discovery_fold_evidence": [],
        "discovery_fold_gate_pass": [],
        "discovery_strict_fold_count": 0,
        "discovery_all_folds_strict": False,
        "discovery_min_alpha_vs_bh_pp": 0.0,
        "discovery_min_alpha_vs_control_pp": 0.0,
        "discovery_alpha_vs_bh_pp": 0.0,
        "discovery_alpha_vs_control_pp": 0.0,
        "discovery_max_tim_distance_from_75": 0.0,
        "_source": {},
    }
    public = campaign._public(row, False)
    assert public["regime_policy"] == "ternary_balanced"
    assert "untouched_final_validation" not in public
