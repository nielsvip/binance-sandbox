import hashlib
import json

from tools.paper_frame_stream import FrameJournal
from tools.paper_forward_results import _digest, ingest, ingest_directory, ingest_terminal_without_journal
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
    manifest = {"run_id": "actual-run-1", "epoch_id": epoch, "scenario_id": "s", "sym_side": "BTCUSDC_LONG", "host": "worker1", "settings_sha256": effective_hash, "parent_settings_sha256": settings, "code_sha256": code, "code_file_hashes": {"backtest_v12_engine.py": code}, "started_at": "1970-01-01T00:00:00Z", "base_settings": base, "effective_settings": effective, "candidate_overrides": candidate, "candidate_getter_resolution": {"BOOL_SWITCH": {"matches": True}}, "source_timeframe": "1m", "source_time_alias_verified": True, "closed_bar_status_verified": True, "source_time_semantics": "native 1m source timeframe; closed-bar status verified", "experiment_window": {"source_timeframe": "1m"}, "context": {"evidence_label": "INDEPENDENT_FORWARD", "source_timeframe": "1m", "source_time_alias_verified": True, "closed_bar_status_verified": True}, "finished": True}
    journal = FrameJournal(tmp_path / "worker.sqlite", {"scenario_id": "s", "run_id": manifest["run_id"], "epoch_id": epoch, "sym_side": "BTCUSDC_LONG", "settings_sha256": effective_hash, "code_sha256": code})
    journal.append_frame(100, {"close": 10})
    journal.record_receipt(100, {"events": [{"type": "eta", "action": "OPEN", "status": "FILLED", "position_key": "inf:BTCUSDC_LONG", "position_before": 0, "position_after": 2, "price": 10, "quantity": 2}], "positions": {"inf:BTCUSDC_LONG": {"quantity": 2}}})
    journal.append_frame(200, {"close": 12})
    journal.record_receipt(200, {"events": [{"type": "webhook", "params": {"type": "MARKET", "side": "SELL"}, "action": "CLOSE", "status": "FILLED", "position_key": "inf:BTCUSDC_LONG", "position_before": 2, "position_after": 0, "price": 12, "quantity": 2}], "positions": {"inf:BTCUSDC_LONG": {"quantity": 0}}})
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


