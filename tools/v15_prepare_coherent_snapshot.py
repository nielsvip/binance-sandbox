"""Prepare a NEW bounded benchmark snapshot; never changes defaults or old snapshots."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--npz-snapshot", type=Path, required=True)
    parser.add_argument("--dest", type=Path, required=True)
    parser.add_argument("--env-pid", type=int, required=True)
    args = parser.parse_args()
    source, old, dest = args.source.resolve(), args.npz_snapshot.resolve(), args.dest.resolve()
    if dest.exists() or source == dest or source in dest.parents or old in dest.parents:
        raise SystemExit("destination must be new and outside production/old snapshot")
    if shutil.disk_usage(dest.parent).free < 25 * 1024 ** 3:
        raise SystemExit("less than25GiB free; hold snapshot preparation")
    old_manifest = json.loads((old / "manifest.json").read_text())
    sides = old_manifest["sides"]
    critical = ["v15_pilot.py", "v12_quick_engine.py", "config.py", "config_tradier.py", "switch_parity.py", "tools/opt/evaluate_v12.py", "data/per_sym_settings.json"]
    templates = [source / "SPREADSHEETS/TEMPLATE_FINAL_NORM" / f"TEMPLATE_{cs}.xlsx" for cs in ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT")]
    before = {str(p.relative_to(source)): sha(p) for p in [source / f for f in critical] + templates}
    dest.mkdir()
    manifest = {"at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "status": "PREPARING_NOT_RUNNABLE", "src": str(source), "npz_source": str(old), "sides": sides, "npz": {}, "starts": {}, "templates": {}, "data_files": 0, "source_sha256": before, "freeze_policy": "immutable NPZ/starts reused; current code, defaults and learning inputs copied without changing values", "hardlink_policy": "only byte-identical existing immutable snapshot files; never production files", "required_data_scope": "top-level JSON policy/state; learn/brain/endgame/sweep_defaults; eight live baseline full sets; selected side zero-formula books"}
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=2))
    def install(src, target, frozen=None):
        if src.is_symlink():
            raise RuntimeError(f"refusing source symlink: {src}")
        payload = src.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        target.parent.mkdir(parents=True, exist_ok=True)
        if frozen and frozen.is_file() and not frozen.is_symlink() and sha(frozen) == digest:
            os.link(frozen, target)
        else:
            target.write_bytes(payload)
        if sha(target) != digest:
            raise RuntimeError(f"snapshot copy mismatch: {src}")
        return digest
    for side in sides:
        symbol = side.rsplit("_", 1)[0]
        file = old / "npz" / f"{symbol}.npz"
        if hashlib.md5(file.read_bytes()).hexdigest() != old_manifest["npz"][symbol]["md5"]:
            raise RuntimeError("old NPZ differs from its immutable manifest")
        manifest["npz"][symbol] = dict(old_manifest["npz"][symbol], sha256=install(file, dest / "npz" / file.name, file))
        start = old / "starts" / f"{side}_autopsy_base.json"
        install(start, dest / "starts" / start.name, start)
        manifest["starts"][side] = hashlib.md5(start.read_bytes()).hexdigest()
    for file in templates:
        install(file, dest / "templates" / file.name, old / "templates" / file.name)
        manifest["templates"][file.name] = hashlib.md5(file.read_bytes()).hexdigest()
    data = source / "data"
    files = set(data.glob("*.json"))
    for folder in ("learn", "brain", "endgame", "sweep_defaults"):
        files.update(p for p in (data / folder).rglob("*") if p.is_file() and not p.is_symlink() and p.suffix in (".json", ".gz"))
    for side in sides:
        files.update(p for p in (data / "zero_formula_book").glob(f"*/{side}.json"))
    data_hashes = {}
    for file in sorted(files):
        if file.stat().st_size > 150 * 1024 ** 2:
            raise RuntimeError(f"required input exceeds existing benchmark file ceiling: {file}")
        relative = file.relative_to(data)
        data_hashes[str(relative)] = install(file, dest / "data" / relative, old / "data" / relative)
    live = data / "live_baseline_snapshots"
    live_manifest = json.loads((live / "manifest.json").read_text())
    for side in sides:
        file = live / f"{side}.json"
        digest = install(file, dest / "data/live_baseline_snapshots" / file.name, old / "data/live_baseline_snapshots" / file.name)
        if digest != live_manifest["sets"][side]:
            raise RuntimeError("live full-set export changed during capture; snapshot not runnable")
    live_manifest["sets"] = {side: live_manifest["sets"][side] for side in sides}
    (dest / "data/live_baseline_snapshots/manifest.json").write_text(json.dumps(live_manifest, indent=2))
    sys.path.insert(0, str(source / "tools"))
    import v15_npz_bench as bench
    excludes = []
    for pattern in bench.CODE_EXCLUDE:
        excludes.extend(("--exclude", pattern))
    subprocess.run(["rsync", "-a", *excludes, f"{source}/", f"{dest}/code/"], check=True)
    after = {name: sha(source / name) for name in before}
    if after != before:
        raise RuntimeError("canonical critical sources changed while freezing; not runnable")
    env = {}
    for item in Path(f"/proc/{args.env_pid}/environ").read_bytes().split(b"\0"):
        key, separator, value = item.decode(errors="replace").partition("=")
        if separator and key.startswith(("V15_", "V12_", "V18_", "V20_")) and key not in bench.ENV_DROP:
            env[key] = value
    manifest.update(status="FROZEN_INPUTS_STARTUP_PREFLIGHT_REQUIRED", env=env, env_source=f"/proc/{args.env_pid}/environ", data_files=len(data_hashes), data_sha256=data_hashes, live_baseline_frozen={"generated_at": live_manifest["generated_at"], "sets": len(sides)}, completed_at_utc=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"))
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps({"snapshot": str(dest), "status": manifest["status"], "data_files": len(data_hashes), "npz": len(manifest["npz"]), "free_gib_after": shutil.disk_usage(dest).free / 1024 ** 3, "source_sha256": before}), flush=True)


if __name__ == "__main__":
    main()
