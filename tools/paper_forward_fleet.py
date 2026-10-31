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
except ImportError:
    from paper_fleet_budget import DEFAULT_THRESHOLDS, HOSTS_BY_VENUE, collect_observed, decide_admission, stable_host

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_SPEC_FIELDS = ("run_id", "epoch_id", "scenario_id", "sym_side", "venue", "host", "plan_code_hash", "engine_root")
TERMINAL_STATES = {"COMPLETED", "FAILED", "INTERRUPTED", "INVALID_SPEC", "UNMANIFESTED_SPEC", "MISSING_SPEC", "SPEC_CHANGED", "STOPPED_BY_REQUEST", "STOPPED_RESOURCE_LIMIT"}
WORKER_STATES = {"WAITING_FRESH_INPUT", "RUNNING", "RESOURCE_LIMIT_STOPPING", "FAILED", "COMPLETED", "COMPLETED_MULTI_FRAME_ENGINE_RUN", "STOPPED_BY_REQUEST", "STOPPED_RESOURCE_LIMIT"}
DEFAULT_WORKER_MEMORY_BYTES = 512 * 1024**2
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

    found = {p.relative_to(specs_dir) for p in specs_dir.rglob("*.json") if p != manifest_path and p.is_file()}
    result = []
    for relpath in sorted(set(listed) | found, key=lambda item: item.as_posix()):
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
            if relpath not in listed:
                raise ValueError("spec is not listed in manifest.json")
            _validate_spec(spec)
            if relpath in expected and any(spec.get(key) != value for key, value in expected[relpath].items()):
                raise ValueError("spec identity does not match manifest job")
            pending_reason = spec.get("pending_reason", "PENDING_PARAMETER_VALUES") if not spec.get("launchable", True) else None
            result.append({"spec_id": _identity_id(spec), "spec_path": str(path), "status": "PENDING_PARAMETERS" if pending_reason else "REGISTERED", "error": pending_reason, "spec": spec, "spec_sha256": _digest(raw)})
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            expected_spec = expected.get(relpath)
            result.append({"spec_id": _identity_id(expected_spec) if expected_spec else spec_id, "spec_path": str(path), "status": "UNMANIFESTED_SPEC" if relpath not in listed else "INVALID_SPEC", "error": str(exc), "spec": expected_spec, "spec_sha256": None})
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
    feed_root = Path(spec.get("feed_root", Path(root) / "data" / "paper_forward_lab" / "feed"))
    snapshot = _resolve_input(specs_dir, spec.get("snapshot", feed_root / f"{venue}.json"))
    receipt = _resolve_input(specs_dir, spec.get("receipt", feed_root / f"{venue}.receipt.json"))
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
               "--snapshot", str(snapshot), "--receipt", str(receipt), "--manifest", spec_row["spec_path"], "--full-settings", str(full_settings),
               "--candidate-overrides", str(candidate), "--run-dir", str(run_dir)]
    command.extend(("--engine-root", str(engine_root)))
    if "poll_seconds" in spec:
        command.extend(("--poll-seconds", str(spec["poll_seconds"])))
    if "max_age_seconds" in spec:
        command.extend(("--max-age-seconds", str(spec["max_age_seconds"])))
    return command, run_dir


def _connect(db_path):
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, timeout=30)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute("""CREATE TABLE IF NOT EXISTS jobs (
        spec_id TEXT PRIMARY KEY, spec_path TEXT NOT NULL, spec_sha256 TEXT,
        run_id TEXT, epoch_id TEXT, scenario_id TEXT, sym_side TEXT, venue TEXT,
        assigned_host TEXT, status TEXT NOT NULL, detail TEXT, pid INTEGER,
        registered_at TEXT NOT NULL, updated_at TEXT NOT NULL, heartbeat_at TEXT,
        exit_code INTEGER, run_dir TEXT, command_json TEXT, is_baseline INTEGER NOT NULL DEFAULT 0
    )""")
    columns = {row[1] for row in connection.execute("PRAGMA table_info(jobs)")}
    if "is_baseline" not in columns:
        connection.execute("ALTER TABLE jobs ADD COLUMN is_baseline INTEGER NOT NULL DEFAULT 0")
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
        old = connection.execute("SELECT status,pid,registered_at,spec_sha256,detail FROM jobs WHERE spec_id=?", (row["spec_id"],)).fetchone()
        was_pending = bool(old and old[0] == "PENDING_PARAMETERS")
        ready_from_pending = was_pending and bool(spec.get("launchable", False))
        if spec and row.get("spec_sha256"):
            prior = connection.execute("""SELECT spec_id,spec_sha256 FROM jobs WHERE run_id=? AND epoch_id=? AND scenario_id=? AND sym_side=? AND spec_id<>? LIMIT 1""",
                                       (spec.get("run_id"), spec.get("epoch_id"), spec.get("scenario_id"), spec.get("sym_side"), row["spec_id"])).fetchone()
            if prior and prior[1] != row["spec_sha256"]:
                status = "SPEC_CHANGED"
                row["error"] = "spec changed without a new run_id/epoch_id; refusing duplicate-epoch execution"
        if old and old[3] and row.get("spec_sha256") and old[3] != row["spec_sha256"] and not was_pending:
            status = "SPEC_CHANGED"
            row["error"] = "spec changed without a new run_id/epoch_id; refusing duplicate-epoch execution"
        elif old and old[0] in {"STARTING", "RUNNING", "WAITING_FRESH_INPUT", "WAITING_FOR_CAPACITY", "RESOURCE_BLOCKED", "ASSIGNED_OTHER_HOST", "COMPLETED", "FAILED", "INTERRUPTED", "SPEC_CHANGED", "PENDING_PARAMETERS"} and not ready_from_pending:
            status = old[0]
        registered_at = old[2] if old else now
        detail = {"error": row.get("error")} if row.get("error") else {}
        detail["launchable"] = bool(spec.get("launchable", True))
        if not spec.get("launchable", True):
            detail["pending_reason"] = spec.get("pending_reason", "PENDING_PARAMETER_VALUES")
        connection.execute("""INSERT INTO jobs(spec_id,spec_path,spec_sha256,run_id,epoch_id,scenario_id,sym_side,venue,
            assigned_host,status,detail,registered_at,updated_at,is_baseline) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(spec_id) DO UPDATE SET spec_path=excluded.spec_path,spec_sha256=excluded.spec_sha256,
            run_id=excluded.run_id,epoch_id=excluded.epoch_id,scenario_id=excluded.scenario_id,
            sym_side=excluded.sym_side,venue=excluded.venue,assigned_host=excluded.assigned_host,
            status=CASE WHEN jobs.status IN ('RUNNING','WAITING_FRESH_INPUT','COMPLETED','FAILED','INTERRUPTED') THEN jobs.status ELSE excluded.status END,
            detail=excluded.detail,updated_at=excluded.updated_at,is_baseline=excluded.is_baseline""",
            (row["spec_id"], row["spec_path"], row.get("spec_sha256"), spec.get("run_id"), spec.get("epoch_id"), spec.get("scenario_id"), spec.get("sym_side"), venue,
             assigned, status, json.dumps(detail, sort_keys=True), registered_at, now, int(spec.get("is_baseline", False) is True)))
        if not old or old[0] != status:
            connection.execute("INSERT INTO events(spec_id,at_utc,status,detail) VALUES(?,?,?,?)",
                               (row["spec_id"], now, status, json.dumps(detail, sort_keys=True)))


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
    if worker_status in {"COMPLETED", "COMPLETED_MULTI_FRAME_ENGINE_RUN"}:
        return "COMPLETED"
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


