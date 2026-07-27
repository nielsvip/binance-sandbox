import copy

from tools import run_state_aware_entry_exit_beam_campaign as campaign


def _row(name, discovery_score, final_return):
    return {
        "entry_family": "ENTRY_X",
        "entry_artifact": "a",
        "exit_family": name,
        "exit_params": {},
        "adapter": "generic",
        "state_policy": "state_refill_gentle",
        "discovery_fold_evidence": [],
        "discovery_fold_gate_pass": [False, False],
        "discovery_strict_fold_count": 0,
        "discovery_all_folds_strict": False,
        "discovery_min_alpha_vs_bh_pp": discovery_score,
        "discovery_min_alpha_vs_control_pp": discovery_score,
        "discovery_alpha_vs_bh_pp": discovery_score,
        "discovery_alpha_vs_control_pp": discovery_score,
        "discovery_max_tim_distance_from_75": 1.0,
        "_source": {"fold_evidence": [{"final": final_return}]},
    }


def test_final_mutation_cannot_change_discovery_freeze():
    rows = [_row("A", 3.0, -999), _row("B", 2.0, 999)]
    frozen = [row["exit_family"] for row in campaign.freeze_candidates(rows, 1)]
    changed = copy.deepcopy(rows)
    changed[0]["_source"]["fold_evidence"][-1]["final"] = 1e20
    changed[1]["_source"]["fold_evidence"][-1]["final"] = -1e20
    refrozen = [
        row["exit_family"] for row in campaign.freeze_candidates(changed, 1)
    ]
    assert frozen == refrozen


def test_public_discovery_row_redacts_final_and_keeps_state_policy():
    public = campaign._public(_row("A", 1.0, 123), False)
    assert public["state_policy"] == "state_refill_gentle"
    assert "untouched_final_validation" not in public
