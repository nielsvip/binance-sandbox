import sqlite3
import math

import pytest

from tools.paper_scenario_db import ScenarioDB


def base(db):
    db.add_path(path_id="p1", venue="binance", path_name="entry", switch_name="ENTRY_X", classification="paper", provenance={"file": "x.py"})
    db.add_scenario(scenario_id="s1", path_id="p1", side="LONG", parameters={"x": 1}, created_at="2026-01-01T00:00:00Z")
    db.add_run(run_id="r1", scenario_id="s1", sym_side="BTCUSDC_LONG", host="s1", settings_hash="set", code_hash="code", evidence_kind="observational", status="done", started_at="2026-01-01T00:00:00Z", ended_at="2026-01-01T01:00:00Z")


def test_foreign_keys_and_immutable_idempotence(tmp_path):
    db = ScenarioDB(tmp_path / "scenarios.sqlite")
    with pytest.raises(sqlite3.IntegrityError):
        db.add_scenario(scenario_id="orphan", path_id="missing", side="LONG", parameters={}, created_at="t")
    base(db)
    row = dict(result_id="res1", run_id="r1", computed_at="2026-01-01T02:00:00Z", source_start="2025-12-01", source_end="2026-01-01", metric_name="net_gain", metric_value=None, unit="fraction", verification="unverified", payload={"source": "raw"})
    assert db.add_result(**row) == "res1"
    assert db.add_result(**row) == "res1"
    row["metric_value"] = 0.0
    with pytest.raises(ValueError, match="conflict"):
        db.add_result(**row)
    db.close()


def test_evidence_views_and_epoch_boundaries_are_separate(tmp_path):
    db = ScenarioDB(tmp_path / "scenarios.sqlite")
    base(db)
    db.add_run(run_id="r2", scenario_id="s1", sym_side="BTCUSDC_LONG", host="s2", settings_hash="set2", code_hash="code2", evidence_kind="independent_forward", status="done", started_at="2026-02-01T00:00:00Z", ended_at=None)
    common = dict(computed_at="2026-02-02T00:00:00Z", source_start="2026-02-01", source_end="2026-02-02", metric_name="trades", metric_value=None, unit="count", verification="raw", payload={})
    db.add_result(result_id="obs", run_id="r1", **common)
    db.add_result(result_id="fwd", run_id="r2", **common)
    assert [r["result_id"] for r in db.query("SELECT * FROM observational_results")] == ["obs"]
    assert [r["result_id"] for r in db.query("SELECT * FROM independent_forward_results")] == ["fwd"]
    coverage = db.query("SELECT * FROM inventory_coverage")[0]
    assert (coverage["scenario_count"], coverage["run_count"], coverage["independent_forward_run_count"]) == (1, 2, 1)
    db.close()


def test_status_history_and_immutable_note_ids(tmp_path):
    db = ScenarioDB(tmp_path / "scenarios.sqlite")
    base(db)
    assert db.query("SELECT status FROM run_current_status WHERE run_id='r1'")[0]["status"] == "done"
    first = dict(event_id="e1", run_id="r1", recorded_at="2026-01-01T01:10:00Z", status="OBSERVATIONAL_ONLY", detail={"why": "paper"})
    db.add_run_status(**first)
    db.add_run_status(**first)
    db.add_run_status(event_id="e2", run_id="r1", recorded_at="2026-01-01T01:20:00Z", status="COMPLETE", detail={})
    assert db.query("SELECT status FROM run_current_status WHERE run_id='r1'")[0]["status"] == "COMPLETE"
    note = dict(id=10, scenario_id="s1", created_at="2026-01-01T03:00:00Z", note="retain this tuning note")
    assert db.add_note(**note) == 10
    assert db.add_note(**note) == 10
    with pytest.raises(ValueError, match="conflict"):
        db.add_note(**{**note, "note": "edited"})
    db.close()


def test_rejects_nonfinite_values_and_bad_parameter_hash(tmp_path):
    db = ScenarioDB(tmp_path / "scenarios.sqlite")
    db.add_path(path_id="p1", venue="binance", path_name="entry", switch_name="ENTRY_X", classification="paper", provenance={})
    with pytest.raises(ValueError, match="parameters_hash"):
        db.add_scenario(scenario_id="s1", path_id="p1", side="LONG", parameters={"x": 1}, parameters_hash="wrong", created_at="t")
    db.add_scenario(scenario_id="s1", path_id="p1", side="LONG", parameters={"x": 1}, created_at="t")
    db.add_run(run_id="r1", scenario_id="s1", sym_side="BTCUSDC_LONG", host="s1", settings_hash="set", code_hash="code", evidence_kind="observational", status="done", started_at="t", ended_at=None)
    with pytest.raises(ValueError, match="metric_value"):
        db.add_result(result_id="bad", run_id="r1", computed_at="t", source_start=None, source_end=None, metric_name="metric", metric_value=math.inf, unit=None, verification="raw", payload={})
    with pytest.raises(ValueError, match="unknown columns"):
        db.add_run_status(event_id="e", run_id="r1", recorded_at="t", status="RUNNING", detail={}, sql="DROP TABLE runs")
    db.close()
