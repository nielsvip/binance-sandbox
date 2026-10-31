import json

from tools.paper_inventory import digest, ingest_plan, variant
from tools.paper_scenario_db import ScenarioDB


def plan_fixture():
    catalog_rows = [
        {"scenario_id": "base", "venue": "crypto", "side": "LONG", "kind": "baseline", "switch": None, "default_kind": "unresolved", "coverage_kind": "baseline", "provenance": []},
        {"scenario_id": "bool", "venue": "crypto", "side": "LONG", "kind": "one_switch", "switch": "BOOL_SWITCH", "default_kind": "boolean", "coverage_kind": "verified_mask", "provenance": [{"source": "config.py", "line": 1}]},
        {"scenario_id": "number", "venue": "crypto", "side": "LONG", "kind": "one_switch", "switch": "NUMBER_SWITCH", "default_kind": "non_boolean", "coverage_kind": "verified_mask", "provenance": [{"source": "config.py", "line": 2}]},
    ]
    return {
        "planned_at": "2026-10-31T00:00:00Z",
        "catalog": {"file_sha256": {"config.py": "a" * 64}, "scenarios": catalog_rows},
        "universe_source": "universe.json",
        "universe_sha256": "b" * 64,
        "groups": [{"sym_side": "BTCUSDC_LONG", "host": "s1", "settings_sha256": "c" * 64, "scenario_ids": ["base", "bool", "number"], "epoch_id": "epoch-1"}],
        "uncovered": [],
    }


