import hashlib
import json
from pathlib import Path

from tools.paper_forward_path_coverage import (
    KNOWN_FINDINGS,
    SOURCE_RUNTIME_ID,
    refresh_path_coverage,
)
from tools.paper_scenario_db import ScenarioDB


def _insert_path_run(db, venue, switch, run_id="run-1"):
    path_id = f"path-{venue}-{switch}"
    scenario_id = f"scenario-{venue}-{switch}"
    db.connection.execute("INSERT INTO paths VALUES(?,?,?,?,?,?)", (path_id, venue, switch, switch, "one_switch", "[]"))
    parameters = {"overrides": {switch: True}, "parameter_status": "RESOLVED"}
    encoded = json.dumps(parameters, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    db.connection.execute("INSERT INTO scenarios VALUES(?,?,?,?,?,?,?)", (scenario_id, path_id, "LONG", encoded, digest, None, "2026-10-31T00:00:00Z"))
    db.connection.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?,?)", (run_id, scenario_id, "BTCUSDC_LONG", "s1", "settings", "code", "catalog_only", "PENDING_VALIDATED_EXECUTION", "2026-10-31T00:00:00Z", None))
    return run_id


def test_path_audit_defaults_unknown_and_never_claims_coverage(tmp_path):
    root = Path(__file__).resolve().parents[1]
    with ScenarioDB(tmp_path / "coverage.sqlite") as db:
        run_id = _insert_path_run(db, "crypto", "MOMENTUM_WATCHDOG_ENABLED")
        db.connection.execute("INSERT INTO results VALUES(?,?,?,?,?,?,?,?,?,?)", ("result-1", run_id, "2026-10-31T00:00:00Z", None, None, "net_return", 0.0, "fraction", "diagnostic", "{}"))
        _insert_path_run(db, "stocks", "MOMENTUM_WATCHDOG_ENABLED", "run-2")
        first = refresh_path_coverage(db, root=root)
        row = db.query("SELECT status,source_runtime_id,coverage_proven,scenario_count,planned_run_count,associated_result_count,result_interpretation FROM paper_forward_path_coverage WHERE venue='crypto' AND switch_name='MOMENTUM_WATCHDOG_ENABLED'")[0]
        assert row["status"] == "NO_OP"
        assert row["source_runtime_id"] == SOURCE_RUNTIME_ID
        assert row["coverage_proven"] == 0
        assert row["scenario_count"] == 1
        assert row["planned_run_count"] == 1
        assert row["associated_result_count"] == 1
        assert row["result_interpretation"] == "PATH_COVERAGE_UNVERIFIED; NOT_A_COVERAGE_CLAIM"
        unknown = db.query("SELECT status,coverage_proven,scenario_count FROM paper_forward_path_coverage WHERE venue='stocks' AND switch_name='MOMENTUM_WATCHDOG_ENABLED'")[0]
        assert tuple(unknown) == ("UNVERIFIED", 0, 1)
        assert first["coverage_proven_rows"] == 0
        before = db.query("SELECT COUNT(*) FROM runs")[0][0]
        second = refresh_path_coverage(db, root=root)
        assert second["new_default_rows"] == 0
        assert second["new_or_changed_findings"] == 0
        assert db.query("SELECT COUNT(*) FROM runs")[0][0] == before
        assert db.query("SELECT COUNT(*) FROM results")[0][0] == 1


def test_known_findings_are_bound_to_immutable_runtime_file_hashes(tmp_path):
    root = Path(__file__).resolve().parents[1]
    runtime_root = root / "data" / "paper_forward_lab" / "runtime" / SOURCE_RUNTIME_ID
    with ScenarioDB(tmp_path / "known.sqlite") as db:
        refresh_path_coverage(db, root=root)
        rows = db.query("SELECT venue,switch_name,status,source_hashes_json,source_runtime_id,coverage_proven FROM paper_forward_path_coverage_audit")
        found = {(row["venue"], row["switch_name"]): row for row in rows}
        for finding in KNOWN_FINDINGS:
            row = found[(finding["venue"], finding["switch_name"])]
            hashes = json.loads(row["source_hashes_json"])
            expected_files = {ref.split(":", 1)[0] for ref in finding["source_refs"]}
            assert set(hashes) == expected_files
            assert all((runtime_root / name).is_file() for name in expected_files)
            assert row["source_runtime_id"] == SOURCE_RUNTIME_ID
            assert row["status"] == finding["status"]
            assert row["coverage_proven"] == 0


def test_missing_immutable_source_downgrades_finding_to_unverified(tmp_path):
    finding = {
        "venue": "crypto", "switch_name": "EXAMPLE", "status": "NO_OP",
        "finding_code": "TEST_NOOP", "summary": "Test-only source finding.",
        "path_scope": "test", "dependencies": [], "blockers": [],
        "source_refs": ["absent.py:1"],
    }
    with ScenarioDB(tmp_path / "missing.sqlite") as db:
        refresh_path_coverage(db, root=tmp_path, findings=(finding,))
        row = db.query("SELECT status,finding_code,coverage_proven,source_hashes_json FROM paper_forward_path_coverage")[0]
        assert row["status"] == "UNVERIFIED"
        assert row["finding_code"] == "SOURCE_EVIDENCE_UNAVAILABLE"
        assert row["coverage_proven"] == 0
        assert json.loads(row["source_hashes_json"]) == {}
