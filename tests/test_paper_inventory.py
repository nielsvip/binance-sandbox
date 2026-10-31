import json

from tools.paper_inventory import ingest_plan, variant
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
