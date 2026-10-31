#!/usr/bin/env python3
"""Prepare isolated, pinned NN comparison snapshots for the existing NPZ bench."""
import argparse
import fnmatch
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import tempfile


CODE_EXCLUDE = ["data", "backtest_v8", "backtest_v7", "SPREADSHEETS", "klines_cache", "klines_cache_macbook", "klines_vault", "klines_canonical", "archive", "backups", ".venv", "backtest_framework", ".git", "logs", "from_scratch_archive_*", "*.log", "*.db", "*.sqlite", "*.npz", "*.xlsx", "__pycache__", ".history", ".elastic"]


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def md5(path):
    digest = hashlib.md5()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_inventory(root):
    root = Path(root)
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"symlink not allowed in pinned input: {path}")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = sha256(path)
    return result


def artifact_relative(name):
    if not re.fullmatch(r"(?:v18_brain|digest|needed_filters|switch_profile|positives_all|binding_map|linknet_proposals|must_pos)_(?:CRYPTO_LONG|CRYPTO_SHORT|STOCKS_LONG|STOCKS_SHORT)\.json(?:\.gz)?", name):
        raise ValueError(f"unexpected declared NN artifact: {name}")
    return "data/" + ("brain/" if name.startswith("v18_brain_") else "learn/") + name