def test_paired_readiness_reports_shared_inputs_without_claiming_outcomes(tmp_path):
    import sqlite3

    db, journal_path, candidate_manifest = _fixture(tmp_path)
    candidate_artifact = _accounting_artifact(journal_path, candidate_manifest)
    _write_accounting(journal_path.parent, candidate_manifest, candidate_artifact)
    ingest(db, journal_path, candidate_manifest, journal_path.parent)
    baseline_id = "baseline-scenario"
    base = {"BOOL_SWITCH": False, "START_POSITION_SIZE": 25}
    parent_hash = candidate_manifest["parent_settings_sha256"]
    db.add_scenario(scenario_id=baseline_id, path_id="p", side="LONG", parameters={"overrides": {}}, created_at=candidate_manifest["started_at"])
    epoch = candidate_manifest["epoch_id"]
    db.add_run(run_id="planned-" + _digest([baseline_id, epoch]), scenario_id=baseline_id, sym_side=candidate_manifest["sym_side"], host=candidate_manifest["host"], settings_hash=parent_hash, code_hash="c" * 64, evidence_kind="catalog_only", status="PENDING", started_at=candidate_manifest["started_at"], ended_at=None)
    baseline = dict(candidate_manifest, run_id="actual-baseline", scenario_id=baseline_id, settings_sha256=parent_hash, candidate_overrides={}, effective_settings=base, finished=True)
    baseline.pop("accounting_file", None)
    baseline.pop("accounting_sha256", None)
    identity = {"scenario_id": baseline_id, "run_id": baseline["run_id"], "epoch_id": epoch, "sym_side": baseline["sym_side"], "settings_sha256": parent_hash, "code_sha256": baseline["code_sha256"]}
    baseline_journal = tmp_path / "baseline.sqlite"
    from tools.paper_frame_stream import FrameJournal

    journal = FrameJournal(baseline_journal, identity)
    source_frames = sqlite3.connect(journal_path)
    for sequence, ts, payload, receipt_json in source_frames.execute("SELECT sequence,closed_ts,payload,receipt_json FROM frames ORDER BY sequence"):
        journal.append_frame(ts, json.loads(payload))
        journal.record_receipt(ts, json.loads(receipt_json))
    source_frames.close()
    journal.close()
    baseline_artifact = _accounting_artifact(baseline_journal, baseline)
    _write_accounting(baseline_journal.parent, baseline, baseline_artifact)
    ingest(db, baseline_journal, baseline, baseline_journal.parent)
    rows = db.query("SELECT * FROM paper_paired_readiness")
    pair = next(row for row in rows if {row["run_a"], row["run_b"]} == {"actual-run-1", "actual-baseline"})
    assert pair["candidate_config_differs"] == 1
    assert pair["shared_context_matches"] == 1
    assert (pair["frames_a"], pair["frames_b"], pair["identical_input_frames"]) == (2, 2, 2)
    assert pair["identical_inputs_ready"] == 1
    lifecycles = {pair["run_a"]: pair["lifecycles_a"], pair["run_b"]: pair["lifecycles_b"]}
    assert lifecycles == {"actual-run-1": 1, "actual-baseline": 1}
    assert pair["known_return_lifecycles_a"] == pair["known_return_lifecycles_b"] == 0
    assert pair["known_fee_lifecycles_a"] == pair["known_fee_lifecycles_b"] == 0
    assert pair["outcomes_computable"] == 0
    cost_pair = next(row for row in db.query("SELECT * FROM paper_paired_simulated_cost_readiness") if {row["run_a"], row["run_b"]} == {"actual-run-1", "actual-baseline"})
    assert cost_pair["outcome_label"] == "diagnostic_simulated_cost"
    assert cost_pair["paired_simulated_cost_ready"] == 1
    assert cost_pair["diagnostic_simulated_cost_ready"] == 1
    assert cost_pair["diagnostic_simulated_net_pnl_dollars_delta"] == 0
    db.connection.execute("UPDATE paper_forward_run_provenance SET evidence_label='DIAGNOSTIC_REPLAY',input_provenance_valid=0,invalid_reasons_json='[\"DIAGNOSTIC_REPLAY\"]' WHERE run_id='actual-baseline'")
    invalid_pair = next(row for row in db.query("SELECT * FROM paper_paired_simulated_cost_readiness") if {row["run_a"], row["run_b"]} == {"actual-run-1", "actual-baseline"})
    assert invalid_pair["paired_simulated_cost_ready"] == 0
    assert invalid_pair["diagnostic_simulated_cost_ready"] == 0
    assert invalid_pair["diagnostic_simulated_net_pnl_dollars_delta"] is None
    db.close()


