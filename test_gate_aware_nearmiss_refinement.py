import json
from pathlib import Path

from tools import append_gate_aware_nearmiss_refinement as lane


def write(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True))


def candidate(root: Path, key: str, *, tim=60.0, clamps=1):
    symbol, side = key.rsplit("_", 1)
    artifact = (
        root / "data/reports/vec_research/capacity_frontier_fixture/"
        f"keys/{key}/entries/ENTRY_STOCH_HHHL/run"
    )
    write(artifact / "result.json", {
        "manifest": {
            "symbol": symbol,
            "side": side,
            "family": "ENTRY_STOCH_HHHL",
            "real_entry_gate_mask": {
                "schema": lane.GATE_SCHEMA,
                "family": "ENTRY_STOCH_HHHL",
                "applied_to_entry_signal": True,
            },
        },
    })
    return {
        "key": key,
        "entry_family": "ENTRY_STOCH_HHHL",
        "exit_family": "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED",
        "exit_params": {},
        "entry_artifact": str(artifact.relative_to(root)),
        "strategy_return_pct": 40.0,
        "bh_return_pct": 10.0,
        "ledger_backed_vector_close_fills": 17,
        "tim_pct": tim,
        "causal_capacity_gate": True,
        "clamp_count": clamps,
        "complete_recipe": {
            "schema": "complete-vector-lifecycle-recipe-v1",
            "ENTRY": {
                "family": "ENTRY_STOCH_HHHL",
                "artifact": str(artifact.relative_to(root)),
                "params": {"enabled_tfs": ["1h"]},
            },
            "EXIT": {"family": "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED", "params": {}},
        },
        "discovery_fold_evidence": [
            {"alpha_vs_bh_pp": 3.0}, {"alpha_vs_bh_pp": -1.0},
        ],
    }


def add_key_receipt(root: Path, key: str, row: dict):
    path = (
        root / "data/reports/vec_research/"
        "capacity_frontier_20240326_20260801_v3_gate_aware_short_fixture/"
        f"keys/{key}/key_result.json"
    )
    write(path, {
        "key": key,
        "npz": f"data/matrix_npz/frozen/{key.rsplit('_', 1)[0]}.npz",
        "npz_sha256": "a" * 64,
        "champion": row,
        "top_candidates": [],
    })


def queue(root: Path) -> Path:
    path = root / "data/reports/admission_queue.json"
    write(path, {"contract": lane.CONTRACT, "queue": []})
    return path


def test_priority_v3_nearmisses_are_hash_bound_and_idempotently_queued(tmp_path):
    add_key_receipt(tmp_path, "CDE_SHORT", candidate(tmp_path, "CDE_SHORT", tim=45))
    add_key_receipt(tmp_path, "CLF_SHORT", candidate(tmp_path, "CLF_SHORT"))
    path = queue(tmp_path)

    result = lane.append(tmp_path, path)
    payload = json.loads(path.read_text())
    item = payload["queue"][0]

    assert result["status"] == "APPENDED"
    assert item["kind"] == "refinement"
    assert item["runner"] == "tools/run_path_productivity_hotlist.py"
    assert item["random_curves"] == 64
    assert item["entry_shortlist"] == 32
    assert item["entry_beam_width"] == 3
    assert item["exit_beam_width"] == 8
    assert item["hotlist_per_key"] == 8
    assert item["defer_until_classic_formation_complete"] is True
    assert "exact" not in canonical(item).lower()
    assert item["gate_aware_nearmiss_identities"][0].startswith("CLF_SHORT@")
    before = path.read_bytes()
    second = lane.append(tmp_path, path)
    assert second["status"] == "NO_COMPLETED_GATE_AWARE_NEARMISS"
    assert path.read_bytes() == before


def test_one_queue_item_never_repeats_a_symbol_side(tmp_path):
    first = candidate(tmp_path, "CLF_SHORT", tim=45)
    second = {**candidate(tmp_path, "CLF_SHORT", tim=85), "exit_params": {"atr_multiple": 3}}
    path = (
        tmp_path / "data/reports/vec_research/"
        "capacity_frontier_20240326_20260801_v3_gate_aware_short_fixture/"
        "keys/CLF_SHORT/key_result.json"
    )
    write(path, {
        "key": "CLF_SHORT", "npz": "data/matrix_npz/frozen/CLF.npz",
        "npz_sha256": "a" * 64, "champion": first, "top_candidates": [second],
    })
    queue_path = queue(tmp_path)
    assert lane.append(tmp_path, queue_path)["status"] == "APPENDED"
    payload = json.loads(queue_path.read_text())
    assert payload["queue"][0]["gate_aware_nearmiss_identities"] == [
        payload["queue"][0]["gate_aware_nearmiss_identities"][0]
    ]


