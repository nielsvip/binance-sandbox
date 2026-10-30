#!/usr/bin/env python3
"""Build matched graph and neural artifacts in a fresh isolated candidate directory; never deploy."""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
CAT_SIDES = ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT")


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1048576), b""):
            digest.update(chunk)
    return digest.hexdigest()


def files_under(root):
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"requires a real snapshot directory: {root}")
    entries = sorted(root.rglob("*"))
    if any(path.is_symlink() for path in entries):
        raise ValueError(f"symlinks are forbidden in snapshot: {root}")
    return [path for path in entries if path.is_file()]


def plan(run_dir, spill_dirs, context_root, cut, python=sys.executable, max_context_mb=256):
    run = Path(run_dir).expanduser().resolve()
    context = Path(context_root).expanduser().resolve()
    if datetime.fromisoformat(cut.replace("Z", "+00:00")).tzinfo is None:
        raise ValueError("cut must carry an explicit timezone")
    if run.exists() or run == ROOT or ROOT / "data" in run.parents or run == ROOT / "data":
        raise ValueError("run directory must be fresh and outside production data")
    if context == ROOT or context in run.parents or run in context.parents:
        raise ValueError("context must be a dedicated snapshot separate from repository and run directory")
    context_files = files_under(context)
    generated = ("ds_", "model_v1_", "model_v2_", "report_v2_", "production_", "linknet_scores_v2_", "linknet_proposals_v2_", "v18_brain_")
    if any(path.name.startswith(generated) for path in context_files):
        raise ValueError("context must not contain pre-existing candidate output artifacts")
    if sum(path.stat().st_size for path in context_files) > max_context_mb * 1048576:
        raise ValueError("context snapshot exceeds explicit size limit")
    for cat_side in CAT_SIDES:
        if not list(context.glob(f"SPREADSHEETS/**/TEMPLATE_{cat_side}.xlsx")):
            raise ValueError(f"missing context template for {cat_side}")
        if not (context / "data" / "learn" / "linknet" / "v2" / f"ctxjoin_{cat_side}.pkl.gz").is_file():
            raise ValueError(f"missing frozen context join for {cat_side}")
    spills = []
    for source in spill_dirs:
        spills.extend(path for path in files_under(Path(source).expanduser().resolve()) if path.name.startswith("spill_") and (path.name.endswith(".tsv") or path.name.endswith(".tsv.gz")))
    spills = sorted(set(spills))
    if not spills:
        raise ValueError("no frozen spill files supplied")
    isolated = run / "context"
    learn = isolated / "data" / "learn"
    dataset = learn / "linknet" / "v2"
    spill_args = ["--spill-dir", str(run / "spills")]
    code_root = run / "code"
    commands = [
        [python, str(code_root / "tools/v18_features.py"), "build", "--out-dir", str(learn)],
        [python, str(code_root / "tools/v18_brain.py"), "train", *spill_args, "--cut", cut, "--tmp-dir", str(run / "tmp_graph"), "--state", str(run / "graph_state.pkl"), "--out-dir", str(run / "brain"), "--report", str(run / "graph_report.json"), "--binding-map-dir", str(learn)],
        [python, str(code_root / "tools/v18_linknet.py"), "dataset", *spill_args, "--tmp-dir", str(run / "tmp_dataset"), "--out-dir", str(dataset), "--ctx-dir", str(dataset)],
        [python, str(code_root / "tools/v18_linknet.py"), "train2", "--ds-dir", str(dataset)],
        [python, str(code_root / "tools/v18_linknet.py"), "score2", "--ds-dir", str(dataset), "--out-dir", str(learn)],
    ]
    code = sorted(set(ROOT.glob("*.py")) | set((ROOT / "tools").rglob("*.py")))
    if any(path.is_symlink() for path in code):
        raise ValueError("source snapshot contains symlinked Python code")
    return {"status": "planned", "run_dir": str(run), "context_root": str(context), "context_files": [str(path) for path in context_files], "spill_files": [str(path) for path in spills], "code_files": [str(path) for path in code], "cut": cut, "commands": commands, "scope": "matched graph and LinkNet candidate; precision/digest rebuild and production gate still required", "eligibility": {"status": "unverified_not_promotable", "preservation": "source files are hashed, never rewritten or deleted; raw source inventory remains an external prerequisite", "reader_retains": ["row cumulative baseline cb", "symbol/side, board id, row timestamp", "naked and marginal deltas, validity bits, propensity weights", "pinned or causally inferred in-force values, joined chain context when available"], "reader_gaps": ["engine era and exact NPZ/window identities are not universal required training features", "invalid rows can enter existing positivity targets as negatives; eligibility repair required", "missing raw/skipped/error evidence cannot be reconstructed from spills", "historic sources need an independent provenance and eligibility audit"]}}


