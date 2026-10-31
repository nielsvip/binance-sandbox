"""Persistent paper scenario inventory, evidence import and immutable tuning variants."""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

try:
    from .paper_scenario_db import ScenarioDB
    from .paper_forward_lab import build_plan
except ImportError:
    from paper_scenario_db import ScenarioDB
    from paper_forward_lab import build_plan

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()

def now():
    return datetime.now(timezone.utc).isoformat()

def ensure_scenario(db, scenario_id, path_id, side, parameters, parent=None):
    previous = db.query("SELECT created_at FROM scenarios WHERE scenario_id=?", (scenario_id,))
    db.add_scenario(scenario_id=scenario_id, path_id=path_id, side=side, parameters=parameters, parent_scenario_id=parent, created_at=previous[0][0] if previous else now())

def ingest_plan(db, plan):
    code_hash = digest(plan["catalog"]["file_sha256"])
    by_catalog = {}
    with db.transaction():
        for item in plan["catalog"]["scenarios"]:
            path_id = "catalog-path-" + digest([item["venue"], item["switch"], item["coverage_kind"], item["provenance"]])
            db.add_path(path_id=path_id, venue=item["venue"], path_name=item["switch"] or "BASELINE", switch_name=item["switch"] or "", classification=item["coverage_kind"], provenance=item["provenance"])
            candidates = [None] if item["kind"] == "baseline" or item["default_kind"] != "boolean" else [False, True]
            by_catalog[item["scenario_id"]] = []
            for value in candidates:
                parameters = {"overrides": {} if value is None else {item["switch"]: value}, "parameter_status": "RESOLVED" if item["kind"] == "baseline" or value is not None else "NEEDS_AUTHORITATIVE_VALUES", "catalog": item}
                scenario_id = "scenario-" + digest([path_id, item["side"], parameters])
                ensure_scenario(db, scenario_id, path_id, item["side"], parameters)
                by_catalog[item["scenario_id"]].append((scenario_id, parameters["parameter_status"]))
        for group in plan["groups"]:
            for catalog_id in group["scenario_ids"]:
                for scenario_id, parameter_status in by_catalog[catalog_id]:
                    run_id = "planned-" + digest([scenario_id, group["epoch_id"]])
                    if db.query("SELECT 1 FROM runs WHERE run_id=?", (run_id,)):
                        continue
                    db.add_run(run_id=run_id, scenario_id=scenario_id, sym_side=group["sym_side"], host=group["host"], settings_hash=group["settings_sha256"], code_hash=code_hash, evidence_kind="catalog_only", status="PENDING_VALIDATED_EXECUTION" if parameter_status == "RESOLVED" else "PENDING_PARAMETERS", started_at=plan["planned_at"], ended_at=None)
        token = "plan-" + digest({"codes": code_hash, "groups": plan["groups"], "uncovered": plan["uncovered"]})
        if not db.query("SELECT 1 FROM imports WHERE import_id=?", (token,)):
            db.record_import(import_id=token, source_path=plan["universe_source"], source_hash=plan["universe_sha256"], imported_at=now(), source_computed_at=None)
    return {"groups": len(plan["groups"]), "uncovered": plan["uncovered"]}

def variant(db, parent, overrides, note):
    rows = db.query("SELECT * FROM scenarios WHERE scenario_id=?", (parent,))
    if not rows:
        raise ValueError("unknown parent scenario")
    row = rows[0]
    parameters = json.loads(row["parameters_json"])
    parameters["overrides"] = {**parameters.get("overrides", {}), **overrides}
    parameters["parameter_status"] = "USER_DEFINED_UNVALIDATED"
    scenario_id = "variant-" + digest([parent, parameters])
    with db.transaction():
        ensure_scenario(db, scenario_id, row["path_id"], row["side"], parameters, parent)
        note_id = int(digest([scenario_id, note])[:15], 16)
        existing = db.query("SELECT created_at FROM tuning_notes WHERE id=?", (note_id,))
        db.add_note(id=note_id, scenario_id=scenario_id, created_at=existing[0][0] if existing else now(), note=note)
    return {"scenario_id": scenario_id, "status": "DEFINED_NOT_RUNNING", "promotion_enabled": False}

def summary(db):
    counts = {table: db.query("SELECT COUNT(*) FROM " + table)[0][0] for table in ("paths", "scenarios", "runs", "decisions", "trades", "results", "imports", "tuning_notes")}
    counts["runs_by_evidence"] = [dict(row) for row in db.query("SELECT evidence_kind,status,COUNT(*) AS count FROM runs GROUP BY evidence_kind,status")]
    counts["integrity"] = db.query("PRAGMA quick_check")[0][0]
    counts["promotion_enabled"] = False
    return counts

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--db", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    refresh = commands.add_parser("refresh")
    refresh.add_argument("--plan", type=Path, help="Frozen plan export; otherwise build from current local settings")
    commands.add_parser("status")
    tune = commands.add_parser("variant")
    tune.add_argument("--parent", required=True)
    tune.add_argument("--set", action="append", required=True)
    tune.add_argument("--note", required=True)
    args = parser.parse_args()
    database = args.db or args.root / "data/paper_forward_lab/scenarios.db"
    database.parent.mkdir(parents=True, exist_ok=True)
    with ScenarioDB(database) as db:
        if args.command == "refresh":
            try:
                from .paper_inventory_evidence import ingest_evidence
            except ImportError:
                from paper_inventory_evidence import ingest_evidence
            plan = json.loads(args.plan.read_text()) if args.plan else build_plan(args.root)
            ingest_plan(db, plan)
            ingest_evidence(db, args.root)
            result = summary(db)
        elif args.command == "variant":
            overrides = {}
            for assignment in args.set:
                key, value = assignment.split("=", 1)
                if not key.isidentifier():
                    parser.error("switch names must be identifiers")
                overrides[key] = json.loads(value)
            result = variant(db, args.parent, overrides, args.note)
        else:
            result = summary(db)
    print(json.dumps(result, indent=2, allow_nan=False))

if __name__ == "__main__":
    main()
