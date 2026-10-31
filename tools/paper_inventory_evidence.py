"""Import existing paper inventories and observations into ScenarioDB."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from tools.paper_scenario_db import ScenarioDB
except ModuleNotFoundError:
    from paper_scenario_db import ScenarioDB


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _id(*parts: Any) -> str:
    return hashlib.sha256(_canonical(parts).encode()).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _side(sym_side: str) -> str:
    suffix = str(sym_side).rsplit("_", 1)[-1].upper()
    return suffix if suffix in {"LONG", "SHORT"} else "UNKNOWN"


def _read_json(path: Path) -> tuple[Any, str]:
    content = path.read_bytes()
    return json.loads(content), hashlib.sha256(content).hexdigest()


def _record_import(db: ScenarioDB, source_path: str, source_hash: str, computed_at: str | None) -> bool:
    import_id = _id("import", source_path, source_hash)
    if db.query("SELECT 1 FROM imports WHERE import_id=?", (import_id,)):
        return False
    db.record_import(import_id=import_id, source_path=source_path, source_hash=source_hash,
                     imported_at=_now(), source_computed_at=computed_at)
    return True


def _path_row(db: ScenarioDB, source_path: str, path_name: str,
              venue: str, kind: str, metadata: dict[str, Any]) -> tuple[str, str]:
    switch_name = metadata.get("gate_knob") or "UNRECORDED"
    path_id = _id("paper-path", source_path, path_name, venue, kind, metadata)
    db.add_path(path_id=path_id, venue=venue or "unknown", path_name=path_name,
                switch_name=switch_name, classification="observational_candidate",
                provenance={"source_path": source_path,
                            "runtime_path_name": path_name, "metadata": metadata,
                            "configuration_status": "UNRECORDED"})
    params = {"evidence_type": "observational", "source_path": source_path,
              "runtime_path_name": path_name, "venue": venue or "unknown", "kind": kind,
              "settings": "UNRECORDED", "code_version": "UNRECORDED",
              "actual_test_configuration": "UNRECORDED"}
    scenario_id = _id("paper-scenario", path_id, params)
    if not db.query("SELECT 1 FROM scenarios WHERE scenario_id=?", (scenario_id,)):
        db.add_scenario(scenario_id=scenario_id, path_id=path_id, side="UNKNOWN", parent_scenario_id=None,
                        parameters=params, created_at=_now())
    return path_id, scenario_id


def _add_result(db: ScenarioDB, *, source_path: str, source_hash: str, source_computed_at: str,
                path_id: str, scenario_id: str, path_name: str, venue: str, kind: str,
                sym_side: str, metric_name: str, metric_value: float | None, raw: dict[str, Any],
                evidence_kind: str = "observational") -> None:
    side = _side(sym_side)
    run_id = _id("paper-run", source_path, source_hash, path_name, venue, kind, sym_side, metric_name)
    if not db.query("SELECT 1 FROM runs WHERE run_id=?", (run_id,)):
        db.add_run(run_id=run_id, scenario_id=scenario_id, sym_side=sym_side, host="UNRECORDED",
                   settings_hash="UNRECORDED", code_hash="UNRECORDED", evidence_kind=evidence_kind,
                   status="OBSERVATIONAL_ONLY", started_at=source_computed_at or "UNKNOWN", ended_at=None)
    db.add_result(result_id=_id("paper-result", run_id, source_computed_at, raw), run_id=run_id,
                  computed_at=source_computed_at, source_start=None, source_end=None,
                  metric_name=f"observational_{metric_name}", metric_value=metric_value, unit="fraction",
                  verification="OBSERVATIONAL_ONLY_UNVERIFIED", payload={
                      "source_path": source_path, "source_hash": source_hash,
                      "source_computed_at": source_computed_at, "path_id": path_id,
                      "scenario_id": scenario_id, "sym_side": sym_side, "side": side,
                      "runtime_path_name": path_name, "raw_record": raw,
                      "settings": "UNRECORDED", "code_version": "UNRECORDED",
                      "execution_status": "not_an_independent_trade"})


def _import_comprehensive(db: ScenarioDB, root: Path, counters: dict[str, int]) -> None:
    base = root / "data" / "paper_forward_comprehensive"
    inventory_path = base / "inventory.json"
    inventory, inventory_hash = _read_json(inventory_path)
    inventory_rel = inventory_path.relative_to(root).as_posix()
    with db.transaction():
        if _record_import(db, inventory_rel, inventory_hash, None):
            for path_name, metadata in sorted(inventory.items()):
                if path_name in {"_meta", "__coverage_workbook__"}:
                    continue
                kind = str(metadata.get("kind") or "UNRECORDED")
                venue = str(metadata.get("venue") or "unknown")
                _path_row(db, inventory_rel, path_name, venue, kind, metadata)
                counters["inventory_paths"] += 1
        else:
            counters["skipped_sources"] += 1

    scores_path = base / "scores.json"
    scores, scores_hash = _read_json(scores_path)
    scores_rel = scores_path.relative_to(root).as_posix()
    generated_at = scores.get("generated_at")
    with db.transaction():
        if not _record_import(db, scores_rel, scores_hash, generated_at):
            counters["skipped_sources"] += 1
            return
        for row in scores.get("per_sym", []):
            path_name = str(row["path"])
            metadata = inventory.get(path_name, {})
            venue, kind = str(row.get("venue") or metadata.get("venue") or "unknown"), str(row.get("kind") or metadata.get("kind") or "UNRECORDED")
            path_id, scenario_id = _path_row(db, inventory_rel, path_name, venue, kind, metadata)
            _add_result(db, source_path=scores_rel, source_hash=scores_hash, source_computed_at=generated_at or "UNKNOWN",
                        path_id=path_id, scenario_id=scenario_id, path_name=path_name, venue=venue, kind=kind,
                        sym_side=str(row.get("sym_side") or "UNRECORDED"), metric_name="mean_delta",
                        metric_value=row.get("mean_delta"), raw=row)
            counters["per_sym_results"] += 1
        for row in scores.get("paths", []):
            path_name = str(row["path"])
            metadata = inventory.get(path_name, {})
            venue, kind = str(row.get("venue") or metadata.get("venue") or "unknown"), str(row.get("kind") or metadata.get("kind") or "UNRECORDED")
            path_id, scenario_id = _path_row(db, inventory_rel, path_name, venue, kind, metadata)
            raw = dict(row)
            raw.setdefault("sym_side", "__POOLED__")
            _add_result(db, source_path=scores_rel, source_hash=scores_hash, source_computed_at=generated_at or "UNKNOWN",
                        path_id=path_id, scenario_id=scenario_id, path_name=path_name, venue=venue, kind=kind,
                        sym_side="__POOLED__", metric_name="pooled_delta", metric_value=row.get("pooled_delta"), raw=raw)
            counters["path_summary_results"] += 1


def _import_shadow(db: ScenarioDB, root: Path, counters: dict[str, int]) -> None:
    source = root / "data" / "paper_shadow" / "shadow.db"
    source_rel = source.relative_to(root).as_posix()
    connection = sqlite3.connect(f"file:{source.resolve()}?mode=ro", uri=True, timeout=30.0)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN")
        rows = [dict(row) for row in connection.execute("SELECT rowid AS _source_rowid,* FROM decisions ORDER BY rowid")]
        schema = [dict(row) for row in connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='decisions'")]
        source_hash = hashlib.sha256(_canonical({"schema": schema, "rows": rows}).encode()).hexdigest()
        with db.transaction():
            if not _record_import(db, source_rel, source_hash, None):
                counters["skipped_sources"] += 1
                return
            path_cache: dict[tuple[str, str, str], tuple[str, str]] = {}
            run_cache: set[str] = set()
            run_first_ts: dict[str, str] = {}
            for row in rows:
                sym_side = str(row.get("sym_side") or "UNRECORDED")
                kind = str(row.get("kind") or "UNRECORDED")
                leg = str(row.get("leg") or "UNRECORDED")
                action = str(row.get("action") or "UNRECORDED")
                cache_key = (str(row.get("venue") or "unknown"), kind, leg, action)
                run_id = _id("shadow-run", source_rel, *cache_key, sym_side)
                stamp = _timestamp_iso(row.get("ts"))
                if stamp != "UNKNOWN" and (run_id not in run_first_ts or stamp < run_first_ts[run_id]):
                    run_first_ts[run_id] = stamp
            for row in rows:
                sym_side = str(row.get("sym_side") or "UNRECORDED")
                venue, kind = str(row.get("venue") or "unknown"), str(row.get("kind") or "UNRECORDED")
                leg, action = str(row.get("leg") or "UNRECORDED"), str(row.get("action") or "UNRECORDED")
                path_name = f"TIMING_SHADOW:{kind}:{leg}:{action}"
                cache_key = (venue, kind, leg, action)
                if cache_key not in path_cache:
                    path_cache[cache_key] = _path_row(db, source_rel, path_name, venue, kind,
                                                       {"source_type": "timing_shadow_decision", "leg": leg,
                                                        "kind": kind, "action": action})
                path_id, scenario_id = path_cache[cache_key]
                run_id = _id("shadow-run", source_rel, *cache_key, sym_side)
                if run_id not in run_cache and not db.query("SELECT 1 FROM runs WHERE run_id=?", (run_id,)):
                    db.add_run(run_id=run_id, scenario_id=scenario_id, sym_side=sym_side,
                               host="UNRECORDED", settings_hash="UNRECORDED", code_hash="UNRECORDED",
                               evidence_kind="timing_shadow", status="OBSERVATIONAL_ONLY",
                               started_at=run_first_ts.get(run_id, "UNKNOWN"), ended_at=None)
                    run_cache.add(run_id)
                raw = dict(row)
                source_ts = str(raw.get("ts"))
                row_hash = hashlib.sha256(_canonical(raw).encode()).hexdigest()
                decision_id = _id("shadow-decision", source_rel, row["_source_rowid"], row_hash)
                if not db.query("SELECT 1 FROM decisions WHERE decision_id=?", (decision_id,)):
                    db.add_decision(decision_id=decision_id, run_id=run_id, source_ts=source_ts,
                                    action=str(raw.get("action") or "UNRECORDED"),
                                    payload={"source_path": source_rel, "source_row_hash": row_hash,
                                             "source_rowid": row["_source_rowid"], "raw_record": raw,
                                             "settings": "UNRECORDED", "code_version": "UNRECORDED",
                                             "execution_status": "decision_only"})
                    counters["shadow_decisions"] += 1
    finally:
        connection.rollback()
        connection.close()


def ingest_evidence(db: ScenarioDB, root: str | Path) -> dict[str, int]:
    """Append/idempotently ingest comprehensive paper scores and timing-shadow decisions."""
    root_path = Path(root).resolve()
    counters = {"inventory_paths": 0, "per_sym_results": 0, "path_summary_results": 0,
                "shadow_decisions": 0, "skipped_sources": 0}
    _import_comprehensive(db, root_path, counters)
    _import_shadow(db, root_path, counters)
    return counters


def _timestamp_iso(value: Any) -> str:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc).isoformat()
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
        except ValueError:
            pass
    return "UNKNOWN"
