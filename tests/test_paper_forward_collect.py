import hashlib
import json
import shutil
import sqlite3
import subprocess
from pathlib import Path

from tools.paper_forward_collect import PaperForwardCollector, ingest_coordinator_database
from tools.paper_scenario_db import ScenarioDB


def _canon(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _plan_and_worker(root, host="s1"):
    base = {"BASE_SWITCH": True}
    parent_hash = hashlib.sha256(_canon(base).encode()).hexdigest()
    code_map = {"ez_manage.py": "a" * 64}
    plan_code_hash = hashlib.sha256(_canon(code_map).encode()).hexdigest()
    plan = {
        "planned_at": "2026-10-31T00:00:00Z",
        "catalog": {
            "file_sha256": code_map,
            "scenarios": [{"scenario_id": "base", "venue": "crypto", "side": "LONG", "kind": "baseline", "switch": None, "default_kind": "unresolved", "coverage_kind": "baseline", "provenance": []}],
        },
        "universe_source": "frozen-universe.json",
        "universe_sha256": "c" * 64,
        "groups": [{"sym_side": "BTCUSDC_LONG", "host": host, "settings_sha256": parent_hash, "scenario_ids": ["base"], "epoch_id": "epoch-1"}],
        "uncovered": [],
    }
    run_root = root / "data" / "paper_forward_lab" / "runs"
    run_dir = run_root / "paper-run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "base_settings.json").write_text(_canon(base))
    (run_dir / "effective_settings.json").write_text(_canon(base))
    engine_hash = "b" * 64
    settings_hash = parent_hash
    # `ingest_plan` derives the concrete catalog scenario id from the frozen catalog row.
    with ScenarioDB(root / "data" / "paper_forward_lab" / "seed.sqlite") as db:
        from tools.paper_inventory import ingest_plan

        ingest_plan(db, plan)
        scenario_id = db.query("SELECT scenario_id FROM scenarios LIMIT 1")[0][0]
    run_id = "paper-run-1"
    manifest = {
        "run_id": run_id,
        "epoch_id": "epoch-1",
        "scenario_id": scenario_id,
        "sym_side": "BTCUSDC_LONG",
        "host": host,
        "settings_sha256": settings_hash,
        "parent_settings_sha256": parent_hash,
        "code_sha256": engine_hash,
        "code_file_hashes": {"backtest_v12_engine.py": engine_hash},
        "plan_code_hash": plan_code_hash,
        "started_at": "2026-10-31T00:00:01Z",
        "ended_at": "2026-10-31T00:00:03Z",
        "status": "COMPLETED",
        "finished": True,
        "base_settings_file": "base_settings.json",
        "effective_settings_file": "effective_settings.json",
        "candidate_overrides": {},
        "isolation_overrides": {},
        "paper_mask_release": {},
        "context": {"evidence_label": "DIAGNOSTIC"},
        "frame_count": 1,
        "committed_receipts": 1,
    }
    (run_dir / "worker.result_manifest.json").write_text(_canon(manifest))
    identity = {"run_id": run_id, "epoch_id": "epoch-1", "scenario_id": scenario_id, "sym_side": "BTCUSDC_LONG", "settings_sha256": settings_hash, "code_sha256": engine_hash}
    journal = sqlite3.connect(run_dir / "receipts.snapshot.db")
    journal.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
    journal.execute("INSERT INTO metadata VALUES('identity',?)", (_canon(identity),))
    journal.execute("CREATE TABLE frames(sequence INTEGER PRIMARY KEY,closed_ts REAL UNIQUE NOT NULL,payload TEXT NOT NULL,input_hash TEXT NOT NULL,receipt_hash TEXT,receipt_json TEXT)")
    source = {"BTCUSDC": {"indicators": {"current_price": 100}}}
    source_json = _canon(source)
    receipt = {"events": [], "positions": {"ang:BTCUSDC_LONG": {"positionAmt": 0}}}
    receipt_json = _canon(receipt)
    journal.execute("INSERT INTO frames VALUES(1,?,?,?,?,?)", (1793419500, source_json, hashlib.sha256(source_json.encode()).hexdigest(), hashlib.sha256(receipt_json.encode()).hexdigest(), receipt_json))
    journal.commit()
    journal.close()
    return plan, run_dir


def _collector_db(path):
    db = ScenarioDB(path)
    return db


def _coordinator_snapshot(path, host, status):
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE jobs(spec_id TEXT PRIMARY KEY,spec_path TEXT NOT NULL,spec_sha256 TEXT,run_id TEXT,epoch_id TEXT,scenario_id TEXT,sym_side TEXT,venue TEXT,assigned_host TEXT,status TEXT NOT NULL,detail TEXT,pid INTEGER,registered_at TEXT NOT NULL,updated_at TEXT NOT NULL,heartbeat_at TEXT,exit_code INTEGER,run_dir TEXT,command_json TEXT)")
    connection.execute("CREATE TABLE events(event_id INTEGER PRIMARY KEY,spec_id TEXT NOT NULL,at_utc TEXT NOT NULL,status TEXT NOT NULL,detail TEXT,pid INTEGER)")
    connection.execute("CREATE TABLE coordinator_heartbeat(host TEXT PRIMARY KEY,at_utc TEXT NOT NULL,status TEXT NOT NULL,detail TEXT)")
    connection.execute("INSERT INTO jobs(spec_id,spec_path,run_id,epoch_id,scenario_id,sym_side,venue,assigned_host,status,registered_at,updated_at) VALUES('j1','specs/j1.json','paper-run-1','epoch-1','scenario-1','BTCUSDC_LONG','crypto',?,?,?,?)", (host, status, "2026-10-31T00:00:01Z", "2026-10-31T00:00:02Z"))
    connection.execute("INSERT INTO events(spec_id,at_utc,status,detail) VALUES('j1','2026-10-31T00:00:02Z',?,'{}')", (status,))
    connection.execute("INSERT INTO coordinator_heartbeat VALUES(?,?,?,?)", (host, "2026-10-31T00:00:03Z", "RUNNING", "{}"))
    connection.commit()
    connection.close()


def test_local_collect_registers_plan_then_imports_receipts_and_coordinator_separately(tmp_path):
    plan, run_dir = _plan_and_worker(tmp_path)
    plan["settings_snapshots"] = {plan["groups"][0]["settings_sha256"]: json.loads((run_dir / "base_settings.json").read_text())}
    coordinator_path = tmp_path / "data" / "paper_forward_lab" / "fleet_s1.snapshot.sqlite3"
    coordinator_path.parent.mkdir(parents=True, exist_ok=True)
    _coordinator_snapshot(coordinator_path, "s1", "DONE")
    with _collector_db(tmp_path / "data" / "paper_forward_lab" / "central.sqlite") as db:
        collector = PaperForwardCollector(tmp_path, db, hosts=("s1",))
        first = collector.collect_once(plan)
        second = collector.collect_once(plan)
        assert first["plan_registered"]["groups"] == 1
        assert first["plan_registered"]["plan_import_id"] == second["plan_registered"]["plan_import_id"]
        assert second["plan_registered"]["registered"] is False
        assert db.query("SELECT COUNT(*) FROM settings_snapshots")[0][0] == 1
        assert first["collected_artifacts"][0]["status"] == "COMPLETED"
        runs = db.query("SELECT evidence_kind FROM runs WHERE run_id='paper-run-1'")
        assert runs[0][0] == "independent_forward"
        assert db.query("SELECT COUNT(*) FROM forward_receipts WHERE run_id='paper-run-1'")[0][0] == 1
        assert db.query("SELECT COUNT(*) FROM decisions WHERE run_id='paper-run-1'")[0][0] == 0
        assert db.query("SELECT COUNT(*) FROM results WHERE run_id='paper-run-1'")[0][0] == 0
        assert db.query("SELECT COUNT(*) FROM paper_coordinator_jobs")[0][0] == 1
        assert db.query("SELECT COUNT(*) FROM paper_coordinator_events")[0][0] == 1
        assert db.query("SELECT coverage_json FROM paper_coordinator_coverage_reports")[0][0] == '{"s1":{"DONE":1}}'
        assert db.query("SELECT evidence_kind FROM paper_coordinator_snapshots")[0][0] == "coordinator_only"
        assert len(list((tmp_path / "data" / "paper_forward_lab" / "collector" / "collected").rglob("receipts.snapshot.db"))) == 1
        assert second["collected_artifacts"][0]["run_id"] == "paper-run-1"


def test_remote_bundle_pull_stages_files_and_rejects_manifest_identity_race(tmp_path):
    plan, source = _plan_and_worker(tmp_path / "remote", host="s2")
    remote_root = tmp_path / "fake_s2" / "runs"
    remote_run = remote_root / source.name
    remote_run.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, remote_run)
    remote_coordinator = tmp_path / "fake_s2" / "fleet_s2.snapshot.sqlite3"
    _coordinator_snapshot(remote_coordinator, "s2", "RUNNING")
    copy_manifest_count = {"n": 0}

    def fake_runner(args, capture_output, text, timeout, check):
        if args[0] == "ssh":
            completed = subprocess.CompletedProcess(args, 0, stdout=(str(remote_run / "worker.result_manifest.json") + "\n").encode(), stderr=b"")
            return completed
        if args[0] == "rsync":
            source_arg = args[-2]
            destination = Path(args[-1])
            path = Path(source_arg.split(":", 1)[1])
            if path.name == "worker.result_manifest.json":
                copy_manifest_count["n"] += 1
                if copy_manifest_count["n"] == 2:
                    manifest = json.loads(path.read_text())
                    manifest["settings_sha256"] = "e" * 64
                    path.write_text(_canon(manifest))
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination)
            return subprocess.CompletedProcess(args, 0, stdout=b"", stderr=b"")
        raise AssertionError(args)

    with _collector_db(tmp_path / "remote" / "central.sqlite") as db:
        collector = PaperForwardCollector(tmp_path / "remote", db, hosts=("s2",), remote_root=str(remote_root), remote_coordinator=str(remote_coordinator), runner=fake_runner)
        result = collector.collect_once(plan)
        failures = [row for row in result["collected_artifacts"] if row.get("status") == "COLLECT_ERROR"]
        assert len(failures) == 1
        assert "identity/settings changed" in failures[0]["error"]
        assert db.query("SELECT COUNT(*) FROM runs WHERE evidence_kind='independent_forward'")[0][0] == 0
        assert list(collector.incoming_root.glob(".*.pending")) == []
        assert db.query("SELECT COUNT(*) FROM paper_coordinator_jobs")[0][0] == 1


