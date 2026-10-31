"""Ingest isolated paper worker receipt journals into the scenario inventory.

This module records immutable engine events, position snapshots, lifecycle
boundaries, and run heartbeats. It does not calculate strategy metrics.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
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


def _journal(path: str | Path) -> tuple[dict, list[dict]]:
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        meta = dict(connection.execute("SELECT key,value FROM metadata"))
        if "identity" not in meta:
            raise ValueError("receipt journal has no identity metadata")
        identity = json.loads(meta["identity"])
        rows = connection.execute(
            "SELECT sequence,closed_ts,input_hash,receipt_hash,receipt_json FROM frames ORDER BY sequence"
        ).fetchall()
    if not rows:
        raise ValueError("receipt journal contains no frames")
    receipts = []
    for seq, ts, input_hash, receipt_hash, receipt_json in rows:
        if not receipt_hash or not receipt_json:
            raise ValueError(f"receipt journal has an uncommitted frame at sequence {seq}")
        receipt = json.loads(receipt_json)
        if not isinstance(receipt, dict) or not isinstance(receipt.get("events"), list) or not isinstance(receipt.get("positions"), dict):
            raise ValueError(f"malformed receipt at sequence {seq}")
        if hashlib.sha256(_canonical(receipt).encode()).hexdigest() != receipt_hash:
            raise ValueError(f"receipt hash mismatch at sequence {seq}")
        receipts.append({"sequence": int(seq), "ts": float(ts), "input_hash": input_hash, "receipt_hash": receipt_hash, "receipt": receipt})
    return identity, receipts


def _ensure_run(db: ScenarioDB, manifest: Mapping[str, Any], identity: Mapping[str, Any]) -> None:
    required = ("run_id", "epoch_id", "scenario_id", "sym_side", "host", "settings_sha256", "code_sha256", "started_at")
    if any(not str(manifest.get(key, "")).strip() for key in required):
        raise ValueError("manifest missing required run epoch fields: " + ", ".join(key for key in required if not str(manifest.get(key, "")).strip()))
    for key, identity_key in (("scenario_id", "scenario_id"), ("settings_sha256", "settings_sha256"), ("code_sha256", "code_sha256"), ("run_id", "run_id"), ("epoch_id", "epoch_id")):
        if str(identity.get(identity_key, "")) != str(manifest[key]):
            raise ValueError(f"worker journal {identity_key} does not match manifest")
    planned_id = "planned-" + _digest([manifest["scenario_id"], manifest["epoch_id"]])
    planned = db.query("SELECT * FROM runs WHERE run_id=?", (planned_id,))
    if not planned:
        raise ValueError(f"scenario/run epoch is not registered in central inventory: {planned_id}")
    planned = planned[0]
    if planned["scenario_id"] != manifest["scenario_id"] or planned["sym_side"] != manifest["sym_side"] or planned["host"] != manifest["host"] or planned["settings_hash"] != manifest["settings_sha256"]:
        raise ValueError("manifest does not match registered scenario/run epoch")
    if manifest.get("plan_code_hash") and planned["code_hash"] != manifest["plan_code_hash"]:
        raise ValueError("manifest plan_code_hash does not match registered plan")
    existing = db.query("SELECT * FROM runs WHERE run_id=?", (manifest["run_id"],))
    if existing:
        row = existing[0]
        expected = (manifest["scenario_id"], manifest["sym_side"], manifest["host"], manifest["settings_sha256"], manifest["code_sha256"], "independent_forward", manifest["started_at"])
        actual = tuple(row[k] for k in ("scenario_id", "sym_side", "host", "settings_hash", "code_hash", "evidence_kind", "started_at"))
        if actual != expected:
            raise ValueError("immutable independent-forward run identity conflict")
        return
    db.add_run(run_id=manifest["run_id"], scenario_id=manifest["scenario_id"], sym_side=manifest["sym_side"], host=manifest["host"], settings_hash=manifest["settings_sha256"], code_hash=manifest["code_sha256"], evidence_kind="independent_forward", status="RUNNING", started_at=manifest["started_at"], ended_at=None)


def _lifecycle(db: ScenarioDB, run_id: str, receipts: list[dict]) -> int:
    """Materialize only complete OPEN-to-FULL_CLOSE lifecycles from FILLED events."""
    opened: dict[str, dict] = {}
    count = 0
    for row in receipts:
        ts = _utc(row["ts"])
        positions = row["receipt"]["positions"]
        for index, event in enumerate(row["receipt"]["events"]):
            if not isinstance(event, Mapping) or str(event.get("status", "")).upper() != "FILLED":
                continue
            key = str(event.get("position_key", ""))
            action = str(event.get("action", "")).upper()
            before, after = event.get("position_before"), event.get("position_after")
            try:
                before, after = abs(float(before)), abs(float(after))
            except (TypeError, ValueError):
                continue
            if not key or before < 1e-12 <= after:
                if key:
                    opened[key] = {"ts": ts, "event": dict(event), "start_sequence": row["sequence"], "start_event": index}
                continue
            if before < 1e-12 or after >= before:
                continue
            if after >= 1e-12:
                continue
            start = opened.pop(key, None)
            if not start:
                continue
            close_id = "paper-close-" + _digest([run_id, row["sequence"], index, key])
            trade_id = "paper-trade-" + _digest([run_id, start["start_sequence"], start["start_event"], key])
            open_event = start["event"]
            close_event = dict(event)
            # Snapshot the observed terminal state when available; all returns and fees remain unknown.
            terminal = positions.get(key)
            db.add_trade(trade_id=trade_id, run_id=run_id, opened_at=start["ts"], closed_at=ts, net_return=None, fees=None,
                         payload={"position_key": key, "open_event": open_event, "close_event": close_event,
                                  "lifecycle_evidence": "FILLED_EXECUTION_BEFORE_AFTER_ZERO_CROSSING",
                                  "terminal_position_snapshot": terminal, "net_return_status": "UNKNOWN", "fees_status": "UNKNOWN"})
            count += 1
    return count


def ingest(db: ScenarioDB, journal_path: str | Path, manifest: Mapping[str, Any]) -> dict:
    identity, receipts = _journal(journal_path)
    with db.transaction():
        _ensure_run(db, manifest, identity)
        sink = PaperSQLSink(db, str(manifest["run_id"]))
        inserted = 0
        for row in receipts:
            ids = sink.record_step(_utc(row["ts"]), row["receipt"])
            inserted += len(ids)
            status_id = "paper-heartbeat-" + _digest([manifest["run_id"], row["sequence"], row["receipt_hash"]])
            db.add_run_status(event_id=status_id, run_id=manifest["run_id"], recorded_at=_utc(row["ts"]), status="RUNNING",
                              detail={"epoch_id": manifest["epoch_id"], "sequence": row["sequence"], "source_ts": _utc(row["ts"]), "receipt_hash": row["receipt_hash"]})
        completed = _lifecycle(db, str(manifest["run_id"]), receipts)
        if manifest.get("finished") is True:
            status_id = "paper-finished-" + _digest([manifest["run_id"], receipts[-1]["receipt_hash"]])
            completion_time = str(manifest.get("ended_at") or _utc(receipts[-1]["ts"] + 0.000001))
            db.add_run_status(event_id=status_id, run_id=manifest["run_id"], recorded_at=completion_time, status="COMPLETED",
                              detail={"epoch_id": manifest["epoch_id"], "receipts": len(receipts), "source_end": _utc(receipts[-1]["ts"])})
    return {"run_id": manifest["run_id"], "epoch_id": manifest["epoch_id"], "receipts": len(receipts), "execution_events": inserted, "completed_lifecycles": completed, "status": "COMPLETED" if manifest.get("finished") is True else "RUNNING", "evidence_kind": "independent_forward"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    with ScenarioDB(args.db) as db:
        result = ingest(db, args.journal, manifest)
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
