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
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    from .paper_fleet_budget import DEFAULT_THRESHOLDS, HOSTS_BY_VENUE, collect_observed, decide_admission, stable_host
except ImportError:
    from paper_fleet_budget import DEFAULT_THRESHOLDS, HOSTS_BY_VENUE, collect_observed, decide_admission, stable_host

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_SPEC_FIELDS = ("run_id", "epoch_id", "scenario_id", "sym_side", "venue", "plan_code_hash")
TERMINAL_STATES = {"COMPLETED", "FAILED", "INTERRUPTED", "INVALID_SPEC", "UNMANIFESTED_SPEC", "MISSING_SPEC", "SPEC_CHANGED", "STOPPED_BY_REQUEST", "STOPPED_RESOURCE_LIMIT"}
WORKER_STATES = {"WAITING_FRESH_INPUT", "RUNNING", "RESOURCE_LIMIT_STOPPING", "FAILED", "COMPLETED", "COMPLETED_MULTI_FRAME_ENGINE_RUN", "STOPPED_BY_REQUEST", "STOPPED_RESOURCE_LIMIT"}
DEFAULT_WORKER_MEMORY_BYTES = 512 * 1024**2
DEFAULT_WORKER_DISK_BYTES = 1024**3
DEFAULT_WORKER_CPU_CORES = 0.25
MAX_SPEC_BYTES = 2 * 1024**2


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
        raw_manifest = manifest_path.read_bytes()
        if len(raw_manifest) > MAX_SPEC_BYTES:
            manifest_error = "manifest exceeds bounded size"
        else:
            try:
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
            result.append({"spec_id": _identity_id(spec), "spec_path": str(path), "status": "REGISTERED", "error": None, "spec": spec, "spec_sha256": _digest(raw)})
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            expected_spec = expected.get(relpath)
            result.append({"spec_id": _identity_id(expected_spec) if expected_spec else spec_id, "spec_path": str(path), "status": "UNMANIFESTED_SPEC" if relpath not in listed else "INVALID_SPEC", "error": str(exc), "spec": expected_spec, "spec_sha256": None})
    return result


