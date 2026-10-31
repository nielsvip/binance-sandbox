"""Compile a separate historical population with unknown document-level predictive context."""
import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import shutil


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as source:
        for payload in iter(lambda: source.read(1048576), b""):
            result.update(payload)
    return result.hexdigest()


def neutralize_event(event):
    result = dict(event)
    result["unverified_document_context_not_features"] = {key: event.get(key) for key in ("g", "ti", "bh", "rd")}
    result.update(g="UNK", ti=None, bh=None, rd=None, runtime_candidate=False)
    return result


def compile_population(source_dir, out_dir):
    source_dir, root = Path(source_dir), Path(out_dir)
    report = json.loads((source_dir / "population.json").read_text())
    if report.get("status") != "expanded_population_converted_not_training_approved":
        raise ValueError("requires a complete but unapproved expanded source population")
    for name, expected in report["artifacts_sha256"].items():
        if digest(source_dir / name) != expected:
            raise ValueError(f"source artifact hash mismatch: {name}")
    root.mkdir(parents=True, exist_ok=False)
    before = dict(report["artifacts_sha256"])
    name = "spill_expanded_explicit_progress.tsv.gz"
    events = 0
    with gzip.open(source_dir / name, "rt") as source, gzip.open(root / name, "wt", compresslevel=3) as output:
        for line in source:
            timestamp, _, payload = line.partition("\t")
            event = json.loads(payload)
            output.write(timestamp + "\t" + json.dumps(neutralize_event(event), separators=(",", ":"), default=str) + "\n")
            events += 1
    cases = "cases_preserved_not_training.jsonl.gz"
    shutil.copyfile(source_dir / cases, root / cases)
    if any(digest(source_dir / name) != expected for name, expected in before.items()):
        raise ValueError("source population changed during context compilation")
    report.update(source_population=str(source_dir), pre_neutralization_artifacts_sha256=before, context_policy="g=UNK; ti/bh/rd=None; global progress-document context has no per-evaluation as-of proof and must never enter historical features", runtime_policy="historical observations are not runtime candidates; actual current-template and known-runtime-field validation required before any candidate emission", neutralized_events=events, neutralizer_source_sha256=digest(__file__), recorded_at=datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"), artifacts_sha256={name: digest(root / name) for name in before})
    (root / "population.json").write_text(json.dumps(report, indent=2))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()
    report = compile_population(args.source_dir, args.out_dir)
    print(json.dumps({"status": report["status"], "neutralized_events": report["neutralized_events"], "artifacts_sha256": report["artifacts_sha256"]}))


if __name__ == "__main__":
    main()