def test_remote_bundle_pull_imports_versioned_artifacts(tmp_path):
    plan, source = _plan_and_worker(tmp_path / "remote", host="s5")
    remote_root = tmp_path / "fake_s5" / "runs"
    remote_run = remote_root / source.name
    remote_run.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, remote_run)
    remote_coordinator = tmp_path / "fake_s5" / "fleet_s5.snapshot.sqlite3"
    _coordinator_snapshot(remote_coordinator, "s5", "RUNNING")

    def fake_runner(args, capture_output, text, timeout, check):
        if args[0] == "ssh":
            return subprocess.CompletedProcess(args, 0, stdout=(str(remote_run / "worker.result_manifest.json") + "\n").encode(), stderr=b"")
        if args[0] == "rsync":
            source_path = Path(args[-2].split(":", 1)[1])
            destination = Path(args[-1])
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_path, destination)
            return subprocess.CompletedProcess(args, 0, stdout=b"", stderr=b"")
        raise AssertionError(args)

    with _collector_db(tmp_path / "remote" / "central.sqlite") as db:
        collector = PaperForwardCollector(tmp_path / "remote", db, hosts=("s5",), remote_root=str(remote_root), remote_coordinator=str(remote_coordinator), runner=fake_runner)
        result = collector.collect_once(plan)
        imported = next(row for row in result["collected_artifacts"] if row.get("run_id") == "paper-run-1")
        assert imported["status"] == "COMPLETED"
        assert Path(imported["bundle_path"]).is_dir()
        assert db.query("SELECT COUNT(*) FROM forward_receipts WHERE run_id='paper-run-1'")[0][0] == 1
        assert db.query("SELECT COUNT(*) FROM paper_coordinator_jobs")[0][0] == 1


