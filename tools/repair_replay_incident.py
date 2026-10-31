import argparse
import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

def sha(data):
    return hashlib.sha256(data).hexdigest()

def quarantine_rows(raw, target):
    if sha(raw) != target["file"]["current_file_sha256"]:
        raise RuntimeError("Target changed since reviewed repair plan")
    intervals = []
    for row in target["rows"]:
        start, end = int(row["byte_start"]), int(row["byte_end_exclusive"])
        if not 0 <= start < end <= len(raw) or (start and raw[start - 1:start] != b"\n") or (end < len(raw) and raw[end:end + 1] != b"\n"):
            raise RuntimeError("Repair row offsets are not complete line boundaries")
        line = raw[start:end]
        if sha(raw[start:end + (end < len(raw))]) != row["raw_line_sha256"] or json.loads(line) != row["record"]:
            raise RuntimeError("Repair row bytes differ from reviewed evidence")
        canonical = json.dumps(json.loads(line), sort_keys=True, separators=(",", ":"), default=str).encode()
        if sha(canonical) != row["canonical_record_sha256"]:
            raise RuntimeError("Repair row identity differs from reviewed evidence")
        intervals.append((start, end + (end < len(raw))))
    intervals.sort()
    cursor, kept = 0, []
    for start, end in intervals:
        if start < cursor:
            raise RuntimeError("Repair rows overlap")
        kept.append(raw[cursor:start])
        cursor = end
    kept.append(raw[cursor:])
    return b"".join(kept)

def repair(plan_path, expected_plan_sha, root, receipt_dir, apply=False):
    plan_bytes = Path(plan_path).read_bytes()
    if sha(plan_bytes) != expected_plan_sha:
        raise RuntimeError("Repair plan hash mismatch")
    plan = json.loads(plan_bytes)
    root, receipt = Path(root).resolve(), Path(receipt_dir).resolve()
    changes = []
    for target in plan["targets"]:
        if target["kind"] != "jsonl_row_quarantine_candidate":
            continue
        relative = Path(target["relative_path"])
        if not (relative.parent == Path("data/per_trade_returns") or relative == Path("data/stale_set_halt/closes_ez.jsonl")):
            raise RuntimeError("Repair target is outside reviewed performance-input scope")
        path = (root / relative).resolve()
        if not path.is_relative_to(root):
            raise RuntimeError("Repair target escapes root")
        raw = path.read_bytes()
        updated = quarantine_rows(raw, target)
        for row in target["rows"]:
            if not row.get("runlog_matches"):
                raise RuntimeError("Repair row lacks run-specific evidence")
            for match in row["runlog_matches"]:
                evidence = Path(match["path"]).read_bytes()
                if sha(evidence) != match["sha256"]:
                    raise RuntimeError("Archived replay evidence changed")
                lines = evidence.decode().splitlines()
                reason = row["record"]["reason"]
                if not any(reason in lines[int(number) - 1] for number in match["line_numbers"]):
                    raise RuntimeError("Archived replay evidence does not contain exact reason")
        changes.append((path, relative, raw, updated, len(target["rows"])))
    report = {"created_utc": datetime.now(timezone.utc).isoformat(), "plan_sha256": expected_plan_sha, "applied": apply, "files": []}
    if apply:
        receipt.mkdir(parents=True, exist_ok=False)
        shutil.copy2(plan_path, receipt / "reviewed_plan.json")
        for path, relative, raw, updated, count in changes:
            backup = receipt / "before" / relative
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_bytes(raw)
            if sha(backup.read_bytes()) != sha(raw):
                raise RuntimeError("Incident repair backup verification failed")
    for path, relative, raw, updated, count in changes:
        record = {"path": str(path), "removed_confirmed_simulation_rows": count, "before_sha256": sha(raw), "after_sha256": sha(updated)}
        if apply:
            if path.read_bytes() != raw:
                raise RuntimeError("Target changed after backup; no stale overwrite allowed")
            temp = path.with_name(f".{path.name}.incident-repair-{os.getpid()}")
            with temp.open("xb") as stream:
                stream.write(updated)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temp, path.stat().st_mode & 0o777)
            os.replace(temp, path)
            if path.read_bytes() != updated:
                raise RuntimeError("Incident repair read-back differs")
            with (receipt / "applied.jsonl").open("a") as stream:
                stream.write(json.dumps(record) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        report["files"].append(record)
    if apply:
        (receipt / "receipt.json").write_text(json.dumps(report, indent=2) + "\n")
    return report

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", required=True)
    parser.add_argument("--plan-sha256", required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--receipt-dir", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(repair(args.plan, args.plan_sha256, args.root, args.receipt_dir, args.apply), indent=2))