def _accounting_artifact(journal_path, manifest):
    import sqlite3

    connection = sqlite3.connect(journal_path)
    rows = connection.execute("SELECT sequence,closed_ts,input_hash,receipt_json FROM frames ORDER BY sequence").fetchall()
    connection.close()
    trades = []
    for sequence, ts, input_hash, receipt_json in rows:
        receipt = json.loads(receipt_json)
        for event_index, event in enumerate(receipt["events"]):
            recording_client_fill = isinstance(event.get("params"), dict) and bool(event.get("position_key")) and event.get("position_before") is not None and event.get("position_after") is not None and float(event.get("price", 0) or 0) > 0 and float(event.get("quantity", 0) or 0) > 0
            if str(event.get("status", "")).upper() != "FILLED" or event.get("decision_only") or (str(event.get("type", "")).lower() not in {"eta", "webhook"} and not recording_client_fill):
                continue
            close = event["action"] == "CLOSE"
            trades.append({
                "source_frame_sequence": sequence, "source_event_index": event_index,
                "source_frame_input_hash": input_hash,
                "source_event_sha256": hashlib.sha256(json.dumps(event, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                "source_event": event, "event_type": event.get("type") or "recording_client", "status": "FILLED",
                "timestamp": "1970-01-01T00:03:20.000000Z" if ts == 200 else "1970-01-01T00:01:40.000000Z",
                "position_key": event["position_key"], "action": event["action"], "position_side": "LONG",
                "price": event["price"], "quantity": event["quantity"],
                "gross_pnl_dollars": 4.0 if close else None, "net_pnl_dollars": 3.8 if close else None,
                "gross_pnl_pct": 20.0 if close else None, "net_pnl_pct": 19.0 if close else None,
                "round_trip_cost_pct_simulated": 1.0 if close else None,
                "fees_dollars_simulated": 0.2 if close else None, "pnl_currency": "USD_SIMULATED",
            })
    return {
        "schema_version": 1, "run_id": manifest["run_id"], "epoch_id": manifest["epoch_id"],
        "scenario_id": manifest["scenario_id"], "sym_side": manifest["sym_side"],
        "settings_sha256": manifest["settings_sha256"], "code_sha256": manifest["code_sha256"],
        "source_input_sha256s": sorted(row[2] for row in rows),
        "cost_model": "engine_simulated_round_trip_cost", "costs_are_exchange_actual": False,
        "trades": trades,
    }


def _write_accounting(path, manifest, artifact):
    raw = (json.dumps(artifact, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()
    (path / "accounting.json").write_bytes(raw)
    manifest["accounting_file"] = "accounting.json"
    manifest["accounting_sha256"] = hashlib.sha256(raw).hexdigest()


def test_authenticated_engine_accounting_fills_ingest_as_simulated_diagnostic(tmp_path):
    db, journal, manifest = _fixture(tmp_path)
    artifact = _accounting_artifact(journal, manifest)
    _write_accounting(journal.parent, manifest, artifact)
    result = ingest(db, journal, manifest, journal.parent)
    assert result["accounting_fills"] == 2
    rows = db.query("SELECT action,gross_pnl_dollars,modeled_net_pnl_dollars,modeled_cost_dollars,cost_model FROM paper_forward_accounting_fills ORDER BY source_frame_sequence")
    assert rows[0]["gross_pnl_dollars"] is None
    assert (rows[1]["gross_pnl_dollars"], rows[1]["modeled_net_pnl_dollars"], rows[1]["modeled_cost_dollars"]) == (4.0, 3.8, 0.2)
    assert all(row["cost_model"] == "engine_simulated_round_trip_cost" for row in rows)
    pair = db.query("SELECT outcome_label,diagnostic_simulated_cost_ready FROM paper_paired_simulated_cost_readiness")
    assert pair == []
    db.close()


def test_accounting_rejects_tampered_execution_quantity(tmp_path):
    db, journal, manifest = _fixture(tmp_path)
    artifact = _accounting_artifact(journal, manifest)
    artifact["trades"][0]["quantity"] = 3
    _write_accounting(journal.parent, manifest, artifact)
    try:
        ingest(db, journal, manifest, journal.parent)
    except ValueError as exc:
        assert "quantity differs" in str(exc)
    else:
        raise AssertionError("expected accounting/raw execution mismatch")
    db.close()


def test_accounting_rejects_modified_artifact_hash_and_missing_fills(tmp_path):
    db, journal, manifest = _fixture(tmp_path)
    artifact = _accounting_artifact(journal, manifest)
    artifact["trades"].pop()
    _write_accounting(journal.parent, manifest, artifact)
    try:
        ingest(db, journal, manifest, journal.parent)
    except ValueError as exc:
        assert "omits" in str(exc)
    else:
        raise AssertionError("expected missing execution accounting rejection")
    manifest["accounting_sha256"] = "f" * 64
    try:
        ingest(db, journal, manifest, journal.parent)
    except ValueError as exc:
        assert "hash" in str(exc)
    else:
        raise AssertionError("expected accounting hash rejection")
    db.close()


def test_accounting_accepts_filled_recording_client_event_without_type(tmp_path):
    import sqlite3

    db, journal, manifest = _fixture(tmp_path)
    connection = sqlite3.connect(journal)
    row = connection.execute("SELECT receipt_json FROM frames WHERE sequence=1").fetchone()
    receipt = json.loads(row[0])
    event = receipt["events"][0]
    event.pop("type")
    event["params"] = {"type": "MARKET"}
    body = json.dumps(receipt, sort_keys=True, separators=(",", ":"))
    connection.execute("UPDATE frames SET receipt_hash=?,receipt_json=? WHERE sequence=1", (hashlib.sha256(body.encode()).hexdigest(), body))
    connection.commit()
    connection.close()
    artifact = _accounting_artifact(journal, manifest)
    _write_accounting(journal.parent, manifest, artifact)
    result = ingest(db, journal, manifest, journal.parent)
    assert result["accounting_fills"] == 2
    db.close()


def test_terminal_data_gap_is_visible_without_fake_receipts_or_trades(tmp_path):
    import sqlite3

    db, journal, manifest = _fixture(tmp_path)
    connection = sqlite3.connect(journal)
    connection.execute("DELETE FROM frames")
    connection.commit()
    connection.close()
    manifest.update(status="DATA_GAP", finished=True, ended_at="1970-01-01T00:05:00Z", reason="NO_INPUT_BEFORE_WINDOW_END")
    result = ingest(db, journal, manifest)
    assert result["status"] == "DATA_GAP"
    assert result["result_valid"] is False
    assert db.query("SELECT status FROM run_current_status WHERE run_id='actual-run-1'")[0][0] == "DATA_GAP"
    assert db.query("SELECT COUNT(*) FROM forward_receipts WHERE run_id='actual-run-1'")[0][0] == 0
    assert db.query("SELECT COUNT(*) FROM trades WHERE run_id='actual-run-1'")[0][0] == 0
    db.close()


def test_terminal_settings_mismatch_is_visible_without_processing_frames(tmp_path):
    import sqlite3

    db, journal, manifest = _fixture(tmp_path)
    connection = sqlite3.connect(journal)
    connection.execute("DELETE FROM frames")
    connection.commit()
    connection.close()
    manifest.update(status="SETTINGS_MISMATCH", finished=True, ended_at="1970-01-01T00:05:00Z", settings_mismatch_keys=["X"])
    result = ingest(db, journal, manifest)
    assert result["status"] == "SETTINGS_MISMATCH"
    assert result["result_valid"] is False
    assert db.query("SELECT status FROM run_current_status WHERE run_id='actual-run-1'")[0][0] == "SETTINGS_MISMATCH"
    assert db.query("SELECT COUNT(*) FROM forward_receipts WHERE run_id='actual-run-1'")[0][0] == 0
    assert db.query("SELECT COUNT(*) FROM trades WHERE run_id='actual-run-1'")[0][0] == 0
    db.close()


def test_terminal_mismatch_without_receipt_journal_is_status_only(tmp_path):
    db, _journal, manifest = _fixture(tmp_path)
    manifest.update(status="SETTINGS_MISMATCH", finished=True, ended_at="1970-01-01T00:05:00Z", settings_mismatch_keys=["X"])
    result = ingest_terminal_without_journal(db, manifest, tmp_path)
    assert result["status"] == "SETTINGS_MISMATCH"
    assert result["receipts"] == result["execution_events"] == result["completed_lifecycles"] == 0
    assert db.query("SELECT status FROM run_current_status WHERE run_id='actual-run-1'")[0][0] == "SETTINGS_MISMATCH"
    assert db.query("SELECT COUNT(*) FROM forward_receipts")[0][0] == 0
    db.close()


def test_diagnostic_replay_and_override_issues_are_invalid_provenance(tmp_path):
    db, journal, manifest = _fixture(tmp_path)
    manifest["context"]["evidence_label"] = "DIAGNOSTIC_REPLAY"
    manifest["override_issues"] = [{"action": "SKIPPED_CONTAINER_TYPE_MISMATCH", "key": "DELTA_TF_WEIGHTS"}]
    result = ingest(db, journal, manifest)
    provenance = db.query("SELECT input_provenance_valid,invalid_reasons_json FROM paper_forward_run_provenance WHERE run_id='actual-run-1'")[0]
    assert result["receipts"] == 2
    assert provenance["input_provenance_valid"] == 0
    assert "DIAGNOSTIC_REPLAY" in provenance["invalid_reasons_json"]
    assert "OVERRIDE_ISSUES" in provenance["invalid_reasons_json"]
    assert db.query("SELECT COUNT(*) FROM forward_receipts WHERE run_id='actual-run-1'")[0][0] == 2
    db.close()