def test_local_collector_copies_manifest_pinned_accounting_artifact(tmp_path):
    plan, run_dir = _plan_and_worker(tmp_path)
    manifest_path = run_dir / "worker.result_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    journal = sqlite3.connect(run_dir / "receipts.snapshot.db")
    input_hash = journal.execute("SELECT input_hash FROM frames").fetchone()[0]
    journal.close()
    accounting = {
        "schema_version": 1, "run_id": manifest["run_id"], "epoch_id": manifest["epoch_id"],
        "scenario_id": manifest["scenario_id"], "sym_side": manifest["sym_side"],
        "settings_sha256": manifest["settings_sha256"], "code_sha256": manifest["code_sha256"],
        "source_input_sha256s": [input_hash], "cost_model": "engine_simulated_round_trip_cost",
        "costs_are_exchange_actual": False, "trades": [],
    }
    body = _canon(accounting) + "\n"
    (run_dir / "accounting.json").write_text(body)
    manifest["accounting_file"] = "accounting.json"
    manifest["accounting_sha256"] = hashlib.sha256(body.encode()).hexdigest()
    manifest_path.write_text(_canon(manifest))
    with _collector_db(tmp_path / "data" / "paper_forward_lab" / "central.sqlite") as db:
        result = PaperForwardCollector(tmp_path, db, hosts=("s1",)).collect_once(plan)
        imported = next(row for row in result["collected_artifacts"] if row.get("run_id") == "paper-run-1")
        assert imported["accounting_fills"] == 0
        assert (Path(imported["bundle_path"]) / "accounting.json").is_file()


