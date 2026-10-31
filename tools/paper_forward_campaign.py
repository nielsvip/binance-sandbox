#!/usr/bin/env python3
"""Prepare the next finite paper epoch from completed coordinator snapshots.

This helper only reads the prior immutable release and coordinator/journal
snapshots. It can export a new immutable batch on an explicit CLI request; it
never deploys, changes service units, or stops workers.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

try:
    from . import paper_forward_launch_plan as launch_plan
except ImportError:
    import paper_forward_launch_plan as launch_plan


TERMINAL_STATES = {
    "COMPLETED", "COMPLETED_MULTI_FRAME_ENGINE_RUN", "FAILED", "INTERRUPTED",
    "INVALID_SPEC", "INVALID_EXPERIMENT_WINDOW", "UNMANIFESTED_SPEC",
    "MISSING_SPEC", "SPEC_CHANGED", "STOPPED_BY_REQUEST",
    "STOPPED_RESOURCE_LIMIT", "DATA_GAP", "SETTINGS_MISMATCH",
}
MAX_STATUS_BYTES = 64 * 1024
MAX_RESULT_BYTES = 2 * 1024 * 1024
DEFAULT_RESERVE_BYTES = 8 * 1024**3


def _canonical_hash(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def _read_json(path, max_bytes):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > max_bytes:
        raise ValueError(f"missing, symlinked, or oversized JSON file: {path}")
    raw = path.read_bytes()
    if len(raw) > max_bytes:
        raise ValueError(f"oversized JSON file: {path}")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _read_only_db(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"missing or symlinked coordinator snapshot: {path}")
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    connection.execute("PRAGMA query_only=ON")
    return connection


def _parse_utc(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.timestamp()


def _read_coordinator(path, host, now, max_snapshot_age_seconds):
    connection = _read_only_db(path)
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"jobs", "coordinator_heartbeat"}.issubset(tables):
            raise ValueError(f"coordinator snapshot missing required tables for {host}")
        heartbeat = connection.execute("SELECT at_utc,status FROM coordinator_heartbeat WHERE host=?", (host,)).fetchone()
        heartbeat_time = _parse_utc(heartbeat[0]) if heartbeat else None
        if heartbeat_time is None or now - heartbeat_time > max_snapshot_age_seconds or heartbeat_time > now + 60:
            raise ValueError(f"coordinator snapshot is stale or has invalid heartbeat for {host}")
        columns = {row[1] for row in connection.execute("PRAGMA table_info(jobs)")}
        needed = {"run_id", "epoch_id", "scenario_id", "sym_side", "window_id", "status", "run_dir", "is_baseline", "detail"}
        if not needed.issubset(columns):
            raise ValueError(f"coordinator snapshot missing job fields for {host}")
        rows = connection.execute("SELECT run_id,epoch_id,scenario_id,sym_side,window_id,status,run_dir,is_baseline,detail FROM jobs WHERE assigned_host=?", (host,)).fetchall()
        return {(row[1], row[2], row[3], row[4]): {"run_id": row[0], "status": str(row[5]).upper(), "run_dir": row[6], "is_baseline": bool(row[7]), "detail": row[8]} for row in rows}
    finally:
        connection.close()


def _read_journal_status(path, host, venue, now, max_status_age_seconds):
    status = _read_json(path, MAX_STATUS_BYTES)
    if status.get("host") != host or status.get("venue") != venue:
        raise ValueError(f"journal status identity mismatch: {path}")
    observed = _parse_utc(status.get("poll_observed_at_utc"))
    if observed is None or now - observed > max_status_age_seconds or observed > now + 60:
        raise ValueError(f"journal status is stale or has invalid clock: {path}")
    blocked = status.get("blocked_timeframes", [])
    eligible = status.get("eligible_timeframes", [])
    if not isinstance(blocked, list) or not isinstance(eligible, list):
        raise ValueError(f"journal timeframe status is malformed: {path}")
    return {**status, "blocked_timeframes": set(blocked), "eligible_timeframes": set(eligible)}


def _result_resolution(run_dir, root, spec, expected_window_id):
    if not run_dir:
        return None
    run_dir = Path(run_dir)
    if run_dir.is_symlink():
        return None
    try:
        run_dir = run_dir.resolve(strict=True)
        if not run_dir.is_relative_to(Path(root).resolve(strict=True)):
            return None
        manifest = _read_json(run_dir / "worker.result_manifest.json", MAX_RESULT_BYTES)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None
    if manifest.get("run_id") != spec.get("run_id") or manifest.get("epoch_id") != spec.get("epoch_id") or manifest.get("sym_side") != spec.get("sym_side"):
        return None
    resolved = manifest.get("experiment_window_resolution")
    if not isinstance(resolved, dict) or resolved.get("window_id") != expected_window_id:
        return None
    try:
        start = float(resolved["start_source_ts"])
        end = float(resolved["end_source_ts"])
    except (KeyError, TypeError, ValueError):
        return None
    if not math.isfinite(start) or not math.isfinite(end) or start <= 0 or end < start:
        return None
    return {"start_source_ts": start, "end_source_ts": end, "window_id": expected_window_id}


def inspect_previous_epoch(release_dir, coordinator_snapshots, journal_status_paths, root, now=None, max_status_age_seconds=300):
    """Check whether eligible clocks may advance; explicitly retain blocked clocks."""
    release_dir = Path(release_dir).resolve(strict=True)
    root = Path(root).resolve(strict=True)
    plan = _read_json(release_dir / "plan.json", 64 * 1024**2)
    groups = plan.get("groups")
    snapshots = plan.get("settings_snapshots")
    window = plan.get("experiment_window")
    if not isinstance(groups, list) or not isinstance(snapshots, dict) or not isinstance(window, dict):
        raise ValueError("prior plan must contain groups, settings_snapshots, and experiment_window")
    timestamp = datetime.now(timezone.utc).timestamp() if now is None else float(now)
    hosts = sorted({str(group["host"]) for group in groups})
    states_by_host = {}
    journals_by_host = {}
    for host in hosts:
        if host not in coordinator_snapshots or host not in journal_status_paths:
            raise ValueError(f"missing read-only snapshot paths for host {host}")
        states_by_host[host] = _read_coordinator(coordinator_snapshots[host], host, timestamp, max_status_age_seconds)
        venue = "crypto" if host in ("s1", "s2") else "stocks"
        journals_by_host[host] = _read_journal_status(journal_status_paths[host], host, venue, timestamp, max_status_age_seconds)

    next_groups = []
    carryovers = []
    pending_parameters = []
    incomplete = []
    resolutions = []
    advanced_clock_groups = 0
    for group in groups:
        host, venue, timeframe = group.get("host"), group.get("venue"), group.get("frame_timeframe")
        journal = journals_by_host[host]
        batch_host_dir = release_dir / host
        manifest = _read_json(batch_host_dir / "specs" / "manifest.json", 64 * 1024**2)
        by_scenario = {item.get("scenario_id"): item for item in manifest.get("jobs", []) if item.get("sym_side") == group.get("sym_side") and item.get("epoch_id") == group.get("epoch_id")}
        if timeframe in journal["blocked_timeframes"]:
            carryovers.append({"host": host, "venue": venue, "sym_side": group.get("sym_side"), "frame_timeframe": timeframe, "scenario_ids": list(group.get("scenario_ids", [])), "reason": "NATIVE_TIMEFRAME_BLOCKED", "journal_status": journal.get("status")})
            for scenario_id in group.get("scenario_ids", []):
                item = by_scenario.get(scenario_id)
                if not item:
                    continue
                blocked_spec = _read_json(batch_host_dir / "specs" / item["spec_file"], 2 * 1024**2)
                if blocked_spec.get("launchable", True) is False:
                    pending_parameters.append({"host": host, "sym_side": group["sym_side"], "scenario_id": scenario_id, "frame_timeframe": timeframe, "status": "PENDING_PARAMETERS"})
            continue
        if timeframe not in journal["eligible_timeframes"]:
            incomplete.append({"host": host, "sym_side": group.get("sym_side"), "frame_timeframe": timeframe, "reason": "TIMEFRAME_NOT_EXPLICITLY_ELIGIBLE"})
            continue
        group_resolutions = []
        group_blockers = []
        for scenario_id in group.get("scenario_ids", []):
            manifest_item = by_scenario.get(scenario_id)
            if not manifest_item:
                group_blockers.append({"scenario_id": scenario_id, "reason": "JOB_MISSING_FROM_MANIFEST"})
                continue
            spec_path = batch_host_dir / "specs" / manifest_item["spec_file"]
            spec = _read_json(spec_path, 2 * 1024**2)
            if spec.get("launchable", True) is False:
                pending_parameters.append({"host": host, "sym_side": group["sym_side"], "scenario_id": scenario_id, "frame_timeframe": timeframe, "status": "PENDING_PARAMETERS"})
                continue
            key = (spec.get("epoch_id"), scenario_id, spec.get("sym_side"), (spec.get("experiment_window") or {}).get("window_id"))
            state = states_by_host[host].get(key)
            if state is None:
                group_blockers.append({"scenario_id": scenario_id, "reason": "JOB_STATUS_MISSING"})
                continue
            if state["status"] not in TERMINAL_STATES:
                group_blockers.append({"scenario_id": scenario_id, "status": state["status"], "reason": "JOB_NOT_TERMINAL"})
                continue
            if spec.get("is_baseline") and state["status"] != "COMPLETED":
                group_blockers.append({"scenario_id": scenario_id, "status": state["status"], "reason": "MATCHED_BASELINE_NOT_COMPLETED"})
                continue
            if state["status"] == "COMPLETED":
                resolved = _result_resolution(state["run_dir"], root, spec, (spec.get("experiment_window") or {}).get("window_id"))
                if resolved:
                    group_resolutions.append(resolved)
                elif spec.get("is_baseline"):
                    group_blockers.append({"scenario_id": scenario_id, "reason": "BASELINE_WINDOW_RESOLUTION_MISSING"})
        if group_blockers:
            incomplete.extend({"host": host, "sym_side": group["sym_side"], "frame_timeframe": timeframe, **item} for item in group_blockers)
            continue
        baselines = [item for item in group_resolutions if item.get("window_id")]
        if not baselines:
            incomplete.append({"host": host, "sym_side": group["sym_side"], "frame_timeframe": timeframe, "reason": "NO_COMPLETED_BASELINE_RESOLUTION"})
            continue
        next_groups.append(copy.deepcopy(group))
        resolutions.extend(baselines)
        advanced_clock_groups += 1

    if incomplete:
        return {"status": "WAITING_FOR_PRIOR_ELIGIBLE_JOBS", "plan": None, "carryovers": carryovers, "pending_parameters": pending_parameters, "incomplete": incomplete}
    if not advanced_clock_groups:
        return {"status": "WAITING_FOR_ELIGIBLE_COMPLETED_CLOCKS", "plan": None, "carryovers": carryovers, "pending_parameters": pending_parameters, "incomplete": incomplete}
    next_not_before = max(item["end_source_ts"] for item in resolutions) + 1.0
    new_window = {"window_id": "prospective-six-hour-" + str(int(next_not_before)), "source_timeframe": "1m", "anchor": "first_available_after", "not_before_source_ts": next_not_before, "duration_seconds": float(window.get("duration_seconds", 21600))}
    next_plan = copy.deepcopy(plan)
    next_plan["experiment_window"] = new_window
    next_plan["groups"] = next_groups
    next_plan["planned_at"] = datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    next_plan["status"] = "PLAN_ONLY"
    next_plan["running_scenarios"] = 0
    next_plan["promotion_enabled"] = False
    next_plan["campaign_parent_release"] = str(release_dir)
    next_plan["campaign_parent_window_id"] = window.get("window_id")
    next_plan["campaign_carryovers"] = carryovers
    next_plan["campaign_pending_parameters"] = pending_parameters
    return {"status": "READY_WITH_CARRYOVER" if carryovers or pending_parameters else "READY", "plan": next_plan, "not_before_source_ts": next_not_before, "carryovers": carryovers, "pending_parameters": pending_parameters, "incomplete": []}


def export_next_epoch(audit, destination, remote_root, engine_root, minimum_free_bytes=DEFAULT_RESERVE_BYTES):
    """Materialize a new immutable local release after a conservative disk check."""
    if audit.get("status") not in {"READY", "READY_WITH_CARRYOVER"} or not isinstance(audit.get("plan"), dict):
        raise ValueError("prior epoch is not ready for rollover")
    destination = Path(destination)
    parent = destination.parent.resolve(strict=True)
    if destination.exists():
        raise FileExistsError(f"epoch destination already exists: {destination}")
    old_release = Path(audit["plan"]["campaign_parent_release"])
    old_plan_path = old_release / "plan.json"
    # The prior specs provide a conservative estimate, scaled by retained groups.
    total_bytes = sum(path.stat().st_size for path in old_release.rglob("*") if path.is_file())
    old_groups = max(1, len(_read_json(old_plan_path, 64 * 1024**2).get("groups", [])))
    new_groups = max(1, len(audit["plan"].get("groups", [])))
    estimate_bytes = int(total_bytes * new_groups / old_groups * 1.5) + 16 * 1024**2
    free_bytes = shutil.disk_usage(parent).free
    if free_bytes < minimum_free_bytes + estimate_bytes:
        raise OSError(f"insufficient disk reserve: free={free_bytes}, reserve={minimum_free_bytes}, estimated_export={estimate_bytes}")
    report = launch_plan.export(audit["plan"], destination, remote_root, engine_root)
    return {**report, "estimated_bytes": estimate_bytes, "free_bytes_before": free_bytes, "minimum_free_bytes": minimum_free_bytes, "campaign_parent_release": str(old_release)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--coordinator-snapshot", action="append", default=[], metavar="HOST=PATH")
    parser.add_argument("--journal-status", action="append", default=[], metavar="HOST=PATH")
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--remote-root", type=Path)
    parser.add_argument("--engine-root", type=Path)
    parser.add_argument("--minimum-free-bytes", type=int, default=DEFAULT_RESERVE_BYTES)
    args = parser.parse_args()
    def parse_pairs(values):
        output = {}
        for value in values:
            host, separator, path = value.partition("=")
            if not separator or host in output:
                parser.error("host paths must be unique HOST=PATH pairs")
            output[host] = Path(path)
        return output
    audit = inspect_previous_epoch(args.release, parse_pairs(args.coordinator_snapshot), parse_pairs(args.journal_status), args.root)
    if args.destination:
        if not args.remote_root or not args.engine_root:
            parser.error("export requires --remote-root and --engine-root")
        audit["export"] = export_next_epoch(audit, args.destination, args.remote_root, args.engine_root, args.minimum_free_bytes)
    print(json.dumps({key: value for key, value in audit.items() if key != "plan"} | ({"next_plan_sha256": _canonical_hash(audit["plan"])} if audit.get("plan") else {}), indent=2, sort_keys=True))
    return 0 if audit["status"] in {"READY", "READY_WITH_CARRYOVER"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
