#!/usr/bin/env python3
"""Run the bounded trend-resume augment screen over frozen cohort controls."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


FAMILY = "ENTRY_AUGMENT_TREND_RESUME_ENABLED"


def latest_controls(root: Path) -> dict[tuple[str, str], Path]:
    chosen = {}
    for path in root.glob("band_ladder_walkforward_*"):
        result = path / "result.json"
        if not result.exists():
            continue
        payload = json.loads(result.read_text())
        manifest = payload.get("manifest", {})
        key = (str(manifest.get("symbol")), str(manifest.get("side")))
        if key[1] not in {"LONG", "SHORT"}:
            continue
        if key not in chosen or path.name > chosen[key].name:
            chosen[key] = path
    return chosen


def completed_keys(root: Path) -> set[tuple[str, str]]:
    out = set()
    for path in root.glob(f"entry_overlay_{FAMILY}_*"):
        result = path / "result.json"
        if not result.exists():
            continue
        payload = json.loads(result.read_text())
        manifest = payload.get("manifest", {})
        out.add((str(manifest.get("symbol")), str(manifest.get("side"))))
    return out


def controls_from_summaries(paths: list[Path]) -> dict[tuple[str, str], Path]:
    chosen = {}
    for path in paths:
        payload = json.loads(path.read_text())
        for row in payload.get("symbols", []):
            artifact = Path(row["artifact"])
            side = str(row.get("side") or "")
            if side not in {"LONG", "SHORT"}:
                side = "SHORT" if artifact.name.endswith("_SHORT") else "LONG"
            key = (str(row["symbol"]), side)
            if not artifact.exists():
                raise FileNotFoundError(f"{key}: frozen control missing: {artifact}")
            chosen[key] = artifact
    return chosen


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--npz-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument(
        "--cohort-summary",
        type=Path,
        action="append",
        default=[],
        help="accepted ladder summary; repeat for LONG and SHORT",
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    controls = (
        controls_from_summaries(args.cohort_summary)
        if args.cohort_summary
        else latest_controls(args.root)
    )
    done = set() if args.force else completed_keys(args.root)
    targets = [
        (key, path)
        for key, path in sorted(controls.items())
        if key not in done
    ]
    runner = Path(__file__).with_name("vec_augment_trend_resume_walkforward.py")

    def launch(item):
        key, control = item
        command = [
            sys.executable,
            str(runner),
            "--control-artifact",
            str(control),
            "--npz-dir",
            str(args.npz_dir),
            "--out-dir",
            str(args.root),
            "--start",
            "2024-01-01",
        ]
        completed = subprocess.run(
            command, text=True, capture_output=True, check=False
        )
        return key, completed

    failures = 0
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(launch, item): item[0] for item in targets}
        for future in as_completed(futures):
            key, completed = future.result()
            if completed.returncode:
                failures += 1
                print(
                    json.dumps(
                        {
                            "key": "_".join(key),
                            "returncode": completed.returncode,
                            "stderr": completed.stderr[-2000:],
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
            else:
                print(completed.stdout.strip(), flush=True)
    print(
        json.dumps(
            {
                "family": FAMILY,
                "controls": len(controls),
                "already_complete": len(done & set(controls)),
                "launched": len(targets),
                "failures": failures,
            },
            sort_keys=True,
        )
    )
    return int(failures > 0)


if __name__ == "__main__":
    raise SystemExit(main())
