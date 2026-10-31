"""Append-only SQL ingestion for observational paper counterfactual actions.

The module stores source events and externally computed outcomes. It does not
score events, emit trading metrics, promote candidates, or activate switches.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any
from datetime import datetime, timezone

try:
    from tools.paper_counterfactual_scope import classify_path
except ModuleNotFoundError:
    from paper_counterfactual_scope import classify_path

try:
    from tools.paper_forward_comprehensive import canon_family
except ModuleNotFoundError:
    from paper_forward_comprehensive import canon_family


_SCHEMA = """
CREATE TABLE IF NOT EXISTS counterfactual_actions (
    action_id TEXT PRIMARY KEY,
    event_hash TEXT NOT NULL UNIQUE,
    venue TEXT,
    sym_side TEXT,
    account TEXT,
    path_name TEXT NOT NULL,
    source_ts TEXT,
    action TEXT,
    route TEXT NOT NULL,
    canonical_switch TEXT,
    source_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS counterfactual_outcomes (
    outcome_id TEXT PRIMARY KEY,
    action_id TEXT NOT NULL REFERENCES counterfactual_actions(action_id),
    computed_at TEXT NOT NULL,
    status TEXT NOT NULL,
    delta_fraction REAL,
    method TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_counterfactual_outcomes_action ON counterfactual_outcomes(action_id,computed_at);
CREATE TABLE IF NOT EXISTS counterfactual_routing (
    action_id TEXT NOT NULL REFERENCES counterfactual_actions(action_id),
    classifier_hash TEXT NOT NULL,
    route TEXT NOT NULL,
    canonical_switch TEXT,
    evidence_json TEXT NOT NULL,
    assessed_at TEXT NOT NULL,
    PRIMARY KEY(action_id,classifier_hash)
);
CREATE INDEX IF NOT EXISTS idx_counterfactual_routing_latest ON counterfactual_routing(action_id,assessed_at,classifier_hash);
DROP VIEW IF EXISTS forward_counterfactual_candidates;
DROP VIEW IF EXISTS vector_control_actions;
DROP VIEW IF EXISTS counterfactual_positive_diagnostics;
CREATE VIEW forward_counterfactual_candidates AS
    WITH ranked_routing AS (
        SELECT r.*,ROW_NUMBER() OVER(PARTITION BY action_id ORDER BY assessed_at DESC,classifier_hash DESC) AS rn
        FROM counterfactual_routing r
    ), ranked_outcomes AS (
        SELECT o.*,ROW_NUMBER() OVER(PARTITION BY action_id ORDER BY computed_at DESC,outcome_id DESC) AS rn
        FROM counterfactual_outcomes o
    )
    SELECT a.action_id,a.event_hash,a.venue,a.sym_side,a.account,a.path_name,a.source_ts,a.action,
           r.route,r.canonical_switch,a.source_json,r.classifier_hash,r.evidence_json AS routing_evidence_json,
           COALESCE(o.status,'PENDING_COMPARISON') AS comparison_status,
           o.outcome_id,o.computed_at AS outcome_computed_at,o.delta_fraction,o.method,o.payload_json AS outcome_payload_json
    FROM counterfactual_actions a JOIN ranked_routing r ON a.action_id=r.action_id AND r.rn=1
    LEFT JOIN ranked_outcomes o ON a.action_id=o.action_id AND o.rn=1
    WHERE r.route IN ('native_counterfactual','nonvector_counterfactual');
CREATE VIEW vector_control_actions AS
    WITH ranked_routing AS (
        SELECT r.*,ROW_NUMBER() OVER(PARTITION BY action_id ORDER BY assessed_at DESC,classifier_hash DESC) AS rn
        FROM counterfactual_routing r
    )
    SELECT a.action_id,a.event_hash,a.venue,a.sym_side,a.account,a.path_name,a.source_ts,a.action,
           r.route,r.canonical_switch,a.source_json,r.classifier_hash,r.evidence_json AS routing_evidence_json
    FROM counterfactual_actions a JOIN ranked_routing r ON a.action_id=r.action_id AND r.rn=1
    WHERE r.route='vector_backtest';
CREATE VIEW counterfactual_positive_diagnostics AS
    WITH ranked_routing AS (
        SELECT r.*,ROW_NUMBER() OVER(PARTITION BY action_id ORDER BY assessed_at DESC,classifier_hash DESC) AS rn
        FROM counterfactual_routing r
    ), ranked_outcomes AS (
        SELECT o.*,ROW_NUMBER() OVER(PARTITION BY action_id ORDER BY computed_at DESC,outcome_id DESC) AS rn
        FROM counterfactual_outcomes o
    )
    SELECT a.action_id,a.venue,a.sym_side,a.path_name,r.route,r.canonical_switch,
           o.outcome_id,o.computed_at,o.status,o.delta_fraction,o.method,o.payload_json,
           'diagnostic_only_not_promotion' AS interpretation
    FROM counterfactual_actions a JOIN ranked_routing r ON a.action_id=r.action_id AND r.rn=1
    JOIN ranked_outcomes o ON a.action_id=o.action_id AND o.rn=1
    WHERE r.route IN ('native_counterfactual','nonvector_counterfactual')
      AND o.status='SCORED' AND o.delta_fraction>0;
"""


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _classifier_hash() -> str:
    root = Path(__file__).resolve().parents[1]
    paths = (
        root / "tools" / "paper_counterfactual_scope.py",
        root / "tools" / "forward_parity" / "families.py",
        root / "tools" / "paper_forward_comprehensive.py",
    )
    fingerprints = {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths
    }
    return _hash(fingerprints)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_venue(value: Any) -> str:
    venue = str(value or "unknown").strip().lower()
    return "stocks" if venue in {"tradier", "stock", "equity"} else venue


def _raw_event(event: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in event.items() if key not in {"_t", "_venue", "_fam"}}


def _sym_side(event: dict[str, Any]) -> str | None:
    direct = event.get("sym_side")
    if direct:
        return str(direct)
    position_key = str(event.get("position_key") or "")
    if ":" in position_key:
        position_key = position_key.rsplit(":", 1)[1]
    if position_key.endswith(("_LONG", "_SHORT")):
        return position_key
    return None


def _schema(db) -> None:
    # executescript is outside the caller transaction and carries its own
    # explicit transaction so view refresh cannot leave a partially updated DB.
    if db.connection.in_transaction:
        raise RuntimeError("counterfactual schema setup must run outside an active transaction")
    try:
        db.connection.executescript("BEGIN IMMEDIATE;\n" + _SCHEMA + "\nCOMMIT;")
    except Exception:
        if db.connection.in_transaction:
            db.connection.rollback()
        raise


def _insert_immutable(db, table: str, key: str, row: dict[str, Any]) -> bool:
    columns = tuple(row)
    placeholders = ",".join("?" for _ in columns)
    cursor = db.connection.execute(
        f"INSERT OR IGNORE INTO {table} ({','.join(columns)}) VALUES ({placeholders})",
        tuple(row[column] for column in columns),
    )
    if cursor.rowcount:
        return True
    existing = db.connection.execute(f"SELECT * FROM {table} WHERE {key}=?", (row[key],)).fetchone()
    if existing is None or any(existing[column] != row[column] for column in columns):
        raise ValueError(f"immutable {table} conflict for {row[key]}")
    return False


def _event_rows(root: Path):
    for source in sorted((root / "data" / "paper_nonvec").glob("events_*.jsonl")):
        with source.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise ValueError(f"event must be an object: {source}:{line_number}")
                yield source, _raw_event(event)


def _event_hash(event: dict[str, Any]) -> str:
    return _hash(_raw_event(event))


def _action_row(event: dict[str, Any]) -> dict[str, Any]:
    raw = _raw_event(event)
    event_hash = _hash(raw)
    raw_path_name = str(raw.get("fn") or raw.get("reason") or "")
    venue = _canonical_venue(raw.get("venue"))
    classification = classify_path(raw_path_name, venue=venue)
    path_name = canon_family(raw_path_name)
    return {
        "action_id": event_hash,
        "event_hash": event_hash,
        "venue": venue,
        "sym_side": _sym_side(raw),
        "account": None if raw.get("account") is None else str(raw.get("account")),
        "path_name": path_name,
        "source_ts": None if raw.get("ts") is None else str(raw.get("ts")),
        "action": None if raw.get("action") is None else str(raw.get("action")),
        "route": classification["route"],
        "canonical_switch": classification["canonical_switch"],
        "source_json": _canonical(raw),
    }


def _finite_delta(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("delta_fraction must be a finite number or null")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("delta_fraction must be a finite number or null")
    return number


def _import_scores(db, root: Path, counters: dict[str, int]) -> None:
    path = root / "data" / "paper_forward_comprehensive" / "counterfactual_action_scores.json"
    if not path.exists():
        return
    payload = json.loads(path.read_text(encoding="utf-8"))
    generated_at = str(payload.get("generated_at") or "UNKNOWN")
    with db.transaction():
        for record in payload.get("records", []):
            if not isinstance(record, dict) or not isinstance(record.get("event"), dict):
                counters["invalid_score_records"] += 1
                continue
            event_hash = _event_hash(record["event"])
            action = db.connection.execute(
                "SELECT action_id FROM counterfactual_actions WHERE event_hash=?", (event_hash,)
            ).fetchone()
            if action is None:
                counters["unmatched_score_records"] += 1
                continue
            status = str(record.get("status") or "PENDING_BASELINE")
            delta = _finite_delta(record.get("delta_fraction"))
            if status.upper() == "SCORED" and delta is None:
                raise ValueError("SCORED outcome requires delta_fraction")
            if status.upper().startswith("PENDING") and delta is not None:
                raise ValueError("pending outcome requires null delta_fraction")
            method = str(record.get("method") or "UNSPECIFIED")
            computed_at = str(record.get("computed_at") or generated_at)
            # computed_at is deliberately excluded from identity: rerunning an
            # unchanged scoring result preserves its first recorded timestamp.
            stable = {key: value for key, value in record.items() if key != "computed_at"}
            outcome_id = _hash([action["action_id"], stable])
            prior = db.connection.execute(
                "SELECT computed_at FROM counterfactual_outcomes WHERE outcome_id=?", (outcome_id,)
            ).fetchone()
            if prior is not None:
                computed_at = prior["computed_at"]
            row = {
                "outcome_id": outcome_id,
                "action_id": action["action_id"],
                "computed_at": computed_at,
                "status": status,
                "delta_fraction": delta,
                "method": method,
                "payload_json": _canonical(stable),
            }
            inserted = _insert_immutable(db, "counterfactual_outcomes", "outcome_id", row)
            counters["outcomes_inserted" if inserted else "outcomes_deduplicated"] += 1


def ingest_counterfactual(db, root) -> dict[str, int]:
    """Append raw event actions and any supplied counterfactual outcomes."""
    root_path = Path(root).resolve()
    _schema(db)
    counters = {
        "actions_inserted": 0,
        "actions_deduplicated": 0,
        "outcomes_inserted": 0,
        "outcomes_deduplicated": 0,
        "unmatched_score_records": 0,
        "invalid_score_records": 0,
        "routing_revisions_inserted": 0,
        "routing_revisions_deduplicated": 0,
    }
    classifier_hash = _classifier_hash()
    with db.transaction():
        for _, event in _event_rows(root_path):
            row = _action_row(event)
            existing = db.connection.execute(
                "SELECT * FROM counterfactual_actions WHERE action_id=?", (row["action_id"],)
            ).fetchone()
            if existing is None:
                db.connection.execute(
                    "INSERT INTO counterfactual_actions (" + ",".join(row) + ") VALUES (" + ",".join("?" for _ in row) + ")",
                    tuple(row.values()),
                )
                counters["actions_inserted"] += 1
            else:
                immutable_columns = set(row) - {"route", "canonical_switch"}
                if any(existing[column] != row[column] for column in immutable_columns):
                    raise ValueError(f"immutable counterfactual_actions conflict for {row['action_id']}")
                counters["actions_deduplicated"] += 1
            routing = {
                "action_id": row["action_id"],
                "classifier_hash": classifier_hash,
                "route": row["route"],
                "canonical_switch": row["canonical_switch"],
                "evidence_json": _canonical(
                    classify_path(
                        json.loads(row["source_json"]).get("fn")
                        or json.loads(row["source_json"]).get("reason")
                        or "",
                        venue=row["venue"],
                    )["evidence"]
                ),
                "assessed_at": _now(),
            }
            # A repeated run of the same classifier must retain the first
            # assessment time and remain an immutable no-op.
            prior_routing = db.connection.execute(
                "SELECT assessed_at FROM counterfactual_routing WHERE action_id=? AND classifier_hash=?",
                (row["action_id"], classifier_hash),
            ).fetchone()
            if prior_routing is not None:
                routing["assessed_at"] = prior_routing["assessed_at"]
            columns = tuple(routing)
            cursor = db.connection.execute(
                f"INSERT OR IGNORE INTO counterfactual_routing ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                tuple(routing[column] for column in columns),
            )
            inserted_route = cursor.rowcount > 0
            if not inserted_route:
                prior = db.connection.execute(
                    "SELECT * FROM counterfactual_routing WHERE action_id=? AND classifier_hash=?",
                    (row["action_id"], classifier_hash),
                ).fetchone()
                if prior is None or any(prior[column] != routing[column] for column in columns):
                    raise ValueError(f"immutable counterfactual_routing conflict for {row['action_id']}:{classifier_hash}")
            counters["routing_revisions_inserted" if inserted_route else "routing_revisions_deduplicated"] += 1
    _import_scores(db, root_path, counters)
    return counters
