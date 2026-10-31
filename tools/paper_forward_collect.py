"""Collect isolated paper worker bundles and coordinator status into central SQL.

Run on S1 after the complete frozen plan has been registered. This collector
only reads remote artifacts, never copies or opens a worker/central live DB.

Ready command on S1 (after placing the immutable plan JSON):
  python3 tools/paper_forward_collect.py --root /home/niels/binance-sandbox \
    --plan /home/niels/binance-sandbox/data/paper_forward_lab/frozen_plan.json \
    --db /home/niels/binance-sandbox/data/paper_forward_lab/scenarios.db \
    --remote-root /home/niels/binance-sandbox/data/paper_forward_lab/runs \
    --coordinator-relative data/paper_forward_lab/fleet_s1.snapshot.sqlite3 \
    --remote-coordinator '/home/niels/binance-sandbox/data/paper_forward_lab/fleet_{host}.snapshot.sqlite3' \
    --watch --interval 60

Each host coordinator can produce its read-only copy with the existing
paper_forward_fleet.py --snapshot-to option. The collector never copies the
live fleet_<host>.sqlite3 database itself.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

try:
    from .paper_forward_results import _canonical, _digest, ingest
    from .paper_inventory import ingest_plan
    from .paper_scenario_db import ScenarioDB
except ImportError:
    from paper_forward_results import _canonical, _digest, ingest
    from paper_inventory import ingest_plan
    from paper_scenario_db import ScenarioDB


HOSTS = ("s1", "s2", "s5", "s6")
SSH_TARGETS = {"s2": "niels@10.0.0.4", "s5": "niels@10.0.0.5", "s6": "niels@10.0.0.6"}
DEFAULT_REMOTE_ROOT = "/home/niels/binance-sandbox/data/paper_forward_lab/runs"
DEFAULT_COORDINATOR_RELATIVE = "data/paper_forward_lab/fleet_s1.snapshot.sqlite3"
DEFAULT_REMOTE_COORDINATOR = "/home/niels/binance-sandbox/data/paper_forward_lab/fleet_{host}.snapshot.sqlite3"
MANIFEST_NAMES = ("worker.result_manifest.json", "manifest.json")
SNAPSHOT_NAMES = ("receipts.snapshot.db", "receipt.snapshot.db")
_SAFE_REMOTE_PATH = re.compile(r"^/[A-Za-z0-9_./-]+$")


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative(value: Any) -> Path:
    path = Path(str(value))
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError("worker settings filenames must be safe relative paths")
    return path


def _run(args: list[str], *, runner: Callable = subprocess.run, timeout: int = 120):
    result = runner(args, capture_output=True, text=False, timeout=timeout, check=False)
    if result.returncode:
        error = result.stderr.decode("utf-8", "replace")[-1000:] if isinstance(result.stderr, bytes) else str(result.stderr)[-1000:]
        raise RuntimeError(f"command failed ({result.returncode}): {args[0]}: {error}")
    return result


def _create_coordinator_tables(connection) -> None:
    statements = ("""
        CREATE TABLE IF NOT EXISTS paper_coordinator_snapshots (
            snapshot_hash TEXT PRIMARY KEY, source_host TEXT NOT NULL,
            observed_at TEXT, evidence_kind TEXT NOT NULL CHECK(evidence_kind='coordinator_only'),
            payload_json TEXT NOT NULL
        );
    """, """
        CREATE TABLE IF NOT EXISTS paper_coordinator_jobs (
            source_host TEXT NOT NULL, job_id TEXT NOT NULL,
            snapshot_hash TEXT NOT NULL REFERENCES paper_coordinator_snapshots(snapshot_hash),
            sym_side TEXT, scenario_id TEXT, assigned_host TEXT,
            status TEXT NOT NULL, updated_at TEXT, payload_json TEXT NOT NULL,
            PRIMARY KEY(source_host,job_id)
        );
    """, """
        CREATE TABLE IF NOT EXISTS paper_coordinator_coverage (
            source_host TEXT NOT NULL, assigned_host TEXT NOT NULL, status TEXT NOT NULL,
            snapshot_hash TEXT NOT NULL REFERENCES paper_coordinator_snapshots(snapshot_hash),
            job_count INTEGER NOT NULL, scenario_count INTEGER NOT NULL,
            PRIMARY KEY(source_host,assigned_host,status)
        );
    """, """
        CREATE TABLE IF NOT EXISTS paper_coordinator_coverage_reports (
            source_host TEXT PRIMARY KEY, snapshot_hash TEXT NOT NULL REFERENCES paper_coordinator_snapshots(snapshot_hash),
            coverage_json TEXT NOT NULL
        );
    """, """
        CREATE TABLE IF NOT EXISTS paper_coordinator_events (
            source_host TEXT NOT NULL, event_id TEXT NOT NULL,
            snapshot_hash TEXT NOT NULL REFERENCES paper_coordinator_snapshots(snapshot_hash),
            job_id TEXT, recorded_at TEXT NOT NULL, status TEXT NOT NULL, detail_json TEXT, pid INTEGER,
            PRIMARY KEY(source_host,event_id)
        );
    """, """
        CREATE TABLE IF NOT EXISTS paper_coordinator_heartbeats (
            source_host TEXT PRIMARY KEY, snapshot_hash TEXT NOT NULL REFERENCES paper_coordinator_snapshots(snapshot_hash),
            recorded_at TEXT NOT NULL, status TEXT NOT NULL, detail_json TEXT
        );
    """, """
        CREATE TABLE IF NOT EXISTS paper_coordinator_artifacts (
            source_host TEXT NOT NULL, source_file_sha256 TEXT NOT NULL,
            snapshot_hash TEXT NOT NULL REFERENCES paper_coordinator_snapshots(snapshot_hash), collected_at TEXT NOT NULL,
            PRIMARY KEY(source_host,source_file_sha256)
        );
    """)
    for statement in statements:
        connection.execute(statement)


def ingest_coordinator_snapshot(db: ScenarioDB, source_host: str, payload: Mapping[str, Any], source_sha256: str | None = None) -> dict:
    """Persist coordinator scheduling evidence separately from execution rows."""
    if not isinstance(payload, Mapping):
        raise ValueError("coordinator snapshot must be a JSON object")
    payload_json = _canonical(payload)
    calculated_hash = hashlib.sha256(payload_json.encode()).hexdigest()
    if source_sha256 and source_sha256 != calculated_hash:
        raise ValueError("coordinator snapshot hash mismatch")
    snapshot_hash = calculated_hash
    jobs = payload.get("jobs", payload.get("job_statuses", payload.get("assignments", [])))
    if isinstance(jobs, Mapping):
        jobs = [dict(value, job_id=key) if isinstance(value, Mapping) else {"job_id": key, "status": str(value)} for key, value in jobs.items()]
    if not isinstance(jobs, list):
        raise ValueError("coordinator jobs must be an array or object")
    return _ingest_coordinator_records(db, source_host, payload, jobs, payload.get("events", []), payload.get("heartbeats", []), calculated_hash, source_sha256)


def _ingest_coordinator_records(db: ScenarioDB, source_host: str, payload: Mapping[str, Any], jobs: list, events: list, heartbeats: list, snapshot_hash: str, source_file_sha256: str | None = None) -> dict:
    created = 0
    by_host_status: Counter = Counter()
    by_host_scenarios: dict[tuple[str, str], set[str]] = {}
    observed_at = str(payload.get("observed_at") or payload.get("observed_at_utc") or payload.get("generated_at") or payload.get("updated_at") or _utc_now())
    summary = {"jobs": len(jobs), "events": len(events), "heartbeats": len(heartbeats), "coverage": payload.get("coverage", {})}
    summary_json = _canonical(summary)
    with db.transaction():
        _create_coordinator_tables(db.connection)
        snapshot = {"snapshot_hash": snapshot_hash, "source_host": source_host, "observed_at": observed_at, "evidence_kind": "coordinator_only", "payload_json": summary_json}
        cursor = db.connection.execute("INSERT OR IGNORE INTO paper_coordinator_snapshots VALUES(:snapshot_hash,:source_host,:observed_at,:evidence_kind,:payload_json)", snapshot)
        if not cursor.rowcount:
            existing = db.connection.execute("SELECT * FROM paper_coordinator_snapshots WHERE snapshot_hash=?", (snapshot_hash,)).fetchone()
            if existing is None or existing[1] != source_host or existing[3] != "coordinator_only" or existing[4] != summary_json:
                raise ValueError("immutable coordinator snapshot hash conflict")
        for index, raw in enumerate(jobs):
            if not isinstance(raw, Mapping):
                raise ValueError("coordinator job row must be an object")
            row = dict(raw)
            job_id = str(row.get("job_id") or row.get("spec_id") or row.get("run_id") or row.get("id") or _digest([snapshot_hash, index, row]))
            assigned_host = str(row.get("host") or row.get("assigned_host") or source_host)
            status = str(row.get("status") or row.get("state") or "UNKNOWN").upper()
            scenario_id = row.get("scenario_id")
            item = {
                "source_host": source_host,
                "job_id": job_id,
                "snapshot_hash": snapshot_hash,
                "sym_side": row.get("sym_side"),
                "scenario_id": scenario_id,
                "assigned_host": assigned_host,
                "status": status,
                "updated_at": row.get("updated_at") or row.get("updated_at_utc") or row.get("observed_at"),
                "payload_json": _canonical(row),
            }
            was_missing = db.connection.execute("SELECT 1 FROM paper_coordinator_jobs WHERE source_host=? AND job_id=?", (source_host, job_id)).fetchone() is None
            db.connection.execute("INSERT INTO paper_coordinator_jobs VALUES(:source_host,:job_id,:snapshot_hash,:sym_side,:scenario_id,:assigned_host,:status,:updated_at,:payload_json) ON CONFLICT(source_host,job_id) DO UPDATE SET snapshot_hash=excluded.snapshot_hash,sym_side=excluded.sym_side,scenario_id=excluded.scenario_id,assigned_host=excluded.assigned_host,status=excluded.status,updated_at=excluded.updated_at,payload_json=excluded.payload_json", item)
            if was_missing:
                created += 1
        all_jobs = db.connection.execute("SELECT assigned_host,status,scenario_id FROM paper_coordinator_jobs WHERE source_host=?", (source_host,)).fetchall()
        for assigned_host, status, scenario_id in all_jobs:
            by_host_status[(assigned_host, status)] += 1
            if scenario_id is not None:
                by_host_scenarios.setdefault((assigned_host, status), set()).add(str(scenario_id))
        db.connection.execute("DELETE FROM paper_coordinator_coverage WHERE source_host=?", (source_host,))
        for (assigned_host, status), count in by_host_status.items():
            row = (source_host, assigned_host, status, snapshot_hash, count, len(by_host_scenarios.get((assigned_host, status), set())))
            db.connection.execute("INSERT INTO paper_coordinator_coverage VALUES(?,?,?,?,?,?)", row)
        if "coverage" in payload:
            coverage_json = _canonical(payload["coverage"])
            db.connection.execute("INSERT INTO paper_coordinator_coverage_reports VALUES(?,?,?) ON CONFLICT(source_host) DO UPDATE SET snapshot_hash=excluded.snapshot_hash,coverage_json=excluded.coverage_json", (source_host, snapshot_hash, coverage_json))
        for index, raw in enumerate(events):
            if not isinstance(raw, Mapping):
                raise ValueError("coordinator event must be an object")
            event = dict(raw)
            event_id = str(event.get("event_id") or _digest([source_host, index, event]))
            event_row = (source_host, event_id, snapshot_hash, event.get("spec_id") or event.get("job_id"), str(event.get("at_utc") or event.get("recorded_at") or observed_at), str(event.get("status") or "UNKNOWN").upper(), _canonical(event.get("detail")) if event.get("detail") is not None else None, event.get("pid"))
            db.connection.execute("INSERT OR IGNORE INTO paper_coordinator_events VALUES(?,?,?,?,?,?,?,?)", event_row)
            prior_event = db.connection.execute("SELECT source_host,event_id,job_id,recorded_at,status,detail_json,pid FROM paper_coordinator_events WHERE source_host=? AND event_id=?", (source_host, event_id)).fetchone()
            if prior_event is None or tuple(prior_event) != (event_row[0], event_row[1], event_row[3], event_row[4], event_row[5], event_row[6], event_row[7]):
                raise ValueError(f"immutable coordinator event conflict: {event_id}")
        for raw in heartbeats:
            if not isinstance(raw, Mapping):
                raise ValueError("coordinator heartbeat must be an object")
            heartbeat = dict(raw)
            heartbeat_host = str(heartbeat.get("host") or source_host)
            heartbeat_row = (heartbeat_host, snapshot_hash, str(heartbeat.get("at_utc") or heartbeat.get("recorded_at") or observed_at), str(heartbeat.get("status") or "UNKNOWN").upper(), _canonical(heartbeat.get("detail")) if heartbeat.get("detail") is not None else None)
            db.connection.execute("INSERT INTO paper_coordinator_heartbeats VALUES(?,?,?,?,?) ON CONFLICT(source_host) DO UPDATE SET snapshot_hash=excluded.snapshot_hash,recorded_at=excluded.recorded_at,status=excluded.status,detail_json=excluded.detail_json", heartbeat_row)
        if source_file_sha256:
            db.connection.execute("INSERT OR IGNORE INTO paper_coordinator_artifacts VALUES(?,?,?,?)", (source_host, source_file_sha256, snapshot_hash, _utc_now()))
    return {"snapshot_hash": snapshot_hash, "source_host": source_host, "jobs": len(jobs), "new_jobs": created, "evidence_kind": "coordinator_only"}


def ingest_coordinator_database(db: ScenarioDB, source_host: str, path: str | Path) -> dict:
    """Import a read-only atomic backup of paper_forward_fleet StateDB."""
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    source_sha = _sha256_file(path)
    with sqlite3.connect(uri, uri=True) as connection:
        connection.execute("BEGIN")
        check = connection.execute("PRAGMA quick_check").fetchone()
        if not check or check[0] != "ok":
            raise ValueError("coordinator SQLite snapshot integrity check failed")
        names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"jobs", "events", "coordinator_heartbeat"}.issubset(names):
            raise ValueError("coordinator snapshot lacks jobs/events/coordinator_heartbeat tables")
        jobs = [dict(zip((col[0] for col in connection.execute("SELECT * FROM jobs LIMIT 0").description), row)) for row in connection.execute("SELECT * FROM jobs")]
        events = [dict(zip((col[0] for col in connection.execute("SELECT * FROM events LIMIT 0").description), row)) for row in connection.execute("SELECT * FROM events")]
        heartbeats = [dict(zip((col[0] for col in connection.execute("SELECT * FROM coordinator_heartbeat LIMIT 0").description), row)) for row in connection.execute("SELECT * FROM coordinator_heartbeat")]
    coverage = {}
    grouped = Counter((str(row.get("assigned_host") or "UNKNOWN"), str(row.get("status") or "UNKNOWN").upper()) for row in jobs)
    for (host, status), count in grouped.items():
        coverage.setdefault(host, {})[status] = count
    observed = max((str(row.get("at_utc") or "") for row in heartbeats), default="") or _utc_now()
    payload = {"observed_at": observed, "jobs": jobs, "events": events, "heartbeats": heartbeats, "coverage": coverage, "source_file_sha256": source_sha}
    result = _ingest_coordinator_records(db, source_host, payload, jobs, events, heartbeats, _digest(payload), source_sha)
    result["source_file_sha256"] = source_sha
    return result


class PaperForwardCollector:
    """Stage and validate immutable worker artifact bundles before SQL ingestion."""

    def __init__(self, root: str | Path, db: ScenarioDB, *, hosts: tuple[str, ...] = HOSTS, remote_root: str = DEFAULT_REMOTE_ROOT, coordinator_relative: str = DEFAULT_COORDINATOR_RELATIVE, remote_coordinator: str = DEFAULT_REMOTE_COORDINATOR, runner: Callable = subprocess.run):
        self.root = Path(root).resolve()
        self.db = db
        self.hosts = tuple(hosts)
        if any(host not in HOSTS for host in self.hosts):
            raise ValueError("collector host must be one of s1, s2, s5, s6")
        if not _SAFE_REMOTE_PATH.fullmatch(remote_root):
            raise ValueError("remote_root must be an absolute safe path")
        self.remote_root = remote_root.rstrip("/")
        if any(not _SAFE_REMOTE_PATH.fullmatch(remote_coordinator.format(host=host)) for host in HOSTS if host != "s1"):
            raise ValueError("remote_coordinator must format to an absolute safe path")
        self.remote_coordinator = remote_coordinator
        self.coordinator_relative = _safe_relative(coordinator_relative)
        self.runner = runner
        self.incoming_root = self.root / "data" / "paper_forward_lab" / "collector" / "incoming"
        self.collected_root = self.root / "data" / "paper_forward_lab" / "collector" / "collected"

    def _remote_manifest_paths(self, host: str) -> list[str]:
        target = SSH_TARGETS[host]
        command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", target, "find", self.remote_root, "-type", "f", "-name", "worker.result_manifest.json", "-print"]
        output = _run(command, runner=self.runner, timeout=30).stdout.decode("utf-8", "strict")
        paths = [line for line in output.splitlines() if line]
        for path in paths:
            if not _SAFE_REMOTE_PATH.fullmatch(path) or not path.startswith(self.remote_root + "/"):
                raise ValueError("remote find returned path outside configured worker root")
        return paths

    def _sync_file(self, host: str, source: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        pending = destination.with_name(destination.name + ".pending")
        if pending.exists():
            pending.unlink()
        target = SSH_TARGETS[host]
        command = ["rsync", "-a", "--protect-args", "--timeout=45", "-e", "ssh -o BatchMode=yes -o ConnectTimeout=10", "--", f"{target}:{source}", str(pending)]
        _run(command, runner=self.runner, timeout=90)
        if not pending.is_file() or pending.stat().st_size == 0:
            raise ValueError(f"remote artifact was absent or empty: {source}")
        os.replace(pending, destination)

    def _copy_local_file(self, source: Path, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        pending = destination.with_name(destination.name + ".pending")
        shutil.copyfile(source, pending)
        with pending.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(pending, destination)

    @staticmethod
    def _immutable_identity(manifest: Mapping[str, Any]) -> tuple:
        keys = ("run_id", "epoch_id", "scenario_id", "sym_side", "host", "started_at", "settings_sha256", "parent_settings_sha256", "base_settings_file", "effective_settings_file", "code_sha256", "code_file_hashes", "candidate_overrides", "isolation_overrides", "paper_mask_release", "context", "candidate_getter_resolution", "worker_isolation", "engine_root", "plan_code_hash", "accounting_file", "accounting_sha256", "result_file", "result_sha256")
        return tuple(_canonical(manifest.get(key)) for key in keys)

    def _stage_bundle(self, host: str, remote_manifest_path: str | None = None, local_run_dir: Path | None = None) -> dict:
        stage = self.incoming_root / f".{host}.{uuid.uuid4().hex}.pending"
        stage.mkdir(parents=True, mode=0o700)
        try:
            manifest_name = "worker.result_manifest.json"
            first_manifest = stage / manifest_name
            if local_run_dir is not None:
                self._copy_local_file(local_run_dir / manifest_name, first_manifest)
            else:
                self._sync_file(host, str(remote_manifest_path), first_manifest)
            manifest = json.loads(first_manifest.read_text())
            if str(manifest.get("host")) != host:
                raise ValueError(f"manifest host mismatch: expected {host}")
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,160}", str(manifest.get("run_id", ""))):
                raise ValueError("manifest run_id is not a safe bundle identifier")
            for key in ("base_settings_file", "effective_settings_file"):
                relative = _safe_relative(manifest[key])
                source = local_run_dir / relative if local_run_dir is not None else str(Path(remote_manifest_path).parent / relative)
                destination = stage / relative
                if local_run_dir is not None:
                    self._copy_local_file(source, destination)
                else:
                    self._sync_file(host, source, destination)
            if manifest.get("accounting_file") or manifest.get("accounting_sha256"):
                if not manifest.get("accounting_file") or not manifest.get("accounting_sha256"):
                    raise ValueError("manifest accounting artifact reference is incomplete")
                relative = _safe_relative(manifest["accounting_file"])
                source = local_run_dir / relative if local_run_dir is not None else str(Path(remote_manifest_path).parent / relative)
                destination = stage / relative
                if local_run_dir is not None:
                    self._copy_local_file(source, destination)
                else:
                    self._sync_file(host, source, destination)
                if _sha256_file(destination) != manifest["accounting_sha256"]:
                    raise ValueError("staged accounting artifact hash does not match manifest")
            result_relative = _safe_relative(manifest["result_file"]) if manifest.get("result_file") else None
            result_name = str(result_relative) if result_relative else "result.json"
            result_source = local_run_dir / result_name if local_run_dir is not None else (str(Path(remote_manifest_path).parent / result_relative) if result_relative else None)
            if result_source is not None and (local_run_dir is not None and Path(result_source).is_file() or local_run_dir is None and result_relative):
                result_target = stage / result_name
                if local_run_dir is not None:
                    self._copy_local_file(result_source, result_target)
                else:
                    self._sync_file(host, result_source, result_target)
                if manifest.get("result_sha256") and _sha256_file(result_target) != manifest["result_sha256"]:
                    raise ValueError("staged result.json hash does not match manifest")
            snapshot_source = local_run_dir / "receipts.snapshot.db" if local_run_dir is not None else str(Path(remote_manifest_path).parent / "receipts.snapshot.db")
            snapshot_target = stage / "receipts.snapshot.db"
            if local_run_dir is not None:
                self._copy_local_file(snapshot_source, snapshot_target)
            else:
                self._sync_file(host, snapshot_source, snapshot_target)
            if remote_manifest_path is not None:
                second = stage / (manifest_name + ".verify")
                self._sync_file(host, str(remote_manifest_path), second)
                latest = json.loads(second.read_text())
                if self._immutable_identity(latest) != self._immutable_identity(manifest):
                    raise ValueError("worker manifest identity/settings changed while staging artifacts")
                os.replace(second, first_manifest)
                manifest = latest
            elif local_run_dir is not None:
                latest = json.loads((local_run_dir / manifest_name).read_text())
                if self._immutable_identity(latest) != self._immutable_identity(manifest):
                    raise ValueError("local worker manifest identity/settings changed while staging artifacts")
                self._copy_local_file(local_run_dir / manifest_name, first_manifest)
                manifest = latest
            bundle_hash = _digest({"manifest_sha256": _sha256_file(first_manifest), "settings_sha256": {str(path.relative_to(stage)): _sha256_file(path) for path in stage.rglob("*.json")}, "receipts_sha256": _sha256_file(snapshot_target)})
            destination = self.collected_root / host / manifest["run_id"] / bundle_hash
            destination.parent.mkdir(parents=True, exist_ok=True)
            result = ingest(self.db, snapshot_target, manifest, stage)
            if destination.exists():
                shutil.rmtree(stage)
            else:
                os.replace(stage, destination)
            result.update({"bundle_hash": bundle_hash, "bundle_path": str(destination), "host": host})
            return result
        except Exception:
            shutil.rmtree(stage, ignore_errors=True)
            raise

    def _collect_remote_host(self, host: str) -> list[dict]:
        results = []
        for manifest_path in self._remote_manifest_paths(host):
            try:
                results.append(self._stage_bundle(host, remote_manifest_path=manifest_path))
            except Exception as exc:
                results.append({"host": host, "source": manifest_path, "status": "COLLECT_ERROR", "error": str(exc)})
        try:
            result = self._collect_coordinator(host, self.remote_coordinator.format(host=host))
            if result:
                results.append(result)
        except FileNotFoundError:
            pass
        except Exception as exc:
            results.append({"host": host, "status": "COORDINATOR_ERROR", "error": str(exc)})
        return results

    def _collect_local_host(self) -> list[dict]:
        runs = self.root / "data" / "paper_forward_lab" / "runs"
        results = []
        if runs.exists():
            for manifest_path in sorted(runs.rglob("worker.result_manifest.json")):
                try:
                    results.append(self._stage_bundle("s1", local_run_dir=manifest_path.parent))
                except Exception as exc:
                    results.append({"host": "s1", "source": str(manifest_path), "status": "COLLECT_ERROR", "error": str(exc)})
        coordinator = self.root / self.coordinator_relative
        if coordinator.exists():
            try:
                results.append(self._collect_coordinator("s1", coordinator))
            except Exception as exc:
                results.append({"host": "s1", "status": "COORDINATOR_ERROR", "error": str(exc)})
        return results

    def _collect_coordinator(self, host: str, source: str | Path) -> dict | None:
        if isinstance(source, Path):
            if not source.is_file():
                return None
            before = source.stat()
            payload_bytes = source.read_bytes()
            after = source.stat()
            if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns) or len(payload_bytes) != before.st_size:
                raise ValueError("coordinator snapshot changed during local read")
            staged = self.incoming_root / f".{host}.coordinator.{uuid.uuid4().hex}.pending"
            self._copy_local_file(source, staged)
        else:
            staged = self.incoming_root / f".{host}.coordinator.{uuid.uuid4().hex}.pending"
            verify = staged.with_name(staged.name + ".verify")
            try:
                self._sync_file(host, source, staged)
                payload_bytes = staged.read_bytes()
                self._sync_file(host, source, verify)
                if hashlib.sha256(payload_bytes).digest() != hashlib.sha256(verify.read_bytes()).digest():
                    raise ValueError("coordinator snapshot changed while staging")
            finally:
                if verify.exists():
                    verify.unlink()
        try:
            if payload_bytes.startswith(b"SQLite format 3\x00"):
                result = ingest_coordinator_database(self.db, host, staged)
            else:
                payload = json.loads(payload_bytes)
                source_hash = hashlib.sha256(_canonical(payload).encode()).hexdigest()
                result = ingest_coordinator_snapshot(self.db, host, payload, source_hash)
            archive_dir = self.collected_root / host / "coordinator"
            archive_dir.mkdir(parents=True, exist_ok=True)
            archive_target = archive_dir / Path(str(source)).name
            os.replace(staged, archive_target)
            result["snapshot_path"] = str(archive_target)
            return result
        finally:
            if staged.exists():
                staged.unlink()

    def collect_once(self, plan: Mapping[str, Any]) -> dict:
        plan_code_hash = hashlib.sha256(_canonical(plan["catalog"]["file_sha256"]).encode()).hexdigest()
        plan_token = "plan-" + _digest({"codes": plan_code_hash, "catalog_scenarios": plan["catalog"]["scenarios"], "groups": plan["groups"], "uncovered": plan["uncovered"], "universe_sha256": plan["universe_sha256"]})
        if self.db.query("SELECT 1 FROM imports WHERE import_id=?", (plan_token,)):
            plan_result = {"registered": False, "plan_import_id": plan_token}
        else:
            plan_result = ingest_plan(self.db, plan)
            plan_result["registered"] = True
            plan_result["plan_import_id"] = plan_token
        results = []
        for host in self.hosts:
            results.extend(self._collect_local_host() if host == "s1" else self._collect_remote_host(host))
        return {"plan_registered": plan_result, "collected_artifacts": results, "collected_at": _utc_now(), "promotion_enabled": False}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--plan", type=Path, required=True, help="Full frozen plan JSON; registered before any receipt import")
    parser.add_argument("--db", type=Path)
    parser.add_argument("--host", action="append", choices=HOSTS, help="Collector source host; default all hosts")
    parser.add_argument("--remote-root", default=DEFAULT_REMOTE_ROOT)
    parser.add_argument("--coordinator-relative", default=DEFAULT_COORDINATOR_RELATIVE)
    parser.add_argument("--remote-coordinator", default=DEFAULT_REMOTE_COORDINATOR)
    parser.add_argument("--watch", action="store_true", help="Repeat collection; use as a user-managed timer process")
    parser.add_argument("--interval", type=float, default=60.0)
    args = parser.parse_args(argv)
    if args.watch and not 1 <= args.interval <= 3600:
        parser.error("--interval must be 1..3600 seconds")
    root = args.root.resolve()
    db_path = args.db or root / "data" / "paper_forward_lab" / "scenarios.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path = args.plan
    if not plan_path.is_absolute():
        plan_path = root / plan_path
    plan = json.loads(plan_path.read_text())
    while True:
        with ScenarioDB(db_path) as db:
            collector = PaperForwardCollector(root, db, hosts=tuple(args.host or HOSTS), remote_root=args.remote_root, coordinator_relative=args.coordinator_relative, remote_coordinator=args.remote_coordinator)
            result = collector.collect_once(plan)
        print(json.dumps(result, sort_keys=True, allow_nan=False), flush=True)
        if not args.watch:
            break
        time.sleep(args.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