def test_local_collector_records_terminal_settings_mismatch_without_receipt_db(tmp_path):
    plan, run_dir = _plan_and_worker(tmp_path)
    (run_dir / "receipts.snapshot.db").unlink()
    manifest_path = run_dir / "worker.result_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest.update(status="SETTINGS_MISMATCH", frame_count=0, committed_receipts=0, settings_mismatch_keys=["X"])
    manifest_path.write_text(_canon(manifest))
    with _collector_db(tmp_path / "data" / "paper_forward_lab" / "central.sqlite") as db:
        result = PaperForwardCollector(tmp_path, db, hosts=("s1",)).collect_once(plan)
        imported = next(row for row in result["collected_artifacts"] if row.get("run_id") == "paper-run-1")
        assert imported["status"] == "SETTINGS_MISMATCH"
        assert imported["result_valid"] is False
        assert not (Path(imported["bundle_path"]) / "receipts.snapshot.db").exists()
        assert db.query("SELECT status FROM run_current_status WHERE run_id='paper-run-1'")[0][0] == "SETTINGS_MISMATCH"


def test_coordinator_repoll_updates_heartbeat_without_rewriting_jobs_or_old_events(tmp_path):
    source = tmp_path / "fleet.snapshot.sqlite3"
    _coordinator_snapshot(source, "s1", "DONE")
    central_path = tmp_path / "central.sqlite"
    with _collector_db(central_path) as db:
        db.connection.execute("CREATE TABLE write_counts(kind TEXT NOT NULL)")
        # Create collector tables before installing triggers against them.
        from tools.paper_forward_collect import _create_coordinator_tables
        _create_coordinator_tables(db.connection)
        db.connection.execute("CREATE TRIGGER count_job_update AFTER UPDATE ON paper_coordinator_jobs BEGIN INSERT INTO write_counts VALUES('job_update'); END")
        db.connection.execute("CREATE TRIGGER count_event_insert AFTER INSERT ON paper_coordinator_events BEGIN INSERT INTO write_counts VALUES('event_insert'); END")
        ingest_coordinator_database(db, "s1", source)
        db.connection.execute("DELETE FROM write_counts")
        source_connection = sqlite3.connect(source)
        source_connection.execute("UPDATE coordinator_heartbeat SET at_utc='2026-10-31T00:01:00Z' WHERE host='s1'")
        source_connection.commit()
        source_connection.close()
        ingest_coordinator_database(db, "s1", source)
        assert db.query("SELECT COUNT(*) FROM write_counts WHERE kind='job_update'")[0][0] == 0
        assert db.query("SELECT COUNT(*) FROM write_counts WHERE kind='event_insert'")[0][0] == 0
        assert db.query("SELECT recorded_at FROM paper_coordinator_heartbeats WHERE source_host='s1'")[0][0] == "2026-10-31T00:01:00Z"
        assert db.query("SELECT COUNT(*) FROM forward_receipts WHERE run_id='paper-run-1'")[0][0] == 0


def test_local_collector_stages_and_checks_manifest_pinned_result_json(tmp_path):
    plan, run_dir = _plan_and_worker(tmp_path)
    manifest_path = run_dir / "worker.result_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    result = {key: manifest[key] for key in ("run_id", "epoch_id", "scenario_id", "sym_side", "settings_sha256", "code_sha256")}
    result.update({"override_issues": [{"key": "X", "action": "SKIPPED"}], "status": "COMPLETED"})
    body = _canon(result) + "\n"
    (run_dir / "result.json").write_text(body)
    manifest["result_file"] = "result.json"
    manifest["result_sha256"] = hashlib.sha256(body.encode()).hexdigest()
    manifest_path.write_text(_canon(manifest))
    with _collector_db(tmp_path / "data" / "paper_forward_lab" / "central.sqlite") as db:
        collected = PaperForwardCollector(tmp_path, db, hosts=("s1",)).collect_once(plan)
        imported = next(row for row in collected["collected_artifacts"] if row.get("run_id") == "paper-run-1")
        assert (Path(imported["bundle_path"]) / "result.json").is_file()
        assert imported["result_valid"] is False
        provenance = db.query("SELECT invalid_reasons_json FROM paper_forward_run_provenance WHERE run_id='paper-run-1'")[0][0]
        assert "OVERRIDE_ISSUES" in provenance
