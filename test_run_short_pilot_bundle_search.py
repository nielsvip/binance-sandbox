import dataclasses

from tools import run_short_pilot_bundle_search as pilot


def _metrics(**updates):
    row = {
        "deployed_alpha_vs_bh_or_cash_pp": 2.0,
        "return_on_deployed_pct": 12.0,
        "exposure_weighted_tim_pct": 72.0,
        "exit_count": 4,
        "fill_ratio": 1.0,
        "bars_flat_beyond_reclaim": 0,
        "insolvent": False,
        "entry_capacity_breach": False,
    }
    row.update(updates)
    return row


def test_candidate_registry_is_bounded_unique_and_short_native():
    assert len(pilot.ENTRY_BUNDLES) == 9
    assert len(pilot.EXIT_LABELS) == 8
    assert len(
        {
            pilot._candidate_id(entry, exit_label)
            for entry in pilot.ENTRY_BUNDLES
            for exit_label in pilot.EXIT_LABELS
        }
    ) == 72
    assert any(
        "correction" in row.families for row in pilot.ENTRY_BUNDLES
    )
    assert any(
        "elevator" in row.families for row in pilot.ENTRY_BUNDLES
    )
    assert all(row.target_mult <= 8 for row in pilot.ENTRY_BUNDLES)


def test_fold_gate_requires_benchmark_control_tim_and_reclaim():
    control = _metrics(return_on_deployed_pct=10.0)
    assert pilot.fold_gate(
        _metrics(), control, tim_lo=65, tim_hi=80
    ) == []
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
        ({"entry_capacity_breach": True}, "ENTRY_CAPACITY"),
    )
    for updates, expected in cases:
        assert expected in pilot.fold_gate(
            _metrics(**updates), control, tim_lo=65, tim_hi=80
        )


def test_candidate_identity_changes_as_a_coherent_bundle():
    entry = pilot.ENTRY_BUNDLES[0]
    changed = dataclasses.replace(entry, target_mult=entry.target_mult + 1)
    assert pilot._candidate_id(entry, pilot.EXIT_LABELS[0]) != (
        pilot._candidate_id(changed, pilot.EXIT_LABELS[0])
    )
    assert pilot._candidate_id(entry, pilot.EXIT_LABELS[0]) != (
        pilot._candidate_id(entry, pilot.EXIT_LABELS[1])
    )


def test_rank_prefers_two_fold_strict_identity():
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
    assert sorted([gray, strict], key=pilot._rank)[0] is strict
