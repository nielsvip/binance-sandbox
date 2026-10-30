#!/usr/bin/env python3
"""Build matched graph and neural artifacts in a fresh isolated candidate directory; never deploy."""
import argparse
import gzip
import io
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


def gain_population(index_path, out_dir, cutoffs, allowed_sym_sides=None, max_boards_per_cat=12, output_stream=None):
    from collections import Counter
    from tools import v18_brain as brain
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=False)
    index_payload = Path(index_path).read_bytes()
    index_digest = hashlib.sha256(index_payload).hexdigest()
    records = json.loads(index_payload)
    counts, board_counts, row_counts = Counter(), Counter(), Counter()
    proofs, seen = [], set()
    spill = root / "spill_gain_explicit_progress.tsv.gz"
    wire_digest = hashlib.sha256()
    class WireWriter:
        def write(self, payload):
            wire_digest.update(payload)
            return output_stream.write(payload)
        def flush(self):
            output_stream.flush()
    destination = gzip.open(spill, "wt", compresslevel=3) if output_stream is None else io.TextIOWrapper(gzip.GzipFile(fileobj=WireWriter(), mode="wb", compresslevel=3))
    with destination as output:
        for entry in records:
            path = Path(entry["file"])
            if str(path) in seen or not path.is_file():
                continue
            seen.add(str(path))
            symbol = entry.get("s")
            category = brain.cat_side_of(symbol)
            if category not in cutoffs or (allowed_sym_sides is not None and symbol not in allowed_sym_sides):
                counts["excluded_identity"] += 1
                continue
            if max_boards_per_cat is not None and board_counts[category] >= max_boards_per_cat:
                counts["deferred_board_cap"] += 1
                continue
            payload = path.read_bytes()
            digest = hashlib.sha256(payload).hexdigest()
            document = json.loads(payload)
            if document.get("symside") != symbol:
                counts["foreign_progress_identity"] += 1
                continue
            groups = {}
            for key, row in (document.get("done") or {}).items():
                if not isinstance(row, dict):
                    counts["malformed_row"] += 1
                    continue
                end = brain.npz_ts_of(row.get("npz"))
                if end is None:
                    counts["unknown_explicit_row_npz"] += 1
                    continue
                if end >= cutoffs[category]:
                    counts["market_end_not_before_holdout"] += 1
                    continue
                groups.setdefault(end, []).append((key, row))
            if not groups:
                continue
            source_rows = 0
            for end, rows in sorted(groups.items()):
                current = [{}]
                def emit(event):
                    if event.get("e") != "board":
                        event.update({name: value for name, value in extra.items() if name != "set0"})
                    event.update(current[0])
                    output.write(event["t"] + "\t" + json.dumps(event, separators=(",", ":"), default=str) + "\n")
                extra = {"src": "progress", "ts_source": "explicit_progress_row_npz", "npz": end, "cs": category, "wd": document.get("window_days"), "file": path.name, "source_file": str(path), "source_sha256": digest, "lineage_policy": "explicit_rec_npz_v1", "label_computation_time": "not inferred from NPZ timestamp", "set0": document.get("initial_overrides") or None}
                emitter = brain.BoardEmitter("progress", symbol, brain.iso_of(end), emit, counts, extra, path.name + "#" + str(end))
                emitter.start(brain._f(document.get("bh")), document.get("tier"), document.get("defaults_round"))
                for key, row in rows:
                    current[0] = {"progress_key": key, "explicit_row_npz": row["npz"], "record_identity": "source_sha256 + progress_key"}
                    brain._progress_row(emitter, key, row, counts, category)
                emitter.finish()
                source_rows += len(rows)
            if sha256(path) != digest:
                raise ValueError(f"progress source changed while freezing: {path}")
            board_counts[category] += 1
            row_counts[category] += source_rows
            proofs.append({"symbol_side": symbol, "category": category, "source_file": str(path), "sha256": digest, "rows": source_rows, "market_end_min": min(groups), "market_end_max": max(groups), "top_npz_id": document.get("npz_id"), "result_start_utc": document.get("result_start_utc"), "result_done_utc": document.get("result_done_utc")})
    report = {"schema": "explicit_progress_gain_population_v1", "cutoffs_epoch": cutoffs, "index_sha256": sha256(index_path), "unique_boards_by_category": dict(board_counts), "source_rows_by_category": dict(row_counts), "sources": proofs, "counters": dict(counts), "spill_sha256": sha256(spill) if output_stream is None else wire_digest.hexdigest(), "status": "declared_older_market_population_not_oos_gain_proof", "scope": "explicit per-row NPZ tags only; no top-NPZ fallback; current-book scope and board cap recorded; computation dates not fabricated"}
    report["index_sha256"] = index_digest
    report["max_boards_per_cat"] = max_boards_per_cat
    report["input_index_entries"] = len(records)
    if sha256(index_path) != index_digest:
        report["status"] = "failed_index_changed_ineligible"
    if set(board_counts) != set(cutoffs):
        report["status"] = "failed_missing_categories_ineligible"
    (root / "population.json").write_text(json.dumps(report, indent=2, default=str))
    if report["status"].startswith("failed_"):
        raise ValueError(report["status"])
    if set(board_counts) != set(cutoffs):
        raise ValueError(f"population does not cover all categories: {dict(board_counts)}")
    return report


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


