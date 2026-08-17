#!/usr/bin/env python3
"""Run separated causal bounce-reason paths over frozen LONG/SHORT controls."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


FAMILIES = (
    "ENTRY_BOUNCE_15M_LOW",
    "ENTRY_BOUNCE_5M_LOW",
    "ENTRY_4H_DEEP_VALUE",
    "ENTRY_1H_TURN_UP",
)


def controls_from_summaries(paths: list[Path]) -> dict[tuple[str, str], Path]:
    out = {}
    for path in paths:
        for row in json.loads(path.read_text()).get("symbols", []):
            artifact = Path(row["artifact"])
            side = str(
                row.get("side")
                or ("SHORT" if artifact.name.endswith("_SHORT") else "LONG")
            )
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
    ap.add_argument("--shortlist", type=int, default=24)
    ap.add_argument("--family", action="append", choices=FAMILIES)
    args = ap.parse_args()
    controls = controls_from_summaries(args.cohort_summary)
    families = args.family or list(FAMILIES)
    runner = Path(__file__).with_name("vec_entry_overlay_walkforward.py")

    def launch(item):
        family, (key, control) = item
        command = [
            sys.executable,
            str(runner),
            "--control-artifact",
            str(control),
            "--family",
            family,
            "--npz-dir",
            str(args.npz_dir),
            "--out-dir",
            str(args.root),
            "--shortlist",
            str(args.shortlist),
            "--target-tim-low",
            "70",
            "--target-tim-high",
            "80",
        ]
        return family, key, subprocess.run(
            command, text=True, capture_output=True, check=False
        )

    failures = 0
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        work = [
            (family, item)
            for family in families
            for item in sorted(controls.items())
        ]
        futures = [pool.submit(launch, item) for item in work]
        for future in as_completed(futures):
            family, key, result = future.result()
            if result.returncode:
                failures += 1
                print(
                    json.dumps(
                        {
                            "key": "_".join(key),
                            "family": family,
                            "returncode": result.returncode,
                            "stderr": result.stderr[-2000:],
                        }
                    ),
                    flush=True,
                )
            else:
                print(result.stdout.strip(), flush=True)
    print(
        json.dumps(
            {
                "families": families,
                "controls_per_family": len(controls),
                "failures": failures,
            },
            sort_keys=True,
        )
    )
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
