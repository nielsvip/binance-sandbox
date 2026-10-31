import hashlib
import json

from tools.paper_frame_stream import FrameJournal
from tools.paper_forward_results import _digest, ingest, ingest_directory
from tools.paper_scenario_db import ScenarioDB


def _fixture(tmp_path):
    db = ScenarioDB(tmp_path / "central.sqlite")
    db.add_path(path_id="p", venue="crypto", path_name="paper", switch_name="X", classification="verified", provenance={})
    candidate = {"BOOL_SWITCH": True}
    db.add_scenario(scenario_id="s", path_id="p", side="LONG", parameters={"overrides": candidate}, created_at="2026-01-01T00:00:00Z")
    epoch = "epoch-abc"
    base = {"BOOL_SWITCH": False, "START_POSITION_SIZE": 25}
    settings = hashlib.sha256(json.dumps(base, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    code = "b" * 64
    db.add_run(run_id="planned-" + _digest(["s", epoch]), scenario_id="s", sym_side="BTCUSDC_LONG", host="worker1", settings_hash=settings, code_hash=code, evidence_kind="catalog_only", status="PENDING_VALIDATED_EXECUTION", started_at="2026-01-01T00:00:00Z", ended_at=None)
    effective = {"BOOL_SWITCH": True, "START_POSITION_SIZE": 25}
    effective_hash = hashlib.sha256(json.dumps(effective, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    manifest = {"run_id": "actual-run-1", "epoch_id": epoch, "scenario_id": "s", "sym_side": "BTCUSDC_LONG", "host": "worker1", "settings_sha256": effective_hash, "parent_settings_sha256": settings, "code_sha256": code, "code_file_hashes": {"backtest_v12_engine.py": code}, "started_at": "1970-01-01T00:00:00Z", "base_settings": base, "effective_settings": effective, "candidate_overrides": candidate, "finished": True}
    journal = FrameJournal(tmp_path / "worker.sqlite", {"scenario_id": "s", "run_id": manifest["run_id"], "epoch_id": epoch, "sym_side": "BTCUSDC_LONG", "settings_sha256": effective_hash, "code_sha256": code})
    journal.append_frame(100, {"close": 10})
    journal.record_receipt(100, {"events": [{"type": "eta", "action": "OPEN", "status": "FILLED", "position_key": "inf:BTCUSDC_LONG", "position_before": 0, "position_after": 2, "price": 10, "quantity": 2}], "positions": {"inf:BTCUSDC_LONG": {"quantity": 2}}})
    journal.append_frame(200, {"close": 12})
    journal.record_receipt(200, {"events": [{"params": {"type": "MARKET", "side": "SELL"}, "action": "CLOSE", "status": "FILLED", "position_key": "inf:BTCUSDC_LONG", "position_before": 2, "position_after": 0, "price": 12, "quantity": 2}], "positions": {"inf:BTCUSDC_LONG": {"quantity": 0}}})
    journal.close()
    return db, tmp_path / "worker.sqlite", manifest


def test_receipt_ingest_is_idempotent_and_keeps_outcome_unknown(tmp_path):
    db, journal, manifest = _fixture(tmp_path)
    first = ingest(db, journal, manifest)
    second = ingest(db, journal, manifest)
    assert first["status"] == "COMPLETED"
    assert first["receipts"] == 2
    assert first["completed_lifecycles"] == 1
    assert second["completed_lifecycles"] == 0
    assert db.query("SELECT evidence_kind FROM runs WHERE run_id='actual-run-1'")[0][0] == "independent_forward"
    assert db.query("SELECT COUNT(*) FROM forward_receipts WHERE run_id='actual-run-1'")[0][0] == 2
    assert db.query("SELECT COUNT(*) FROM decisions WHERE run_id='actual-run-1'")[0][0] == 2
    trade = db.query("SELECT * FROM trades WHERE run_id='actual-run-1'")[0]
    assert trade["opened_at"].startswith("1970-01-01T00:01:40")
    assert trade["closed_at"].startswith("1970-01-01T00:03:20")
    assert trade["net_return"] is None and trade["fees"] is None
    assert db.query("SELECT status FROM run_current_status WHERE run_id='actual-run-1'")[0][0] == "COMPLETED"
    db.close()


def test_receipt_ingest_rejects_wrong_worker_epoch(tmp_path):
    db, journal, manifest = _fixture(tmp_path)
    manifest["epoch_id"] = "other-epoch"
    try:
        ingest(db, journal, manifest)
    except ValueError as exc:
        assert "does not match manifest" in str(exc)
    else:
        raise AssertionError("expected journal epoch mismatch")
    db.close()


def test_receipt_ingest_rejects_effective_settings_hash_mismatch(tmp_path):
    db, journal, manifest = _fixture(tmp_path)
    manifest["settings_sha256"] = "f" * 64
    try:
        ingest(db, journal, manifest)
    except ValueError as exc:
        assert "settings_sha256" in str(exc)
    else:
        raise AssertionError("expected effective settings hash mismatch")
    db.close()


def test_online_ingest_ignores_uncommitted_tail_and_retries_prefix(tmp_path):
    db, journal, manifest = _fixture(tmp_path)
    manifest["finished"] = False
    connection = __import__("sqlite3").connect(journal)
    payload = '{"close":13}'
    connection.execute("INSERT INTO frames(closed_ts,payload,input_hash) VALUES(?,?,?)", (300, payload, hashlib.sha256(payload.encode()).hexdigest()))
    connection.commit()
    connection.close()
    first = ingest(db, journal, manifest)
    assert first["status"] == "RUNNING" and first["receipts"] == 2
    assert ingest(db, journal, manifest)["receipts"] == 2
    assert db.query("SELECT COUNT(*) FROM forward_receipts WHERE run_id='actual-run-1'")[0][0] == 2
    db.close()


def test_directory_daemon_ingests_worker_manifest_and_snapshot(tmp_path):
    import shutil

    db, journal, manifest = _fixture(tmp_path)
    manifest["finished"] = False
    run_dir = tmp_path / "workers" / "actual-run-1"
    run_dir.mkdir(parents=True)
    shutil.copy2(journal, run_dir / "receipt.snapshot.db")
    (run_dir / "manifest.json").write_text(json.dumps(manifest))
    result = ingest_directory(db, tmp_path / "workers")
    assert result[0]["status"] == "RUNNING"
    assert result[0]["receipts"] == 2
    db.close()


def test_execution_without_verified_fill_does_not_create_trade(tmp_path):
    db, journal, manifest = _fixture(tmp_path)
    manifest["finished"] = False
    import sqlite3

    connection = sqlite3.connect(journal)
    receipt = {"events": [{"type": "exec_now", "action": "OPEN", "decision_only": True, "position_key": "inf:BTCUSDC_LONG", "position_before": 0, "position_after": 2, "price": 10, "quantity": 2}], "positions": {"inf:BTCUSDC_LONG": {"positionAmt": 2}}}
    body = json.dumps(receipt, sort_keys=True, separators=(",", ":"))
    receipt_hash = hashlib.sha256(body.encode()).hexdigest()
    connection.execute("UPDATE frames SET receipt_hash=?,receipt_json=? WHERE closed_ts=200", (receipt_hash, body))
    connection.commit()
    connection.close()
    ingest(db, journal, manifest)
    assert db.query("SELECT COUNT(*) FROM trades WHERE run_id='actual-run-1'")[0][0] == 0
    db.close()


def test_empty_receipt_keeps_snapshot_without_synthetic_action(tmp_path):
    import sqlite3

    db, journal, manifest = _fixture(tmp_path)
    manifest["finished"] = False
    connection = sqlite3.connect(journal)
    for closed_ts in (100, 200):
        receipt = {"events": [], "positions": {"inf:BTCUSDC_LONG": {"positionAmt": 0}}}
        body = json.dumps(receipt, sort_keys=True, separators=(",", ":"))
        connection.execute("UPDATE frames SET receipt_hash=?,receipt_json=? WHERE closed_ts=?", (hashlib.sha256(body.encode()).hexdigest(), body, closed_ts))
    connection.commit()
    connection.close()
    result = ingest(db, journal, manifest)
    assert result["execution_events"] == result["decision_rows"] == 0
    assert db.query("SELECT COUNT(*) FROM forward_receipts WHERE run_id='actual-run-1'")[0][0] == 2
    assert db.query("SELECT COUNT(*) FROM decisions WHERE run_id='actual-run-1'")[0][0] == 0
    db.close()