def required_artifacts(run):
    run = Path(run)
    dataset = run / "context/data/learn/linknet/v2"
    learn = run / "context/data/learn"
    result = [run / "graph_report.json", run / "graph_state.pkl"]
    for cat_side in CAT_SIDES:
        result.extend([run / "brain" / f"v18_brain_{cat_side}.json", dataset / f"ds_{cat_side}.npz", dataset / f"report_v2_{cat_side}.json", dataset / f"production_{cat_side}.json", dataset / f"model_v2_{cat_side}.npz", learn / f"linknet_scores_v2_{cat_side}.npz", learn / f"linknet_proposals_v2_{cat_side}.json"])
    return result


def execute(candidate):
    run = Path(candidate["run_dir"])
    run.mkdir(parents=True, exist_ok=False)
    manifest = dict(candidate, status="staging", phases=[], deployed=False)
    target = run / "manifest.json"
    def save():
        manifest["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
        temp = target.with_suffix(".tmp")
        temp.write_text(json.dumps(manifest, indent=2))
        temp.replace(target)
    save()
    try:
        sources = candidate["context_files"] + candidate["spill_files"] + candidate["code_files"]
        manifest["source_sha256"] = {path: sha256(path) for path in sources}
        shutil.copytree(candidate["context_root"], run / "context")
        for source in candidate["context_files"]:
            copied = run / "context" / Path(source).relative_to(candidate["context_root"])
            if sha256(copied) != manifest["source_sha256"][source]:
                raise ValueError(f"context changed during snapshot: {source}")
        for index, source in enumerate(candidate["spill_files"]):
            copied = run / "spills" / f"spill_{index:06d}_{Path(source).name}"
            copied.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, copied)
            if sha256(copied) != manifest["source_sha256"][source]:
                raise ValueError(f"spill changed during snapshot: {source}")
        for source in candidate["code_files"]:
            copied = run / "code" / Path(source).relative_to(ROOT)
            copied.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, copied)
            if sha256(copied) != manifest["source_sha256"][source]:
                raise ValueError(f"code changed during snapshot: {source}")
        env = dict(os.environ, V18_ROOT=str(run / "context"), V18_LEARN_DIR=str(run / "context/data/learn"), PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(run / "code"))
        for index, command in enumerate(candidate["commands"]):
            manifest["status"] = "running"
            phase = {"index": index, "command": command, "status": "running"}
            manifest["phases"].append(phase)
            save()
            with (run / f"phase_{index}.log").open("w") as output:
                result = subprocess.run(command, cwd=run, env=env, stdout=output, stderr=subprocess.STDOUT, check=False)
            phase.update(status="complete" if result.returncode == 0 else "failed", returncode=result.returncode)
            save()
            if result.returncode:
                raise RuntimeError(f"phase {index} failed with exit {result.returncode}")
        if any(sha256(path) != digest for path, digest in manifest["source_sha256"].items()):
            raise ValueError("source changed during candidate build; artifacts are not eligible")
        artifacts = required_artifacts(run)
        missing = [str(path) for path in artifacts if not path.is_file() or path.stat().st_size == 0]
        if missing:
            raise ValueError(f"missing required artifacts: {missing}")
        from tools.v20_absorb_cycle import corpus_provenance
        manifest["corpus_provenance"] = corpus_provenance(json.loads((run / "graph_report.json").read_text()))
        manifest["artifact_sha256"] = {str(path.relative_to(run)): sha256(path) for path in artifacts}
        manifest["status"] = "graph_neural_built_not_deployed"
    except Exception as error:
        manifest.update(status="failed", error=str(error))
        save()
        raise
    save()
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--spill-dir", required=True, action="append")
    parser.add_argument("--context-root", required=True, help="dedicated frozen snapshot with templates and data/learn/linknet/v2/ctxjoin_* files; copied before execution")
    parser.add_argument("--cut", required=True)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--max-context-mb", type=int, default=256)
    parser.add_argument("--execute", action="store_true", help="default only prints the plan; never trains on the Mac")
    args = parser.parse_args()
    candidate = plan(args.run_dir, args.spill_dir, args.context_root, args.cut, args.python, args.max_context_mb)
    if args.execute and sys.platform == "darwin":
        parser.error("candidate training runs on a worker, not the Mac")
    print(json.dumps(execute(candidate) if args.execute else candidate, indent=2))


if __name__ == "__main__":
    main()
