import hashlib
import json
import shutil
import sqlite3
import subprocess
from pathlib import Path

from tools.paper_forward_collect import PaperForwardCollector
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


def test_local_collect_registers_plan_then_imports_receipts_and_coordinator_separately(tmp_path):
    plan, run_dir = _plan_and_worker(tmp_path)
    coordinator_path = tmp_path / "data" / "paper_forward_lab" / "coordinator_snapshot.json"
    coordinator_path.write_text(_canon({"observed_at_utc": "2026-10-31T00:00:04Z", "jobs": [{"job_id": "j1", "run_id": "paper-run-1", "scenario_id": "scenario-1", "sym_side": "BTCUSDC_LONG", "host": "s1", "status": "DONE"}], "coverage": {"planned": 1, "done": 1}}))
    with _collector_db(tmp_path / "data" / "paper_forward_lab" / "central.sqlite") as db:
        collector = PaperForwardCollector(tmp_path, db, hosts=("s1",))
        first = collector.collect_once(plan)
        second = collector.collect_once(plan)
        assert first["plan_registered"]["groups"] == 1
        assert first["collected_artifacts"][0]["status"] == "COMPLETED"
        runs = db.query("SELECT evidence_kind FROM runs WHERE run_id='paper-run-1'")
        assert runs[0][0] == "independent_forward"
        assert db.query("SELECT COUNT(*) FROM forward_receipts WHERE run_id='paper-run-1'")[0][0] == 1
        assert db.query("SELECT COUNT(*) FROM decisions WHERE run_id='paper-run-1'")[0][0] == 0
        assert db.query("SELECT COUNT(*) FROM results WHERE run_id='paper-run-1'")[0][0] == 0
        assert db.query("SELECT COUNT(*) FROM paper_coordinator_jobs")[0][0] == 1
        assert db.query("SELECT coverage_json FROM paper_coordinator_coverage_reports")[0][0] == '{"done":1,"planned":1}'
        assert db.query("SELECT evidence_kind FROM paper_coordinator_snapshots")[0][0] == "coordinator_only"
        assert len(list((tmp_path / "data" / "paper_forward_lab" / "collector" / "collected").rglob("receipts.snapshot.db"))) == 1
        assert second["collected_artifacts"][0]["run_id"] == "paper-run-1"


def test_remote_bundle_pull_stages_files_and_rejects_manifest_identity_race(tmp_path):
    plan, source = _plan_and_worker(tmp_path / "remote", host="s2")
    remote_root = tmp_path / "fake_s2" / "runs"
    remote_run = remote_root / source.name
    remote_run.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, remote_run)
    remote_coordinator = tmp_path / "fake_s2" / "coordinator.json"
    remote_coordinator.write_text(_canon({"jobs": [{"job_id": "j2", "host": "s2", "status": "RUNNING"}]}))
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