def source_files(root):
    import ast
    root = Path(root)
    excluded = {"data", "backups", "venv", "node_modules", "__pycache__", "site-packages", "projects", "project", "archive", "artifacts", "tests", "reports", "outputs", "logs", "scratch", "scratch_def2", "staging", "tmp", "old", "rollback", "patches", "proposed_patches", "contained_instance", "SPREADSHEETS", "indicators"}
    result, pending, visited = set(), list(root.glob("*.py")), set()
    packages = {path.name for path in root.iterdir() if path.is_dir() and (path / "__init__.py").is_file()}
    packages.add("tools")
    def add_package(name):
        if name in visited or name in excluded or name.startswith("."):
            return
        visited.add(name)
        package = root / name
        if package.is_symlink():
            raise ValueError(f"source package contains symlink: {package}")
        if not package.is_dir():
            return
        for directory, names, files in os.walk(package):
            names[:] = [value for value in names if value not in excluded and not value.startswith(".") and not (Path(directory) / value).is_symlink()]
            pending.extend(Path(directory) / value for value in files if value.endswith(".py"))
    for name in sorted(packages):
        add_package(name)
    while pending:
        path = pending.pop()
        if path in result:
            continue
        if path.is_symlink():
            raise ValueError(f"source snapshot contains symlink: {path}")
        result.add(path)
        try:
            syntax = ast.parse(path.read_bytes())
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(syntax):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    add_package(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                add_package(node.module.split(".")[0])
    return sorted(result)


def plan(run_dir, spill_dirs, context_root, cut, python=sys.executable, max_context_mb=256, max_epochs=20, max_train=None, stop_after=None):
    run = Path(run_dir).expanduser().resolve()
    context = Path(context_root).expanduser().resolve()
    if datetime.fromisoformat(cut.replace("Z", "+00:00")).tzinfo is None:
        raise ValueError("cut must carry an explicit timezone")
    if run.exists() or run == ROOT or ROOT / "data" in run.parents or run == ROOT / "data":
        raise ValueError("run directory must be fresh and outside production data")
    if context == ROOT or context in run.parents or run in context.parents:
        raise ValueError("context must be a dedicated snapshot separate from repository and run directory")
    context_files = [path for path in files_under(context) if "_cache" not in path.relative_to(context).parts]
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
    dependencies = ["data/SWITCH_BIBLE.json", "data/switch_dependencies.json", "data/v17_tf_semantics.json", *[f"data/learn/binding_map_{cat_side}.json" for cat_side in CAT_SIDES]]
    missing_inputs = [name for name in dependencies if not (context / name).is_file()]
    if missing_inputs and max_train is None and stop_after is None:
        raise ValueError(f"full candidate requires all feature/binding dependencies: {missing_inputs}")
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
        [python, str(code_root / "tools/v18_precision_build.py"), "split"],
        [python, str(code_root / "tools/v18_precision_build.py"), "build", "--replace"],
        [python, str(code_root / "tools/v18_digest.py"), "--learn-dir", str(learn), "--out-dir", str(learn), "--linknet-report-dir", str(dataset), "--apply"],
    ]
    commands[3].extend(["--max-epochs", str(max_epochs)])
    if max_train is not None:
        commands[3].extend(["--max-train", str(max_train)])
    if stop_after is not None:
        commands = commands[:{"features": 1, "graph": 2, "dataset": 3, "train": 4, "score": 5, "precision": 7, "digest": 8}[stop_after]]
    code = source_files(ROOT)
    if any(path.is_symlink() for path in code):
        raise ValueError("source snapshot contains symlinked Python code")
    return {"status": "planned", "mode": "compatibility_smoke" if max_train is not None or stop_after is not None else "candidate", "missing_context_inputs": missing_inputs, "run_dir": str(run), "context_root": str(context), "context_files": [str(path) for path in context_files], "spill_files": [str(path) for path in spills], "code_files": [str(path) for path in code], "cut": cut, "commands": commands, "scope": "matched graph and LinkNet candidate; precision/digest rebuild and production gate still required", "eligibility": {"schema": "candidate_evidence_v1", "gain_eligibility_policy": "valid_finite_gain_v1", "gain_target": "positive delta among eligible valid finite measurements; common eligible test population required for comparisons", "status": "unverified_not_promotable", "preservation": "source files are hashed, never rewritten or deleted; raw source inventory remains an external prerequisite", "reader_retains": ["row cumulative baseline cb", "symbol/side, board id, row timestamp", "naked and marginal deltas, validity bits, propensity weights", "pinned or causally inferred in-force values, joined chain context when available"], "reader_gaps": ["engine era and exact NPZ/window identities are not universal required training features", "chronological board/event test slices can overlap underlying market windows; no market OOS proof", "quality-proof training requires actual training NPZ/data-end before holdout start, overlap purged, unknown lineage excluded, and pre-cut templates/defaults frozen", "ambiguous invalid/missing/nonfinite gain labels are conservatively excluded; exclusions require raw-source audit", "missing raw/skipped/error evidence cannot be reconstructed from spills", "historic sources need raw-log re-extraction to recover reasons missing in old spills; no success inferred from synthetic zero"]}}


