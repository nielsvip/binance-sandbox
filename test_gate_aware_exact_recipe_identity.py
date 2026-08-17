import hashlib
import json

from tools.run_hotlist_v8_full_recipe import (
    canonical,
    classic_formation_recipe_identity_valid,
    find_champion,
    select_hot_for_key,
    strict_vector_exact_admission_valid,
)


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True))


def test_classic_formation_identity_requires_bound_receipt_artifact_and_recipe():
    recipe = {
        "schema": "complete-vector-lifecycle-recipe-v1",
        "KEY": "ABC_LONG",
        "ENTRY": {
            "family": "ENTRY_CLASSIC_FORMATION_TREND_STRUCTURE",
            "artifact": "data/reports/vec_research/form/entry",
            "artifact_result_sha256": "b" * 64,
            "params": {"timeframe": "ALL", "min_score": 0.65, "position_size_mult": 1.0},
        },
        "EXIT": {
            "family": "EXIT_CLASSIC_FORMATION_TREND_STRUCTURE",
            "params": {"timeframe": "ALL", "min_score": 0.65, "exit_min_gain_pct": 0.0},
        },
    }
    row = {
        "key": "ABC_LONG",
        "classic_formation_vector_pass": True,
        "exact_v8_candidate_authorized": True,
        "classic_formation_vector_complete_recipe": recipe,
        "classic_formation_vector_complete_recipe_sha256": hashlib.sha256(
            canonical(recipe).encode()
        ).hexdigest(),
        "classic_formation_vector_artifact_sha256": "b" * 64,
        "classic_formation_vector_source_receipt": (
            "data/reports/vec_research/form/campaign_receipt.json"
        ),
        "classic_formation_vector_source_receipt_sha256": "a" * 64,
    }
    assert classic_formation_recipe_identity_valid(row) is True
    assert classic_formation_recipe_identity_valid(
        {key: value for key, value in row.items() if key != "exact_v8_candidate_authorized"}
    ) is False
    assert classic_formation_recipe_identity_valid(
        {
            **row,
            "classic_formation_vector_source_receipt_sha256": "tampered",
        }
    ) is False


def test_gate_aware_selection_and_replay_use_bound_recipe_not_legacy_hotlist(tmp_path):
    artifact = tmp_path / "data/reports/vec_research/v3/entry/result.json"
    _write_json(artifact, {"proof": "gate-aware-v3"})
    artifact_sha = hashlib.sha256(artifact.read_bytes()).hexdigest()
    recipe = {
        "schema": "complete-vector-lifecycle-recipe-v1",
        "ENTRY": {
            "family": "ENTRY_STOCH_HHHL",
            "artifact": str(artifact.parent.relative_to(tmp_path)),
            "artifact_result_sha256": artifact_sha,
            "params": {"enabled_tfs": ["1h"], "min_confirming_tfs": 1, "stoch_threshold": 20},
        },
        "EXIT": {"family": "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED", "params": {}},
    }
    receipt = tmp_path / "data/reports/vec_research/capacity_frontier_v3/campaign_receipt.json"
    # The top candidate is the independently strict survivor.  The failed
    # beam's display champion is intentionally a different recipe.
    _write_json(
        receipt,
        {
            "hotlist": [{
                "key": "ABC_LONG",
                "champion": {
                    "key": "ABC_LONG",
                    "entry_family": "ENTRY_LADDER_GREEN",
                    "exit_family": "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED",
                    "exit_params": {},
                },
                "top_candidates": [{
                    "key": "ABC_LONG",
                    "entry_family": "ENTRY_STOCH_HHHL",
                    "entry_artifact": recipe["ENTRY"]["artifact"],
                    "exit_family": "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED",
                    "exit_params": {},
                    "complete_recipe": recipe,
                }],
            }],
        },
    )
    receipt_sha = hashlib.sha256(receipt.read_bytes()).hexdigest()
    gate_row = {
        "key": "ABC_LONG",
        "entry_family": "ENTRY_LADDER_GREEN",  # stale display field
        "exit_family": "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED",
        "gate_aware_vector_pass": True,
        "gate_aware_vector_source_receipt": str(receipt.relative_to(tmp_path)),
        "gate_aware_vector_source_receipt_sha256": receipt_sha,
        "gate_aware_vector_artifact": str(artifact.relative_to(tmp_path)),
        "gate_aware_vector_artifact_sha256": artifact_sha,
        "gate_aware_vector_complete_recipe": recipe,
        "gate_aware_vector_complete_recipe_sha256": hashlib.sha256(
            canonical(recipe).encode()
        ).hexdigest(),
    }
    _write_json(
        tmp_path / "data/reports/MATRIX_SYMBOL_SIDE_ACHIEVEMENT_CURRENT.json",
        {"rows": [gate_row]},
    )
    _write_json(
        tmp_path / "data/reports/PATH_COMBINATION_HOTLIST_CURRENT.json",
        {"rows": [{"key": "ABC_LONG", "entry_family": "ENTRY_LADDER_GREEN"}]},
    )

    hot, surface = select_hot_for_key(tmp_path, "ABC_LONG")
    receipt_path, champion = find_champion(tmp_path, hot)

    assert surface == "MATRIX_SYMBOL_SIDE_ACHIEVEMENT_CURRENT_GATE_AWARE_BOUND"
    assert hot["entry_family"] == "ENTRY_STOCH_HHHL"
    assert receipt_path == receipt
    assert champion["complete_recipe"] == recipe