def test_component_report_without_completed_key_receipt_does_not_queue(tmp_path):
    write(
        tmp_path / "data/reports/vec_research/"
        "capacity_frontier_20240326_20260801_v3_gate_aware_short_fixture/"
        "keys/CLF_SHORT/component_report.json",
        {"rows": [{"family": "ENTRY_STOCH_HHHL"}]},
    )

    result = lane.append(tmp_path, queue(tmp_path))

    assert result["status"] == "WAITING_FOR_COMPLETED_GATE_AWARE_RECEIPT"
    assert result["selected"] == 0


def test_post_source_sync_registers_adaptive_gate_aware_appender():
    source = Path("tools/post_source_sync_s1.sh").read_text()
    assert "append_gate_aware_nearmiss_refinement.py" in source


def test_legacy_duplicate_labels_and_writable_paths_are_repaired():
    queue_rows = [
        {
            "label": "gate_nearmiss_01_long",
            "out_dir": "data/reports/vec_research/capacity_gate_nearmiss_01_long_20260803",
            "pidfile": "data/reports/gate_nearmiss_01_long.pid",
            "stdout": "data/reports/gate_nearmiss_01_long.stdout",
            "stderr": "data/reports/gate_nearmiss_01_long.stderr",
        },
        {
            "label": "gate_nearmiss_01_long",
            "out_dir": "data/reports/vec_research/capacity_gate_nearmiss_01_long_20260803",
            "pidfile": "data/reports/gate_nearmiss_01_long.pid",
            "stdout": "data/reports/gate_nearmiss_01_long.stdout",
            "stderr": "data/reports/gate_nearmiss_01_long.stderr",
        },
    ]

    repaired = lane.repair_duplicate_labels(queue_rows)

    assert repaired == [
        {"from": "gate_nearmiss_01_long", "to": "gate_nearmiss_01_long_r02"}
    ]
    assert queue_rows[0]["label"] == "gate_nearmiss_01_long"
    assert queue_rows[1]["label"] == "gate_nearmiss_01_long_r02"
    assert queue_rows[0]["out_dir"] != queue_rows[1]["out_dir"]
    assert queue_rows[0]["pidfile"] != queue_rows[1]["pidfile"]


def test_later_job_with_same_logical_key_and_npz_is_removed(tmp_path):
    first = {
        "label": "gate_nearmiss_01_short",
        "gate_aware_nearmiss_identities": ["CLF_SHORT@receipt_a@recipe_a"],
        "source_npz_sha256": {"CLF_SHORT": "a" * 64},
    }
    duplicate_with_different_recipe = {
        "label": "gate_nearmiss_02_short",
        "gate_aware_nearmiss_identities": ["CLF_SHORT@receipt_b@recipe_b"],
        "source_npz_sha256": {"CLF_SHORT": "a" * 64},
    }
    unrelated = {
        "label": "frontier_existing",
        "gate_aware_nearmiss_identities": ["CDE_SHORT@receipt@recipe"],
        "source_npz_sha256": {"CDE_SHORT": "b" * 64},
    }
    rows = [first, duplicate_with_different_recipe, unrelated]

    removed = lane.remove_redundant_key_npz_jobs(rows)

    assert removed == ["gate_nearmiss_02_short"]
    assert [row["label"] for row in rows] == [
        "gate_nearmiss_01_short", "frontier_existing"
    ]


def test_adaptive_seed_zero_replay_of_existing_frontier_is_removed():
    adaptive = {
        "label": "gate_nearmiss_01_short",
        "runner": "tools/run_path_productivity_hotlist.py",
        "npz_dir": "data/matrix_npz/frozen",
        "gate_aware_nearmiss_identities": ["CLF_SHORT@receipt@recipe"],
        "source_npz_sha256": {"CLF_SHORT": "a" * 64},
    }
    frontier = {
        "label": "frontier_existing",
        "runner": "tools/run_path_productivity_hotlist.py",
        "npz_dir": "data/matrix_npz/frozen",
        "frontier_identities": ["CLF_SHORT@window_v3_gate_aware"],
    }
    rows = [adaptive, frontier]

    removed = lane.remove_deterministic_frontier_replays(rows)

    assert removed == ["gate_nearmiss_01_short"]
    assert rows == [frontier]


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))
