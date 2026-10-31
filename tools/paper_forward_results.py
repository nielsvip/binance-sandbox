"""Ingest isolated paper worker receipt journals into the scenario inventory.

This module records immutable engine events, position snapshots, lifecycle
boundaries, and run heartbeats. It does not calculate strategy metrics.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
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


def _clock_epoch(value: Any) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("clock timestamp must be finite")
        return number
    if not isinstance(value, str) or not value:
        raise ValueError("clock timestamp is missing")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("clock timestamp must include timezone")
    return parsed.astimezone(timezone.utc).timestamp()


def _terminal_recorded_at(manifest: Mapping[str, Any], last_receipt: Mapping[str, Any]) -> str:
    candidates = [str(value) for value in (manifest.get("ended_at"), last_receipt.get("observed_at_utc"), _utc(last_receipt["ts"])) if value]
    parsed = []
    for value in candidates:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError("terminal status timestamps must include timezone")
        parsed.append(stamp.astimezone(timezone.utc))
    return (max(parsed) + timedelta(microseconds=1)).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _journal(path: str | Path) -> tuple[dict, list[dict], dict]:
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
    provenance_rows = []
    observation_clock_verified = True
    source_time_alias_verified = True
    closed_bar_status_verified = True
    source_timeframes = []
    provenance_reasons = []
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
        observations = [value.get("observation") for value in payload.values() if isinstance(value, dict) and isinstance(value.get("observation"), dict)] if isinstance(payload, dict) else []
        observation = observations[0] if observations else {}
        observed_at = observation.get("observed_at_utc")
        frame_timeframe = observation.get("frame_timeframe")
        source_timeframes.append(str(frame_timeframe) if frame_timeframe else None)
        try:
            decision_ts = _clock_epoch(observed_at)
            if abs(float(ts) - decision_ts) > 0.001:
                observation_clock_verified = False
                provenance_reasons.append("OBSERVATION_CLOCK_TIMESTAMP_MISMATCH")
        except (TypeError, ValueError, OverflowError):
            decision_ts = None
            observation_clock_verified = False
            provenance_reasons.append("OBSERVATION_CLOCK_TIMESTAMP_MISSING_OR_INVALID")
        source_provenance = observation.get("source_timeframe_provenance") if isinstance(observation.get("source_timeframe_provenance"), Mapping) else {}
        valid_statuses = {"VERIFIED_NATIVE"} if frame_timeframe == "1m" else {"NATIVE_SOURCE", "VERIFIED_NATIVE"}
        alias_verified = (
            source_provenance.get("schema_version") == 1
            and source_provenance.get("timeframe") == frame_timeframe
            and source_provenance.get("source_field") == f"timestamp_{frame_timeframe}"
            and bool(source_provenance.get("producer_source_sha256"))
            and source_provenance.get("status") in valid_statuses
            and (frame_timeframe != "1m" or source_provenance.get("receipt_verified_native_1m") is True)
        )
        source_time_alias_verified = source_time_alias_verified and bool(alias_verified)
        frame_closed_bar_verified = observation.get("closed_bar_status_verified") is True or source_provenance.get("closed_bar_status_verified") is True
        closed_bar_status_verified = closed_bar_status_verified and bool(frame_closed_bar_verified)
        provenance_rows.append({"sequence": int(seq), "frame_timeframe": frame_timeframe, "source_timeframe_provenance": source_provenance, "source_time_alias_verified": bool(alias_verified), "closed_bar_status_verified": bool(frame_closed_bar_verified), "observed_at_utc": observed_at})
        if decision_ts is not None:
            for event in receipt.get("events", []):
                if not isinstance(event, Mapping):
                    continue
                event_ts = next((event.get(key) for key in ("timestamp", "ts", "executed_at") if event.get(key) is not None), None)
                if event_ts is None:
                    continue
                try:
                    if _clock_epoch(event_ts) > decision_ts + 0.001:
                        observation_clock_verified = False
                        provenance_reasons.append("RECEIPT_EVENT_AFTER_OBSERVATION_CLOCK")
                except (TypeError, ValueError, OverflowError):
                    observation_clock_verified = False
                    provenance_reasons.append("RECEIPT_EVENT_TIMESTAMP_INVALID")
        receipts.append({"sequence": int(seq), "ts": float(ts), "input_hash": input_hash, "receipt_hash": receipt_hash, "observed_at_utc": observed_at, "receipt": receipt})
    if saw_incomplete:
        observation_clock_verified = False
        provenance_reasons.append("INCOMPLETE_FRAME_RECEIPT_TAIL")
        source_time_alias_verified = False
        closed_bar_status_verified = False
    unique_timeframes = sorted({value for value in source_timeframes if value})
    if len(unique_timeframes) != 1:
        source_time_alias_verified = False
        provenance_reasons.append("SOURCE_TIMEFRAME_INCONSISTENT_OR_MISSING")
    time_policy = "OBSERVED_AT_UTC" if observation_clock_verified and receipts else ("VERIFIED_CLOSED_BAR" if closed_bar_status_verified and receipts else "UNVERIFIED")
    journal_provenance = {"observation_clock_verified": bool(observation_clock_verified and receipts), "source_time_alias_verified": bool(source_time_alias_verified and receipts), "closed_bar_status_verified": bool(closed_bar_status_verified and receipts), "source_timeframe": unique_timeframes[0] if len(unique_timeframes) == 1 else None, "time_policy": time_policy, "frames": provenance_rows, "invalid_reasons": sorted(set(provenance_reasons))}
    return identity, receipts, journal_provenance


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
    db.connection.execute("""CREATE TABLE IF NOT EXISTS paper_forward_accounting_fills (
        run_id TEXT NOT NULL REFERENCES runs(run_id), source_frame_sequence INTEGER NOT NULL,
        source_event_index INTEGER NOT NULL, source_event_sha256 TEXT NOT NULL,
        position_key TEXT NOT NULL, action TEXT NOT NULL, side TEXT, quantity REAL NOT NULL, price REAL NOT NULL,
        gross_pnl_dollars REAL, modeled_net_pnl_dollars REAL, modeled_cost_dollars REAL,
        gross_pnl_pct REAL, modeled_net_pnl_pct REAL, modeled_round_trip_cost_pct REAL,
        cost_model TEXT NOT NULL, pnl_currency TEXT NOT NULL, source_event_json TEXT NOT NULL,
        accounting_sha256 TEXT NOT NULL,
        PRIMARY KEY(run_id,source_frame_sequence,source_event_index)
    )""")
    db.connection.execute("""CREATE TABLE IF NOT EXISTS paper_forward_run_provenance (
        run_id TEXT PRIMARY KEY REFERENCES runs(run_id), evidence_label TEXT NOT NULL,
        override_issues_json TEXT NOT NULL, source_time_semantics TEXT NOT NULL,
        source_timeframe TEXT, source_time_alias_verified INTEGER NOT NULL,
        closed_bar_status_verified INTEGER NOT NULL, coverage_gaps_json TEXT NOT NULL,
        observation_clock_verified INTEGER NOT NULL, time_policy TEXT NOT NULL,
        source_timeframe_provenance_json TEXT NOT NULL, invalid_reasons_json TEXT NOT NULL,
        input_provenance_valid INTEGER NOT NULL, performance_claim INTEGER NOT NULL
    )""")
    db.connection.execute("DROP VIEW IF EXISTS paper_paired_simulated_cost_readiness")
    db.connection.execute("""CREATE VIEW paper_paired_simulated_cost_readiness AS
        SELECT p.*, ma.code_snapshot_sha256 AS code_snapshot_sha256_a,
               mb.code_snapshot_sha256 AS code_snapshot_sha256_b,
               pa.evidence_label AS evidence_label_a,pb.evidence_label AS evidence_label_b,
               pa.input_provenance_valid AS input_provenance_valid_a,pb.input_provenance_valid AS input_provenance_valid_b,
               pa.invalid_reasons_json AS invalid_reasons_a,pb.invalid_reasons_json AS invalid_reasons_b,
               pa.source_timeframe AS source_timeframe_a,pb.source_timeframe AS source_timeframe_b,
               pa.source_time_alias_verified AS source_time_alias_verified_a,pb.source_time_alias_verified AS source_time_alias_verified_b,
               pa.closed_bar_status_verified AS closed_bar_status_verified_a,pb.closed_bar_status_verified AS closed_bar_status_verified_b,
               pa.observation_clock_verified AS observation_clock_verified_a,pb.observation_clock_verified AS observation_clock_verified_b,
               pa.time_policy AS time_policy_a,pb.time_policy AS time_policy_b,
               pa.source_timeframe_provenance_json AS source_timeframe_provenance_a,pb.source_timeframe_provenance_json AS source_timeframe_provenance_b,
               CASE WHEN p.identical_inputs_ready=1 AND p.shared_context_matches=1 AND p.candidate_config_differs=1 AND ((ma.candidate_overrides_json='{}' AND (SELECT COUNT(*) FROM json_each(mb.candidate_overrides_json))=1) OR (mb.candidate_overrides_json='{}' AND (SELECT COUNT(*) FROM json_each(ma.candidate_overrides_json))=1)) AND pa.input_provenance_valid=1 AND pb.input_provenance_valid=1
                         AND pa.source_time_alias_verified=1 AND pb.source_time_alias_verified=1
                         AND pa.time_policy=pb.time_policy AND pa.time_policy IN ('OBSERVED_AT_UTC','VERIFIED_CLOSED_BAR')
                         AND p.status_a='COMPLETED' AND p.status_b='COMPLETED'
                         AND ma.code_snapshot_sha256=mb.code_snapshot_sha256 THEN 1 ELSE 0 END AS paired_simulated_cost_ready,
               (SELECT COUNT(*) FROM paper_forward_accounting_fills fa WHERE fa.run_id=p.run_a) AS accounting_fills_a,
               (SELECT COUNT(*) FROM paper_forward_accounting_fills fb WHERE fb.run_id=p.run_b) AS accounting_fills_b,
               (SELECT COUNT(*) FROM paper_forward_accounting_fills fa WHERE fa.run_id=p.run_a AND fa.modeled_net_pnl_dollars IS NOT NULL AND fa.modeled_cost_dollars IS NOT NULL) AS known_modeled_close_costs_a,
               (SELECT COUNT(*) FROM paper_forward_accounting_fills fb WHERE fb.run_id=p.run_b AND fb.modeled_net_pnl_dollars IS NOT NULL AND fb.modeled_cost_dollars IS NOT NULL) AS known_modeled_close_costs_b,
               CASE WHEN p.identical_inputs_ready=1 AND p.shared_context_matches=1 AND p.candidate_config_differs=1 AND ((ma.candidate_overrides_json='{}' AND (SELECT COUNT(*) FROM json_each(mb.candidate_overrides_json))=1) OR (mb.candidate_overrides_json='{}' AND (SELECT COUNT(*) FROM json_each(ma.candidate_overrides_json))=1)) AND pa.input_provenance_valid=1 AND pb.input_provenance_valid=1
                         AND pa.source_time_alias_verified=1 AND pb.source_time_alias_verified=1 AND pa.time_policy=pb.time_policy AND pa.time_policy IN ('OBSERVED_AT_UTC','VERIFIED_CLOSED_BAR')
                         AND p.status_a='COMPLETED' AND p.status_b='COMPLETED'
                         AND ma.code_snapshot_sha256=mb.code_snapshot_sha256
                         AND EXISTS (SELECT 1 FROM paper_forward_accounting_fills fa WHERE fa.run_id=p.run_a AND fa.modeled_net_pnl_dollars IS NOT NULL)
                         AND EXISTS (SELECT 1 FROM paper_forward_accounting_fills fb WHERE fb.run_id=p.run_b AND fb.modeled_net_pnl_dollars IS NOT NULL)
                         AND NOT EXISTS (SELECT 1 FROM paper_forward_accounting_fills fa WHERE fa.run_id=p.run_a AND (fa.action IN ('CLOSE','FULL_CLOSE','QUICK_CLOSE','REDUCE','PROFIT_TAKE','HEDGE_CLOSE') OR (fa.action='SELL' AND fa.side='LONG') OR (fa.action='BUY' AND fa.side='SHORT')) AND (fa.gross_pnl_dollars IS NULL OR fa.modeled_net_pnl_dollars IS NULL OR fa.modeled_cost_dollars IS NULL))
                         AND NOT EXISTS (SELECT 1 FROM paper_forward_accounting_fills fb WHERE fb.run_id=p.run_b AND (fb.action IN ('CLOSE','FULL_CLOSE','QUICK_CLOSE','REDUCE','PROFIT_TAKE','HEDGE_CLOSE') OR (fb.action='SELL' AND fb.side='LONG') OR (fb.action='BUY' AND fb.side='SHORT')) AND (fb.gross_pnl_dollars IS NULL OR fb.modeled_net_pnl_dollars IS NULL OR fb.modeled_cost_dollars IS NULL))
                    THEN 1 ELSE 0 END AS diagnostic_simulated_cost_ready,
               (SELECT SUM(fa.modeled_net_pnl_dollars) FROM paper_forward_accounting_fills fa WHERE fa.run_id=p.run_a) AS diagnostic_simulated_net_pnl_dollars_a,
               (SELECT SUM(fb.modeled_net_pnl_dollars) FROM paper_forward_accounting_fills fb WHERE fb.run_id=p.run_b) AS diagnostic_simulated_net_pnl_dollars_b,
               CASE WHEN p.identical_inputs_ready=1 AND p.shared_context_matches=1 AND p.candidate_config_differs=1 AND ((ma.candidate_overrides_json='{}' AND (SELECT COUNT(*) FROM json_each(mb.candidate_overrides_json))=1) OR (mb.candidate_overrides_json='{}' AND (SELECT COUNT(*) FROM json_each(ma.candidate_overrides_json))=1)) AND pa.input_provenance_valid=1 AND pb.input_provenance_valid=1
                         AND pa.source_time_alias_verified=1 AND pb.source_time_alias_verified=1 AND pa.time_policy=pb.time_policy AND pa.time_policy IN ('OBSERVED_AT_UTC','VERIFIED_CLOSED_BAR')
                         AND p.status_a='COMPLETED' AND p.status_b='COMPLETED'
                         AND ma.code_snapshot_sha256=mb.code_snapshot_sha256
                         AND EXISTS (SELECT 1 FROM paper_forward_accounting_fills fa WHERE fa.run_id=p.run_a AND fa.modeled_net_pnl_dollars IS NOT NULL)
                         AND EXISTS (SELECT 1 FROM paper_forward_accounting_fills fb WHERE fb.run_id=p.run_b AND fb.modeled_net_pnl_dollars IS NOT NULL)
                         AND NOT EXISTS (SELECT 1 FROM paper_forward_accounting_fills fa WHERE fa.run_id=p.run_a AND (fa.action IN ('CLOSE','FULL_CLOSE','QUICK_CLOSE','REDUCE','PROFIT_TAKE','HEDGE_CLOSE') OR (fa.action='SELL' AND fa.side='LONG') OR (fa.action='BUY' AND fa.side='SHORT')) AND (fa.gross_pnl_dollars IS NULL OR fa.modeled_net_pnl_dollars IS NULL OR fa.modeled_cost_dollars IS NULL))
                         AND NOT EXISTS (SELECT 1 FROM paper_forward_accounting_fills fb WHERE fb.run_id=p.run_b AND (fb.action IN ('CLOSE','FULL_CLOSE','QUICK_CLOSE','REDUCE','PROFIT_TAKE','HEDGE_CLOSE') OR (fb.action='SELL' AND fb.side='LONG') OR (fb.action='BUY' AND fb.side='SHORT')) AND (fb.gross_pnl_dollars IS NULL OR fb.modeled_net_pnl_dollars IS NULL OR fb.modeled_cost_dollars IS NULL))
                    THEN (SELECT SUM(fb.modeled_net_pnl_dollars) FROM paper_forward_accounting_fills fb WHERE fb.run_id=p.run_b)
                       - (SELECT SUM(fa.modeled_net_pnl_dollars) FROM paper_forward_accounting_fills fa WHERE fa.run_id=p.run_a)
                    ELSE NULL END AS diagnostic_simulated_net_pnl_dollars_delta,
               'diagnostic_simulated_cost' AS outcome_label,
               CASE WHEN pa.time_policy=pb.time_policy THEN pa.time_policy ELSE 'TIME_POLICY_MISMATCH' END AS delta_time_policy
        FROM paper_paired_readiness p
        JOIN paper_forward_run_metadata ma ON ma.run_id=p.run_a
        JOIN paper_forward_run_metadata mb ON mb.run_id=p.run_b
        JOIN paper_forward_run_provenance pa ON pa.run_id=p.run_a
        JOIN paper_forward_run_provenance pb ON pb.run_id=p.run_b;
    """)
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


def _store_run_provenance(db: ScenarioDB, manifest: Mapping[str, Any], run_dir: str | Path | None, journal_provenance: Mapping[str, Any] | None = None) -> None:
    context = manifest.get("context") if isinstance(manifest.get("context"), Mapping) else {}
    journal_provenance = journal_provenance if isinstance(journal_provenance, Mapping) else {}
    result = {}
    result_unpinned = False
    if run_dir is None and manifest.get("result_file"):
        raise ValueError("run_dir is required to verify the manifest-pinned result artifact")
    if run_dir is not None:
        result_file = manifest.get("result_file")
        if result_file:
            relative = Path(str(result_file))
            if relative.is_absolute() or ".." in relative.parts or not manifest.get("result_sha256"):
                raise ValueError("manifest result artifact reference is unsafe or missing its hash")
            result_path = Path(run_dir) / relative
            result_bytes = result_path.read_bytes()
            if hashlib.sha256(result_bytes).hexdigest() != manifest["result_sha256"]:
                raise ValueError("result artifact hash does not match manifest")
            result = json.loads(result_bytes)
        elif (Path(run_dir) / "result.json").is_file():
            result_unpinned = True
            result = json.loads((Path(run_dir) / "result.json").read_text())
        for key in ("run_id", "epoch_id", "scenario_id", "sym_side", "settings_sha256", "code_sha256"):
            if result.get(key) is not None and str(result[key]) != str(manifest.get(key)):
                raise ValueError(f"result.json {key} does not match worker manifest")
    evidence_label = str(context.get("evidence_label") or manifest.get("evidence_label") or "UNVERIFIED")
    issues = manifest.get("override_issues", result.get("override_issues", [])) or []
    mismatch_keys = manifest.get("settings_mismatch_keys", result.get("settings_mismatch_keys", [])) or []
    source_time_semantics = str(manifest.get("source_time_semantics") or result.get("source_time_semantics") or "UNVERIFIED")
    source_timeframe = journal_provenance.get("source_timeframe")
    alias_verified = journal_provenance.get("source_time_alias_verified") is True
    observation_clock_verified = journal_provenance.get("observation_clock_verified") is True
    closed_bar_verified = journal_provenance.get("closed_bar_status_verified") is True
    time_policy = str(journal_provenance.get("time_policy") or "UNVERIFIED")
    gaps = manifest.get("coverage_gaps") or result.get("coverage_gaps") or []
    getter = manifest.get("candidate_getter_resolution") or {}
    candidate = manifest.get("candidate_overrides") or {}
    reasons = []
    if evidence_label.upper() == "DIAGNOSTIC_REPLAY":
        reasons.append("DIAGNOSTIC_REPLAY")
    elif evidence_label == "UNVERIFIED":
        reasons.append("EVIDENCE_LABEL_UNVERIFIED")
    if issues:
        reasons.append("OVERRIDE_ISSUES")
    if mismatch_keys:
        reasons.append("SETTINGS_MISMATCH")
    if gaps:
        reasons.append("COVERAGE_GAPS")
    if result_unpinned:
        reasons.append("RESULT_PROVENANCE_UNPINNED")
    reasons.extend(journal_provenance.get("invalid_reasons", []))
    if not source_timeframe:
        reasons.append("SOURCE_TIMEFRAME_UNVERIFIED")
    if not alias_verified:
        reasons.append("SOURCE_TIMEFRAME_ALIAS_UNVERIFIED")
    if time_policy not in {"OBSERVED_AT_UTC", "VERIFIED_CLOSED_BAR"}:
        reasons.append("TIME_POLICY_UNVERIFIED")
    if not observation_clock_verified and (not closed_bar_verified or "unverified" in source_time_semantics.lower()):
        reasons.append("CLOSED_BAR_STATUS_UNVERIFIED")
    if candidate and any(not isinstance(getter.get(key), Mapping) or getter[key].get("matches") is not True for key in candidate):
        reasons.append("CANDIDATE_GETTER_UNVERIFIED")
    values = (
        manifest["run_id"], evidence_label, _canonical(issues), source_time_semantics,
        str(source_timeframe) if source_timeframe else None, int(alias_verified), int(closed_bar_verified),
        _canonical(gaps), int(observation_clock_verified), time_policy,
        _canonical(journal_provenance.get("frames", [])), _canonical(sorted(set(reasons))), int(not reasons), int(context.get("performance_claim") is True),
    )
    db.connection.execute("""INSERT INTO paper_forward_run_provenance VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(run_id) DO UPDATE SET evidence_label=excluded.evidence_label,
        override_issues_json=excluded.override_issues_json,source_time_semantics=excluded.source_time_semantics,
        source_timeframe=excluded.source_timeframe,source_time_alias_verified=excluded.source_time_alias_verified,
        closed_bar_status_verified=excluded.closed_bar_status_verified,coverage_gaps_json=excluded.coverage_gaps_json,
        observation_clock_verified=excluded.observation_clock_verified,time_policy=excluded.time_policy,
        source_timeframe_provenance_json=excluded.source_timeframe_provenance_json,
        invalid_reasons_json=excluded.invalid_reasons_json,input_provenance_valid=excluded.input_provenance_valid,
        performance_claim=excluded.performance_claim""", values)


def _ensure_run(db: ScenarioDB, manifest: Mapping[str, Any], identity: Mapping[str, Any], run_dir: str | Path | None = None, journal_provenance: Mapping[str, Any] | None = None) -> dict:
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
    terminal_status = str(manifest.get("status", "")).upper()
    settings_mismatch_status = terminal_status == "SETTINGS_MISMATCH"
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
        if effective_settings.get(key) != value and not settings_mismatch_status:
            raise ValueError(f"effective settings candidate mismatch for {key}")
    declared.update(manifest["candidate_overrides"])
    if declared != effective_settings and not settings_mismatch_status:
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
        _store_run_provenance(db, manifest, run_dir, journal_provenance)
        return
    db.add_settings_snapshot(settings_hash=effective_hash, settings=effective_settings)
    db.add_source_code_snapshot(code_hash=code_snapshot_hash, file_hashes=code_file_hashes)
    db.add_run(run_id=manifest["run_id"], scenario_id=manifest["scenario_id"], sym_side=manifest["sym_side"], host=manifest["host"], settings_hash=manifest["settings_sha256"], code_hash=code_snapshot_hash, evidence_kind="independent_forward", status="RUNNING", started_at=manifest["started_at"], ended_at=None)
    _store_pair_metadata(db, manifest, effective_hash, code_snapshot_hash)
    _store_run_provenance(db, manifest, run_dir, journal_provenance)
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


def _same_number(left: Any, right: Any) -> bool:
    try:
        a, b = float(left), float(right)
        return abs(a - b) <= max(1e-12, abs(a) * 1e-12, abs(b) * 1e-12)
    except (TypeError, ValueError):
        return False


def _positive_number(value: Any) -> bool:
    try:
        number = float(value)
        return math.isfinite(number) and number > 0
    except (TypeError, ValueError):
        return False


def _ingest_accounting(db: ScenarioDB, manifest: Mapping[str, Any], run_dir: str | Path | None, receipts: list[dict]) -> int:
    relative_name = manifest.get("accounting_file")
    expected_hash = manifest.get("accounting_sha256")
    if not relative_name and not expected_hash:
        return 0
    if not relative_name or not expected_hash or run_dir is None:
        raise ValueError("accounting artifact requires accounting_file, accounting_sha256, and run_dir")
    relative = Path(str(relative_name))
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("accounting_file must be a safe relative path")
    path = Path(run_dir) / relative
    raw = path.read_bytes()
    actual_hash = hashlib.sha256(raw).hexdigest()
    if actual_hash != expected_hash:
        raise ValueError("accounting artifact hash does not match manifest")
    artifact = json.loads(raw)
    identity_fields = ("run_id", "epoch_id", "scenario_id", "sym_side", "settings_sha256", "code_sha256")
    for key in identity_fields:
        if str(artifact.get(key, "")) != str(manifest.get(key, "")):
            raise ValueError(f"accounting {key} does not match manifest")
    if artifact.get("schema_version") != 1 or artifact.get("cost_model") != "engine_simulated_round_trip_cost" or artifact.get("costs_are_exchange_actual") is not False:
        raise ValueError("accounting artifact must identify the engine simulated-cost model")
    if not isinstance(artifact.get("trades"), list):
        raise ValueError("accounting trades must be a list")
    source_frames = {row["sequence"]: row for row in receipts}
    declared_inputs = sorted({row["input_hash"] for row in receipts})
    if sorted(artifact.get("source_input_sha256s", [])) != declared_inputs:
        raise ValueError("accounting source input hashes do not match journal receipts")
    eligible = {}
    for row in receipts:
        for index, event in enumerate(row["receipt"]["events"]):
            if not isinstance(event, Mapping) or str(event.get("status", "")).upper() != "FILLED" or event.get("decision_only") is True:
                continue
            event_type = str(event.get("type", "")).lower()
            recording_client_fill = isinstance(event.get("params"), Mapping) and bool(event.get("position_key")) and event.get("position_before") is not None and event.get("position_after") is not None and _positive_number(event.get("price")) and _positive_number(event.get("quantity"))
            if event_type in {"eta", "webhook"} or recording_client_fill:
                eligible[(row["sequence"], index)] = (row, event)
    seen = set()
    imported = 0
    for item in artifact["trades"]:
        if not isinstance(item, Mapping):
            raise ValueError("accounting trade entry must be an object")
        try:
            reference = (int(item["source_frame_sequence"]), int(item["source_event_index"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("accounting trade lacks source event coordinates") from exc
        if reference in seen or reference not in eligible:
            raise ValueError("accounting trade has duplicate or unknown source event coordinates")
        seen.add(reference)
        frame, event = eligible[reference]
        if item.get("source_frame_input_hash") != frame["input_hash"]:
            raise ValueError("accounting source frame input hash mismatch")
        source_event = item.get("source_event")
        if not isinstance(source_event, Mapping) or _canonical(source_event) != _canonical(event):
            raise ValueError("accounting source event differs from immutable receipt event")
        source_hash = hashlib.sha256(_canonical(event).encode()).hexdigest()
        if item.get("source_event_sha256") != source_hash:
            raise ValueError("accounting source event hash mismatch")
        for field, aliases in (("price", ("price",)), ("quantity", ("quantity", "executed_qty"))):
            source_value = next((event[name] for name in aliases if name in event), None)
            if source_value is None or not _same_number(item.get(field), source_value):
                raise ValueError(f"accounting {field} differs from source execution event")
        for field in ("action", "position_key", "symbol", "position_side", "side"):
            if event.get(field) is not None and str(item.get(field, "")) != str(event[field]):
                raise ValueError(f"accounting {field} differs from source execution event")
        event_ts = next((event.get(name) for name in ("timestamp", "ts", "executed_at") if event.get(name) is not None), None)
        accounted_ts = item.get("timestamp")
        if event_ts is not None:
            if str(accounted_ts) != str(event_ts):
                raise ValueError("accounting timestamp differs from source execution event")
        elif not (_same_number(accounted_ts, frame["ts"]) or str(accounted_ts) == _utc(frame["ts"])):
            raise ValueError("accounting timestamp differs from source receipt frame")
        gross = item.get("gross_pnl_dollars")
        net = item.get("net_pnl_dollars")
        cost = item.get("fees_dollars_simulated")
        if gross is not None and net is not None:
            if cost is None or not _same_number(float(gross) - float(net), cost):
                raise ValueError("accounting modeled cost does not equal gross minus net pnl")
        elif cost is not None:
            raise ValueError("accounting cost is present without gross and net pnl")
        if gross is not None and not _same_number(gross, gross) or net is not None and not _same_number(net, net):
            raise ValueError("accounting pnl values must be finite numbers")
        cursor = db.connection.execute("""INSERT OR IGNORE INTO paper_forward_accounting_fills
            (run_id,source_frame_sequence,source_event_index,source_event_sha256,position_key,action,side,quantity,price,
             gross_pnl_dollars,modeled_net_pnl_dollars,modeled_cost_dollars,gross_pnl_pct,modeled_net_pnl_pct,
             modeled_round_trip_cost_pct,cost_model,pnl_currency,source_event_json,accounting_sha256)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (manifest["run_id"], reference[0], reference[1], source_hash, str(item.get("position_key") or ""), str(item.get("action") or "").upper(),
             item.get("position_side") or item.get("side"), float(item["quantity"]), float(item["price"]),
             gross, net, cost, item.get("gross_pnl_pct"), item.get("net_pnl_pct"), item.get("round_trip_cost_pct_simulated"),
             artifact["cost_model"], str(item.get("pnl_currency") or "USD_SIMULATED"), _canonical(event), actual_hash))
        if cursor.rowcount:
            imported += 1
        existing = db.connection.execute("SELECT source_event_sha256,gross_pnl_dollars,modeled_net_pnl_dollars,modeled_cost_dollars,accounting_sha256 FROM paper_forward_accounting_fills WHERE run_id=? AND source_frame_sequence=? AND source_event_index=?", (manifest["run_id"], reference[0], reference[1])).fetchone()
        expected = (source_hash, gross, net, cost, actual_hash)
        if existing is None or tuple(existing) != expected:
            raise ValueError("immutable accounting fill conflict")
    if seen != set(eligible):
        raise ValueError("accounting artifact omits one or more FILLED eta/webhook receipt events")
    return imported