def read_bundle(bundle_root):
    bundle_root = Path(bundle_root).resolve()
    manifest_path = bundle_root / "tools/v18_model_bundle.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError(f"missing materialized model declaration: {manifest_path}")
    manifest_bytes = manifest_path.read_bytes()
    declaration = json.loads(manifest_bytes)
    expected = declaration.get("expected_sha256_by_filename")
    if not isinstance(expected, dict) or len(expected) != 32:
        raise ValueError("each NN arm must declare exactly 32 artifacts")
    artifacts = {}
    for name, expected_sha in expected.items():
        relative = artifact_relative(name)
        if not isinstance(expected_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
            raise ValueError(f"invalid declared artifact hash: {name}")
        source = bundle_root / relative
        if source.is_symlink() or not source.is_file() or sha256(source) != expected_sha:
            raise ValueError(f"bundle artifact does not match declaration: {relative}")
        artifacts[relative] = expected_sha
    return {"root": bundle_root, "manifest": declaration, "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(), "artifacts": artifacts}


def validate_parent_snapshot(snapshot):
    snapshot = Path(snapshot).resolve()
    manifest_path = snapshot / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("parent snapshot manifest missing or symlinked")
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    sides = manifest.get("sides")
    if not isinstance(sides, list) or len(sides) != 8 or len(set(sides)) != 8:
        raise ValueError("parent snapshot must pin exactly eight unique sides")
    for section in ("npz", "starts", "templates"):
        if not isinstance(manifest.get(section), dict):
            raise ValueError(f"parent snapshot lacks {section} manifest")
    for side in sides:
        symbol = side.rsplit("_", 1)[0]
        npz_entry = manifest["npz"].get(symbol)
        npz_path = snapshot / "npz" / f"{symbol}.npz"
        start_path = snapshot / "starts" / f"{side}_autopsy_base.json"
        if not npz_entry or not npz_path.is_file() or npz_path.is_symlink() or md5(npz_path) != npz_entry.get("md5"):
            raise ValueError(f"pinned NPZ missing or changed for {side}")
        start_hash = manifest["starts"].get(side)
        if not start_path.is_file() or start_path.is_symlink() or not start_hash or md5(start_path) != start_hash:
            raise ValueError(f"pinned start base missing or changed for {side}")
    for filename, expected_md5 in manifest["templates"].items():
        template = snapshot / "templates" / filename
        if not template.is_file() or template.is_symlink() or md5(template) != expected_md5:
            raise ValueError(f"pinned template missing or changed: {filename}")
    if not (snapshot / "data").is_dir():
        raise ValueError("parent snapshot data directory missing")
    return snapshot, manifest, hashlib.sha256(manifest_bytes).hexdigest()


def hardlink_copy(source, destination):
    source = Path(source)
    destination = Path(destination)
    if source.is_symlink():
        raise ValueError(f"symlink not allowed in snapshot: {source}")
    if source.is_dir():
        destination.mkdir()
        for child in source.iterdir():
            hardlink_copy(child, destination / child.name)
    elif source.is_file():
        os.link(source, destination)
    else:
        raise ValueError(f"unsupported snapshot entry: {source}")


def overlay_bundle(bundle, snapshot_data):
    snapshot_data = Path(snapshot_data).resolve()
    receipts = {}
    for relative, expected in bundle["artifacts"].items():
        source = bundle["root"] / relative
        target = snapshot_data / relative.removeprefix("data/")
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".v22_model_", dir=target.parent)
        os.close(descriptor)
        try:
            shutil.copyfile(source, temporary)
            if sha256(temporary) != expected:
                raise ValueError(f"artifact changed during snapshot overlay: {relative}")
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        receipts[relative] = expected
    return receipts


def shared_snapshot_inventory(snapshot, model_paths):
    snapshot = Path(snapshot)
    excluded = {"manifest.json"}
    excluded.update(model_paths)
    return {name: digest for name, digest in safe_inventory(snapshot).items() if name not in excluded}


def _ignore_code(_directory, names):
    return {name for name in names if any(fnmatch.fnmatch(name, pattern) for pattern in CODE_EXCLUDE)}


def load_artifact_verifier(code_root, fallback_path=None):
    path = Path(code_root) / "tools/v15_nextgen_gate_artifacts.py"
    if not path.is_file() and fallback_path:
        path = Path(fallback_path)
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"frozen runtime lacks model artifact verifier: {path}")
    spec = importlib.util.spec_from_file_location("v22_gate_artifact_verifier", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_after_build_root(snapshot, code_root, flags, expected_artifacts, scratch_parent, verifier_path, active_policy):
    snapshot, code_root = Path(snapshot), Path(code_root)
    scratch_parent = Path(scratch_parent)
    with tempfile.TemporaryDirectory(prefix="v22_build_root_probe_", dir=scratch_parent) as temporary:
        root = Path(temporary) / "root"
        shutil.copytree(code_root, root, copy_function=os.link, ignore=_ignore_code)
        shutil.copytree(snapshot / "data", root / "data", copy_function=os.link)
        verifier = load_artifact_verifier(root, verifier_path)
        if active_policy:
            receipt = verifier.verify_declared_bundle(root, flags, required=True)
        else:
            declaration_path = root / "tools/v18_model_bundle.json"
            declaration_bytes = declaration_path.read_bytes()
            declaration = json.loads(declaration_bytes)
            expected = declaration.get("expected_sha256_by_filename")
            if not isinstance(expected, dict) or len(expected) != 32:
                raise ValueError("reference arm must declare exactly 32 model artifacts")
            for name, expected_sha in expected.items():
                artifact = root / artifact_relative(name)
                if not artifact.is_file() or sha256(artifact) != expected_sha:
                    raise ValueError(f"reference model artifact differs from declaration: {name}")
            receipt = verifier.resolve_models(root, flags, expected)
            receipt["bundle_manifest_sha256"] = hashlib.sha256(declaration_bytes).hexdigest()
            receipt["bundle_id"] = declaration.get("bundle_id")
            receipt["verification_policy"] = "pinned reference-off env plus actual planner resolver; expanded-bundle activation guard is candidate-only"
        model_receipts = receipt.get("models", {})
        if len(model_receipts) != 32:
            raise ValueError("post-build-root resolver did not consume all 32 declared NN artifacts")
        consumed = {name: row["sha256"] for name, row in model_receipts.items()}
        expected_by_name = {Path(relative).name: digest for relative, digest in expected_artifacts.items()}
        if consumed != expected_by_name:
            raise ValueError("post-build-root resolver resolved hashes differ from intended arm")
        return {"bundle_id": receipt.get("bundle_id"), "bundle_manifest_sha256": receipt.get("bundle_manifest_sha256"), "planner_sha256": receipt.get("planner_sha256"), "resolved_artifact_sha256": consumed, "model_paths": {name: row["path"] for name, row in model_receipts.items()}, "verification": "actual frozen resolver after same code-exclude then snapshot-data-copy sequence as v15_npz_bench.build_root"}


def verify_pilot_root(root, spec_path, arm_name):
    root = Path(root).resolve()
    spec_path = Path(spec_path).resolve()
    spec = json.loads(spec_path.read_text())
    arm = spec[arm_name]
    runtime_pin = spec["runtime"]
    runtime_manifest_path = Path(runtime_pin["path"])
    runtime_bytes = runtime_manifest_path.read_bytes()
    if hashlib.sha256(runtime_bytes).hexdigest() != runtime_pin["sha256"]:
        raise ValueError("frozen runtime manifest pin mismatch")
    runtime = json.loads(runtime_bytes)
    expected_code = dict(runtime["files"])
    expected_code.update(spec.get("compatibility", {}))
    for relative, expected_sha in expected_code.items():
        path = root / relative
        if path.is_symlink() or not path.is_file() or sha256(path) != expected_sha:
            raise ValueError(f"actual pilot root common-runtime mismatch: {relative}")
    bundle = read_bundle(Path(arm["path"]).parent.parent)
    if bundle["manifest_sha256"] != arm["sha256"]:
        raise ValueError("pinned arm model declaration sha256 mismatch")
    manifest_path = root / "tools/v18_model_bundle.json"
    if manifest_path.is_symlink() or not manifest_path.is_file() or sha256(manifest_path) != bundle["manifest_sha256"]:
        raise ValueError("actual pilot root model declaration mismatch")
    flags = {**spec.get("equal_runtime_flags", {}), **spec[f"{arm_name}_runtime_flags"]}
    verifier_path = Path(spec["reference_policy"]["path"]).resolve().parent / "tools/v15_nextgen_gate_artifacts.py"
    if not verifier_path.is_file():
        raise ValueError("pinned NN runtime resolver helper is unavailable")
    verifier = load_artifact_verifier(root, verifier_path)
    if arm_name == "candidate":
        receipt = verifier.verify_declared_bundle(root, flags, required=True)
    else:
        declaration_bytes = manifest_path.read_bytes()
        declaration = json.loads(declaration_bytes)
        expected = declaration.get("expected_sha256_by_filename")
        if not isinstance(expected, dict) or len(expected) != 32:
            raise ValueError("reference arm must declare exactly 32 model artifacts")
        for name, expected_sha in expected.items():
            path = root / artifact_relative(name)
            if path.is_symlink() or not path.is_file() or sha256(path) != expected_sha:
                raise ValueError(f"actual pilot root reference artifact mismatch: {name}")
        receipt = verifier.resolve_models(root, flags, expected)
        receipt["bundle_manifest_sha256"] = hashlib.sha256(declaration_bytes).hexdigest()
        receipt["bundle_id"] = declaration.get("bundle_id")
    if len(receipt.get("models", {})) != 32:
        raise ValueError("actual pilot root resolver did not resolve all32 artifacts")
    return {"schema": "v22_actual_pilot_root_verification/1", "arm": arm_name, "root": str(root), "runtime_manifest_sha256": runtime_pin["sha256"], "common_runtime_files_verified": len(expected_code), "bundle_id": receipt.get("bundle_id"), "bundle_manifest_sha256": receipt.get("bundle_manifest_sha256"), "planner_sha256": receipt.get("planner_sha256"), "resolved_artifact_sha256": {name: row["sha256"] for name, row in receipt["models"].items()}, "resolver_policy": "actual run-root resolver under recorded arm flags; confirms resolved hashes, not an OS-level runtime read trace"}


def prepare(parent_snapshot, spec_path, reference_code, candidate_code, output_dir, bench_dir, snapshot_tag=""):
    parent, parent_manifest, parent_manifest_sha = validate_parent_snapshot(parent_snapshot)
    spec_path = Path(spec_path).resolve()
    spec = json.loads(spec_path.read_text())
    if spec.get("schema") != "v15_nn_comparison/1":
        raise ValueError("unknown NN comparison spec")
    arms = {
        "reference": {"bundle": read_bundle(Path(spec["reference"]["path"]).parent.parent), "code": Path(reference_code).resolve(), "flags": {**spec.get("equal_runtime_flags", {}), **spec["reference_runtime_flags"]}},
        "candidate": {"bundle": read_bundle(Path(spec["candidate"]["path"]).parent.parent), "code": Path(candidate_code).resolve(), "flags": {**spec.get("equal_runtime_flags", {}), **spec["candidate_runtime_flags"]}},
    }
    policy_path = Path(spec["reference_policy"]["path"]).resolve()
    verifier_path = policy_path.parent / "tools/v15_nextgen_gate_artifacts.py"
    if not verifier_path.is_file():
        raise ValueError(f"frozen comparison artifact verifier missing: {verifier_path}")
    verifier_sha = sha256(verifier_path)
    for arm in arms.values():
        if arm["bundle"]["manifest_sha256"] != spec["reference" if arm is arms["reference"] else "candidate"]["sha256"]:
            raise ValueError("model declaration sha256 differs from pinned comparison spec")
        if not arm["code"].is_dir() or arm["code"].is_symlink():
            raise ValueError("materialized runtime code root missing or symlinked")
    if set(arms["reference"]["bundle"]["artifacts"]) != set(arms["candidate"]["bundle"]["artifacts"]):
        raise ValueError("reference and candidate artifact path sets differ")
    output_dir, bench_dir = Path(output_dir).resolve(), Path(bench_dir).resolve()
    if output_dir.exists():
        raise FileExistsError(f"preparation output already exists: {output_dir}")
    output_dir.mkdir(parents=True)
    parent_id = parent.name.removeprefix("snap_")
    comparison = re.sub(r"[^A-Za-z0-9_]+", "_", spec.get("comparison_id", "v22"))[:36]
    model_paths = {"data/" + relative.removeprefix("data/") for relative in arms["reference"]["bundle"]["artifacts"]}
    original_shared = shared_snapshot_inventory(parent, model_paths)
    records = {}
    for name, arm in arms.items():
        suffix = f"_{snapshot_tag}" if snapshot_tag else ""
        snapshot_id = f"{parent_id}_v22_{comparison}{suffix}_{name}"
        destination = bench_dir / f"snap_{snapshot_id}"
        if destination.exists():
            raise FileExistsError(f"derived snapshot already exists: {destination}")
        hardlink_copy(parent, destination)
        overlay_receipt = overlay_bundle(arm["bundle"], destination / "data")
        derived_shared = shared_snapshot_inventory(destination, model_paths)
        if derived_shared != original_shared:
            raise ValueError(f"shared parent snapshot inputs changed while preparing {name}")
        manifest = json.loads((destination / "manifest.json").read_text())
        manifest["v22_derived_snapshot"] = {"schema": "v22_derived_snapshot/1", "comparison_id": spec.get("comparison_id"), "arm": name, "parent_snapshot": str(parent), "parent_manifest_sha256": parent_manifest_sha, "model_bundle_manifest_sha256": arm["bundle"]["manifest_sha256"], "model_artifact_sha256": overlay_receipt, "shared_snapshot_inventory_sha256": hashlib.sha256(json.dumps(original_shared, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}
        manifest_path = destination / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=1))
        if json.loads(manifest_path.read_text())["v22_derived_snapshot"]["parent_manifest_sha256"] != parent_manifest_sha:
            raise ValueError("derived snapshot manifest failed parent pin round-trip")
        resolved = verify_after_build_root(destination, arm["code"], arm["flags"], arm["bundle"]["artifacts"], output_dir, verifier_path, name == "candidate")
        resolved["artifact_verifier_sha256"] = verifier_sha
        env = dict(parent_manifest.get("env") or {})
        env.update(spec.get("equal_runtime_flags", {}))
        env.update(spec[f"{name}_runtime_flags"])
        env_path = output_dir / f"{name}_env.json"
        env_path.write_text(json.dumps(env, indent=1, sort_keys=True))
        command = ["python3", "~/binance-sandbox/tools/v15_npz_bench.py", "run", "--snap", snapshot_id, "--label", f"v22_{name}", "--code", str(arm["code"]), "--env-file", str(env_path), "--parallel", "1", "--workers", "2", "--min-disk-mb", "20000", "--min-free-mb", "7000"]
        records[name] = {"snapshot_id": snapshot_id, "snapshot_path": str(destination), "env_file": str(env_path), "code_root": str(arm["code"]), "bundle_manifest_sha256": arm["bundle"]["manifest_sha256"], "resolved_after_build_root": resolved, "command": " ".join(command), "shared_inputs_match_parent": True}
    common = shared_snapshot_inventory(bench_dir / f"snap_{records['reference']['snapshot_id']}", model_paths)
    candidate_shared = shared_snapshot_inventory(bench_dir / f"snap_{records['candidate']['snapshot_id']}", model_paths)
    if common != candidate_shared:
        raise ValueError("reference and candidate derived snapshots do not share identical non-model inputs")
    report = {"schema": "v22_bench_preparation/1", "comparison_id": spec.get("comparison_id"), "parent_snapshot": str(parent), "parent_manifest_sha256": parent_manifest_sha, "sides": parent_manifest["sides"], "shared_input_file_count": len(original_shared), "shared_input_inventory_sha256": hashlib.sha256(json.dumps(original_shared, sort_keys=True, separators=(",", ":")).encode()).hexdigest(), "runtime_flags": {name: arm["flags"] for name, arm in arms.items()}, "official_preflight_floors": {"min_disk_mb": 20000, "min_free_mb": 7000}, "arms": records, "launch_started": False}
    report_path = output_dir / "v22_bench_preparation.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True))
    return report_path, report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    staging = subparsers.add_parser("prepare")
    staging.add_argument("--snapshot", required=True)
    staging.add_argument("--spec", required=True)
    staging.add_argument("--reference-code", required=True)
    staging.add_argument("--candidate-code", required=True)
    staging.add_argument("--output-dir", required=True)
    staging.add_argument("--bench-dir", default=str(Path.home() / "v15_bench"))
    staging.add_argument("--snapshot-tag", default="", help="optional isolated suffix when a prior preparation left a partial snapshot")
    verify = subparsers.add_parser("verify-root")
    verify.add_argument("--root", required=True, help="actual v15_npz_bench run root")
    verify.add_argument("--spec", required=True)
    verify.add_argument("--arm", choices=("reference", "candidate"), required=True)
    args = parser.parse_args(argv)
    if args.command == "verify-root":
        receipt = verify_pilot_root(args.root, args.spec, args.arm)
        print(json.dumps(receipt, indent=2, sort_keys=True))
        return 0
    report_path, report = prepare(args.snapshot, args.spec, args.reference_code, args.candidate_code, args.output_dir, args.bench_dir, args.snapshot_tag)
    print(json.dumps({"report": str(report_path), "launch_started": False, "arms": {name: value["command"] for name, value in report["arms"].items()}}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
