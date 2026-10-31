import hashlib

from tools.paper_frame_stream import FrameJournal
from tools.paper_forward_results import _digest, ingest
from tools.paper_scenario_db import ScenarioDB


def _fixture(tmp_path):
    db = ScenarioDB(tmp_path / "central.sqlite")
    db.add_path(path_id="p", venue="crypto", path_name="paper", switch_name="X", classification="verified", provenance={})
    db.add_scenario(scenario_id="s", path_id="p", side="LONG", parameters={}, created_at="2026-01-01T00:00:00Z")
    epoch = "epoch-abc"
    settings = "a" * 64
    code = "b" * 64
    db.add_run(run_id="planned-" + _digest(["s", epoch]), scenario_id="s", sym_side="BTCUSDC_LONG", host="worker1", settings_hash=settings, code_hash=code, evidence_kind="catalog_only", status="PENDING_VALIDATED_EXECUTION", started_at="2026-01-01T00:00:00Z", ended_at=None)
    manifest = {"run_id": "actual-run-1", "epoch_id": epoch, "scenario_id": "s", "sym_side": "BTCUSDC_LONG", "host": "worker1", "settings_sha256": settings, "code_sha256": code, "started_at": "2026-01-01T00:00:00Z", "finished": True}
    journal = FrameJournal(tmp_path / "worker.sqlite", {"scenario_id": "s", "run_id": manifest["run_id"], "epoch_id": epoch, "settings_sha256": settings, "code_sha256": code})
    journal.append_frame(100, {"close": 10})
    journal.record_receipt(100, {"events": [{"action": "OPEN", "status": "FILLED", "position_key": "inf:BTCUSDC_LONG", "position_before": 0, "position_after": 2, "price": 10, "quantity": 2}], "positions": {"inf:BTCUSDC_LONG": {"quantity": 2}}})
    journal.append_frame(200, {"close": 12})
    journal.record_receipt(200, {"events": [{"action": "CLOSE", "status": "FILLED", "position_key": "inf:BTCUSDC_LONG", "position_before": 2, "position_after": 0, "price": 12, "quantity": 2}], "positions": {"inf:BTCUSDC_LONG": {"quantity": 0}}})
    journal.close()
    return db, tmp_path / "worker.sqlite", manifest


def test_receipt_ingest_is_idempotent_and_keeps_outcome_unknown(tmp_path):
    db, journal, manifest = _fixture(tmp_path)
    first = ingest(db, journal, manifest)
    second = ingest(db, journal, manifest)
    assert first["status"] == "COMPLETED"
    assert first["receipts"] == 2
    assert second["completed_lifecycles"] == first["completed_lifecycles"] == 1
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
