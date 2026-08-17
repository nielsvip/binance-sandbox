import json

from tools import prepare_r6_tim50_campaign as migration


def test_prepare_creates_clean_hash_bound_campaign(tmp_path, monkeypatch):
    source = tmp_path / "data/reports/vec_research/r5"
    destination = tmp_path / "data/reports/vec_research/r6"
    source.mkdir(parents=True)
    (source / "plan.json").write_text(json.dumps({
        "schema": "coupled-r4-sealed-recipe-candidate-plan-v1",
        "plans": [], "engine_hashes": {"old": "hash"},
    }))
    monkeypatch.setattr(migration, "current_engine_hashes", lambda _root: {
        "v8_vec_sweep_sha256": "v8", "executor_sha256": "executor",
        "run_entry_exit_beam_campaign_sha256": "beam",
    })
    monkeypatch.setattr(migration.priority, "build", lambda _root, plan_path: {
        "schema": "coupled-path-vector-priority-overlay-v1",
        "source_plan_sha256": migration.sha256(plan_path), "entries": [],
    })
    receipt = migration.prepare(tmp_path, source, destination)
    plan = json.loads((destination / "plan.json").read_text())
    assert receipt["status"] == "PASS"
    assert plan["qualification_tim_band_pct"] == [50.0, 80.0]
    assert plan["engine_hashes"]["run_entry_exit_beam_campaign_sha256"] == "beam"
    assert list((destination / "results").iterdir()) == [destination / "results/runtime"]
    assert receipt["historical_results_copied"] is False


def test_prepare_is_idempotent_only_for_current_destination(tmp_path, monkeypatch):
    source = tmp_path / "data/reports/vec_research/r5"
    destination = tmp_path / "data/reports/vec_research/r6"
    source.mkdir(parents=True)
    (source / "plan.json").write_text(json.dumps({"plans": []}))
    hashes = {"v8_vec_sweep_sha256": "v8"}
    monkeypatch.setattr(migration, "current_engine_hashes", lambda _root: hashes)
    monkeypatch.setattr(migration.priority, "build", lambda _root, _plan: {"entries": []})
    migration.prepare(tmp_path, source, destination)
    assert migration.prepare(tmp_path, source, destination)["status"] == "ALREADY_CURRENT"


def test_prepare_accepts_empty_watchdog_runtime_skeleton(tmp_path, monkeypatch):
    source = tmp_path / "data/reports/vec_research/r5"
    destination = tmp_path / "data/reports/vec_research/r6"
    source.mkdir(parents=True)
    (source / "plan.json").write_text(json.dumps({"plans": []}))
    (destination / "results/runtime").mkdir(parents=True)
    monkeypatch.setattr(migration, "current_engine_hashes", lambda _root: {
        "v8_vec_sweep_sha256": "v8"
    })
    monkeypatch.setattr(migration.priority, "build", lambda _root, _plan: {"entries": []})
    assert migration.prepare(tmp_path, source, destination)["status"] == "PASS"


def test_cross_product_is_unique_entry_artifact_by_supported_exit_family():
    def candidate(candidate_id, artifact, family):
        recipe = {"ENTRY": {"family": "ENTRY_TEST", "params": {"x": artifact}},
                  "EXIT": {"family": family, "params": {"seed": family}}}
        return {"candidate_id": candidate_id, "baseline": {
            "entry_artifact": artifact,
            "entry_artifact_result_sha256": f"sha-{artifact}",
            "complete_recipe": recipe,
            "complete_recipe_sha256": "old",
        }}
    plan = {"plans": [{"key": "ABC_LONG", "recipe_candidates": [
        candidate("one", "a", "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED"),
        candidate("duplicate", "a", "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED"),
        candidate("two", "b", "EXIT_MTF_ATR_TRAIL"),
    ]}]}
    expanded = migration.expand_entry_exit_family_cross_product(plan)
    rows = expanded["plans"][0]["recipe_candidates"]
    assert len(rows) == 2 * len(migration.SUPPORTED_EXIT_FAMILIES)
    assert len({row["candidate_id"] for row in rows}) == len(rows)
    assert expanded["r6_cross_product"]["unique_sealed_entry_count"] == 2
    assert {row["baseline"]["complete_recipe"]["EXIT"]["family"] for row in rows} == set(migration.SUPPORTED_EXIT_FAMILIES)
