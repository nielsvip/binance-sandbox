"""Export every registered scenario/symbol-side into immutable fleet job specs."""
import argparse
import hashlib
import json
import sqlite3
import tempfile
import time
from pathlib import Path

try:
    from .paper_forward_lab import utc_now
    from .paper_scenario_catalog import inventory
    from .paper_fleet_budget import stable_host
    from .paper_inventory import digest, ingest_plan
    from .paper_forward_parameters import resolve_catalog
    from .paper_scenario_db import ScenarioDB
except ImportError:
    from paper_forward_lab import utc_now
    from paper_scenario_catalog import inventory
    from paper_fleet_budget import stable_host
    from paper_inventory import digest, ingest_plan
    from paper_forward_parameters import resolve_catalog
    from paper_scenario_db import ScenarioDB

def write_once(path, value):
    path = Path(path)
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_text() != raw:
            raise ValueError(f"immutable plan file conflict: {path}")
        return
    path.write_text(raw)

def build(root):
    root = Path(root)
    anchor = int(time.time())
    catalog = resolve_catalog(inventory(root), root / "SPREADSHEETS/TEMPLATE_FINAL_NORM")
    database = root / "data/hourly_reconfig/per_sym_store.db"
    groups, missing = [], []
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as source:
        source.execute("BEGIN")
        rows = source.execute("SELECT sym_side,cat_side,updated_at,full_config_json FROM per_sym_active ORDER BY sym_side").fetchall()
        universe = [row[0] for row in rows]
        for sym_side, cat_side, updated_at, raw in rows:
            if not isinstance(sym_side, str) or "_" not in sym_side or sym_side.rsplit("_", 1)[1] not in ("LONG", "SHORT"):
                missing.append({"sym_side": str(sym_side), "reason": "INVALID_SYM_SIDE"})
                continue
            symbol, side = sym_side.rsplit("_", 1)
            venue = {"CRYPTO_LONG": "crypto", "CRYPTO_SHORT": "crypto", "STOCKS_LONG": "stocks", "STOCKS_SHORT": "stocks"}.get(cat_side)
            if venue is None or not cat_side.endswith("_" + side):
                missing.append({"sym_side": sym_side, "reason": "INVALID_CAT_SIDE"})
                continue
            settings = json.loads(raw) if raw else None
            if not isinstance(settings, dict) or not settings:
                missing.append({"sym_side": sym_side, "reason": "EMPTY_ACTIVE_SETTINGS"})
                continue
            settings_hash = digest(settings)
            selected = [item["scenario_id"] for item in catalog["scenarios"] if item["venue"] == venue and item["side"] == side]
            groups.append({"sym_side": sym_side, "venue": venue, "side": side, "host": stable_host(venue, symbol, side), "settings_updated_at": updated_at, "settings_sha256": settings_hash, "effective_settings": settings, "scenario_ids": selected, "scenario_count": len(selected), "status": "AWAITING_ADMISSION", "epoch_id": digest({"sym_side": sym_side, "settings": settings_hash, "sources": catalog["file_sha256"]})})
    allowed = json.loads((root / "data/v15_best_first.json").read_text())["allowed"]
    missing.extend({"sym_side": symbol, "reason": "NO_ACTIVE_SETTINGS"} for symbol in sorted(set(allowed) - set(universe)))
    return {"schema_version": 1, "experiment_window": {"window_id": "prospective-six-hour-" + str(anchor), "source_timeframe": "1m", "anchor": "first_available_after", "not_before_source_ts": anchor, "duration_seconds": 21600}, "planned_at": utc_now(), "status": "PLAN_ONLY", "running_scenarios": 0, "promotion_enabled": False, "catalog": catalog, "universe_sha256": digest(universe), "universe_source": str(database) + "#per_sym_active/all", "settings_source": str(database), "groups": groups, "uncovered": missing}

def native_timeframes(switch):
    name = "_" + str(switch or "").lower() + "_"
    return [timeframe for timeframe in ("1m", "3m", "5m") if "_" + timeframe + "_" in name]

def partition_clock_groups(plan):
    catalog = {item["scenario_id"]: item for item in plan["catalog"]["scenarios"]}
    snapshots = dict(plan.get("settings_snapshots", {}))
    groups = []
    for original in plan["groups"]:
        group = dict(original)
        if "effective_settings" in group:
            snapshots[group["settings_sha256"]] = group.pop("effective_settings")
        if group.get("frame_timeframe"):
            groups.append(group)
            continue
        baseline = [key for key in group["scenario_ids"] if catalog[key]["kind"] == "baseline"]
        clocks = {}
        default = "3m" if group["venue"] == "crypto" else "5m"
        for key in group["scenario_ids"]:
            item = catalog[key]
            if item["kind"] == "baseline":
                continue
            needed = native_timeframes(item.get("switch"))
            clock = needed[0] if needed else default
            clocks.setdefault(clock, []).append(key)
        for clock, selected in sorted(clocks.items()):
            groups.append({**group, "frame_timeframe": clock, "scenario_ids": baseline + selected, "scenario_count": len(baseline) + len(selected)})
    return {**plan, "groups": groups, "settings_snapshots": snapshots}

