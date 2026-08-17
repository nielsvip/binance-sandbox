import numpy as np

from tools import run_chrd_flat_density_followup as follow


def _metrics(**updates):
    row = {
        "deployed_alpha_vs_bh_or_cash_pp": 2.0,
        "return_on_deployed_pct": 12.0,
        "exposure_weighted_tim_pct": 70.0,
        "exit_count": 4,
        "fill_ratio": 1.0,
        "bars_flat_beyond_reclaim": 0,
        "insolvent": False,
        "entry_capacity_breach": False,
    }
    row.update(updates)
    return row


def test_source_identity_is_frozen_and_policy_grid_is_small():
    assert follow.SOURCE_ENTRY_LABEL == "CHRD_GRWTDC_DIRECT6"
    assert follow.SOURCE_EXIT_LABEL == "B_HB_BAL_1H"
    assert len(follow.DENSITY_POLICIES) == 6
    assert len(
        {follow._candidate_id(row) for row in follow.DENSITY_POLICIES}
    ) == 6
    assert all(row.target_mult <= 6 for row in follow.DENSITY_POLICIES)


def test_prior_flat_fraction_excludes_current_event():
    values = np.array([0, 1, 1, 0, 1], dtype=np.uint8)
    got = follow.prior_flat_fraction(values, 3)
    assert np.isnan(got[:3]).all()
    assert got[3] == 2 / 3
    assert got[4] == 2 / 3
    changed_current = values.copy()
    changed_current[3] = 1
    changed = follow.prior_flat_fraction(changed_current, 3)
    # The score at row 3 cannot consume row 3.
    assert changed[3] == got[3]
    # The changed observation becomes available to the next row.
    assert changed[4] == 1.0


def test_density_emits_only_once_per_frozen_source_flat_episode():
    flat = np.array([1, 1, 1, 0, 1, 1, 0, 1], dtype=np.uint8)
    qualified = np.array([0, 1, 1, 0, 1, 1, 0, 1], dtype=np.uint8)
    got = follow.one_event_per_flat_episode(qualified, flat)
    assert np.flatnonzero(got).tolist() == [1, 4, 7]
    assert np.all(flat[got] == 1)


def test_fold_gate_requires_benchmark_control_tim_and_reclaim():
    control = _metrics(return_on_deployed_pct=10.0)
    assert follow.fold_gate(_metrics(), control, 65, 80) == []
    cases = (
        (
            {"deployed_alpha_vs_bh_or_cash_pp": 0.0},
            "NOT_ABOVE_SIDE_BH_OR_CASH",
        ),
        (
            {"return_on_deployed_pct": 10.0},
            "NOT_ABOVE_SAME_ENTRY_E02_CONTROL",
        ),
        ({"exposure_weighted_tim_pct": 64.9}, "TIM_NOT_65_80"),
        ({"bars_flat_beyond_reclaim": 1}, "FORGOTTEN_MANDATORY_RECLAIM"),
    )
    for updates, expected in cases:
        assert expected in follow.fold_gate(
            _metrics(**updates), control, 65, 80
        )


def test_rank_prefers_two_fold_strict_candidate():
    fold = {
        "metrics": {
            **_metrics(),
            "max_drawdown_account_pct": 4.0,
        },
        "same_entry_control": _metrics(return_on_deployed_pct=10.0),
        "failures": [],
    }
    strict = {
        "candidate_id": "strict",
        "discovery_folds": [fold, fold],
        "discovery_all_folds_strict": True,
    }
    gray = {
        "candidate_id": "gray",
        "discovery_folds": [
            fold,
            {**fold, "failures": ["TIM_NOT_65_80"]},
        ],
        "discovery_all_folds_strict": False,
    }
    assert sorted([gray, strict], key=follow._rank)[0] is strict
