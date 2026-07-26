#!/usr/bin/env python3
"""Run active DC-tier augment semantics over frozen LONG/SHORT controls."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


FAMILY = "ENTRY_DC_TIER_AUG_ENABLED"


def controls_from_summaries(paths: list[Path]) -> dict[tuple[str, str], Path]:
    out = {}
    for path in paths:
        for row in json.loads(path.read_text()).get("symbols", []):
            artifact = Path(row["artifact"])
            side = str(row.get("side") or (
                "SHORT" if artifact.name.endswith("_SHORT") else "LONG"
            ))
            if not artifact.exists():
                raise FileNotFoundError(artifact)
            out[(str(row["symbol"]), side)] = artifact
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--npz-dir", type=Path, required=True)
    ap.add_argument("--cohort-summary", type=Path, action="append", required=True)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()
    controls = controls_from_summaries(args.cohort_summary)
    runner = Path(__file__).with_name("vec_dc_tier_augment_walkforward.py")

    def launch(item):
        key, control = item
        command = [
            sys.executable, str(runner),
            "--control-artifact", str(control),
            "--npz-dir", str(args.npz_dir),
            "--out-dir", str(args.root),
        ]
        return key, subprocess.run(
            command, text=True, capture_output=True, check=False
        )

    failures = 0
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = [pool.submit(launch, item) for item in sorted(controls.items())]
        for future in as_completed(futures):
            key, result = future.result()
            if result.returncode:
                failures += 1
                print(json.dumps({
                    "key": "_".join(key), "returncode": result.returncode,
                    "stderr": result.stderr[-2000:],
                }), flush=True)
            else:
                print(result.stdout.strip(), flush=True)
    print(json.dumps({
        "family": FAMILY, "controls": len(controls), "failures": failures
    }, sort_keys=True))
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