def ingest(db: ScenarioDB, journal_path: str | Path, manifest: Mapping[str, Any], run_dir: str | Path | None = None) -> dict:
    identity, receipts, journal_provenance = _journal(journal_path)
    terminal_status = str(manifest.get("status", "COMPLETED" if manifest.get("finished") is True else "RUNNING")).upper()
    is_terminal = terminal_status in {"COMPLETED", "STOPPED_BY_REQUEST", "STOPPED_RESOURCE_LIMIT", "FAILED", "DATA_GAP", "SETTINGS_MISMATCH"}
    with db.transaction():
        _ensure_run(db, manifest, identity, run_dir, journal_provenance)
        if not receipts:
            if is_terminal and terminal_status not in {"FAILED", "DATA_GAP", "SETTINGS_MISMATCH"}:
                raise ValueError("finished worker manifest has no committed receipts")
            if terminal_status in {"DATA_GAP", "SETTINGS_MISMATCH"}:
                recorded_at = str(manifest.get("ended_at") or manifest.get("started_at") or "")
                if not recorded_at:
                    raise ValueError("empty terminal worker journal requires ended_at or started_at")
                stamp = datetime.fromisoformat(recorded_at.replace("Z", "+00:00"))
                if stamp.tzinfo is None:
                    raise ValueError("terminal status timestamps must include timezone")
                status_id = "paper-finished-" + _digest([manifest["run_id"], terminal_status, manifest.get("reason"), manifest.get("settings_mismatch_keys")])
                db.add_run_status(event_id=status_id, run_id=manifest["run_id"], recorded_at=recorded_at, status=terminal_status,
                                  detail={"epoch_id": manifest["epoch_id"], "receipts": 0, "reason": manifest.get("reason"), "settings_mismatch_keys": manifest.get("settings_mismatch_keys", [])})
                return {"run_id": manifest["run_id"], "epoch_id": manifest["epoch_id"], "receipts": 0, "execution_events": 0, "decision_rows": 0, "completed_lifecycles": 0, "accounting_fills": 0, "status": terminal_status, "evidence_kind": "independent_forward", "result_valid": False}
            return {"run_id": manifest["run_id"], "epoch_id": manifest["epoch_id"], "receipts": 0, "execution_events": 0, "decision_rows": 0, "completed_lifecycles": 0, "accounting_fills": 0, "status": "WAITING_FOR_RECEIPTS", "evidence_kind": "independent_forward"}
        if terminal_status in {"COMPLETED", "STOPPED_BY_REQUEST", "STOPPED_RESOURCE_LIMIT", "DATA_GAP", "SETTINGS_MISMATCH"} and _journal_frame_count(journal_path) != len(receipts):
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
        accounting_fills = _ingest_accounting(db, manifest, run_dir, receipts)
    return {"run_id": manifest["run_id"], "epoch_id": manifest["epoch_id"], "receipts": len(receipts), "execution_events": raw_events, "decision_rows": decision_rows, "completed_lifecycles": completed, "accounting_fills": accounting_fills, "status": terminal_status if is_terminal else "RUNNING", "evidence_kind": "independent_forward", "result_valid": bool(db.query("SELECT input_provenance_valid FROM paper_forward_run_provenance WHERE run_id=?", (manifest["run_id"],))[0][0])}


