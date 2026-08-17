from tools import run_entry_exit_beam_campaign as beam


def test_strict_fold_requires_both_alphas_tim_exit_and_reclaim():
    row = {
        "alpha_vs_bh_pp": 3.0,
        "alpha_vs_same_entry_e02_pp": 1.0,
        "weighted_tim_pct": 75.0,
        "real_close_trades": 2,
        "unfilled_obligations": 0,
        "future_htf_source_count": 0,
        "bars_flat_beyond_reclaim": 0,
        "entry_capacity_breach": False,
        "insolvent": False,
    }
    assert beam._strict_fold(row)
    for key, bad in (
        ("alpha_vs_bh_pp", 0.0),
        ("alpha_vs_same_entry_e02_pp", 0.0),
        ("weighted_tim_pct", 80.01),
        ("real_close_trades", 0),
        ("unfilled_obligations", 1),
        ("future_htf_source_count", 1),
        ("bars_flat_beyond_reclaim", 1),
        ("entry_capacity_breach", True),
        ("insolvent", True),
    ):
        changed = dict(row)
        changed[key] = bad
        assert not beam._strict_fold(changed), key


def test_exit_rank_never_uses_validation():
    discovery = {
        "discovery_all_folds_strict": False,
        "discovery_strict_fold_count": 1,
        "discovery_min_alpha_vs_control_pp": 2.0,
        "discovery_min_alpha_vs_bh_pp": 3.0,
        "discovery_max_tim_distance_from_75": 1.0,
        "discovery_alpha_vs_control_pp": 9.0,
        "exit_family": "EXIT_E02_DONCHIAN",
    }
    left = {**discovery, "untouched_final_validation": {"strict": False}}
    right = {**discovery, "untouched_final_validation": {"strict": True}}
    assert beam._exit_rank(left) == beam._exit_rank(right)


def test_plain_ladder_entry_is_its_own_e02_control(tmp_path):
    artifact = tmp_path / "ladder"
    artifact.mkdir()
    fold = {
        "fold": 1,
        "validation": ["2025-01-01", "2025-07-01"],
        "validation_metrics": {
            "capital_return_pct": 10.0,
            "bh_capital_return_pct": 2.0,
            "exposure_weighted_tim_pct": 75.0,
        },
    }
    payload = {
        "manifest": {"symbol": "MU", "side": "LONG"},
        "outer_folds": [fold, {**fold, "fold": 2}],
    }
    (artifact / "result.json").write_text(
        __import__("json").dumps(payload)
    )
    row = beam._entry_discovery(artifact)
    assert row["family"] == "ENTRY_LADDER_GREEN"
    assert row["discovery_alpha_vs_control_pp"] == 0.0


def test_candidate_summary_redacts_final_until_selected():
    folds = [
        {
            "alpha_vs_bh_pp": 2.0,
            "alpha_vs_same_entry_e02_pp": 1.0,
            "weighted_tim_pct": 75.0,
            "real_close_trades": 1,
        },
        {
            "alpha_vs_bh_pp": 5.0,
            "alpha_vs_same_entry_e02_pp": 4.0,
            "weighted_tim_pct": 76.0,
            "real_close_trades": 1,
        },
    ]
    source = {
        "family": "EXIT_E05_DIVERGENCE_RETEST",
        "params": {"x": 1},
        "fold_evidence": folds,
        "metrics": {"clip_obligations_unfilled_at_end": 0},
    }
    row = beam._candidate_discovery_summary(
        source,
        "e05",
        {"family": "ENTRY_BOUNCE_5M_LOW", "artifact": "/tmp/a"},
    )
    hidden = beam._public_candidate(row, False)
    shown = beam._public_candidate(row, True)
    assert "untouched_final_validation" not in hidden
    assert hidden["discovery_fold_evidence"] == folds[:-1]
    assert shown["untouched_final_validation"]["fold_evidence"] == folds[-1]
    assert shown["all_folds_strict"]


def test_close_gate_rejects_broad_exit_actions_without_terminal_close_count():
    row = {
        "alpha_vs_bh_pp": 3.0,
        "alpha_vs_same_entry_e02_pp": 1.0,
        "weighted_tim_pct": 75.0,
        "exit_fills": 99,
        "partial_exit_fills": 98,
        "unfilled_obligations": 0,
        "future_htf_source_count": 0,
        "bars_flat_beyond_reclaim": 0,
        "entry_capacity_breach": False,
        "insolvent": False,
    }
    assert beam._fold_exit_count(row) == 0
    assert not beam._strict_fold(row)
    row["terminal_lifecycle_closes"] = 11
    assert beam._fold_exit_count(row) == 11
    assert beam._strict_fold(row)


def test_forced_entry_baselines_can_expand_bounded_rank_beam(monkeypatch, tmp_path):
    reports = []
    rows = [
        ("ENTRY_X", tmp_path / "x"),
        ("ENTRY_LADDER_GREEN", tmp_path / "ladder"),
        ("ENTRY_DC_TIER_AUG_ENABLED", tmp_path / "dc"),
    ]
    for index, (family, artifact) in enumerate(rows):
        artifact.mkdir()
        (artifact / "result.json").write_text(
            '{"manifest":{"symbol":"MU","side":"LONG"},'
            '"outer_folds":[{},{}]}'
        )
        reports.append(
            {
                "symbol": "MU",
                "side": "LONG",
                "family": family,
                "artifact": str(artifact),
            }
        )
    component = tmp_path / "components.json"
    dc = tmp_path / "dc.json"
    component.write_text(__import__("json").dumps({"rows": reports[:2]}))
    dc.write_text(__import__("json").dumps({"rows": reports[2:]}))

    def fake_entry(artifact):
        family = next(f for f, path in rows if path == artifact)
        return {
            "symbol": "MU",
            "side": "LONG",
            "family": family,
            "artifact": str(artifact),
            "discovery_all_folds_strict": family == "ENTRY_X",
            "discovery_strict_fold_count": int(family == "ENTRY_X"),
            "discovery_alpha_vs_control_pp": 1.0,
            "discovery_alpha_vs_bh_pp": 1.0,
            "discovery_max_tim_distance_from_75": 0.0,
        }

    monkeypatch.setattr(beam, "_entry_discovery", fake_entry)
    selected, _ = beam.load_entry_beam(
        component, dc, {"MU_LONG"}, beam_width=1
    )
    assert {row["family"] for row in selected} == {
        "ENTRY_X",
        "ENTRY_LADDER_GREEN",
        "ENTRY_DC_TIER_AUG_ENABLED",
    }


def test_repo_root_is_importable_for_remote_script_execution():
    import sys

    assert str(beam.ROOT) in sys.path


def test_independent_entry_errors_do_not_discard_strict_survivors():
    error = [{"entry_family": "ENTRY_BROKEN"}]
    survivor = [{"entry_family": "ENTRY_PROVEN", "exit_family": "EXIT_PROVEN"}]
    assert beam._campaign_exit_code(error, survivor) == 0
    assert beam._campaign_exit_code(error, []) == 1
