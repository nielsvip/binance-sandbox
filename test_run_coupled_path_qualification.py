import hashlib
import json

from tools import run_coupled_path_qualification as coupled


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True))


def test_plan_binds_31_rows_and_preflights_invalid_companion_exit_moves(tmp_path):
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools/vec_entry_overlay_walkforward.py").write_text(
        "def _counter_trend_entry_allowed(): pass\n"
    )
    plans = []
    for index in range(31):
        key = f"S{index}_LONG"
        artifact = tmp_path / f"data/a{index}"
        result = artifact / "result.json"
        write_json(result, {"entry": index})
        recipe = {
            "schema": "complete-vector-lifecycle-recipe-v1",
            "ENTRY": {
                "family": "ENTRY_4H_DEEP_VALUE",
                "entry_role": "direct",
                "params": {"stoch_k_threshold": 50.0},
                "artifact": str(artifact.relative_to(tmp_path)),
                "artifact_result_sha256": hashlib.sha256(result.read_bytes()).hexdigest(),
            },
            "EXIT": {
                "family": "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED",
                "params": {
                    "arm_timeframe": "1h", "trail_timeframe": "5m",
                    "mode": "DC", "break_buffer_atr": 0.5,
                    "distance_mult": 1.0, "lookback": 80,
                },
            },
        }
        receipt = tmp_path / f"data/r{index}.json"
        write_json(receipt, {"hotlist": [{"champion": {"key": key, "complete_recipe": recipe}}]})
        plans.append({
            "key": key, "symbol": f"S{index}", "side": "LONG",
            "gate_aware_vector_blocker": coupled.BLOCKER,
            "beats_bh_and_over_10_real_trades": True,
            "entry_family": recipe["ENTRY"]["family"], "entry_params": recipe["ENTRY"]["params"],
            "exit_family": recipe["EXIT"]["family"], "exit_params": recipe["EXIT"]["params"],
            "source_receipt": str(receipt.relative_to(tmp_path)),
            "source_receipt_sha256": hashlib.sha256(receipt.read_bytes()).hexdigest(),
            "strategy_return_pct": 4.0, "bh_return_pct": 1.0,
            "real_close_trades": 11, "time_in_market_pct": 75.0,
        })
    readiness = tmp_path / "data/readiness.json"
    write_json(readiness, {"qualifying_rows": plans})

    payload = coupled.build(tmp_path, readiness, 31)

    assert payload["plan_count"] == 31
    assert payload["contract"]["exact_v8_allowed"] is False
    # The declared +/- exit moves are not exact published A_EXT grid points,
    # so only sealed baseline/timing plus the adapter-emitted supported-grid
    # refinement pack are executable.  The latter invents no numeric params.
    assert all(len(plan["coupled_packs"]) == 3 for plan in payload["plans"])
    assert all(len({pack["pack_id"] for pack in plan["coupled_packs"]}) == 3 for plan in payload["plans"])
    assert all(plan["adapter_preflight"]["blocked_proposals"] for plan in payload["plans"])
    assert payload["plans"][0]["baseline"]["entry_artifact_result_sha256"]


def test_plan_refuses_wrong_preliminary_count(tmp_path):
    write_json(tmp_path / "data/readiness.json", {"qualifying_rows": []})
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools/vec_entry_overlay_walkforward.py").write_text("_counter_trend_entry_allowed")
    try:
        coupled.build(tmp_path, tmp_path / "data/readiness.json", 31)
    except ValueError as exc:
        assert "expected 31" in str(exc)
    else:
        raise AssertionError("expected preliminary-count refusal")


def test_legacy_receipt_artifact_baseline_is_bound_without_inventing_params(tmp_path):
    artifact = tmp_path / "data/legacy-entry"
    result = artifact / "result.json"
    write_json(result, {"legacy": True})
    receipt = tmp_path / "data/legacy-receipt.json"
    row = {
        "key": "ACN_SHORT", "entry_family": "ENTRY_BB_RECOVERY", "entry_params": {},
        "exit_family": "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED", "exit_params": {"distance_mult": 2.0},
    }
    write_json(receipt, {"hotlist": [{"champion": {
        "key": "ACN_SHORT", "entry_family": "ENTRY_BB_RECOVERY",
        "entry_artifact": str(artifact.relative_to(tmp_path)),
        "exit_family": "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED", "exit_params": {"distance_mult": 2.0},
    }}]})
    row["source_receipt"] = str(receipt.relative_to(tmp_path))
    row["source_receipt_sha256"] = hashlib.sha256(receipt.read_bytes()).hexdigest()
    baseline = coupled.resolve_baseline(tmp_path, row)
    assert baseline["recipe_provenance"] == "LEGACY_RECEIPT_ENTRY_ARTIFACT_BOUND"
    assert baseline["complete_recipe"]["ENTRY"]["params"] == {}


def test_adapter_preflight_blocks_unsupported_exit_and_never_emits_a_pack():
    recipe = {
        "ENTRY": {"family": "ENTRY_BOUNCE_5M_LOW", "entry_role": "direct", "params": {}},
        "EXIT": {"family": "EXIT_CLASSIC_FORMATION_WEDGE", "params": {}},
    }
    packs, blocked = coupled.adapter_preflight_packs(recipe, {"outer_folds": []})
    assert packs == []
    assert blocked[0]["code"] == "BLOCKED_EXIT_FAMILY_NOT_IN_CAUSAL_ADAPTER"
