from tools import run_actionable_three_key_bundle_search as search


def _metrics(**updates):
    row = {
        "deployed_alpha_vs_bh_or_cash_pp": 3.0,
        "return_on_deployed_pct": 13.0,
        "exposure_weighted_tim_pct": 72.0,
        "exit_count": 5,
        "fill_ratio": 1.0,
        "bars_flat_beyond_reclaim": 0,
        "insolvent": False,
        "entry_capacity_breach": False,
    }
    row.update(updates)
    return row


def test_registry_is_bounded_key_specific_and_unique():
    expected = {
        "DINO_LONG": (8, 1, 8),
        "PBF_LONG": (9, 2, 18),
        "CHRD_SHORT": (8, 4, 32),
    }
    for key, (entries, exits, candidates) in expected.items():
        assert len(search.profiles_for(key)) == entries
        assert len(search.exits_for(key)) == exits
        ids = {
            search._candidate_id(profile, exit_label)
            for profile in search.profiles_for(key)
            for exit_label in search.exits_for(key)
        }
        assert len(ids) == candidates


def test_pbf_profiles_keep_stochastic_trigger_and_add_density():
    rows = search.profiles_for("PBF_LONG")
    assert {row.stoch_threshold for row in rows} == {25.0, 30.0, 40.0}
    assert all(row.trigger == "structure" for row in rows)
    assert all("REFILL" in row.density_overlay for row in rows)


def test_chrd_profiles_are_short_native_and_capacity_bounded():
    rows = search.profiles_for("CHRD_SHORT")
    assert all(row.wt_dc_threshold in {35.0, 50.0} for row in rows)
    assert all(row.gr_min_tfs in {2, 3} for row in rows)
    assert all(row.cap_mult <= 6.0 for row in rows)


def test_fold_gate_requires_benchmark_control_tim_and_reclaim():
    control = _metrics(return_on_deployed_pct=10.0)
    assert search.fold_gate(_metrics(), control, 65, 80) == []
    cases = (
        (
            {"deployed_alpha_vs_bh_or_cash_pp": 0.0},
            "NOT_ABOVE_SIDE_BH_OR_CASH",
        ),
        (
            {"return_on_deployed_pct": 10.0},
            "NOT_ABOVE_SAME_ENTRY_E02_CONTROL",
        ),
        ({"exposure_weighted_tim_pct": 80.1}, "TIM_NOT_65_80"),
        ({"bars_flat_beyond_reclaim": 1}, "FORGOTTEN_MANDATORY_RECLAIM"),
    )
    for updates, failure in cases:
        assert failure in search.fold_gate(
            _metrics(**updates), control, 65, 80
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
    assert sorted([gray, strict], key=search._rank)[0] is strict
