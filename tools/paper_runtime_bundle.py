#!/usr/bin/env python3
"""Build a content-addressed, read-only snapshot of the paper engine source."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOTS = ("tools", "vec_paths", "vec_decisions", "live_twins", "v15_decisions")
RUNTIME_ASSETS = ("data/reports/ALL_PATHS_ALLOWLIST.csv",)
EXCLUDED_DIRS = {".git", ".venv", "venv", "env", "__pycache__", "node_modules", "data", "backups", "tests", "test", "output", "outputs", "logs", "plots", "artifacts", "reports", "archive", "scratch", "tmp"}


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_paths(source_root):
    source_root = Path(source_root).resolve(strict=True)
    paths = set(source_root.glob("*.py"))
    for package in PACKAGE_ROOTS:
        package_root = source_root / package
        if not package_root.is_dir() or package_root.is_symlink():
            continue
        for current, directories, files in os.walk(package_root, topdown=True, followlinks=False):
            directories[:] = sorted(name for name in directories if name not in EXCLUDED_DIRS and not name.startswith("."))
            current_path = Path(current)
            for name in files:
                if name.endswith(".py"):
                    path = current_path / name
                    if not path.is_symlink():
                        paths.add(path)
    for relative in RUNTIME_ASSETS:
        asset = source_root / relative
        if asset.is_file() and not asset.is_symlink():
            paths.add(asset)
    valid = []
    allowed_assets = set(RUNTIME_ASSETS)
    for path in paths:
        if path.is_symlink() or not path.is_file():
            continue
        relative = path.relative_to(source_root)
        if relative.as_posix() not in allowed_assets and any(part in EXCLUDED_DIRS or part.startswith(".") for part in relative.parts[:-1]):
            continue
        valid.append((relative.as_posix(), path))
    if "backtest_v12_engine.py" not in {relative for relative, _ in valid}:
        raise FileNotFoundError("source root does not contain backtest_v12_engine.py")
    return source_root, sorted(valid)


def _bundle_identity(entries):
    digest = hashlib.sha256()
    for relative, source in entries:
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(_sha256(source)))
    return digest.hexdigest()


def _manifest_for(source_root, entries, runtime_hash):
    files = []
    total_bytes = 0
    for relative, source in entries:
        size = source.stat().st_size
        total_bytes += size
        files.append({"path": relative, "sha256": _sha256(source), "bytes": size})
    engine_sha = next(item["sha256"] for item in files if item["path"] == "backtest_v12_engine.py")
    return {
        "schema_version": 1,
        "runtime_sha256": runtime_hash,
        "source_root": str(source_root),
        "engine_path": "backtest_v12_engine.py",
        "engine_sha256": engine_sha,
        "package_roots": list(PACKAGE_ROOTS),
        "file_count": len(files),
        "total_source_bytes": total_bytes,
        "files": files,
        "file_sha256": {row["path"]: row["sha256"] for row in files},
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
    }


def _verify_existing(runtime_root, expected_manifest):
    manifest_path = runtime_root / "runtime_manifest.json"
    if not manifest_path.is_file():
        raise RuntimeError(f"content-addressed runtime is missing its manifest: {runtime_root}")
    actual = json.loads(manifest_path.read_text(encoding="utf-8"))
    if actual.get("runtime_sha256") != expected_manifest["runtime_sha256"]:
        raise RuntimeError("existing runtime manifest hash does not match directory name")
    expected_files = {row["path"]: row["sha256"] for row in expected_manifest["files"]}
    actual_files = {row["path"]: row["sha256"] for row in actual.get("files", [])}
    if actual_files != expected_files:
        raise RuntimeError("existing runtime manifest file set differs from source snapshot")
    for relative, expected_hash in expected_files.items():
        path = runtime_root / relative
        if not path.is_file() or _sha256(path) != expected_hash:
            raise RuntimeError(f"existing runtime source hash mismatch: {relative}")
        if path.stat().st_mode & 0o222:
            raise RuntimeError(f"runtime source is writable: {relative}")
    if runtime_root.stat().st_mode & 0o222:
        raise RuntimeError("runtime directory is writable")
    return actual


def _make_read_only(root):
    for current, directories, files in os.walk(root, topdown=False, followlinks=False):
        for name in files:
            path = Path(current) / name
            if path.is_symlink():
                raise RuntimeError(f"runtime bundle unexpectedly contains a symlink: {path}")
            path.chmod(0o444)
        for name in directories:
            path = Path(current) / name
            if path.is_symlink():
                raise RuntimeError(f"runtime bundle unexpectedly contains a symlink directory: {path}")
            path.chmod(0o555)
    Path(root).chmod(0o555)


def build_bundle(source_root=ROOT, runtime_parent=None):
    source_root, entries = _source_paths(source_root)
    runtime_parent = Path(runtime_parent or (ROOT / "data" / "paper_forward_lab" / "runtime")).resolve()
    runtime_parent.mkdir(parents=True, exist_ok=True)
    runtime_hash = _bundle_identity(entries)
    target = runtime_parent / runtime_hash
    manifest = _manifest_for(source_root, entries, runtime_hash)
    if target.exists():
        _verify_existing(target, manifest)
        return {"runtime_root": str(target), "runtime_sha256": runtime_hash, "engine_sha256": manifest["engine_sha256"], "file_count": manifest["file_count"], "total_source_bytes": manifest["total_source_bytes"], "reused": True}
    staging = runtime_parent / (".build-" + uuid.uuid4().hex)
    staging.mkdir(mode=0o700)
    try:
        expected_hashes = {row["path"]: row["sha256"] for row in manifest["files"]}
        for relative, source in entries:
            destination = staging / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            if _sha256(destination) != expected_hashes[relative]:
                raise RuntimeError(f"runtime copy hash mismatch: {relative}")
        manifest_path = staging / "runtime_manifest.json"
        manifest_path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n", encoding="utf-8")
        _make_read_only(staging)
        try:
            os.replace(staging, target)
        except FileExistsError:
            _verify_existing(target, manifest)
            shutil.rmtree(staging, onerror=_writable_rmtree)
        _verify_existing(target, manifest)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging, onerror=_writable_rmtree)
        raise
    return {"runtime_root": str(target), "runtime_sha256": runtime_hash, "engine_sha256": manifest["engine_sha256"], "file_count": manifest["file_count"], "total_source_bytes": manifest["total_source_bytes"], "reused": False}


def _writable_rmtree(function, path, exc_info):
    try:
        Path(path).chmod(stat.S_IWUSR | stat.S_IRUSR | stat.S_IXUSR)
    except OSError:
        pass
    function(path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--runtime-parent", type=Path, default=ROOT / "data" / "paper_forward_lab" / "runtime")
    args = parser.parse_args(argv)
    result = build_bundle(args.source_root, args.runtime_parent)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
