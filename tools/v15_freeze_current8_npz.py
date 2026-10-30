"""Freeze eight admitted NPZs and derive holdout boundaries from their actual bytes."""
import argparse
import ast
import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import numpy as np


def stamp(seconds=None):
    value = datetime.datetime.now(datetime.timezone.utc) if seconds is None else datetime.datetime.fromtimestamp(float(seconds), datetime.timezone.utc)
    return value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mac-inputs", type=Path, required=True)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--dest", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--reuse-six", type=Path)
    args = parser.parse_args()
    assert not args.dest.exists(), "new snapshot only"
    admission = json.loads((args.mac_inputs / "admission.json").read_text())
    base = json.loads((args.base / "manifest.json").read_text())
    for name, digest in admission["book_sha256"].items():
        assert hashlib.sha256((args.mac_inputs / name).read_bytes()).hexdigest() == digest
    source = (args.source / "tools/opt/evaluate_v12.py").read_text()
    tree = ast.parse(source)
    selected = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ("_exact_30d_slice", "_compact_to_15m")]
    namespace = {}
    exec(compile(ast.Module(body=selected, type_ignores=[]), "frozen_actual_slicers", "exec"), namespace)
    args.dest.mkdir()
    (args.dest / "npz").mkdir()
    (args.dest / "starts").mkdir()
    manifest = {"at": stamp(), "status": "NPZ_AND_ADMISSION_READY_DEPENDENCY_CLOSURE_PENDING", "src": str(args.source), "sides": admission["sides"], "npz": {}, "starts": {}, "templates": base["templates"], "env": base["env"], "env_source": base["env_source"], "admission": admission, "slicer_source_sha256": hashlib.sha256(source.encode()).hexdigest(), "holdout_days": 7, "npz_policy": "six live NPZ byte copies; existing immutable COIN/CDE data reused; every bar vintage explicit"}
    (args.dest / "historical_comparison_manifest.json").write_text(json.dumps(base, indent=2))
    for side in admission["sides"]:
        symbol = side.rsplit("_", 1)[0]
        old = symbol not in ("COIN", "CDE") if args.reuse_six else symbol in ("COIN", "CDE")
        immutable = args.reuse_six if args.reuse_six else args.base
        file = (immutable / "npz" if old else args.source / "backtest_v8/indicators") / f"{symbol}.npz"
        payload = file.read_bytes()
        target = args.dest / "npz" / file.name
        if old:
            os.link(file, target)
        else:
            target.write_bytes(payload)
        digest = hashlib.sha256(payload).hexdigest()
        assert hashlib.sha256(target.read_bytes()).hexdigest() == digest
        with np.load(io.BytesIO(payload), allow_pickle=True) as z:
            times = np.asarray(z["timestamps"], dtype=float)
            mapping = {"timestamps": times}
            if "timestamp_15m" in z.files:
                mapping["timestamp_15m"] = np.asarray(z["timestamp_15m"])
        finite = times[np.isfinite(times) & (times > 0)]
        seconds = finite / (1000 if finite[-1] > 1e11 else 1)
        crypto = admission["admission"][side]["venue"] == "crypto"
        sliced, policy = namespace["_exact_30d_slice"](mapping, crypto, 7)
        bound_kind = "actual_raw_slice_first_bar_conservative_before_compaction"
        if "timestamp_15m" in sliced:
            sliced = namespace["_compact_to_15m"](sliced)
            bound_kind = "actual_first_completed_15m_event"
        start = float(sliced["timestamps"][0])
        start /= 1000 if start > 1e11 else 1
        manifest["npz"][symbol] = {"md5": hashlib.md5(payload).hexdigest(), "sha256": digest, "bytes": len(payload), "src": str(file), "src_mtime": file.stat().st_mtime, "captured_at_utc": stamp(), "first_bar_utc": stamp(seconds[0]), "last_bar_utc": stamp(seconds[-1]), "days_15m": round((seconds[-1] - seconds[0]) / 86400, 6), "holdout_start_utc": stamp(start), "holdout_start_epoch": start, "holdout_policy": policy, "holdout_bound_kind": bound_kind, "training_event_latest_allowed": "strictly before holdout_start_epoch; no example/label/aggregate may include later observations"}
        full = args.mac_inputs / "live_baseline_snapshots" / f"{side}.json"
        assert hashlib.sha256(full.read_bytes()).hexdigest() == admission["baseline_sha256"][side]
        (args.dest / "starts" / f"{side}_autopsy_base.json").write_bytes(full.read_bytes())
        manifest["starts"][side] = hashlib.md5(full.read_bytes()).hexdigest()
    if args.reuse_six:
        manifest["npz_policy"] = "six immutable NPZs reused from prior current8; fresh COIN/CDE source byte copies, original bar timestamps retained"
        manifest["previous_current8_snapshot"] = str(args.reuse_six)
    (args.dest / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps({"snapshot": str(args.dest), "status": manifest["status"], "sides": [{"side": side, **{k: manifest["npz"][side.rsplit('_', 1)[0]][k] for k in ("sha256", "last_bar_utc", "holdout_start_utc", "days_15m", "holdout_bound_kind")}} for side in admission["sides"]]}, indent=2), flush=True)


if __name__ == "__main__":
    main()