def test_ingest_plan_repeat_is_stable_and_nonbool_stays_pending(tmp_path):
    db = ScenarioDB(tmp_path / "inventory.sqlite")
    plan = plan_fixture()
    first = ingest_plan(db, plan)
    counts1 = {table: db.query(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"] for table in ("paths", "scenarios", "runs", "imports")}
    second = ingest_plan(db, plan)
    counts2 = {table: db.query(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"] for table in ("paths", "scenarios", "runs", "imports")}
    assert first == second
    assert counts1 == counts2
    runs = db.query("SELECT r.status,s.parameters_json FROM runs r JOIN scenarios s ON s.scenario_id=r.scenario_id")
    number_rows = [row for row in runs if json.loads(row["parameters_json"])["catalog"]["switch"] == "NUMBER_SWITCH"]
    assert len(number_rows) == 1
    number_params = json.loads(number_rows[0]["parameters_json"])
    assert number_params["parameter_status"] == "NEEDS_AUTHORITATIVE_VALUES"
    assert number_params["overrides"] == {}
    assert number_rows[0]["status"] == "PENDING_PARAMETERS"
    boolean_rows = [row for row in runs if json.loads(row["parameters_json"])["catalog"]["switch"] == "BOOL_SWITCH"]
    assert {json.loads(row["parameters_json"])["overrides"]["BOOL_SWITCH"] for row in boolean_rows} == {False, True}
    db.close()


def test_ingest_plan_honors_authoritative_nonbool_values(tmp_path):
    db = ScenarioDB(tmp_path / "inventory.sqlite")
    plan = plan_fixture()
    number = next(row for row in plan["catalog"]["scenarios"] if row["scenario_id"] == "number")
    number.update({"candidate_values": [2.5, 5], "candidate_value_types": ["float", "int"], "candidate_value_evidence": [{"value": 2.5, "effective_type": "float", "raw_cell_types": ["float"]}, {"value": 5, "effective_type": "int", "raw_cell_types": ["int"]}], "candidate_source": "TEMPLATE_FINAL_NORM", "candidate_source_xlsx_sha256": "d" * 64})
    ingest_plan(db, plan)
    runs = db.query("SELECT r.status,s.parameters_json FROM runs r JOIN scenarios s ON s.scenario_id=r.scenario_id")
    number_rows = [row for row in runs if json.loads(row["parameters_json"])["catalog"]["scenario_id"] == "number"]
    assert len(number_rows) == 2
    assert {json.loads(row["parameters_json"])["overrides"]["NUMBER_SWITCH"] for row in number_rows} == {2.5, 5}
    assert all(row["status"] == "PENDING_VALIDATED_EXECUTION" for row in number_rows)
    assert all(json.loads(row["parameters_json"])["candidate_resolution"]["source_xlsx_sha256"] == "d" * 64 for row in number_rows)
    assert {json.loads(row["parameters_json"])["candidate_resolution"]["value_evidence"]["effective_type"] for row in number_rows} == {"float", "int"}
    db.close()


def test_plan_registers_shared_settings_snapshots_once_without_per_group_payloads(tmp_path):
    db = ScenarioDB(tmp_path / "inventory.sqlite")
    plan = plan_fixture()
    shared = {"DELTA_TF_WEIGHTS": {"3m": 0.25}, "START_POSITION_SIZE": 15}
    settings_hash = digest(shared)
    plan["settings_snapshots"] = {settings_hash: shared}
    plan["groups"][0]["settings_sha256"] = settings_hash
    assert "effective_settings" not in plan["groups"][0]
    first = ingest_plan(db, plan)
    ingest_plan(db, plan)
    rows = db.query("SELECT settings_hash,settings_json FROM settings_snapshots")
    assert len(rows) == 1
    assert rows[0]["settings_hash"] == settings_hash
    assert json.loads(rows[0]["settings_json"]) == shared
    assert first["groups"] == 1
    db.close()


def test_plan_rejects_shared_settings_snapshot_hash_mismatch(tmp_path):
    db = ScenarioDB(tmp_path / "inventory.sqlite")
    plan = plan_fixture()
    plan["settings_snapshots"] = {"f" * 64: {"A": 1}}
    try:
        ingest_plan(db, plan)
    except ValueError as exc:
        assert "settings snapshot hash mismatch" in str(exc)
    else:
        raise AssertionError("expected invalid settings snapshot hash rejection")
    assert db.query("SELECT COUNT(*) FROM settings_snapshots")[0][0] == 0
    db.close()


def test_variants_keep_off_on_values_and_parent_history(tmp_path):
    db = ScenarioDB(tmp_path / "inventory.sqlite")
    ingest_plan(db, plan_fixture())
    boolean_parent = db.query("SELECT scenario_id FROM scenarios WHERE parameters_json LIKE '%BOOL_SWITCH%' ORDER BY scenario_id LIMIT 1")[0][0]
    original_parent = boolean_parent
    off = variant(db, boolean_parent, {"BOOL_SWITCH": False}, "OFF trial")
    on = variant(db, original_parent, {"BOOL_SWITCH": True}, "ON trial")
    assert off["status"] == on["status"] == "DEFINED_NOT_RUNNING"
    assert off["promotion_enabled"] is on["promotion_enabled"] is False
    off_row = db.query("SELECT * FROM scenarios WHERE scenario_id=?", (off["scenario_id"],))[0]
    on_row = db.query("SELECT * FROM scenarios WHERE scenario_id=?", (on["scenario_id"],))[0]
    assert off_row["parent_scenario_id"] == original_parent
    assert on_row["parent_scenario_id"] == original_parent
    assert json.loads(off_row["parameters_json"])["overrides"]["BOOL_SWITCH"] is False
    assert json.loads(on_row["parameters_json"])["overrides"]["BOOL_SWITCH"] is True
    child = variant(db, off["scenario_id"], {"SECOND_SWITCH": "1h"}, "child of OFF")
    child_row = db.query("SELECT parent_scenario_id FROM scenarios WHERE scenario_id=?", (child["scenario_id"],))[0]
    assert child_row[0] == off["scenario_id"]
    assert db.query("SELECT COUNT(*) FROM runs WHERE scenario_id IN (?,?)", (off["scenario_id"], on["scenario_id"]))[0][0] == 0
    db.close()


def test_result_inventory_keeps_catalog_rows_without_runs_or_results(tmp_path):
    db = ScenarioDB(tmp_path / "inventory.sqlite")
    db.add_path(path_id="p", venue="stocks", path_name="catalog_only", switch_name="", classification="baseline", provenance={})
    db.add_scenario(scenario_id="s", path_id="p", side="LONG", parameters={}, created_at="t")
    rows = db.query("SELECT * FROM scenario_result_inventory WHERE scenario_id='s'")
    assert len(rows) == 1
    assert rows[0]["scenario_id"] == "s"
    assert rows[0]["run_id"] is None
    assert rows[0]["result_id"] is None
    db.close()
