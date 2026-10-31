"""Ingest isolated paper worker receipt journals into the scenario inventory.

This module records immutable engine events, position snapshots, lifecycle
boundaries, and run heartbeats. It does not calculate strategy metrics.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

try:
    from .paper_scenario_db import ScenarioDB
    from .paper_sql_sink import PaperSQLSink
except ImportError:
    from paper_scenario_db import ScenarioDB
    from paper_sql_sink import PaperSQLSink


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _utc(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _terminal_recorded_at(manifest: Mapping[str, Any], last_receipt: Mapping[str, Any]) -> str:
    candidates = [str(value) for value in (manifest.get("ended_at"), last_receipt.get("observed_at_utc"), _utc(last_receipt["ts"])) if value]
    parsed = []
    for value in candidates:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError("terminal status timestamps must include timezone")
        parsed.append(stamp.astimezone(timezone.utc))
    return (max(parsed) + timedelta(microseconds=1)).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _journal(path: str | Path) -> tuple[dict, list[dict]]:
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.execute("BEGIN")
        meta = dict(connection.execute("SELECT key,value FROM metadata"))
        if "identity" not in meta:
            raise ValueError("receipt journal has no identity metadata")
        identity = json.loads(meta["identity"])
        rows = connection.execute(
            "SELECT sequence,closed_ts,payload,input_hash,receipt_hash,receipt_json FROM frames ORDER BY sequence"
        ).fetchall()
    receipts = []
    saw_incomplete = False
    for seq, ts, payload_json, input_hash, receipt_hash, receipt_json in rows:
        if hashlib.sha256(payload_json.encode()).hexdigest() != input_hash:
            raise ValueError(f"input hash mismatch at sequence {seq}")
        if not receipt_hash or not receipt_json:
            saw_incomplete = True
            continue
        if saw_incomplete:
            raise ValueError(f"receipt journal has a committed frame after incomplete sequence {seq}")
        receipt = json.loads(receipt_json)
        if not isinstance(receipt, dict) or not isinstance(receipt.get("events"), list) or not isinstance(receipt.get("positions"), dict):
            raise ValueError(f"malformed receipt at sequence {seq}")
        if hashlib.sha256(_canonical(receipt).encode()).hexdigest() != receipt_hash:
            raise ValueError(f"receipt hash mismatch at sequence {seq}")
        payload = json.loads(payload_json)
        observed_at = next((value.get("observation", {}).get("observed_at_utc") for value in payload.values() if isinstance(value, dict) and isinstance(value.get("observation"), dict) and value["observation"].get("observed_at_utc")), None) if isinstance(payload, dict) else None
        receipts.append({"sequence": int(seq), "ts": float(ts), "input_hash": input_hash, "receipt_hash": receipt_hash, "observed_at_utc": observed_at, "receipt": receipt})
    return identity, receipts


def _settings_payload(manifest: Mapping[str, Any], key: str, run_dir: str | Path | None) -> dict:
    value = manifest.get(key)
    path_key = key + "_file"
    if value is None and manifest.get(path_key):
        if run_dir is None:
            raise ValueError(f"run_dir is required to read {path_key}")
        relative = Path(str(manifest[path_key]))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"{path_key} must be a safe relative path")
        value = json.loads((Path(run_dir) / relative).read_text())
    if not isinstance(value, dict):
        raise ValueError(f"manifest must provide {key} or {path_key}")
    return value


def _store_pair_metadata(db: ScenarioDB, manifest: Mapping[str, Any], settings_hash: str, code_hash: str) -> None:
    db.connection.execute("""CREATE TABLE IF NOT EXISTS paper_forward_run_metadata (
        run_id TEXT PRIMARY KEY REFERENCES runs(run_id), epoch_id TEXT NOT NULL,
        parent_settings_sha256 TEXT NOT NULL, settings_sha256 TEXT NOT NULL, code_snapshot_sha256 TEXT NOT NULL,
        candidate_overrides_json TEXT NOT NULL, isolation_overrides_json TEXT NOT NULL,
        paper_mask_release_json TEXT NOT NULL, context_json TEXT NOT NULL
    )""")
    db.connection.execute("""CREATE TABLE IF NOT EXISTS paper_run_input_frames (
        run_id TEXT NOT NULL REFERENCES runs(run_id), sequence INTEGER NOT NULL,
        source_ts TEXT NOT NULL, input_hash TEXT NOT NULL,
        PRIMARY KEY(run_id,sequence)
    )""")
    db.connection.execute("""CREATE VIEW IF NOT EXISTS paper_paired_readiness AS
        SELECT a.run_id AS run_a,b.run_id AS run_b,a.sym_side,
               ma.epoch_id,ma.parent_settings_sha256 AS parent_settings_hash,
               CASE WHEN ma.candidate_overrides_json<>mb.candidate_overrides_json THEN 1 ELSE 0 END AS candidate_config_differs,
               CASE WHEN ma.parent_settings_sha256=mb.parent_settings_sha256
                       AND ma.isolation_overrides_json=mb.isolation_overrides_json
                       AND ma.paper_mask_release_json=mb.paper_mask_release_json
                       AND ma.context_json=mb.context_json THEN 1 ELSE 0 END AS shared_context_matches,
               (SELECT COUNT(*) FROM paper_run_input_frames fa WHERE fa.run_id=a.run_id) AS frames_a,
               (SELECT COUNT(*) FROM paper_run_input_frames fb WHERE fb.run_id=b.run_id) AS frames_b,
               (SELECT COUNT(*) FROM paper_run_input_frames fa JOIN paper_run_input_frames fb
                    ON fb.run_id=b.run_id AND fb.sequence=fa.sequence AND fb.source_ts=fa.source_ts AND fb.input_hash=fa.input_hash
                    WHERE fa.run_id=a.run_id) AS identical_input_frames,
               CASE WHEN (SELECT COUNT(*) FROM paper_run_input_frames fa WHERE fa.run_id=a.run_id)>0
                       AND (SELECT COUNT(*) FROM paper_run_input_frames fa WHERE fa.run_id=a.run_id)=(SELECT COUNT(*) FROM paper_run_input_frames fb WHERE fb.run_id=b.run_id)
                       AND (SELECT COUNT(*) FROM paper_run_input_frames fa WHERE fa.run_id=a.run_id)=(SELECT COUNT(*) FROM paper_run_input_frames fa JOIN paper_run_input_frames fb
                           ON fb.run_id=b.run_id AND fb.sequence=fa.sequence AND fb.source_ts=fa.source_ts AND fb.input_hash=fa.input_hash WHERE fa.run_id=a.run_id)
                    THEN 1 ELSE 0 END AS identical_inputs_ready,
               (SELECT status FROM run_current_status WHERE run_id=a.run_id) AS status_a,
               (SELECT status FROM run_current_status WHERE run_id=b.run_id) AS status_b,
               (SELECT COUNT(*) FROM trades WHERE run_id=a.run_id) AS lifecycles_a,
               (SELECT COUNT(*) FROM trades WHERE run_id=b.run_id) AS lifecycles_b,
               (SELECT COUNT(*) FROM trades WHERE run_id=a.run_id AND net_return IS NOT NULL) AS known_return_lifecycles_a,
               (SELECT COUNT(*) FROM trades WHERE run_id=b.run_id AND net_return IS NOT NULL) AS known_return_lifecycles_b,
               (SELECT COUNT(*) FROM trades WHERE run_id=a.run_id AND fees IS NOT NULL) AS known_fee_lifecycles_a,
               (SELECT COUNT(*) FROM trades WHERE run_id=b.run_id AND fees IS NOT NULL) AS known_fee_lifecycles_b,
               CASE WHEN (SELECT COUNT(*) FROM trades WHERE run_id=a.run_id)>0
                       AND (SELECT COUNT(*) FROM trades WHERE run_id=b.run_id)>0
                       AND (SELECT COUNT(*) FROM trades WHERE run_id=a.run_id AND net_return IS NOT NULL AND fees IS NOT NULL)=(SELECT COUNT(*) FROM trades WHERE run_id=a.run_id)
                       AND (SELECT COUNT(*) FROM trades WHERE run_id=b.run_id AND net_return IS NOT NULL AND fees IS NOT NULL)=(SELECT COUNT(*) FROM trades WHERE run_id=b.run_id)
                    THEN 1 ELSE 0 END AS outcomes_computable
        FROM runs a JOIN runs b ON b.sym_side=a.sym_side AND b.run_id>a.run_id
        JOIN paper_forward_run_metadata ma ON ma.run_id=a.run_id
        JOIN paper_forward_run_metadata mb ON mb.run_id=b.run_id AND mb.epoch_id=ma.epoch_id
        WHERE a.evidence_kind='independent_forward' AND b.evidence_kind='independent_forward';
    """)
    row = {
        "run_id": manifest["run_id"], "epoch_id": manifest["epoch_id"],
        "parent_settings_sha256": manifest["parent_settings_sha256"], "settings_sha256": settings_hash,
        "code_snapshot_sha256": code_hash,
        "candidate_overrides_json": _canonical(manifest["candidate_overrides"]),
        "isolation_overrides_json": _canonical(manifest.get("isolation_overrides", {})),
        "paper_mask_release_json": _canonical(manifest.get("paper_mask_release", {})),
        "context_json": _canonical(manifest.get("context", {})),
    }
    db.connection.execute("INSERT OR IGNORE INTO paper_forward_run_metadata VALUES(:run_id,:epoch_id,:parent_settings_sha256,:settings_sha256,:code_snapshot_sha256,:candidate_overrides_json,:isolation_overrides_json,:paper_mask_release_json,:context_json)", row)
    existing = db.connection.execute("SELECT * FROM paper_forward_run_metadata WHERE run_id=?", (manifest["run_id"],)).fetchone()
    if existing is None or tuple(existing) != tuple(row.values()):
        raise ValueError("immutable paired-readiness run metadata conflict")


def _ensure_run(db: ScenarioDB, manifest: Mapping[str, Any], identity: Mapping[str, Any], run_dir: str | Path | None = None) -> dict:
    required = ("run_id", "epoch_id", "scenario_id", "sym_side", "host", "settings_sha256", "parent_settings_sha256", "code_sha256", "code_file_hashes", "started_at", "candidate_overrides")
    if any(not str(manifest.get(key, "")).strip() for key in required):
        raise ValueError("manifest missing required run epoch fields: " + ", ".join(key for key in required if not str(manifest.get(key, "")).strip()))
    for key, identity_key in (("scenario_id", "scenario_id"), ("settings_sha256", "settings_sha256"), ("code_sha256", "code_sha256"), ("run_id", "run_id"), ("epoch_id", "epoch_id"), ("sym_side", "sym_side")):
        if str(identity.get(identity_key, "")) != str(manifest[key]):
            raise ValueError(f"worker journal {identity_key} does not match manifest")
    planned_id = "planned-" + _digest([manifest["scenario_id"], manifest["epoch_id"]])
    planned = db.query("SELECT * FROM runs WHERE run_id=?", (planned_id,))
    if not planned:
        raise ValueError(f"scenario/run epoch is not registered in central inventory: {planned_id}")
    planned = planned[0]
    if planned["scenario_id"] != manifest["scenario_id"] or planned["sym_side"] != manifest["sym_side"] or planned["host"] != manifest["host"] or planned["settings_hash"] != manifest["parent_settings_sha256"]:
        raise ValueError("manifest does not match registered scenario/run epoch")
    base_settings = _settings_payload(manifest, "base_settings", run_dir)
    effective_settings = _settings_payload(manifest, "effective_settings", run_dir)
    scenario = db.query("SELECT parameters_json FROM scenarios WHERE scenario_id=?", (manifest["scenario_id"],))[0]
    parameters = json.loads(scenario["parameters_json"])
    if parameters.get("overrides", {}) != manifest["candidate_overrides"]:
        raise ValueError("manifest candidate_overrides do not match registered scenario")
    if hashlib.sha256(_canonical(base_settings).encode()).hexdigest() != manifest["parent_settings_sha256"]:
        raise ValueError("manifest parent_settings_sha256 does not match base_settings")
    declared = dict(base_settings)
    isolation_overrides = manifest.get("isolation_overrides", {})
    paper_mask_release = manifest.get("paper_mask_release", {})
    if not isinstance(isolation_overrides, dict) or not isinstance(paper_mask_release, (dict, list)):
        raise ValueError("isolation_overrides must be an object and paper_mask_release an object or list")
    declared.update(isolation_overrides)
    if isinstance(paper_mask_release, dict):
        declared.update(paper_mask_release)
    else:
        for key in paper_mask_release:
            if key not in base_settings:
                raise ValueError(f"paper_mask_release key absent from base settings: {key}")
            declared[key] = base_settings[key]
    for key, value in manifest["candidate_overrides"].items():
        if effective_settings.get(key) != value:
            raise ValueError(f"effective settings candidate mismatch for {key}")
    declared.update(manifest["candidate_overrides"])
    if declared != effective_settings:
        raise ValueError("effective settings contain undeclared changes outside isolation_overrides, paper_mask_release, and candidate_overrides")
    effective_hash = hashlib.sha256(_canonical(effective_settings).encode()).hexdigest()
    if effective_hash != manifest["settings_sha256"]:
        raise ValueError("manifest settings_sha256 does not match effective_settings")
    code_file_hashes = manifest["code_file_hashes"]
    if not isinstance(code_file_hashes, dict) or code_file_hashes.get("backtest_v12_engine.py") != manifest["code_sha256"]:
        raise ValueError("manifest code_file_hashes do not verify the running engine SHA")
    code_snapshot_hash = hashlib.sha256(_canonical(code_file_hashes).encode()).hexdigest()
    if manifest.get("plan_code_hash") and planned["code_hash"] != manifest["plan_code_hash"]:
        raise ValueError("manifest plan_code_hash does not match registered plan")
    existing = db.query("SELECT * FROM runs WHERE run_id=?", (manifest["run_id"],))
    if existing:
        row = existing[0]
        expected = (manifest["scenario_id"], manifest["sym_side"], manifest["host"], manifest["settings_sha256"], code_snapshot_hash, "independent_forward", manifest["started_at"])
        actual = tuple(row[k] for k in ("scenario_id", "sym_side", "host", "settings_hash", "code_hash", "evidence_kind", "started_at"))
        if actual != expected:
            raise ValueError("immutable independent-forward run identity conflict")
        _store_pair_metadata(db, manifest, effective_hash, code_snapshot_hash)
        return
    db.add_settings_snapshot(settings_hash=effective_hash, settings=effective_settings)
    db.add_source_code_snapshot(code_hash=code_snapshot_hash, file_hashes=code_file_hashes)
    db.add_run(run_id=manifest["run_id"], scenario_id=manifest["scenario_id"], sym_side=manifest["sym_side"], host=manifest["host"], settings_hash=manifest["settings_sha256"], code_hash=code_snapshot_hash, evidence_kind="independent_forward", status="RUNNING", started_at=manifest["started_at"], ended_at=None)
    _store_pair_metadata(db, manifest, effective_hash, code_snapshot_hash)
    context_id = "paper-context-" + _digest([manifest["run_id"], manifest["epoch_id"], manifest["settings_sha256"]])
    db.add_run_status(event_id=context_id, run_id=manifest["run_id"], recorded_at=manifest["started_at"], status="RUNNING", detail={"epoch_id": manifest["epoch_id"], "parent_settings_sha256": manifest["parent_settings_sha256"], "settings_sha256": manifest["settings_sha256"], "candidate_overrides": manifest["candidate_overrides"], "isolation_overrides": isolation_overrides, "paper_mask_release": paper_mask_release, "context": manifest.get("context", {})})
    return {"base_settings": base_settings, "effective_settings": effective_settings}


def _lifecycle(db: ScenarioDB, run_id: str, receipts: list[dict]) -> int:
    """Materialize only complete OPEN-to-FULL_CLOSE lifecycles from FILLED events."""
    opened: dict[str, dict] = {}
    count = 0
    for row in receipts:
        ts = _utc(row["ts"])
        positions = row["receipt"]["positions"]
        for index, event in enumerate(row["receipt"]["events"]):
            if not isinstance(event, Mapping) or str(event.get("status", "")).upper() != "FILLED" or event.get("decision_only") is True:
                continue
            key = str(event.get("position_key", ""))
            action = str(event.get("action", "")).upper()
            before, after = event.get("position_before"), event.get("position_after")
            try:
                before, after = abs(float(before)), abs(float(after))
            except (TypeError, ValueError):
                continue
            event_type = str(event.get("type", "")).lower()
            qty = event.get("executed_qty", event.get("quantity"))
            price = event.get("price")
            try:
                verified_sink_shape = event_type in {"eta", "eta_real", "webhook", "exec_now"} or (not event_type and isinstance(event.get("params"), Mapping))
                valid_execution = verified_sink_shape and float(qty) > 0 and float(price) > 0
            except (TypeError, ValueError):
                valid_execution = False
            if not valid_execution or not key:
                continue
            if before < 1e-12 <= after:
                if action in {"OPEN", "AUGMENT", "QUICK_OPEN", "REENTRY", "HEDGE_OPEN"}:
                    opened[key] = {"ts": ts, "event": dict(event), "start_sequence": row["sequence"], "start_event": index}
                continue
            if before < 1e-12 or after >= before:
                continue
            if after >= 1e-12 or action not in {"CLOSE", "FULL_CLOSE", "QUICK_CLOSE"}:
                continue
            start = opened.pop(key, None)
            if not start:
                continue
            trade_id = "paper-trade-" + _digest([run_id, start["start_sequence"], start["start_event"], key])
            open_event = start["event"]
            close_event = dict(event)
            # Snapshot the observed terminal state when available; all returns and fees remain unknown.
            terminal = positions.get(key)
            is_new = not db.query("SELECT 1 FROM trades WHERE trade_id=?", (trade_id,))
            db.add_trade(trade_id=trade_id, run_id=run_id, opened_at=start["ts"], closed_at=ts, net_return=None, fees=None,
                         payload={"position_key": key, "open_event": open_event, "close_event": close_event,
                                  "lifecycle_evidence": "ENGINE_FILLED_EVENT_AND_POSITION_ZERO_CROSSING",
                                  "terminal_position_snapshot": terminal, "net_return_status": "UNKNOWN", "fees_status": "UNKNOWN"})
            count += int(is_new)
    return count


def ingest(db: ScenarioDB, journal_path: str | Path, manifest: Mapping[str, Any], run_dir: str | Path | None = None) -> dict:
    identity, receipts = _journal(journal_path)
    terminal_status = str(manifest.get("status", "COMPLETED" if manifest.get("finished") is True else "RUNNING")).upper()
    is_terminal = terminal_status in {"COMPLETED", "STOPPED_BY_REQUEST", "STOPPED_RESOURCE_LIMIT", "FAILED"}
    with db.transaction():
        _ensure_run(db, manifest, identity, run_dir)
        if not receipts:
            if is_terminal and terminal_status != "FAILED":
                raise ValueError("finished worker manifest has no committed receipts")
            return {"run_id": manifest["run_id"], "epoch_id": manifest["epoch_id"], "receipts": 0, "execution_events": 0, "decision_rows": 0, "completed_lifecycles": 0, "status": "WAITING_FOR_RECEIPTS", "evidence_kind": "independent_forward"}
        if terminal_status in {"COMPLETED", "STOPPED_BY_REQUEST", "STOPPED_RESOURCE_LIMIT"} and _journal_frame_count(journal_path) != len(receipts):
            raise ValueError("finished worker manifest has incomplete frame receipts")
        sink = PaperSQLSink(db, str(manifest["run_id"]))
        decision_rows = 0
        raw_events = 0
        for row in receipts:
            db.connection.execute("INSERT OR IGNORE INTO paper_run_input_frames VALUES(?,?,?,?)", (manifest["run_id"], row["sequence"], _utc(row["ts"]), row["input_hash"]))
            saved_input = db.connection.execute("SELECT source_ts,input_hash FROM paper_run_input_frames WHERE run_id=? AND sequence=?", (manifest["run_id"], row["sequence"])).fetchone()
            if saved_input is None or tuple(saved_input) != (_utc(row["ts"]), row["input_hash"]):
                raise ValueError("immutable run input frame conflict")
            source_ts = _utc(row["ts"])
            if row["receipt"]["events"]:
                ids = sink.record_step(source_ts, row["receipt"])
                decision_rows += len(ids)
                raw_events += len(row["receipt"]["events"])
            else:
                db.add_forward_receipt(receipt_id=sink._receipt_id(str(manifest["run_id"]), source_ts), run_id=str(manifest["run_id"]), source_ts=source_ts, payload=row["receipt"])
            status_id = "paper-heartbeat-" + _digest([manifest["run_id"], row["sequence"], row["receipt_hash"]])
            db.add_run_status(event_id=status_id, run_id=manifest["run_id"], recorded_at=str(row.get("observed_at_utc") or _utc(row["ts"])), status="RUNNING",
                              detail={"epoch_id": manifest["epoch_id"], "sequence": row["sequence"], "source_ts": _utc(row["ts"]), "receipt_hash": row["receipt_hash"]})
        completed = _lifecycle(db, str(manifest["run_id"]), receipts)
        if is_terminal:
            status_id = "paper-finished-" + _digest([manifest["run_id"], terminal_status, receipts[-1]["receipt_hash"]])
            completion_time = _terminal_recorded_at(manifest, receipts[-1])
            db.add_run_status(event_id=status_id, run_id=manifest["run_id"], recorded_at=completion_time, status=terminal_status,
                              detail={"epoch_id": manifest["epoch_id"], "receipts": len(receipts), "source_end": _utc(receipts[-1]["ts"]), "reason": manifest.get("reason")})
    return {"run_id": manifest["run_id"], "epoch_id": manifest["epoch_id"], "receipts": len(receipts), "execution_events": raw_events, "decision_rows": decision_rows, "completed_lifecycles": completed, "status": terminal_status if is_terminal else "RUNNING", "evidence_kind": "independent_forward"}


def _journal_frame_count(path: str | Path) -> int:
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        return int(connection.execute("SELECT COUNT(*) FROM frames").fetchone()[0])


def ingest_directory(db: ScenarioDB, root: str | Path) -> list[dict]:
    """Ingest each worker's latest consistent receipt snapshot; retries are harmless."""
    root = Path(root)
    results = []
    manifest_paths = sorted(set(root.rglob("manifest.json")) | set(root.rglob("worker.result_manifest.json")))
    for manifest_path in manifest_paths:
        run_dir = manifest_path.parent
        journal_path = next((run_dir / name for name in ("receipt.snapshot.db", "receipts.snapshot.db", "receipts.sqlite3") if (run_dir / name).is_file()), None)
        if journal_path is None:
            continue
        try:
            raw = manifest_path.read_bytes()
            manifest = json.loads(raw)
            result = ingest(db, journal_path, manifest, run_dir)
            result["manifest_sha256"] = hashlib.sha256(raw).hexdigest()
            results.append(result)
        except (OSError, sqlite3.Error, ValueError, KeyError) as exc:
            results.append({"run_dir": str(run_dir), "status": "INGEST_ERROR", "error": str(exc)})
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--journal", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--watch-dir", type=Path, help="Continuously ingest worker receipt snapshots beneath this directory")
    parser.add_argument("--interval", type=float, default=5.0)
    args = parser.parse_args()
    if args.watch_dir:
        if args.journal or args.manifest or not 0.2 <= args.interval <= 300:
            parser.error("--watch-dir excludes --journal/--manifest; --interval must be 0.2..300")
        while True:
            with ScenarioDB(args.db) as db:
                results = ingest_directory(db, args.watch_dir)
            print(json.dumps({"ingests": results}, allow_nan=False), flush=True)
            time.sleep(args.interval)
    else:
        if not args.journal or not args.manifest:
            parser.error("--journal and --manifest are required unless --watch-dir is used")
        manifest = json.loads(args.manifest.read_text())
        with ScenarioDB(args.db) as db:
            result = ingest(db, args.journal, manifest, args.journal.parent)
        print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
