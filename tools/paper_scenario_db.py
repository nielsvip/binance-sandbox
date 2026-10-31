"""Append-only SQLite inventory for paper scenarios and forward evidence."""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any


def _json(value: Any) -> str:
    if isinstance(value, str):
        value = json.loads(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


class ScenarioDB:
    """Store scenario definitions and immutable observations without computing metrics."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        self.connection = sqlite3.connect(self.path, timeout=30.0, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA busy_timeout=30000")
        self._schema()

    def _schema(self) -> None:
        self.connection.executescript("""
        CREATE TABLE IF NOT EXISTS paths (
            path_id TEXT PRIMARY KEY, venue TEXT NOT NULL, path_name TEXT NOT NULL,
            switch_name TEXT NOT NULL, classification TEXT NOT NULL, provenance_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS scenarios (
            scenario_id TEXT PRIMARY KEY, path_id TEXT NOT NULL REFERENCES paths(path_id),
            side TEXT NOT NULL, parameters_json TEXT NOT NULL, parameters_hash TEXT NOT NULL,
            parent_scenario_id TEXT REFERENCES scenarios(scenario_id), created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY, scenario_id TEXT NOT NULL REFERENCES scenarios(scenario_id),
            sym_side TEXT NOT NULL, host TEXT NOT NULL, settings_hash TEXT NOT NULL,
            code_hash TEXT NOT NULL, evidence_kind TEXT NOT NULL CHECK(evidence_kind IN ('catalog_only','observational','timing_shadow','independent_forward')),
            status TEXT NOT NULL, started_at TEXT NOT NULL, ended_at TEXT
        );
        CREATE TABLE IF NOT EXISTS run_status_events (
            event_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id),
            recorded_at TEXT NOT NULL, status TEXT NOT NULL, detail_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS decisions (
            decision_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id),
            source_ts TEXT NOT NULL, action TEXT NOT NULL, payload_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS forward_receipts (
            receipt_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id),
            source_ts TEXT NOT NULL, payload_json TEXT NOT NULL, UNIQUE(run_id,source_ts)
        );
        CREATE TABLE IF NOT EXISTS trades (
            trade_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id),
            opened_at TEXT NOT NULL, closed_at TEXT, net_return REAL, fees REAL, payload_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS results (
            result_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id),
            computed_at TEXT NOT NULL, source_start TEXT, source_end TEXT, metric_name TEXT NOT NULL,
            metric_value REAL, unit TEXT, verification TEXT NOT NULL, payload_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS imports (
            import_id TEXT PRIMARY KEY, source_path TEXT NOT NULL, source_hash TEXT NOT NULL,
            imported_at TEXT NOT NULL, source_computed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS inventory_gaps (
            gap_id TEXT PRIMARY KEY, sym_side TEXT NOT NULL, reason TEXT NOT NULL,
            observed_at TEXT NOT NULL, source_hash TEXT NOT NULL, payload_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS settings_snapshots (
            settings_hash TEXT PRIMARY KEY, settings_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS source_code_snapshots (
            code_hash TEXT PRIMARY KEY, file_hashes_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tuning_notes (
            id INTEGER PRIMARY KEY, scenario_id TEXT NOT NULL REFERENCES scenarios(scenario_id),
            created_at TEXT NOT NULL, note TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_runs_scenario_sym_side ON runs(scenario_id,sym_side);
        CREATE INDEX IF NOT EXISTS idx_run_status_run_recorded_event ON run_status_events(run_id,recorded_at,event_id);
        CREATE INDEX IF NOT EXISTS idx_results_run_computed ON results(run_id,computed_at);
        CREATE INDEX IF NOT EXISTS idx_decisions_run_source_ts ON decisions(run_id,source_ts);
        CREATE INDEX IF NOT EXISTS idx_trades_run_closed ON trades(run_id,closed_at);
        CREATE INDEX IF NOT EXISTS idx_inventory_gaps_sym_source ON inventory_gaps(sym_side,source_hash);
        CREATE VIEW IF NOT EXISTS inventory_coverage AS
            SELECT p.path_id,p.venue,p.path_name,p.switch_name,p.classification,
                   COUNT(DISTINCT s.scenario_id) AS scenario_count,
                   COUNT(DISTINCT r.run_id) AS run_count,
                   COUNT(DISTINCT CASE WHEN r.evidence_kind='observational' THEN r.run_id END) AS observational_run_count,
                   COUNT(DISTINCT CASE WHEN r.evidence_kind='independent_forward' THEN r.run_id END) AS independent_forward_run_count
            FROM paths p LEFT JOIN scenarios s ON s.path_id=p.path_id
            LEFT JOIN runs r ON r.scenario_id=s.scenario_id GROUP BY p.path_id;
        CREATE VIEW IF NOT EXISTS inventory_coverage_by_path AS
            SELECT p.venue,p.path_name,p.switch_name,p.classification,
                   COUNT(DISTINCT p.path_id) AS path_version_count,
                   COUNT(DISTINCT s.scenario_id) AS scenario_count,
                   COUNT(DISTINCT r.run_id) AS run_count,
                   COUNT(DISTINCT CASE WHEN r.evidence_kind='observational' THEN r.run_id END) AS observational_run_count,
                   COUNT(DISTINCT CASE WHEN r.evidence_kind='independent_forward' THEN r.run_id END) AS independent_forward_run_count
            FROM paths p LEFT JOIN scenarios s ON s.path_id=p.path_id
            LEFT JOIN runs r ON r.scenario_id=s.scenario_id
            GROUP BY p.venue,p.path_name,p.switch_name,p.classification;
        CREATE VIEW IF NOT EXISTS results_by_evidence_kind AS
            SELECT r.evidence_kind,x.* FROM results x JOIN runs r ON r.run_id=x.run_id;
        CREATE VIEW IF NOT EXISTS observational_results AS
            SELECT * FROM results_by_evidence_kind WHERE evidence_kind='observational';
        CREATE VIEW IF NOT EXISTS independent_forward_results AS
            SELECT * FROM results_by_evidence_kind WHERE evidence_kind='independent_forward';
        CREATE VIEW IF NOT EXISTS run_current_status AS
            SELECT r.run_id,COALESCE(e.status,r.status) AS status,
                   COALESCE(e.recorded_at,r.started_at) AS recorded_at,
                   e.event_id,e.detail_json
            FROM runs r LEFT JOIN run_status_events e ON e.event_id=(
                SELECT e2.event_id FROM run_status_events e2 WHERE e2.run_id=r.run_id
                ORDER BY e2.recorded_at DESC,e2.event_id DESC LIMIT 1
            );
        DROP VIEW IF EXISTS scenario_result_inventory;
        CREATE VIEW scenario_result_inventory AS
            SELECT p.path_id,p.venue,p.path_name,p.switch_name,p.classification,
                   s.scenario_id,s.side,s.parameters_json,s.parameters_hash,s.parent_scenario_id,s.created_at AS scenario_created_at,
                   r.run_id,r.sym_side,r.host,r.settings_hash,ss.settings_json,r.code_hash,cs.file_hashes_json,
                   r.evidence_kind,r.status AS initial_status,
                   r.started_at,r.ended_at,x.result_id,x.computed_at,x.source_start,x.source_end,
                   x.metric_name,x.metric_value,x.unit,x.verification,x.payload_json AS result_payload_json
            FROM paths p LEFT JOIN scenarios s ON s.path_id=p.path_id
            LEFT JOIN runs r ON r.scenario_id=s.scenario_id
            LEFT JOIN settings_snapshots ss ON ss.settings_hash=r.settings_hash
            LEFT JOIN source_code_snapshots cs ON cs.code_hash=r.code_hash
            LEFT JOIN results x ON x.run_id=r.run_id;
        """)

    def _insert_immutable(self, table: str, row: dict[str, Any], key: str) -> str:
        allowed = {
            "paths": {"path_id", "venue", "path_name", "switch_name", "classification", "provenance_json"},
            "scenarios": {"scenario_id", "path_id", "side", "parameters_json", "parameters_hash", "parent_scenario_id", "created_at"},
            "runs": {"run_id", "scenario_id", "sym_side", "host", "settings_hash", "code_hash", "evidence_kind", "status", "started_at", "ended_at"},
            "run_status_events": {"event_id", "run_id", "recorded_at", "status", "detail_json"},
            "decisions": {"decision_id", "run_id", "source_ts", "action", "payload_json"},
            "forward_receipts": {"receipt_id", "run_id", "source_ts", "payload_json"},
            "trades": {"trade_id", "run_id", "opened_at", "closed_at", "net_return", "fees", "payload_json"},
            "results": {"result_id", "run_id", "computed_at", "source_start", "source_end", "metric_name", "metric_value", "unit", "verification", "payload_json"},
            "imports": {"import_id", "source_path", "source_hash", "imported_at", "source_computed_at"},
            "inventory_gaps": {"gap_id", "sym_side", "reason", "observed_at", "source_hash", "payload_json"},
            "settings_snapshots": {"settings_hash", "settings_json"},
            "source_code_snapshots": {"code_hash", "file_hashes_json"},
            "tuning_notes": {"id", "scenario_id", "created_at", "note"},
        }
        if table not in allowed or not set(row).issubset(allowed[table]):
            raise ValueError(f"unknown columns for {table}: {sorted(set(row) - allowed.get(table, set()))}")
        columns = tuple(row)
        if key not in row:
            raise ValueError(f"{key} is required")
        values = tuple(row[c] for c in columns)
        placeholders = ",".join("?" for _ in columns)
        item_id = row[key]
        with self.transaction():
            cursor = self.connection.execute(
                f"INSERT OR IGNORE INTO {table} ({','.join(columns)}) VALUES ({placeholders})", values
            )
            if cursor.rowcount == 0:
                existing = self.connection.execute(f"SELECT * FROM {table} WHERE {key}=?", (item_id,)).fetchone()
                if existing is None or any(existing[c] != row[c] for c in columns):
                    raise ValueError(f"immutable {table} conflict for {item_id}")
                if set(existing.keys()) != set(columns):
                    raise ValueError(f"incomplete retry for immutable {table} {item_id}")
        return item_id

    @contextmanager
    def transaction(self):
        depth = getattr(self, "_transaction_depth", 0)
        savepoint = f"scenario_db_{depth}"
        self.connection.execute("BEGIN IMMEDIATE" if depth == 0 else f"SAVEPOINT {savepoint}")
        self._transaction_depth = depth + 1
        try:
            yield
            if depth == 0:
                self.connection.commit()
            else:
                self.connection.execute(f"RELEASE SAVEPOINT {savepoint}")
        except Exception:
            if depth == 0:
                self.connection.rollback()
            else:
                self.connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                self.connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise
        finally:
            self._transaction_depth = depth

    def add_path(self, **row: Any) -> str:
        provenance = row.get("provenance", row.get("provenance_json", {}))
        row["provenance_json"] = _json(provenance)
        row.pop("provenance", None)
        return self._insert_immutable("paths", row, "path_id")

    def add_scenario(self, **row: Any) -> str:
        params = row.pop("parameters", row.get("parameters_json", {}))
        params_json = _json(params)
        row["parameters_json"] = params_json
        expected_hash = hashlib.sha256(params_json.encode()).hexdigest()
        if row.get("parameters_hash", expected_hash) != expected_hash:
            raise ValueError("parameters_hash does not match canonical parameters_json")
        row["parameters_hash"] = expected_hash
        return self._insert_immutable("scenarios", row, "scenario_id")

    def add_run(self, **row: Any) -> str:
        return self._insert_immutable("runs", row, "run_id")

    def add_run_status(self, **row: Any) -> str:
        detail = row.pop("detail", row.get("detail_json", {}))
        row["detail_json"] = _json(detail)
        return self._insert_immutable("run_status_events", row, "event_id")

    @staticmethod
    def _validate_finite(row: dict[str, Any], columns: tuple[str, ...]) -> None:
        for column in columns:
            value = row.get(column)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
                raise ValueError(f"{column} must be a finite number or None")

    def add_result(self, **row: Any) -> str:
        self._validate_finite(row, ("metric_value",))
        payload = row.pop("payload", row.get("payload_json", {}))
        row["payload_json"] = _json(payload)
        return self._insert_immutable("results", row, "result_id")

    def add_decision(self, **row: Any) -> str:
        payload = row.pop("payload", row.get("payload_json", {}))
        row["payload_json"] = _json(payload)
        return self._insert_immutable("decisions", row, "decision_id")

    def add_forward_receipt(self, **row: Any) -> str:
        payload = row.pop("payload", row.get("payload_json", {}))
        row["payload_json"] = _json(payload)
        return self._insert_immutable("forward_receipts", row, "receipt_id")

    def add_trade(self, **row: Any) -> str:
        self._validate_finite(row, ("net_return", "fees"))
        payload = row.pop("payload", row.get("payload_json", {}))
        row["payload_json"] = _json(payload)
        return self._insert_immutable("trades", row, "trade_id")

    def record_import(self, **row: Any) -> str:
        return self._insert_immutable("imports", row, "import_id")

    def add_inventory_gap(self, **row: Any) -> str:
        payload = row.pop("payload", row.get("payload_json", {}))
        row["payload_json"] = _json(payload)
        return self._insert_immutable("inventory_gaps", row, "gap_id")

    def add_settings_snapshot(self, **row: Any) -> str:
        settings = row.pop("settings", row.get("settings_json", {}))
        settings_json = _json(settings)
        expected_hash = hashlib.sha256(settings_json.encode()).hexdigest()
        if row.get("settings_hash") != expected_hash:
            raise ValueError("settings_hash does not match canonical settings_json")
        row["settings_json"] = settings_json
        return self._insert_immutable("settings_snapshots", row, "settings_hash")

    def add_source_code_snapshot(self, **row: Any) -> str:
        file_hashes = row.pop("file_hashes", row.get("file_hashes_json", {}))
        file_hashes_json = _json(file_hashes)
        expected_hash = hashlib.sha256(file_hashes_json.encode()).hexdigest()
        if row.get("code_hash") != expected_hash:
            raise ValueError("code_hash does not match canonical file_hashes_json")
        row["file_hashes_json"] = file_hashes_json
        return self._insert_immutable("source_code_snapshots", row, "code_hash")

    def add_note(self, **row: Any) -> int:
        return int(self._insert_immutable("tuning_notes", row, "id"))

    def query(self, sql: str, parameters: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        return list(self.connection.execute(sql, parameters))

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "ScenarioDB":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()