def required_artifacts(run):
    run = Path(run)
    dataset = run / "context/data/learn/linknet/v2"
    learn = run / "context/data/learn"
    result = [run / "graph_report.json", run / "graph_state.pkl"]
    for cat_side in CAT_SIDES:
        result.extend([run / "brain" / f"v18_brain_{cat_side}.json", dataset / f"ds_{cat_side}.npz", dataset / f"report_v2_{cat_side}.json", dataset / f"production_{cat_side}.json", dataset / f"model_v2_{cat_side}.npz", learn / f"linknet_scores_v2_{cat_side}.npz", learn / f"linknet_proposals_v2_{cat_side}.json"])
        production = dataset / f"production_{cat_side}.json"
        if production.is_file() and json.loads(production.read_text()).get("production_scorer") in ("v1", "hybrid_v1"):
            result.append(dataset / f"model_v1_{cat_side}.npz")
        result.extend([learn / f"switch_cards_{cat_side}.json", learn / f"switch_profile_{cat_side}.json", learn / f"needed_filters_{cat_side}.json", learn / f"digest_{cat_side}.json", learn / "precision" / f"stats_{cat_side}.json"])
        positive = learn / f"positives_all_{cat_side}.json"
        result.append(positive if positive.exists() else positive.with_suffix(".json.gz"))
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
        shutil.copytree(candidate["context_root"], run / "context", ignore=shutil.ignore_patterns("_cache"))
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
        precision = run / "context/data/learn/precision/extract"
        precision.mkdir(parents=True, exist_ok=False)
        (run / "context/backups").mkdir(exist_ok=True)
        for frozen in sorted((run / "spills").iterdir()):
            destination = precision / (frozen.name if frozen.name.endswith(".gz") else frozen.name + ".gz")
            if frozen.name.endswith(".gz"):
                shutil.copy2(frozen, destination)
            else:
                with frozen.open("rb") as source, gzip.open(destination, "wb") as target_stream:
                    shutil.copyfileobj(source, target_stream)
        for source in candidate["code_files"]:
            copied = run / "code" / Path(source).relative_to(ROOT)
            copied.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, copied)
            if sha256(copied) != manifest["source_sha256"][source]:
                raise ValueError(f"code changed during snapshot: {source}")
        env = dict(os.environ, V18_ROOT=str(run / "context"), V18_LEARN_DIR=str(run / "context/data/learn"), PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(run / "code"), OPENBLAS_NUM_THREADS="2", OMP_NUM_THREADS="2", MKL_NUM_THREADS="2", NUMEXPR_NUM_THREADS="2")
        for index, command in enumerate(candidate["commands"]):
            if "v18_precision_build.py" in command[1] and "split" in command:
                if not (run / "brain").is_dir():
                    raise ValueError("missing required artifacts: graph exports before precision build")
                shutil.copytree(run / "brain", run / "context/data/brain", dirs_exist_ok=True)
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
        if len(candidate["commands"]) < 8:
            manifest.update(status="partial_smoke_not_deployed", incomplete_phases=["full artifact validation", "remaining refresh phases", "raw-source/context eligibility", "production gate"])
            save()
            return manifest
        artifacts = required_artifacts(run)
        missing = [str(path) for path in artifacts if not path.is_file() or path.stat().st_size == 0]
        if missing:
            raise ValueError(f"missing required artifacts: {missing}")
        from tools.v20_absorb_cycle import corpus_provenance
        manifest["corpus_provenance"] = corpus_provenance(json.loads((run / "graph_report.json").read_text()))
        dataset = run / "context/data/learn/linknet/v2"
        manifest["gain_eligibility_by_cat_side"] = {}
        for cat_side in CAT_SIDES:
            report = json.loads((dataset / f"report_v2_{cat_side}.json").read_text())
            if report.get("selection", {}).get("v1_gate", {}).get("test_used_for_selection") is not False or not report.get("calibration_source") or not report.get("gain_eligibility"):
                raise ValueError(f"missing independent-test or gain-eligibility proof for {cat_side}")
            manifest["gain_eligibility_by_cat_side"][cat_side] = report["gain_eligibility"]
        manifest["artifact_sha256"] = {str(path.relative_to(run)): sha256(path) for path in artifacts}
        manifest["status"] = "learner_candidate_built_not_deployed"
        manifest["scope"] = "matched graph, LinkNet, precision profiles and digest; staged ancillary evidence remains dated input; raw-source/context eligibility and production gate still required"
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
    parser.add_argument("--max-epochs", type=int, default=20)
    parser.add_argument("--max-train", type=int)
    parser.add_argument("--stop-after", choices=("features", "graph", "dataset", "train", "score", "precision", "digest"), help="bounded smoke only; incomplete phases stay explicitly unfinished")
    parser.add_argument("--execute", action="store_true", help="default only prints the plan; never trains on the Mac")
    args = parser.parse_args()
    candidate = plan(args.run_dir, args.spill_dir, args.context_root, args.cut, args.python, args.max_context_mb, args.max_epochs, args.max_train, args.stop_after)
    if args.execute and sys.platform == "darwin":
        parser.error("candidate training runs on a worker, not the Mac")
    print(json.dumps(execute(candidate) if args.execute else candidate, indent=2))


if __name__ == "__main__":
    main()