def _validate_spec(spec):
    missing = [key for key in REQUIRED_SPEC_FIELDS if not isinstance(spec.get(key), str) or not spec[key].strip()]
    if missing:
        raise ValueError("missing required string fields: " + ", ".join(missing))
    _validate_identity(spec)
    sym_side = spec["sym_side"]
    symbol, side = sym_side.rsplit("_", 1)
    venue = spec["venue"].lower()
    if venue not in HOSTS_BY_VENUE:
        raise ValueError("venue must be crypto or stocks")
    if spec.get("schema_version", 1) != 1:
        raise ValueError("unsupported spec schema_version")
    if not isinstance(spec.get("full_settings"), str) or not isinstance(spec.get("candidate_overrides"), str):
        raise ValueError("full_settings and candidate_overrides paths are required")
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
    run_dir = Path(root) / "data" / "paper_forward_lab" / "runs" / spec_row["spec_id"]
    command = [sys.executable, str(Path(root) / "tools" / "paper_forward_worker.py"), "--follow", "--sym-side", spec["sym_side"], "--venue", venue,
               "--snapshot", str(snapshot), "--receipt", str(receipt), "--manifest", spec_row["spec_path"], "--full-settings", str(full_settings),
               "--candidate-overrides", str(candidate), "--run-dir", str(run_dir)]
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
        exit_code INTEGER, run_dir TEXT, command_json TEXT
    )""")
    connection.execute("CREATE INDEX IF NOT EXISTS jobs_status_host ON jobs(status, assigned_host)")
    connection.execute("""CREATE TABLE IF NOT EXISTS events (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT, spec_id TEXT NOT NULL,
        at_utc TEXT NOT NULL, status TEXT NOT NULL, detail TEXT, pid INTEGER
    )""")
    connection.execute("""CREATE TABLE IF NOT EXISTS coordinator_heartbeat (
        host TEXT PRIMARY KEY, at_utc TEXT NOT NULL, status TEXT NOT NULL, detail TEXT
    )""")
    return connection


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
        old = connection.execute("SELECT status,pid,registered_at,spec_sha256 FROM jobs WHERE spec_id=?", (row["spec_id"],)).fetchone()
        if spec and row.get("spec_sha256"):
            prior = connection.execute("""SELECT spec_id,spec_sha256 FROM jobs WHERE run_id=? AND epoch_id=? AND scenario_id=? AND sym_side=? AND spec_id<>? LIMIT 1""",
                                       (spec.get("run_id"), spec.get("epoch_id"), spec.get("scenario_id"), spec.get("sym_side"), row["spec_id"])).fetchone()
            if prior and prior[1] != row["spec_sha256"]:
                status = "SPEC_CHANGED"
                row["error"] = "spec changed without a new run_id/epoch_id; refusing duplicate-epoch execution"
        if old and old[3] and row.get("spec_sha256") and old[3] != row["spec_sha256"]:
            status = "SPEC_CHANGED"
            row["error"] = "spec changed without a new run_id/epoch_id; refusing duplicate-epoch execution"
        elif old and old[0] in {"STARTING", "RUNNING", "WAITING_FRESH_INPUT", "WAITING_FOR_CAPACITY", "RESOURCE_BLOCKED", "ASSIGNED_OTHER_HOST", "COMPLETED", "FAILED", "INTERRUPTED", "SPEC_CHANGED"}:
            status = old[0]
        registered_at = old[2] if old else now
        detail = {"error": row.get("error")} if row.get("error") else {}
        connection.execute("""INSERT INTO jobs(spec_id,spec_path,spec_sha256,run_id,epoch_id,scenario_id,sym_side,venue,
            assigned_host,status,detail,registered_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(spec_id) DO UPDATE SET spec_path=excluded.spec_path,spec_sha256=excluded.spec_sha256,
            run_id=excluded.run_id,epoch_id=excluded.epoch_id,scenario_id=excluded.scenario_id,
            sym_side=excluded.sym_side,venue=excluded.venue,assigned_host=excluded.assigned_host,
            status=CASE WHEN jobs.status IN ('RUNNING','WAITING_FRESH_INPUT','COMPLETED','FAILED','INTERRUPTED') THEN jobs.status ELSE excluded.status END,
            detail=excluded.detail,updated_at=excluded.updated_at""",
            (row["spec_id"], row["spec_path"], row.get("spec_sha256"), spec.get("run_id"), spec.get("epoch_id"), spec.get("scenario_id"), spec.get("sym_side"), venue,
             assigned, status, json.dumps(detail, sort_keys=True), registered_at, now))
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


def resource_worker_limit(observed, venue, host, max_workers=1, worker_memory_bytes=DEFAULT_WORKER_MEMORY_BYTES,
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


def reconcile(specs_dir, db_path, root=ROOT, host=None, once=True, dry_run=False, max_workers=1, poll_seconds=5,
              observed=None, launcher=subprocess.Popen):
    if host not in {item for hosts in HOSTS_BY_VENUE.values() for item in hosts}:
        raise ValueError("host must be one of s1, s2, s5, s6")
    root = Path(root).resolve(strict=True)
    rows = discover_specs(specs_dir)
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
        pending = connection.execute("SELECT spec_id FROM jobs WHERE assigned_host=? AND status IN ('REGISTERED','WAITING_FOR_CAPACITY','RESOURCE_BLOCKED') ORDER BY registered_at,scenario_id,sym_side,spec_id", (host,)).fetchall()
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
    parser.add_argument("--host", choices=("s1", "s2", "s5", "s6"), required=True)
    parser.add_argument("--specs-dir", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--state-db", type=Path)
    parser.add_argument("--max-workers", type=int, default=1)
    parser.add_argument("--poll-seconds", type=float, default=5)
    parser.add_argument("--once", action="store_true", help="reconcile once and exit")
    parser.add_argument("--dry-run", action="store_true", help="report every queued spec without writing or launching")
    args = parser.parse_args(argv)
    if args.max_workers < 1 or args.poll_seconds <= 0:
        parser.error("max-workers must be positive and poll-seconds must be positive")
    db_path = args.state_db or (args.root / "data" / "paper_forward_lab" / f"fleet_{args.host}.sqlite3")
    if args.dry_run:
        print(json.dumps(reconcile(args.specs_dir, db_path, args.root, args.host, once=True, dry_run=True, max_workers=args.max_workers), indent=2, sort_keys=True))
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
                result = reconcile(args.specs_dir, db_path, args.root, args.host, once=args.once, max_workers=args.max_workers)
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
