#!/usr/bin/env python3
"""Fail-closed local coordinator for isolated paper forward workers.

Input directory contract:
  manifest.json: {"schema_version": 1, "jobs": [{"spec_file": "job.json",
    "run_id": "...", "epoch_id": "...", "scenario_id": "...",
    "sym_side": "...", "venue": "crypto|stocks", "host": "s1"}, ...]}
  each job JSON includes run_id, epoch_id, scenario_id, sym_side, venue,
  plan_code_hash, full_settings, candidate_overrides, and optional feed/run
  controls. Paths are relative to the specs directory unless absolute.

The older spec_files list is accepted, but jobs is preferred because it lets
the coordinator register expected jobs whose spec file has not arrived yet.

Every JSON spec discovered is registered, including unlisted specs and missing
manifest entries. Unlisted/malformed jobs remain visible and are never launched.
Workers are persistent --follow processes; stale feeds are worker wait states,
not reasons for the coordinator to restart them. Dead workers are marked
INTERRUPTED and are never automatically restarted.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    from .paper_fleet_budget import DEFAULT_THRESHOLDS, HOSTS_BY_VENUE, collect_observed, decide_admission, stable_host
    from .paper_forward_window import resolve_source_window, validate_window
except ImportError:
    from paper_fleet_budget import DEFAULT_THRESHOLDS, HOSTS_BY_VENUE, collect_observed, decide_admission, stable_host
    from paper_forward_window import resolve_source_window, validate_window

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_SPEC_FIELDS = ("run_id", "epoch_id", "scenario_id", "sym_side", "venue", "host", "plan_code_hash", "engine_root")
TERMINAL_STATES = {"COMPLETED", "FAILED", "INTERRUPTED", "INVALID_SPEC", "INVALID_EXPERIMENT_WINDOW", "UNMANIFESTED_SPEC", "MISSING_SPEC", "SPEC_CHANGED", "STOPPED_BY_REQUEST", "STOPPED_RESOURCE_LIMIT", "DATA_GAP", "SETTINGS_MISMATCH"}
WORKER_STATES = {"WAITING_FRESH_INPUT", "RUNNING", "RESOURCE_LIMIT_STOPPING", "FAILED", "COMPLETED", "COMPLETED_MULTI_FRAME_ENGINE_RUN", "STOPPED_BY_REQUEST", "STOPPED_RESOURCE_LIMIT", "DATA_GAP", "SETTINGS_MISMATCH"}
DEFAULT_WORKER_MEMORY_BYTES = 1024**3
DEFAULT_WORKER_DISK_BYTES = 1024**3
DEFAULT_WORKER_CPU_CORES = 0.25
MAX_SPEC_BYTES = 2 * 1024**2
MAX_MANIFEST_BYTES = 64 * 1024**2
DEFAULT_MAX_WORKERS = 64
DEFAULT_POLL_SECONDS = 60
DEFAULT_SPEC_REFRESH_SECONDS = 300


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _identity_id(spec):
    identity = {key: spec[key] for key in ("run_id", "epoch_id", "scenario_id", "sym_side", "venue", "host")}
    return _digest(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode())


def _safe_relpath(value):
    path = Path(str(value))
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("manifest spec paths must be non-empty relative paths without '..'")
    return path


def _validate_identity(spec):
    missing = [key for key in ("run_id", "epoch_id", "scenario_id", "sym_side", "venue", "host") if not isinstance(spec.get(key), str) or not spec[key].strip()]
    if missing:
        raise ValueError("manifest job missing identity fields: " + ", ".join(missing))
    symbol, separator, side = spec["sym_side"].rpartition("_")
    if not separator or not symbol or side not in {"LONG", "SHORT"}:
        raise ValueError("sym_side must end in _LONG or _SHORT")
    venue = spec["venue"].lower()
    if venue not in HOSTS_BY_VENUE:
        raise ValueError("venue must be crypto or stocks")
    if spec["host"] != stable_host(venue, symbol, side):
        raise ValueError("host disagrees with stable sym_side assignment")


def discover_specs(specs_dir):
    """Return every listed and discovered job, preserving missing/malformed rows."""
    specs_dir = Path(specs_dir).resolve(strict=True)
    if not specs_dir.is_dir():
        raise ValueError("specs directory must exist")
    manifest_path = specs_dir / "manifest.json"
    listed = []
    expected = {}
    manifest_error = None
    if manifest_path.exists():
        try:
            manifest_size = manifest_path.stat().st_size
        except OSError:
            manifest_size = MAX_MANIFEST_BYTES + 1
        if manifest_size > MAX_MANIFEST_BYTES:
            manifest_error = "manifest exceeds bounded size"
        else:
            try:
                raw_manifest = manifest_path.read_bytes()
                if len(raw_manifest) > MAX_MANIFEST_BYTES:
                    raise ValueError("manifest exceeds bounded size")
                manifest = json.loads(raw_manifest)
                if manifest.get("schema_version") != 1:
                    raise ValueError("manifest requires schema_version=1")
                if isinstance(manifest.get("jobs"), list):
                    identity_ids = set()
                    for item in manifest["jobs"]:
                        if not isinstance(item, dict) or not isinstance(item.get("spec_file"), str):
                            raise ValueError("each manifest job requires a relative spec_file")
                        relpath = _safe_relpath(item["spec_file"])
                        _validate_identity(item)
                        if relpath in expected:
                            raise ValueError("manifest contains duplicate spec_file")
                        identity = {key: item[key] for key in ("run_id", "epoch_id", "scenario_id", "sym_side", "venue", "host")}
                        if _identity_id(identity) in identity_ids:
                            raise ValueError("manifest contains duplicate scenario/sym_side epoch")
                        identity_ids.add(_identity_id(identity))
                        expected[relpath] = identity
                    listed = list(expected)
                elif isinstance(manifest.get("spec_files"), list):
                    listed = [_safe_relpath(item) for item in manifest["spec_files"]]
                else:
                    raise ValueError("manifest requires jobs list or spec_files list")
                if len(set(listed)) != len(listed):
                    raise ValueError("manifest contains duplicate spec path")
            except (ValueError, TypeError, AttributeError, json.JSONDecodeError) as exc:
                manifest_error = str(exc)
    else:
        manifest_error = "manifest.json is missing"

    listed_set = set(listed)
    found = {p.relative_to(specs_dir) for p in specs_dir.rglob("*.json") if p != manifest_path and p.is_file()}
    result = []
    for relpath in sorted(listed_set | found, key=lambda item: item.as_posix()):
        path = specs_dir / relpath
        spec_id = _digest(relpath.as_posix().encode())
        if manifest_error:
            result.append({"spec_id": spec_id, "spec_path": str(path), "status": "INVALID_SPEC", "error": manifest_error, "spec": None, "spec_sha256": None})
            continue
        if relpath not in found:
            expected_spec = expected.get(relpath)
            result.append({"spec_id": _identity_id(expected_spec) if expected_spec else spec_id, "spec_path": str(path), "status": "MISSING_SPEC", "error": "manifest entry has no JSON file", "spec": expected_spec, "spec_sha256": None})
            continue
        try:
            if not path.resolve().is_relative_to(specs_dir):
                raise ValueError("spec path resolves outside specs directory")
            raw = path.read_bytes()
            if len(raw) > MAX_SPEC_BYTES:
                raise ValueError("spec exceeds bounded size")
            spec = json.loads(raw)
            if not isinstance(spec, dict):
                raise ValueError("spec must be a JSON object")
            if relpath not in listed_set:
                raise ValueError("spec is not listed in manifest.json")
            _validate_spec(spec)
            if relpath in expected and any(spec.get(key) != value for key, value in expected[relpath].items()):
                raise ValueError("spec identity does not match manifest job")
            pending_reason = spec.get("pending_reason", "PENDING_PARAMETER_VALUES") if not spec.get("launchable", True) else None
            result.append({"spec_id": _identity_id(spec), "spec_path": str(path), "status": "PENDING_PARAMETERS" if pending_reason else "REGISTERED", "error": pending_reason, "spec": spec, "spec_sha256": _digest(raw)})
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            expected_spec = expected.get(relpath)
            result.append({"spec_id": _identity_id(expected_spec) if expected_spec else spec_id, "spec_path": str(path), "status": "UNMANIFESTED_SPEC" if relpath not in listed_set else "INVALID_SPEC", "error": str(exc), "spec": expected_spec, "spec_sha256": None})
    return result


def _validate_spec(spec):
    _validate_identity(spec)
    if "launchable" in spec and not isinstance(spec["launchable"], bool):
        raise ValueError("launchable must be boolean")
    if "is_baseline" in spec and not isinstance(spec["is_baseline"], bool):
        raise ValueError("is_baseline must be boolean")
    if not spec.get("launchable", True):
        if not isinstance(spec.get("pending_reason", "PENDING_PARAMETER_VALUES"), str):
            raise ValueError("pending_reason must be a string")
        return
    missing = [key for key in REQUIRED_SPEC_FIELDS if not isinstance(spec.get(key), str) or not spec[key].strip()]
    if missing:
        raise ValueError("missing required string fields: " + ", ".join(missing))
    sym_side = spec["sym_side"]
    symbol, side = sym_side.rsplit("_", 1)
    venue = spec["venue"].lower()
    if venue not in HOSTS_BY_VENUE:
        raise ValueError("venue must be crypto or stocks")
    if spec.get("schema_version", 1) != 1:
        raise ValueError("unsupported spec schema_version")
    if not isinstance(spec.get("full_settings"), str) or not isinstance(spec.get("candidate_overrides"), str):
        raise ValueError("full_settings and candidate_overrides paths are required")
    if not isinstance(spec.get("engine_root"), str) or not spec["engine_root"].strip():
        raise ValueError("engine_root path is required")
    release = spec.get("paper_mask_release", [])
    if not isinstance(release, list) or any(not isinstance(key, str) for key in release):
        raise ValueError("paper_mask_release must be a list of keys")
    if release and (not isinstance(spec.get("experiment_context"), dict) or not spec["experiment_context"]):
        raise ValueError("nonempty paper_mask_release requires shared experiment_context")
    for key in ("poll_seconds", "max_age_seconds"):
        if key in spec and (isinstance(spec[key], bool) or not isinstance(spec[key], (int, float)) or not math.isfinite(spec[key]) or spec[key] <= 0):
            raise ValueError(f"{key} must be a finite positive number")


def _resolve_input(specs_dir, value):
    path = Path(value)
    if not path.is_absolute():
        path = Path(specs_dir) / path
    return path.resolve()


def worker_command(spec_row, root):
    spec = spec_row["spec"]
    specs_dir = Path(spec_row["spec_path"]).parent
    symbol = spec["sym_side"].rsplit("_", 1)[0]
    venue = spec["venue"].lower()
    full_settings = _resolve_input(specs_dir, spec["full_settings"])
    candidate = _resolve_input(specs_dir, spec["candidate_overrides"])
    engine_root = _resolve_input(specs_dir, spec["engine_root"])
    if not engine_root.is_dir() or not (engine_root / "backtest_v12_engine.py").is_file() or not engine_root.is_relative_to(Path(root).resolve()):
        raise ValueError("engine_root must be a private workspace directory containing backtest_v12_engine.py")
    worker_script = engine_root / "tools" / "paper_forward_worker.py"
    if not worker_script.is_file():
        raise ValueError("engine_root must contain tools/paper_forward_worker.py")
    run_dir = Path(root) / "data" / "paper_forward_lab" / "runs" / spec_row["spec_id"]
    command = [sys.executable, str(worker_script), "--workspace-root", str(Path(root).resolve()), "--follow", "--sym-side", spec["sym_side"], "--venue", venue,
               "--manifest", spec_row["spec_path"], "--full-settings", str(full_settings),
               "--candidate-overrides", str(candidate), "--run-dir", str(run_dir)]
    command.extend(("--engine-root", str(engine_root)))
    if spec.get("source_journal"):
        source_journal = _resolve_input(specs_dir, spec["source_journal"])
        command.extend(("--source-journal", str(source_journal)))
    else:
        feed_root = Path(spec.get("feed_root", Path(root) / "data" / "paper_forward_lab" / "feed"))
        snapshot = _resolve_input(specs_dir, spec.get("snapshot", feed_root / f"{venue}.json"))
        receipt = _resolve_input(specs_dir, spec.get("receipt", feed_root / f"{venue}.receipt.json"))
        command.extend(("--snapshot", str(snapshot), "--receipt", str(receipt)))
    if "poll_seconds" in spec:
        command.extend(("--poll-seconds", str(spec["poll_seconds"])))
    if "max_age_seconds" in spec:
        command.extend(("--max-age-seconds", str(spec["max_age_seconds"])))
    return command, run_dir


def source_window_available(spec, specs_dir, root):
    """Check inclusive finite-window coverage for this sym_side without writing to the journal."""
    window = spec.get("experiment_window")
    if window is None:
        return (spec.get("execution_mode") == "continuous", "FINITE_EXPERIMENT_WINDOW_REQUIRED")
    try:
        normalized = validate_window(window, spec.get("frame_timeframe", "1m"))
    except (TypeError, ValueError):
        return False, "INVALID_EXPERIMENT_WINDOW"
    journal_value = spec.get("source_journal")
    if not isinstance(journal_value, str) or not journal_value.strip():
        return False, "SOURCE_JOURNAL_REQUIRED"
    raw_database = Path(journal_value)
    if not raw_database.is_absolute():
        raw_database = Path(specs_dir) / raw_database
    if raw_database.is_symlink():
        return False, "SOURCE_JOURNAL_INVALID"
    database = raw_database.resolve()
    if not database.is_relative_to(Path(root).resolve()):
        return False, "SOURCE_JOURNAL_OUTSIDE_WORKSPACE"
    poll_status_path = Path(f"{database}.status.json")
    poll_status = None
    try:
        if poll_status_path.is_file() and poll_status_path.stat().st_size <= 64 * 1024:
            poll_status = json.loads(poll_status_path.read_text(encoding="utf-8"))
            if not isinstance(poll_status, dict) or poll_status.get("host") != spec["host"] or poll_status.get("venue") != spec["venue"].lower():
                poll_status = None
    except (OSError, ValueError, json.JSONDecodeError):
        poll_status = None
    def poll_detail(status):
        blocked = poll_status.get("blocked_timeframes", [])
        eligible = poll_status.get("eligible_timeframes", [])
        suffix = f"timeframe={window['source_timeframe']}; blocked_timeframes={','.join(blocked)}; eligible_timeframes={','.join(eligible)}"
        return f"{status}: {suffix}" if status else f"SOURCE_PROVENANCE_GAP: {suffix}"
    if poll_status:
        provenance_gaps = poll_status.get("provenance_gaps", [])
        if window["source_timeframe"] in provenance_gaps:
            return False, poll_detail("UNVERIFIED_NATIVE_PROVENANCE")
    if not database.is_file():
        if poll_status and poll_status.get("status") in {"UNVERIFIED_NATIVE_PROVENANCE", "NO_FRESH_FRAMES", "STALE_CAPTURE", "INPUT_ERROR", "DISK_RESERVE_BLOCKED", "ARCHIVE_FULL"}:
            return False, poll_detail(poll_status["status"])
        return False, "WAITING_FOR_SOURCE_JOURNAL"
    symbol = spec["sym_side"].rsplit("_", 1)[0]
    venue = spec["venue"].lower()
    resolved = resolve_source_window(database, spec["host"], venue, symbol, normalized)
    return resolved["ready"], resolved["status"]


def cached_source_window_available(cache, spec, specs_dir, root):
    window = spec.get("experiment_window")
    journal_value = spec.get("source_journal")
    journal_path = None
    if isinstance(journal_value, str) and journal_value.strip():
        journal_path = Path(journal_value)
        if not journal_path.is_absolute():
            journal_path = Path(specs_dir) / journal_path
        journal_path = str(journal_path.resolve())
    window_data = window if isinstance(window, dict) else {}
    window_key = tuple(window_data.get(key) for key in ("window_id", "source_timeframe", "anchor", "start_source_ts", "end_source_ts", "not_before_source_ts", "duration_seconds"))
    key = (journal_path, spec.get("host"), str(spec.get("venue", "")).lower(), spec.get("sym_side"), spec.get("execution_mode"), window_key)
    if key not in cache:
        cache[key] = source_window_available(spec, specs_dir, root)
    return cache[key]


def _connect(db_path):
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, timeout=30)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute("""CREATE TABLE IF NOT EXISTS jobs (
        spec_id TEXT PRIMARY KEY, spec_path TEXT NOT NULL, spec_sha256 TEXT,
        run_id TEXT, epoch_id TEXT, scenario_id TEXT, sym_side TEXT, venue TEXT, window_id TEXT,
        assigned_host TEXT, status TEXT NOT NULL, detail TEXT, pid INTEGER,
        registered_at TEXT NOT NULL, updated_at TEXT NOT NULL, heartbeat_at TEXT,
        exit_code INTEGER, run_dir TEXT, command_json TEXT, is_baseline INTEGER NOT NULL DEFAULT 0
    )""")
    columns = {row[1] for row in connection.execute("PRAGMA table_info(jobs)")}
    if "is_baseline" not in columns:
        connection.execute("ALTER TABLE jobs ADD COLUMN is_baseline INTEGER NOT NULL DEFAULT 0")
    if "window_id" not in columns:
        connection.execute("ALTER TABLE jobs ADD COLUMN window_id TEXT")
    connection.execute("CREATE INDEX IF NOT EXISTS jobs_status_host ON jobs(status, assigned_host)")
    connection.execute("""CREATE TABLE IF NOT EXISTS events (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT, spec_id TEXT NOT NULL,
        at_utc TEXT NOT NULL, status TEXT NOT NULL, detail TEXT, pid INTEGER
    )""")
    connection.execute("""CREATE TABLE IF NOT EXISTS coordinator_heartbeat (
        host TEXT PRIMARY KEY, at_utc TEXT NOT NULL, status TEXT NOT NULL, detail TEXT
    )""")
    return connection


def coverage_summary(db_path):
    """Read-only per-assignment and status counts for an operator/central monitor."""
    path = Path(db_path).resolve(strict=True)
    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        rows = connection.execute("SELECT assigned_host,venue,status,COUNT(*),COUNT(DISTINCT scenario_id),COUNT(DISTINCT sym_side) FROM jobs GROUP BY assigned_host,venue,status").fetchall()
        distinct = connection.execute("SELECT assigned_host,COUNT(DISTINCT scenario_id),COUNT(DISTINCT sym_side) FROM jobs GROUP BY assigned_host").fetchall()
    finally:
        connection.close()
    by_host = {host: {"jobs": 0, "scenarios": 0, "sym_sides": 0, "statuses": {}, "venues": {}} for host in ("s1", "s2", "s5", "s6")}
    overall_statuses = {}
    for host, venue, status, count, scenarios, sym_sides in rows:
        group = by_host.get(host)
        if group is None:
            continue
        group["jobs"] += count
        group["statuses"][status] = group["statuses"].get(status, 0) + count
        venue_counts = group["venues"].setdefault(venue or "UNKNOWN", {})
        venue_counts[status] = venue_counts.get(status, 0) + count
        overall_statuses[status] = overall_statuses.get(status, 0) + count
    for host, scenarios, sym_sides in distinct:
        if host in by_host:
            by_host[host]["scenarios"] = scenarios
            by_host[host]["sym_sides"] = sym_sides
    return {"total_jobs": sum(row[3] for row in rows), "by_host": by_host, "statuses": overall_statuses}


def snapshot_state_database(source_path, destination_path):
    """Create an atomic consistent SQLite online backup for external collection."""
    source_path = Path(source_path).resolve(strict=True)
    destination_path = Path(destination_path).expanduser().resolve()
    if source_path == destination_path:
        raise ValueError("snapshot destination must differ from source database")
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{destination_path.name}.", suffix=".tmp", dir=destination_path.parent)
    os.close(fd)
    temporary_path = Path(temporary_name)
    source = sqlite3.connect(f"{source_path.as_uri()}?mode=ro", uri=True, timeout=30)
    try:
        destination = sqlite3.connect(temporary_path, timeout=30)
        try:
            source.backup(destination, pages=256, sleep=0.01)
            check = destination.execute("PRAGMA integrity_check").fetchone()
            if not check or check[0] != "ok":
                raise RuntimeError("SQLite snapshot integrity check failed")
            destination.commit()
        finally:
            destination.close()
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    finally:
        source.close()
    os.replace(temporary_path, destination_path)
    raw = destination_path.read_bytes()
    return {"snapshot_path": str(destination_path), "snapshot_bytes": len(raw), "snapshot_sha256": _digest(raw), "coverage": coverage_summary(destination_path)}


class SpecCache:
    """Cache spec reads; manifest metadata changes invalidate immediately."""

    def __init__(self, specs_dir, refresh_seconds=DEFAULT_SPEC_REFRESH_SECONDS):
        self.specs_dir = Path(specs_dir)
        self.refresh_seconds = float(refresh_seconds)
        self.last_refresh = 0.0
        self.manifest_signature = None
        self.rows = None

    def get(self, now=None):
        now = time.monotonic() if now is None else float(now)
        manifest_path = self.specs_dir / "manifest.json"
        try:
            stat = manifest_path.stat()
            signature = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
        except OSError:
            signature = None
        if self.rows is not None and signature == self.manifest_signature and now - self.last_refresh < self.refresh_seconds:
            return self.rows
        self.rows = discover_specs(self.specs_dir)
        self.manifest_signature = signature
        self.last_refresh = now
        return self.rows


def _upsert_discovered(connection, rows, host, now):
    fields = ("spec_id", "status", "pid", "registered_at", "spec_sha256", "detail", "spec_path", "run_id", "epoch_id", "scenario_id", "sym_side", "venue", "window_id", "assigned_host", "is_baseline")
    existing_by_id = {record[0]: dict(zip(fields, record)) for record in connection.execute("SELECT " + ",".join(fields) + " FROM jobs")}
    identity_hashes = {}
    for existing in existing_by_id.values():
        identity = (existing["run_id"], existing["epoch_id"], existing["scenario_id"], existing["sym_side"])
        if all(identity) and existing["spec_sha256"]:
            identity_hashes.setdefault(identity, {})[existing["spec_id"]] = existing["spec_sha256"]
    for row in rows:
        spec = row.get("spec") or {}
        if not spec or not row.get("spec_sha256"):
            continue
        identity = (spec.get("run_id"), spec.get("epoch_id"), spec.get("scenario_id"), spec.get("sym_side"))
        if all(identity):
            identity_hashes.setdefault(identity, {})[row["spec_id"]] = row["spec_sha256"]
    duplicate_identities = {identity for identity, versions in identity_hashes.items() if len(versions) > 1 and len(set(versions.values())) > 1}
    preserved_states = {"REGISTERED", "STARTING", "RUNNING", "WAITING_FRESH_INPUT", "WAITING_FOR_CAPACITY", "RESOURCE_BLOCKED", "WAITING_FOR_SOURCE_WINDOW", "WAITING_FOR_BASELINE", "ASSIGNED_OTHER_HOST", "COMPLETED", "COMPLETED_MULTI_FRAME_ENGINE_RUN", "FAILED", "INTERRUPTED", "SPEC_CHANGED", "PENDING_PARAMETERS", "STOPPED_BY_REQUEST", "STOPPED_RESOURCE_LIMIT", "DATA_GAP", "SETTINGS_MISMATCH", "INVALID_EXPERIMENT_WINDOW"}
    state_by_id = {}
    for row in rows:
        spec = row.get("spec") or {}
        venue = str(spec.get("venue", "")).lower() or None
        assigned = None
        if venue in HOSTS_BY_VENUE and spec.get("sym_side"):
            try:
                symbol, side = spec["sym_side"].rsplit("_", 1)
                assigned = stable_host(venue, symbol, side)
            except ValueError:
                pass
        status = row["status"]
        if status == "REGISTERED" and assigned != host:
            status = "ASSIGNED_OTHER_HOST"
        old = existing_by_id.get(row["spec_id"])
        was_pending = bool(old and old["status"] == "PENDING_PARAMETERS")
        ready_from_pending = was_pending and bool(spec.get("launchable", False))
        if spec and row.get("spec_sha256"):
            identity = (spec.get("run_id"), spec.get("epoch_id"), spec.get("scenario_id"), spec.get("sym_side"))
            if identity in duplicate_identities:
                status = "SPEC_CHANGED"
                row["error"] = "spec changed without a new run_id/epoch_id; refusing duplicate-epoch execution"
        if old and old["spec_sha256"] and row.get("spec_sha256") and old["spec_sha256"] != row["spec_sha256"] and not was_pending:
            status = "SPEC_CHANGED"
            row["error"] = "spec changed without a new run_id/epoch_id; refusing duplicate-epoch execution"
        elif status != "SPEC_CHANGED" and old and old["status"] in preserved_states and not ready_from_pending:
            status = old["status"]
        registered_at = old["registered_at"] if old else now
        detail = {"error": row.get("error")} if row.get("error") else {}
        detail["launchable"] = bool(spec.get("launchable", True))
        if not spec.get("launchable", True):
            detail["pending_reason"] = spec.get("pending_reason", "PENDING_PARAMETER_VALUES")
        detail_json = json.dumps(detail, sort_keys=True)
        window = spec.get("experiment_window")
        window_id = window.get("window_id") if isinstance(window, dict) else None
        is_baseline = int(spec.get("is_baseline", False) is True)
        if old and row.get("spec_sha256") and old["spec_sha256"] == row["spec_sha256"] and old["spec_path"] == row["spec_path"] and old["assigned_host"] == assigned and old["run_id"] == spec.get("run_id") and old["epoch_id"] == spec.get("epoch_id") and old["scenario_id"] == spec.get("scenario_id") and old["sym_side"] == spec.get("sym_side") and old["venue"] == venue and old["window_id"] == window_id and old["is_baseline"] == is_baseline and old["status"] in preserved_states and not ready_from_pending:
            state_by_id[row["spec_id"]] = (old["status"], old["detail"])
            continue
        connection.execute("""INSERT INTO jobs(spec_id,spec_path,spec_sha256,run_id,epoch_id,scenario_id,sym_side,venue,window_id,
            assigned_host,status,detail,registered_at,updated_at,is_baseline) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(spec_id) DO UPDATE SET spec_path=excluded.spec_path,spec_sha256=excluded.spec_sha256,
            run_id=excluded.run_id,epoch_id=excluded.epoch_id,scenario_id=excluded.scenario_id,
            sym_side=excluded.sym_side,venue=excluded.venue,window_id=excluded.window_id,assigned_host=excluded.assigned_host,
            status=excluded.status,detail=excluded.detail,updated_at=excluded.updated_at,is_baseline=excluded.is_baseline""",
            (row["spec_id"], row["spec_path"], row.get("spec_sha256"), spec.get("run_id"), spec.get("epoch_id"), spec.get("scenario_id"), spec.get("sym_side"), venue, window_id,
             assigned, status, detail_json, registered_at, now, is_baseline))
        state_by_id[row["spec_id"]] = (status, detail_json)
        if not old or old["status"] != status or old["detail"] != detail_json:
            connection.execute("INSERT INTO events(spec_id,at_utc,status,detail) VALUES(?,?,?,?)",
                               (row["spec_id"], now, status, detail_json))
    return state_by_id


def _pid_alive(pid, spec_path=None):
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    if spec_path and Path(f"/proc/{pid}/cmdline").exists():
        try:
            command = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ")
            if b"paper_forward_worker.py" not in command or os.fsencode(str(spec_path)) not in command:
                return False
        except OSError:
            pass
    return True


def _worker_status(run_dir):
    path = Path(run_dir) / "worker.status.json"
    try:
        if path.stat().st_size > 256 * 1024:
            return None
        row = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(row, dict) or row.get("status") not in WORKER_STATES:
            return None
        return row
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _map_worker_status(worker_status):
    if isinstance(worker_status, dict):
        worker_status = worker_status.get("status")
    if isinstance(worker_status, str) and worker_status.startswith("DATA_GAP_"):
        return "DATA_GAP"
    if isinstance(worker_status, str) and worker_status.startswith("SETTINGS_MISMATCH_"):
        return "SETTINGS_MISMATCH"
    if worker_status in {"COMPLETED", "COMPLETED_MULTI_FRAME_ENGINE_RUN"}:
        return "COMPLETED"
    if worker_status in {"DATA_GAP", "SETTINGS_MISMATCH"}:
        return worker_status
    if worker_status in {"STOPPED_BY_REQUEST", "STOPPED_RESOURCE_LIMIT"}:
        return worker_status
    if worker_status == "FAILED":
        return "FAILED"
    if worker_status == "WAITING_FRESH_INPUT":
        return "WAITING_FRESH_INPUT"
    return "RUNNING"


def resource_worker_limit(observed, venue, host, max_workers=DEFAULT_MAX_WORKERS, worker_memory_bytes=DEFAULT_WORKER_MEMORY_BYTES,
                          worker_disk_bytes=DEFAULT_WORKER_DISK_BYTES, worker_cpu_cores=DEFAULT_WORKER_CPU_CORES):
    """Fail closed at the admission gate, then bound concurrency by measured headroom."""
    decision = decide_admission(venue, host, observed)
    if not decision["admit"]:
        return 0, decision
    values = decision["observed"]
    mem_slots = math.floor((values["mem_available_bytes"] - DEFAULT_THRESHOLDS["mem_available_bytes"]) / worker_memory_bytes)
    disk_slots = math.floor((values["disk_free_bytes"] - DEFAULT_THRESHOLDS["disk_free_bytes"]) / worker_disk_bytes)
    cpu_headroom = max(0.0, values["ncpu"] * DEFAULT_THRESHOLDS["load1_per_cpu"] - values["load1"])
    cpu_slots = math.floor(cpu_headroom / worker_cpu_cores)
    return max(0, min(int(max_workers), mem_slots, disk_slots, cpu_slots)), decision


def _mark(connection, spec_id, status, now, detail=None, pid=None, exit_code=None, heartbeat=None, run_dir=None, command=None, clear_pid=False):
    rendered_detail = json.dumps(detail or {}, sort_keys=True)
    old = connection.execute("SELECT status,detail,pid,exit_code,heartbeat_at,run_dir,command_json FROM jobs WHERE spec_id=?", (spec_id,)).fetchone()
    if old is None:
        return
    rendered_command = json.dumps(command) if command else None
    new_pid = None if clear_pid else old[2] if pid is None else pid
    new_exit_code = old[3] if exit_code is None else exit_code
    event_changed = (old[0], old[1], old[2], old[3]) != (status, rendered_detail, new_pid, new_exit_code)
    new_heartbeat = old[4] if heartbeat is None else heartbeat
    new_run_dir = old[5] if run_dir is None else str(run_dir)
    new_command = old[6] if rendered_command is None else rendered_command
    metadata_changed = (old[4], old[5], old[6]) != (new_heartbeat, new_run_dir, new_command)
    if not event_changed and not metadata_changed:
        return
    connection.execute("""UPDATE jobs SET status=?,detail=?,pid=CASE WHEN ? THEN NULL ELSE COALESCE(?,pid) END,exit_code=COALESCE(?,exit_code),
        heartbeat_at=COALESCE(?,heartbeat_at),run_dir=COALESCE(?,run_dir),command_json=COALESCE(?,command_json),updated_at=? WHERE spec_id=?""",
        (status, rendered_detail, int(clear_pid), pid, exit_code, heartbeat, str(run_dir) if run_dir else None,
         rendered_command, now, spec_id))
    if event_changed:
        connection.execute("INSERT INTO events(spec_id,at_utc,status,detail,pid) VALUES(?,?,?,?,?)", (spec_id, now, status, rendered_detail, pid))


def _set_pending_state(connection, host, status, source_states, now, detail):
    marks = ",".join("?" for _ in source_states)
    rows = connection.execute(f"SELECT spec_id,status,detail FROM jobs WHERE assigned_host=? AND status IN ({marks})", (host, *source_states)).fetchall()
    rendered = json.dumps(detail or {}, sort_keys=True)
    for spec_id, prior_status, prior_detail in rows:
        if prior_status != status or prior_detail != rendered:
            _mark(connection, spec_id, status, now, detail)


def _launch(connection, row, root, now, launcher=subprocess.Popen):
    command, run_dir = worker_command(row, root)
    run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    stdout_path = run_dir / "coordinator.stdout.log"
    output = stdout_path.open("ab")
    try:
        process = launcher(command, cwd=str(root), stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                           close_fds=True, start_new_session=True)
    finally:
        output.close()
    _mark(connection, row["spec_id"], "STARTING", now, {"process_created": True, "awaiting_worker_heartbeat": True}, pid=process.pid, heartbeat=now, run_dir=run_dir, command=command)
    return process.pid


def reconcile(specs_dir, db_path, root=ROOT, host=None, once=True, dry_run=False, max_workers=DEFAULT_MAX_WORKERS, poll_seconds=DEFAULT_POLL_SECONDS,
              observed=None, launcher=subprocess.Popen, rows=None):
    if host not in {item for hosts in HOSTS_BY_VENUE.values() for item in hosts}:
        raise ValueError("host must be one of s1, s2, s5, s6")
    root = Path(root).resolve(strict=True)
    rows = discover_specs(specs_dir) if rows is None else rows
    venue = "crypto" if host in HOSTS_BY_VENUE["crypto"] else "stocks"
    observed = observed if observed is not None else collect_observed(Path(root) / "data")
    capacity, admission = resource_worker_limit(observed, venue, host, max_workers=max_workers)
    window_cache = {}
    if dry_run:
        plan = []
        dry_baselines = set()
        if Path(db_path).is_file():
            with sqlite3.connect(f"{Path(db_path).resolve().as_uri()}?mode=ro", uri=True) as state:
                dry_baselines = {(row[0], row[1], row[2]) for row in state.execute("SELECT DISTINCT sym_side,epoch_id,window_id FROM jobs WHERE assigned_host=? AND is_baseline=1 AND status='COMPLETED'", (host,))}
        for row in rows:
            spec = row.get("spec")
            status = row["status"]
            assigned = None
            if spec:
                symbol, side = spec["sym_side"].rsplit("_", 1)
                assigned = stable_host(spec["venue"], symbol, side)
                if assigned != host:
                    status = "ASSIGNED_OTHER_HOST"
                elif status == "REGISTERED":
                    ready, reason = cached_source_window_available(window_cache, spec, Path(row["spec_path"]).parent, root)
                    baseline_key = (spec.get("sym_side"), spec.get("epoch_id"), (spec.get("experiment_window") or {}).get("window_id"))
                    if not spec.get("is_baseline", False) and baseline_key not in dry_baselines:
                        status = "WAITING_FOR_BASELINE"
                    else:
                        status = ("WAITING_FOR_CAPACITY" if capacity else "RESOURCE_BLOCKED") if ready else "WAITING_FOR_SOURCE_WINDOW"
                    if reason == "INVALID_EXPERIMENT_WINDOW":
                        status = "INVALID_EXPERIMENT_WINDOW"
            command = worker_command(row, root)[0] if spec and status == "REGISTERED" else None
            plan.append({"spec_id": row["spec_id"], "spec_path": row["spec_path"], "status": status, "assigned_host": assigned, "command": command})
        return {"host": host, "mode": "DRY_RUN", "capacity": capacity, "admission": admission, "jobs": plan}

    connection = _connect(db_path)
    now = utc_now()
    state_by_id = _upsert_discovered(connection, rows, host, now)
    for row in rows:
        spec = row.get("spec")
        if not spec or row.get("status") != "REGISTERED" or spec.get("launchable", True) is False:
            continue
        if stable_host(spec["venue"], *spec["sym_side"].rsplit("_", 1)) != host:
            continue
        ready, reason = cached_source_window_available(window_cache, spec, Path(row["spec_path"]).parent, root)
        previous = state_by_id.get(row["spec_id"])
        rendered_detail = json.dumps({"reason": reason}, sort_keys=True)
        if not ready:
            state = "INVALID_EXPERIMENT_WINDOW" if reason == "INVALID_EXPERIMENT_WINDOW" else "WAITING_FOR_SOURCE_WINDOW"
            if previous and (previous[0] != state or previous[1] != rendered_detail):
                _mark(connection, row["spec_id"], state, utc_now(), {"reason": reason})
                state_by_id[row["spec_id"]] = (state, rendered_detail)
        elif previous and previous[0] == "WAITING_FOR_SOURCE_WINDOW":
            _mark(connection, row["spec_id"], "REGISTERED", utc_now(), {"reason": reason})
            state_by_id[row["spec_id"]] = ("REGISTERED", rendered_detail)
    connection.execute("INSERT INTO coordinator_heartbeat(host,at_utc,status,detail) VALUES(?,?,?,?) ON CONFLICT(host) DO UPDATE SET at_utc=excluded.at_utc,status=excluded.status,detail=excluded.detail",
                       (host, now, "RECONCILING", json.dumps({"spec_count": len(rows)})))
    connection.commit()
    baseline_complete = {(item[0], item[1], item[2]) for item in connection.execute("SELECT DISTINCT sym_side,epoch_id,window_id FROM jobs WHERE assigned_host=? AND is_baseline=1 AND status='COMPLETED'", (host,))}
    for spec_id, is_baseline, sym_side, epoch_id, window_id, status in connection.execute("SELECT spec_id,is_baseline,sym_side,epoch_id,window_id,status FROM jobs WHERE assigned_host=?", (host,)).fetchall():
        if is_baseline or status not in {"REGISTERED", "WAITING_FOR_CAPACITY", "RESOURCE_BLOCKED", "WAITING_FOR_BASELINE"}:
            continue
        baseline_key = (sym_side, epoch_id, window_id)
        if baseline_key not in baseline_complete and status != "WAITING_FOR_BASELINE":
            _mark(connection, spec_id, "WAITING_FOR_BASELINE", utc_now(), {"reason": "matching sym_side baseline has not completed"})
        elif baseline_key in baseline_complete and status == "WAITING_FOR_BASELINE":
            _mark(connection, spec_id, "REGISTERED", utc_now(), {"reason": "matching sym_side baseline completed"})
    connection.commit()
    row_by_id = {row["spec_id"]: row for row in rows}
    # Refresh worker heartbeats and preserve stale-input waiters as live workers.
    active = connection.execute("SELECT spec_id,pid,status,run_dir,spec_path FROM jobs WHERE assigned_host=? AND status IN ('STARTING','RUNNING','WAITING_FRESH_INPUT')", (host,)).fetchall()
    active_count = 0
    for spec_id, pid, status, run_dir, spec_path in active:
        alive = _pid_alive(pid, spec_path)
        worker = _worker_status(run_dir) if run_dir else None
        worker_mapped = _map_worker_status(worker) if worker else None
        if worker and (worker_mapped in TERMINAL_STATES):
            mapped = worker_mapped
            _mark(connection, spec_id, mapped, utc_now(), worker, pid=pid, heartbeat=worker.get("heartbeat_at_utc") or worker.get("observed_at_utc") or utc_now(), clear_pid=True)
        elif not alive:
            _mark(connection, spec_id, "INTERRUPTED", utc_now(), {"reason": "worker_pid_not_alive; automatic restart disabled"}, exit_code=None)
        else:
            active_count += 1
            if worker:
                mapped = "WAITING_FRESH_INPUT" if worker["status"] == "WAITING_FRESH_INPUT" else "RUNNING"
                _mark(connection, spec_id, mapped, utc_now(), worker, pid=pid, heartbeat=worker.get("heartbeat_at_utc") or worker.get("observed_at_utc") or utc_now())
            else:
                _mark(connection, spec_id, status, utc_now(), {"worker_status_missing": True}, pid=pid, heartbeat=utc_now())
    connection.commit()
    max_slots = capacity
    free_slots = max(0, max_slots - active_count)
    if free_slots:
        for _ in range(free_slots):
            baseline_complete = {(item[0], item[1], item[2]) for item in connection.execute("SELECT DISTINCT sym_side,epoch_id,window_id FROM jobs WHERE assigned_host=? AND is_baseline=1 AND status='COMPLETED'", (host,))}
            candidates = connection.execute("SELECT spec_id,is_baseline,sym_side,epoch_id,window_id,status FROM jobs WHERE assigned_host=? AND status IN ('REGISTERED','WAITING_FOR_CAPACITY','RESOURCE_BLOCKED','WAITING_FOR_BASELINE') ORDER BY registered_at,scenario_id,sym_side,spec_id", (host,)).fetchall()
            ready = []
            for candidate_id, is_baseline, sym_side, epoch_id, window_id, candidate_status in candidates:
                baseline_key = (sym_side, epoch_id, window_id)
                if not is_baseline and baseline_key not in baseline_complete:
                    if candidate_status != "WAITING_FOR_BASELINE":
                        _mark(connection, candidate_id, "WAITING_FOR_BASELINE", utc_now(), {"reason": "matching sym_side baseline has not completed"})
                    continue
                if current := row_by_id.get(candidate_id):
                    if current.get("spec") is not None:
                        ready.append((candidate_id, bool(is_baseline), sym_side, current))
            if not ready:
                break
            counts = dict(connection.execute("SELECT is_baseline,COUNT(*) FROM jobs WHERE assigned_host=? AND pid IS NOT NULL AND status IN ('STARTING','RUNNING','WAITING_FRESH_INPUT','COMPLETED') GROUP BY is_baseline", (host,)).fetchall())
            preferred_baseline = counts.get(1, 0) <= counts.get(0, 0)
            ready.sort(key=lambda item: (item[1] != preferred_baseline, item[2], item[3]["spec"].get("scenario_id", ""), item[0]))
            spec_id, _, _, row = ready[0]
            try:
                _launch(connection, row, root, utc_now(), launcher=launcher)
            except (OSError, subprocess.SubprocessError, ValueError) as exc:
                _mark(connection, spec_id, "FAILED", utc_now(), {"launch_error": str(exc)})
            connection.commit()
    elif not admission["admit"]:
        _set_pending_state(connection, host, "RESOURCE_BLOCKED", ("REGISTERED", "WAITING_FOR_CAPACITY"), utc_now(), {"reasons": admission["reasons"] or ["no per-worker headroom"]})
    else:
        _set_pending_state(connection, host, "WAITING_FOR_CAPACITY", ("REGISTERED", "RESOURCE_BLOCKED"), utc_now(), {"active_workers": active_count, "worker_slots": max_slots})
    if admission["admit"]:
        _set_pending_state(connection, host, "WAITING_FOR_CAPACITY", ("REGISTERED", "RESOURCE_BLOCKED"), utc_now(), {"active_workers": active_count, "worker_slots": max_slots})
    connection.commit()
    status_counts = dict(connection.execute("SELECT status,COUNT(*) FROM jobs GROUP BY status").fetchall())
    connection.execute("UPDATE coordinator_heartbeat SET at_utc=?,status=?,detail=? WHERE host=?",
                       (utc_now(), "IDLE" if once else "RUNNING", json.dumps({"status_counts": status_counts}), host))
    connection.commit()
    result = {"host": host, "mode": "ONCE" if once else "DAEMON", "worker_slots": max_slots, "free_slots": free_slots, "admission": admission, "status_counts": status_counts,
              "registered_specs": len(rows), "observed_at": utc_now()}
    connection.close()
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", choices=("s1", "s2", "s5", "s6"))
    parser.add_argument("--specs-dir", type=Path)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--state-db", type=Path)
    parser.add_argument("--snapshot-to", type=Path, help="atomically back up --state-db for a central read-only collector")
    parser.add_argument("--max-workers", type=int, default=DEFAULT_MAX_WORKERS)
    parser.add_argument("--poll-seconds", type=float, default=DEFAULT_POLL_SECONDS)
    parser.add_argument("--spec-refresh-seconds", type=float, default=DEFAULT_SPEC_REFRESH_SECONDS)
    parser.add_argument("--once", action="store_true", help="reconcile once and exit")
    parser.add_argument("--dry-run", action="store_true", help="report every queued spec without writing or launching")
    args = parser.parse_args(argv)
    if args.snapshot_to:
        if not args.state_db:
            parser.error("--snapshot-to requires --state-db source path")
        print(json.dumps(snapshot_state_database(args.state_db, args.snapshot_to), sort_keys=True), flush=True)
        return 0
    if not args.host or not args.specs_dir:
        parser.error("--host and --specs-dir are required for coordinator mode")
    if args.max_workers < 1 or args.poll_seconds <= 0 or args.spec_refresh_seconds <= 0:
        parser.error("max-workers, poll-seconds, and spec-refresh-seconds must be positive")
    db_path = args.state_db or (args.root / "data" / "paper_forward_lab" / f"fleet_{args.host}.sqlite3")
    spec_cache = SpecCache(args.specs_dir, args.spec_refresh_seconds)
    if args.dry_run:
        print(json.dumps(reconcile(args.specs_dir, db_path, args.root, args.host, once=True, dry_run=True, max_workers=args.max_workers, rows=spec_cache.get()), indent=2, sort_keys=True))
        return 0
    lock_path = Path(db_path).with_suffix(Path(db_path).suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error("another coordinator already owns this host state DB")
        stop = False
        def request_stop(_sig, _frame):
            nonlocal stop
            stop = True
        old_handlers = {sig: signal.signal(sig, request_stop) for sig in (signal.SIGTERM, signal.SIGINT)}
        try:
            while True:
                result = reconcile(args.specs_dir, db_path, args.root, args.host, once=args.once, max_workers=args.max_workers, rows=spec_cache.get())
                snapshot_path = Path(args.root) / "data" / "paper_forward_lab" / f"fleet_{args.host}.snapshot.sqlite3"
                try:
                    snapshot = snapshot_state_database(db_path, snapshot_path)
                    result["fleet_snapshot"] = {"path": snapshot["snapshot_path"], "bytes": snapshot["snapshot_bytes"], "sha256": snapshot["snapshot_sha256"]}
                except (OSError, sqlite3.Error, RuntimeError) as exc:
                    result["fleet_snapshot_error"] = str(exc)
                print(json.dumps(result, sort_keys=True), flush=True)
                if args.once or stop:
                    break
                time.sleep(args.poll_seconds)
        finally:
            for sig, handler in old_handlers.items():
                signal.signal(sig, handler)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
