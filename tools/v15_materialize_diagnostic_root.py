"""Build a new diagnostic root from frozen inputs; no live output links are copied."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import shutil


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--dest", type=Path, required=True)
    parser.add_argument("--side", required=True)
    args = parser.parse_args()
    snapshot, dest = args.snapshot.resolve(), args.dest.resolve()
    if dest.exists():
        raise SystemExit("diagnostic destination already exists")
    source = snapshot / "code"
    manifest = json.loads((snapshot / "manifest.json").read_text())
    assert manifest["status"] == "FROZEN_INPUTS_STARTUP_PREFLIGHT_REQUIRED"
    assert args.side in manifest["sides"]
    code_extensions = {".py", ".json", ".csv", ".sh", ".txt", ".html", ".css", ".js", ".gz"}
    packages = ("tools", "vec_decisions", "charts", "backtest_framework", "backtest_v8", "backtest_v12", "indicators", "services", "shared", "strategies")
    files = set(p for p in source.iterdir() if (p.is_file() or p.is_symlink()) and p.suffix in code_extensions)
    for package in packages:
        files.update(p for p in (source / package).rglob("*") if p.is_file() and not p.is_symlink() and p.suffix in code_extensions)
    dest.mkdir()
    hashes = {}
    materialized_links = []
    omitted_links = []
    def install(file, relative):
        payload = file.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        target = dest / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        assert hashlib.sha256(target.read_bytes()).hexdigest() == digest
        hashes[str(relative)] = digest
        return digest
    for file in sorted(files):
        if file.is_symlink():
            omitted_links.append({"source": str(file), "resolved_source": str(file.resolve()), "reason": "not a frozen materialized input; required import must fail rather than follow a live path"})
            continue
        else:
            install(file, file.relative_to(source))
    for file in (snapshot / "data").rglob("*"):
        if not file.is_file():
            continue
        assert not file.is_symlink()
        relative = file.relative_to(snapshot)
        if relative.parts[:2] == ("data", "live_baseline_snapshots") and file.name not in (f"{args.side}.json", "manifest.json"):
            continue
        install(file, relative)
    live_manifest_path = dest / "data/live_baseline_snapshots/manifest.json"
    live_manifest = json.loads(live_manifest_path.read_text())
    live_manifest["sets"] = {args.side: live_manifest["sets"][args.side]}
    live_manifest_path.write_text(json.dumps(live_manifest, indent=2))
    hashes[str(live_manifest_path.relative_to(dest))] = hashlib.sha256(live_manifest_path.read_bytes()).hexdigest()
    for file in (snapshot / "templates").glob("*.xlsx"):
        install(file, Path("SPREADSHEETS/TEMPLATE_FINAL_NORM") / file.name)
    symbol = args.side.rsplit("_", 1)[0]
    install(snapshot / "npz" / f"{symbol}.npz", Path("backtest_v8/indicators") / f"{symbol}.npz")
    install(snapshot / "starts" / f"{args.side}_autopsy_base.json", Path("research/starts") / f"{args.side}.json")
    links = [str(p.relative_to(dest)) for p in dest.rglob("*") if p.is_symlink()]
    assert not links
    report = {"created_at_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "status": "MATERIALIZED_BOOK_AND_ROUTING_PREFLIGHT_REQUIRED", "side": args.side, "snapshot": str(snapshot), "source_env": manifest["env"], "frozen_source_sha256": manifest["source_sha256"], "files_sha256": hashes, "symlinks": links, "materialized_python_links": materialized_links, "omitted_source_links": omitted_links, "code_allowlist": {"root_extensions": sorted(code_extensions), "packages": packages}, "production_registration_allowed": False}
    (dest / "research/materialization.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({"dest": str(dest), "files": len(hashes), "symlinks": links, "materialized_python_links": materialized_links, "status": report["status"]}), flush=True)


if __name__ == "__main__":
    main()
