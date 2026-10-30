"""Complete immutable data/template inputs after source closure, without running a board."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--mac-inputs", type=Path, required=True)
    args = parser.parse_args()
    snapshot = args.snapshot.resolve()
    closure = json.loads((snapshot / "dependency_closure.json").read_text())
    assert closure["status"] == "SOURCE_CLOSURE_PREFLIGHT_PASS"
    manifest = json.loads((snapshot / "manifest.json").read_text())
    assert not (snapshot / "data").exists() and not (snapshot / "templates").exists()
    hashes = {}
    for directory in ("data", "templates"):
        for file in (args.base / directory).rglob("*"):
            if not file.is_file():
                continue
            assert not file.is_symlink()
            relative = file.relative_to(args.base)
            if relative.parts[:2] == ("data", "live_baseline_snapshots"):
                continue
            target = snapshot / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            os.link(file, target)  # only previously frozen immutable snapshot bytes
            hashes[str(relative)] = hashlib.sha256(target.read_bytes()).hexdigest()
    live = snapshot / "data/live_baseline_snapshots"
    live.mkdir(parents=True)
    for file in (args.mac_inputs / "live_baseline_snapshots").glob("*.json"):
        target = live / file.name
        target.write_bytes(file.read_bytes())
        hashes[str(target.relative_to(snapshot))] = hashlib.sha256(target.read_bytes()).hexdigest()
    # Keep dataset and code versions explicit. The gate supplies the separate NN
    # bundle declaration; snapshot knowledge alone must not masquerade as that NN.
    manifest.update(status="FROZEN_CURRENT8_COMMON_RUNTIME_READY_NN_BUNDLE_REQUIRED", common_code=str(snapshot / "common_code"), dependency_closure_sha256=hashlib.sha256((snapshot / "dependency_closure.json").read_bytes()).hexdigest(), frozen_data_sha256=hashes, data_files=sum(name.startswith("data/") for name in hashes), completed_at_utc=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"), required_next_step="Existing S2 repaired gate, explicit prior/candidate8-model hashes and matched common runtime; complete real8-side results before gain claim")
    previous = snapshot / ("manifest_before_fixture_ready_" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".json")
    previous.write_bytes((snapshot / "manifest.json").read_bytes())
    (snapshot / "manifest.json").write_text(json.dumps(manifest, indent=2))
    assert not any(file.is_symlink() for file in snapshot.rglob("*"))
    print(json.dumps({"snapshot": str(snapshot), "status": manifest["status"], "common_code": manifest["common_code"], "code_files": closure["files"], "compiled_modules": closure["static_modules_compiled"], "data_files": manifest["data_files"], "sides": manifest["sides"]}, indent=2), flush=True)


if __name__ == "__main__":
    main()