def test_gate_aware_row_with_mutated_recipe_hash_cannot_select(tmp_path):
    _write_json(
        tmp_path / "data/reports/MATRIX_SYMBOL_SIDE_ACHIEVEMENT_CURRENT.json",
        {"rows": [{
            "key": "ABC_LONG",
            "gate_aware_vector_pass": True,
            "gate_aware_vector_source_receipt": "receipt.json",
            "gate_aware_vector_source_receipt_sha256": "a" * 64,
            "gate_aware_vector_artifact_sha256": "b" * 64,
            "gate_aware_vector_complete_recipe": {
                "schema": "complete-vector-lifecycle-recipe-v1",
                "ENTRY": {"family": "ENTRY_STOCH_HHHL", "artifact": "entry", "artifact_result_sha256": "b" * 64},
                "EXIT": {"family": "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED"},
            },
            "gate_aware_vector_complete_recipe_sha256": "c" * 64,
        }]},
    )

    try:
        select_hot_for_key(tmp_path, "ABC_LONG")
    except RuntimeError as exc:
        assert "hash-bound complete recipe" in str(exc)
    else:
        raise AssertionError("mutated gate-aware recipe was selectable")


def test_watchdog_blocks_legacy_exact_without_all_lifecycle_folds():
    source = (
        __import__("pathlib").Path(__file__).parent
        / "tools/s1_hotlist_v8_full_recipe_watchdog.sh"
    ).read_text()
    assert "There are no legacy revalidation bypasses" in source


def test_watchdog_does_not_veto_bound_recipe_with_unrelated_display_gate():
    source = (
        __import__("pathlib").Path(__file__).parent
        / "tools/s1_hotlist_v8_full_recipe_watchdog.sh"
    ).read_text()
    eligible = source.split("eligible = [", 1)[1].split("gate_aware_pending = [", 1)[0]
    # The achievement row also carries an older display champion.  Its
    # lifecycle flag must not veto a separately hash-bound gate-aware recipe;
    # gate_aware_vector_pass already means the selected candidate passed every
    # aggregate and per-fold strict gate.
    assert 'row.get("lifecycle_all_folds_strict_gate")' not in eligible


def test_watchdog_canary_terminal_identity_uses_receipt_hashes():
    source = (
        __import__("pathlib").Path(__file__).parent
        / "tools/s1_hotlist_v8_full_recipe_watchdog.sh"
    ).read_text()
    branch = source.split(
        'elif row.get("exact_v8_status") == "BLOCKED_SHARED_DIRECT_ROUTE_CANARY":',
        1,
    )[1].split("def replay_dispatchable", 1)[0]
    assert 'canary.get("tradier_manage_sha256")' in branch
    assert 'canary.get("engine_sha256")' in branch
    assert "current_code" not in branch.split("canary =", 1)[1]


def test_exact_runner_rejects_legacy_and_requires_formation_preflight():
    assert strict_vector_exact_admission_valid({
        "key": "ABC_LONG",
        "beats_bh_and_over_10_real_trades": True,
        "lifecycle_all_folds_strict_gate": True,
    }) is False

    recipe = {
        "schema": "complete-vector-lifecycle-recipe-v1",
        "KEY": "ABC_LONG",
        "ENTRY": {
            "family": "ENTRY_CLASSIC_FORMATION_TREND_STRUCTURE",
            "artifact": "data/reports/vec_research/form/entry",
            "artifact_result_sha256": "b" * 64,
            "params": {"timeframe": "ALL", "min_score": 0.65},
        },
        "EXIT": {
            "family": "EXIT_CLASSIC_FORMATION_TREND_STRUCTURE",
            "params": {"timeframe": "ALL", "min_score": 0.65},
        },
    }
    row = {
        "key": "ABC_LONG",
        "classic_formation_vector_pass": True,
        "exact_v8_candidate_authorized": True,
        "classic_formation_vector_source_receipt": "data/reports/receipt.json",
        "classic_formation_vector_source_receipt_sha256": "a" * 64,
        "classic_formation_vector_artifact_sha256": "b" * 64,
        "classic_formation_vector_complete_recipe": recipe,
        "classic_formation_vector_complete_recipe_sha256": hashlib.sha256(
            canonical(recipe).encode()
        ).hexdigest(),
    }
    assert strict_vector_exact_admission_valid(row) is False
    assert strict_vector_exact_admission_valid({
        **row, "classic_formation_exact_route_preflight_pass": True
    }) is True