def _mark(connection, spec_id, status, now, detail=None, pid=None, exit_code=None, heartbeat=None, run_dir=None, command=None):
    rendered_detail = json.dumps(detail or {}, sort_keys=True)
    connection.execute("""UPDATE jobs SET status=?,detail=?,pid=COALESCE(?,pid),exit_code=COALESCE(?,exit_code),
        heartbeat_at=COALESCE(?,heartbeat_at),run_dir=COALESCE(?,run_dir),command_json=COALESCE(?,command_json),updated_at=? WHERE spec_id=?""",
        (status, rendered_detail, pid, exit_code, heartbeat, str(run_dir) if run_dir else None,
         json.dumps(command) if command else None, now, spec_id))
    connection.execute("INSERT INTO events(spec_id,at_utc,status,detail,pid) VALUES(?,?,?,?,?)", (spec_id, now, status, rendered_detail, pid))


def _set_pending_state(connection, host, status, source_states, now, detail):
    marks = ",".join("?" for _ in source_states)
    rows = connection.execute(f"SELECT spec_id FROM jobs WHERE assigned_host=? AND status IN ({marks})", (host, *source_states)).fetchall()
    for (spec_id,) in rows:
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
    if dry_run:
        plan = []
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
                    status = "WAITING_FOR_CAPACITY" if capacity else "RESOURCE_BLOCKED"
            command = worker_command(row, root)[0] if spec and status == "REGISTERED" else None
            plan.append({"spec_id": row["spec_id"], "spec_path": row["spec_path"], "status": status, "assigned_host": assigned, "command": command})
        return {"host": host, "mode": "DRY_RUN", "capacity": capacity, "admission": admission, "jobs": plan}

    connection = _connect(db_path)
    now = utc_now()
    _upsert_discovered(connection, rows, host, now)
    connection.execute("INSERT INTO coordinator_heartbeat(host,at_utc,status,detail) VALUES(?,?,?,?) ON CONFLICT(host) DO UPDATE SET at_utc=excluded.at_utc,status=excluded.status,detail=excluded.detail",
                       (host, now, "RECONCILING", json.dumps({"spec_count": len(rows)})))
    connection.commit()
    row_by_id = {row["spec_id"]: row for row in rows}
    # Refresh worker heartbeats and preserve stale-input waiters as live workers.
    active = connection.execute("SELECT spec_id,pid,status,run_dir,spec_path FROM jobs WHERE assigned_host=? AND status IN ('STARTING','RUNNING','WAITING_FRESH_INPUT')", (host,)).fetchall()
    active_count = 0
    for spec_id, pid, status, run_dir, spec_path in active:
        alive = _pid_alive(pid, spec_path)
        worker = _worker_status(run_dir) if run_dir else None
        worker_mapped = _map_worker_status(worker["status"]) if worker else None
        if worker and (worker_mapped in TERMINAL_STATES):
            mapped = worker_mapped
            _mark(connection, spec_id, mapped, utc_now(), worker, pid=pid, heartbeat=worker.get("heartbeat_at_utc") or worker.get("observed_at_utc") or utc_now())
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
        pending = connection.execute("SELECT spec_id FROM jobs WHERE assigned_host=? AND status IN ('REGISTERED','WAITING_FOR_CAPACITY','RESOURCE_BLOCKED') ORDER BY is_baseline DESC,registered_at,scenario_id,sym_side,spec_id", (host,)).fetchall()
        for (spec_id,) in pending[:free_slots]:
            row = row_by_id.get(spec_id)
            if row is None or row.get("spec") is None:
                continue
            try:
                _launch(connection, row, root, utc_now(), launcher=launcher)
            except (OSError, subprocess.SubprocessError, ValueError) as exc:
                _mark(connection, spec_id, "FAILED", utc_now(), {"launch_error": str(exc)})
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