def ingest_terminal_without_journal(db: ScenarioDB, manifest: Mapping[str, Any], run_dir: str | Path | None = None) -> dict:
    status = str(manifest.get("status", "")).upper()
    if status not in {"DATA_GAP", "SETTINGS_MISMATCH"} or manifest.get("finished") is not True:
        raise ValueError("journal-free ingestion is limited to finished DATA_GAP or SETTINGS_MISMATCH runs")
    if int(manifest.get("frame_count", 0) or 0) != 0 or int(manifest.get("committed_receipts", 0) or 0) != 0:
        raise ValueError("journal-free terminal status must declare zero frames and receipts")
    if manifest.get("accounting_file") or manifest.get("accounting_sha256"):
        raise ValueError("journal-free terminal status cannot claim accounting fills")
    identity = {key: manifest[key] for key in ("run_id", "epoch_id", "scenario_id", "sym_side", "settings_sha256", "code_sha256") if key in manifest}
    if len(identity) != 6:
        raise ValueError("journal-free terminal status is missing run identity")
    with db.transaction():
        _ensure_run(db, manifest, identity, run_dir)
        recorded_at = str(manifest.get("ended_at") or manifest.get("started_at") or "")
        if not recorded_at or datetime.fromisoformat(recorded_at.replace("Z", "+00:00")).tzinfo is None:
            raise ValueError("terminal status requires timezone-aware ended_at or started_at")
        status_id = "paper-finished-" + _digest([manifest["run_id"], status, manifest.get("reason"), manifest.get("settings_mismatch_keys")])
        db.add_run_status(event_id=status_id, run_id=manifest["run_id"], recorded_at=recorded_at, status=status,
                          detail={"epoch_id": manifest["epoch_id"], "receipts": 0, "reason": manifest.get("reason"), "settings_mismatch_keys": manifest.get("settings_mismatch_keys", []), "journal_present": False})
    valid = bool(db.query("SELECT input_provenance_valid FROM paper_forward_run_provenance WHERE run_id=?", (manifest["run_id"],))[0][0])
    return {"run_id": manifest["run_id"], "epoch_id": manifest["epoch_id"], "receipts": 0, "execution_events": 0, "decision_rows": 0, "completed_lifecycles": 0, "accounting_fills": 0, "status": status, "evidence_kind": "independent_forward", "result_valid": False if status in {"DATA_GAP", "SETTINGS_MISMATCH"} else valid}


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
        try:
            raw = manifest_path.read_bytes()
            manifest = json.loads(raw)
            if journal_path is None:
                result = ingest_terminal_without_journal(db, manifest, run_dir)
            else:
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
