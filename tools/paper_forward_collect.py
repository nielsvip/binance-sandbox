"""Collect isolated paper worker bundles and coordinator status into central SQL.

Run on S1 after the complete frozen plan has been registered. This collector
only reads remote artifacts, never copies or opens a worker/central live DB.

Ready command on S1 (after placing the immutable plan JSON):
  python3 tools/paper_forward_collect.py --root /home/niels/binance-sandbox \
    --plan /home/niels/binance-sandbox/data/paper_forward_lab/frozen_plan.json \
    --db /home/niels/binance-sandbox/data/paper_forward_lab/scenarios.db \
    --watch --interval 15
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
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
DEFAULT_REMOTE_ROOT = "/home/niels/binance-sandbox/data/paper_forward_lab/runs"
DEFAULT_COORDINATOR_RELATIVE = "data/paper_forward_lab/coordinator_snapshot.json"
DEFAULT_REMOTE_COORDINATOR = "/home/niels/binance-sandbox/data/paper_forward_lab/coordinator_snapshot.json"
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
            snapshot_hash TEXT NOT NULL REFERENCES paper_coordinator_snapshots(snapshot_hash),
            job_id TEXT NOT NULL, sym_side TEXT, scenario_id TEXT, assigned_host TEXT,
            status TEXT NOT NULL, updated_at TEXT, payload_json TEXT NOT NULL,
            PRIMARY KEY(snapshot_hash,job_id)
        );
    """, """
        CREATE TABLE IF NOT EXISTS paper_coordinator_coverage (
            snapshot_hash TEXT NOT NULL REFERENCES paper_coordinator_snapshots(snapshot_hash),
            assigned_host TEXT NOT NULL, status TEXT NOT NULL, job_count INTEGER NOT NULL,
            scenario_count INTEGER NOT NULL, PRIMARY KEY(snapshot_hash,assigned_host,status)
        );
    """, """
        CREATE TABLE IF NOT EXISTS paper_coordinator_coverage_reports (
            snapshot_hash TEXT PRIMARY KEY REFERENCES paper_coordinator_snapshots(snapshot_hash),
            coverage_json TEXT NOT NULL
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
    created = 0
    by_host_status: Counter = Counter()
    by_host_scenarios: dict[tuple[str, str], set[str]] = {}
    with db.transaction():
        _create_coordinator_tables(db.connection)
        snapshot = {
            "snapshot_hash": snapshot_hash,
            "source_host": source_host,
            "observed_at": str(payload.get("observed_at") or payload.get("observed_at_utc") or payload.get("generated_at") or payload.get("updated_at") or _utc_now()),
            "evidence_kind": "coordinator_only",
            "payload_json": payload_json,
        }
        cursor = db.connection.execute("INSERT OR IGNORE INTO paper_coordinator_snapshots VALUES(:snapshot_hash,:source_host,:observed_at,:evidence_kind,:payload_json)", snapshot)
        if not cursor.rowcount:
            existing = db.connection.execute("SELECT * FROM paper_coordinator_snapshots WHERE snapshot_hash=?", (snapshot_hash,)).fetchone()
            if existing is None or tuple(existing) != tuple(snapshot.values()):
                raise ValueError("immutable coordinator snapshot hash conflict")
        for index, raw in enumerate(jobs):
            if not isinstance(raw, Mapping):
                raise ValueError("coordinator job row must be an object")
            row = dict(raw)
            job_id = str(row.get("job_id") or row.get("run_id") or row.get("id") or _digest([snapshot_hash, index, row]))
            assigned_host = str(row.get("host") or row.get("assigned_host") or source_host)
            status = str(row.get("status") or row.get("state") or "UNKNOWN").upper()
            scenario_id = row.get("scenario_id")
            item = {
                "snapshot_hash": snapshot_hash,
                "job_id": job_id,
                "sym_side": row.get("sym_side"),
                "scenario_id": scenario_id,
                "assigned_host": assigned_host,
                "status": status,
                "updated_at": row.get("updated_at") or row.get("updated_at_utc") or row.get("observed_at"),
                "payload_json": _canonical(row),
            }
            cur = db.connection.execute("INSERT OR IGNORE INTO paper_coordinator_jobs VALUES(:snapshot_hash,:job_id,:sym_side,:scenario_id,:assigned_host,:status,:updated_at,:payload_json)", item)
            if cur.rowcount:
                created += 1
            else:
                prior = db.connection.execute("SELECT * FROM paper_coordinator_jobs WHERE snapshot_hash=? AND job_id=?", (snapshot_hash, job_id)).fetchone()
                if prior is None or tuple(prior) != tuple(item.values()):
                    raise ValueError(f"immutable coordinator job conflict: {job_id}")
            by_host_status[(assigned_host, status)] += 1
            if scenario_id is not None:
                by_host_scenarios.setdefault((assigned_host, status), set()).add(str(scenario_id))
        for (assigned_host, status), count in by_host_status.items():
            row = (snapshot_hash, assigned_host, status, count, len(by_host_scenarios.get((assigned_host, status), set())))
            db.connection.execute("INSERT OR IGNORE INTO paper_coordinator_coverage VALUES(?,?,?,?,?)", row)
            prior = db.connection.execute("SELECT * FROM paper_coordinator_coverage WHERE snapshot_hash=? AND assigned_host=? AND status=?", row[:3]).fetchone()
            if prior is None or tuple(prior) != row:
                raise ValueError("immutable coordinator coverage conflict")
        if "coverage" in payload:
            coverage_json = _canonical(payload["coverage"])
            db.connection.execute("INSERT OR IGNORE INTO paper_coordinator_coverage_reports VALUES(?,?)", (snapshot_hash, coverage_json))
            prior_coverage = db.connection.execute("SELECT coverage_json FROM paper_coordinator_coverage_reports WHERE snapshot_hash=?", (snapshot_hash,)).fetchone()
            if prior_coverage is None or prior_coverage[0] != coverage_json:
                raise ValueError("immutable coordinator coverage report conflict")
    return {"snapshot_hash": snapshot_hash, "source_host": source_host, "jobs": len(jobs), "new_jobs": created, "evidence_kind": "coordinator_only"}


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
        if not _SAFE_REMOTE_PATH.fullmatch(remote_coordinator):
            raise ValueError("remote_coordinator must be an absolute safe path")
        self.remote_coordinator = remote_coordinator
        self.coordinator_relative = _safe_relative(coordinator_relative)
        self.runner = runner
        self.incoming_root = self.root / "data" / "paper_forward_lab" / "collector" / "incoming"
        self.collected_root = self.root / "data" / "paper_forward_lab" / "collector" / "collected"

    def _remote_manifest_paths(self, host: str) -> list[str]:
        command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, "find", self.remote_root, "-type", "f", "-name", "worker.result_manifest.json", "-print"]
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
        command = ["rsync", "-a", "--protect-args", "--timeout=45", "-e", "ssh -o BatchMode=yes -o ConnectTimeout=10", "--", f"{host}:{source}", str(pending)]
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
        keys = ("run_id", "epoch_id", "scenario_id", "sym_side", "host", "settings_sha256", "parent_settings_sha256", "code_sha256", "code_file_hashes", "candidate_overrides", "isolation_overrides", "paper_mask_release", "plan_code_hash")
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
            result = self._collect_coordinator(host, self.remote_coordinator)
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
            payload_bytes = source.read_bytes()
            source_host = host
        else:
            stage = self.incoming_root / f".{host}.coordinator.{uuid.uuid4().hex}.pending"
            try:
                self._sync_file(host, source, stage)
                payload_bytes = stage.read_bytes()
                source_host = host
            finally:
                if stage.exists():
                    stage.unlink()
        payload = json.loads(payload_bytes)
        source_hash = hashlib.sha256(_canonical(payload).encode()).hexdigest()
        return ingest_coordinator_snapshot(self.db, source_host, payload, source_hash)

    def collect_once(self, plan: Mapping[str, Any]) -> dict:
        plan_result = ingest_plan(self.db, plan)
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
    parser.add_argument("--interval", type=float, default=15.0)
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
