"""Export every registered scenario/symbol-side into immutable fleet job specs."""
import argparse
import hashlib
import json
import sqlite3
import tempfile
from pathlib import Path

try:
    from .paper_forward_lab import build_plan
    from .paper_inventory import digest, ingest_plan
    from .paper_scenario_db import ScenarioDB
except ImportError:
    from paper_forward_lab import build_plan
    from paper_inventory import digest, ingest_plan
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
    plan = build_plan(root)
    with sqlite3.connect((root / "data/hourly_reconfig/per_sym_store.db").resolve().as_uri() + "?mode=ro", uri=True) as source:
        source.execute("BEGIN")
        for group in plan["groups"]:
            row = source.execute("SELECT full_config_json FROM per_sym_active WHERE sym_side=?", (group["sym_side"],)).fetchone()
            settings = json.loads(row[0]) if row else None
            if digest(settings) != group["settings_sha256"]:
                raise RuntimeError("settings changed during export; rebuild the plan")
            group["effective_settings"] = settings
    return plan

def export(plan, destination, remote_root, engine_root):
    destination = Path(destination)
    remote_root = Path(remote_root)
    code_hash = digest(plan["catalog"]["file_sha256"])
    batch_id = digest({"groups": plan["groups"], "code": code_hash})[:20]
    batch = destination / batch_id
    remote_batch = remote_root / "data/paper_forward_lab/independent" / batch_id
    counts = {host: {"jobs": 0, "launchable": 0, "pending_parameters": 0} for host in ("s1", "s2", "s5", "s6")}
    jobs = {host: [] for host in counts}
    write_once(batch / "plan.json", plan)
    with tempfile.TemporaryDirectory(prefix="paper-launch-registry-") as temporary:
        with ScenarioDB(Path(temporary) / "registry.db") as db:
            ingest_plan(db, plan)
            for group in plan["groups"]:
                host = group["host"]
                base_hash = group["settings_sha256"]
                write_once(batch / host / "inputs/settings" / (base_hash + ".json"), group["effective_settings"])
                rows = db.query("SELECT r.run_id,r.scenario_id,r.status,s.parameters_json FROM runs r JOIN scenarios s USING(scenario_id) WHERE r.sym_side=? AND r.settings_hash=? AND r.evidence_kind='catalog_only'", (group["sym_side"], base_hash))
                for row in rows:
                    parameters = json.loads(row["parameters_json"])
                    overrides = parameters["overrides"]
                    override_hash = digest(overrides)
                    write_once(batch / host / "inputs/overrides" / (override_hash + ".json"), overrides)
                    run_id = "forward-" + digest([row["scenario_id"], group["epoch_id"], "independent-v1"])
                    native = sorted({entry for entry in ("1m", "3m", "5m") if ("_" + entry + "_") in ("_" + str(parameters["catalog"].get("switch") or "").lower() + "_")})
                    timeframe = "1m"
                    launchable = row["status"] != "PENDING_PARAMETERS"
                    spec = {"schema_version": 1, "run_id": run_id, "epoch_id": group["epoch_id"], "scenario_id": row["scenario_id"], "planned_run_id": row["run_id"], "sym_side": group["sym_side"], "venue": group["venue"], "host": host, "plan_code_hash": code_hash, "parent_settings_sha256": base_hash, "full_settings": str(remote_batch / host / "inputs/settings" / (base_hash + ".json")), "candidate_overrides": str(remote_batch / host / "inputs/overrides" / (override_hash + ".json")), "registered_candidate_overrides": overrides, "paper_mask_release": sorted(overrides), "experiment_context": "isolated_all_blanket_paths_blocked_except_candidate_v1", "engine_root": str(engine_root), "frame_timeframe": timeframe, "required_timeframes": sorted(set([timeframe] + native)), "poll_seconds": 10, "max_age_seconds": 120, "launchable": launchable, "pending_reason": None if launchable else "PENDING_PARAMETER_VALUES", "snapshot": str(remote_root / "data/paper_forward_lab/feed" / (group["venue"] + ".json")), "receipt": str(remote_root / "data/paper_forward_lab/feed" / (group["venue"] + ".receipt.json"))}
                    filename = run_id + ".json"
                    write_once(batch / host / "specs" / filename, spec)
                    jobs[host].append({"spec_file": filename, **{key: spec[key] for key in ("run_id", "epoch_id", "scenario_id", "sym_side", "venue", "host")}})
                    counts[host]["jobs"] += 1
                    counts[host]["launchable" if launchable else "pending_parameters"] += 1
            for host in counts:
                write_once(batch / host / "specs/manifest.json", {"schema_version": 1, "jobs": jobs[host]})
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