def export(plan, destination, remote_root, engine_root):
    plan = partition_clock_groups(plan)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    remote_root = Path(remote_root)
    for group in plan["groups"]:
        group["epoch_id"] = digest({"sym_side": group["sym_side"], "settings": group["settings_sha256"], "sources": plan["catalog"]["file_sha256"], "runtime": str(engine_root), "clock": group["frame_timeframe"], "window": plan.get("experiment_window"), "experiment": "isolated_all_blanket_paths_blocked_except_candidate_v1"})
    code_hash = digest(plan["catalog"]["file_sha256"])
    batch_id = digest({"groups": plan["groups"], "code": code_hash})[:20]
    batch = destination / batch_id
    remote_batch = remote_root / "data/paper_forward_lab/independent" / batch_id
    counts = {host: {"jobs": 0, "launchable": 0, "pending_parameters": 0} for host in ("s1", "s2", "s5", "s6")}
    jobs = {host: [] for host in counts}
    write_once(batch / "plan.json", plan)
    with tempfile.TemporaryDirectory(prefix="paper-launch-registry-", dir=destination) as temporary:
        with ScenarioDB(Path(temporary) / "registry.db") as db:
            ingest_plan(db, plan)
            for group in plan["groups"]:
                host = group["host"]
                base_hash = group["settings_sha256"]
                write_once(batch / host / "inputs/settings" / (base_hash + ".json"), plan["settings_snapshots"][base_hash])
                rows = db.query("SELECT r.run_id,r.scenario_id,r.status,s.parameters_json FROM runs r JOIN scenarios s USING(scenario_id) WHERE r.sym_side=? AND r.settings_hash=? AND r.evidence_kind='catalog_only'", (group["sym_side"], base_hash))
                for row in rows:
                    if row["run_id"] != "planned-" + digest([row["scenario_id"], group["epoch_id"]]):
                        continue
                    parameters = json.loads(row["parameters_json"])
                    overrides = parameters["overrides"]
                    override_hash = digest(overrides)
                    write_once(batch / host / "inputs/overrides" / (override_hash + ".json"), overrides)
                    run_id = "forward-" + digest([row["scenario_id"], group["epoch_id"], "independent-v1"])
                    native = native_timeframes(parameters["catalog"].get("switch"))
                    timeframe = group["frame_timeframe"]
                    window = {**plan["experiment_window"], "source_timeframe": timeframe, "window_id": plan["experiment_window"]["window_id"] + "-" + timeframe} if plan.get("experiment_window") else None
                    launchable = row["status"] != "PENDING_PARAMETERS"
                    spec = {"schema_version": 1, "experiment_window": window, "is_baseline": parameters["catalog"]["kind"] == "baseline", "source_journal": str(remote_root / "data/paper_forward_lab" / ("native_journal_" + group["venue"] + ".sqlite3")), "run_id": run_id, "epoch_id": group["epoch_id"], "scenario_id": row["scenario_id"], "planned_run_id": row["run_id"], "sym_side": group["sym_side"], "venue": group["venue"], "host": host, "plan_code_hash": code_hash, "parent_settings_sha256": base_hash, "full_settings": str(remote_batch / host / "inputs/settings" / (base_hash + ".json")), "candidate_overrides": str(remote_batch / host / "inputs/overrides" / (override_hash + ".json")), "registered_candidate_overrides": overrides, "paper_mask_release": sorted(overrides), "experiment_context": {"name": "isolated_all_blanket_paths_blocked_except_candidate_v1", "release_nonvectorizable": True, "release_sub15m": True}, "engine_root": str(engine_root), "frame_timeframe": timeframe, "required_timeframes": sorted(set([timeframe] + native)), "poll_seconds": 10, "max_age_seconds": {"1m":120,"3m":360,"5m":600}[timeframe], "launchable": launchable, "pending_reason": None if launchable else "PENDING_PARAMETER_VALUES", "snapshot": str(remote_root / "data/paper_forward_lab/feed" / (group["venue"] + ".json")), "receipt": str(remote_root / "data/paper_forward_lab/feed" / (group["venue"] + ".receipt.json"))}
                    filename = run_id + ".json"
                    write_once(batch / host / "specs" / filename, spec)
                    jobs[host].append({"spec_file": filename, **{key: spec[key] for key in ("run_id", "epoch_id", "scenario_id", "sym_side", "venue", "host")}})
                    counts[host]["jobs"] += 1
                    counts[host]["launchable" if launchable else "pending_parameters"] += 1
            for host in counts:
                write_once(batch / host / "specs/manifest.json", {"schema_version": 1, "jobs": jobs[host]})
    for host in counts:
        host_root = batch / host
        file_hashes = {str(path.relative_to(host_root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(host_root.rglob("*.json")) if path.name != "bundle_manifest.json"}
        write_once(host_root / "bundle_manifest.json", {"file_sha256": file_hashes})
    report = {"batch_id": batch_id, "directory": str(batch), "remote_directory": str(remote_batch), "counts": counts, "uncovered": plan["uncovered"], "running_workers": 0}
    write_once(batch / "export.json", report)
    return report

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--remote-root", type=Path, required=True)
    parser.add_argument("--engine-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(export(build(args.root), args.destination, args.remote_root, args.engine_root), indent=2))

if __name__ == "__main__":
    main()
