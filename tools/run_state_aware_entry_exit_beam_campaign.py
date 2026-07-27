#!/usr/bin/env python3
"""Discovery-frozen state-aware exposure × bottom-exit beam.

The campaign uses one frozen entry family at a time.  Four preregistered
global policies condition it using only source-E02 schedule/account state.
Only E02 and the extended bottom A/B families are screened.  Exit ranking is
discovery-only; the final fold is exposed only for the frozen top-eight beam.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools import run_entry_exit_beam_campaign as beam  # noqa: E402
from tools import state_aware_exposure_grid as state_grid  # noqa: E402

FAMILIES = "E02_GRID,BOTTOM_A_EXT,BOTTOM_B_EXT"


def _run_one(
    artifact: Path,
    npz_dir: Path,
    output_root: Path,
    policy: str,
) -> dict[str, Any]:
    source = json.loads((artifact / "result.json").read_text())
    manifest = source["manifest"]
    symbol = str(manifest["symbol"]).upper()
    side = str(manifest["side"]).upper()
    family = str(manifest.get("family", "ENTRY_LADDER_GREEN"))
    digest = hashlib.sha256(str(artifact).encode()).hexdigest()[:10]
    base = output_root / f"{symbol}_{side}" / f"{family}_{digest}" / policy
    out = base / "generic"
    base.mkdir(parents=True, exist_ok=False)
    cmd = [
        sys.executable,
        str(ROOT / "tools" / "vec_entry_exit_beam_adapter.py"),
        "--adapter",
        "generic",
        "--artifact",
        str(artifact),
        "--npz-dir",
        str(npz_dir),
        "--out-dir",
        str(out),
        "--families",
        FAMILIES,
        "--fold-mode",
        "nested",
        "--state-policy",
        policy,
        "--exposure-min-pct",
        "70",
        "--exposure-max-pct",
        "80",
    ]
    proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True)
    (base / "stdout.log").write_text(proc.stdout)
    (base / "stderr.log").write_text(proc.stderr)
    if proc.returncode:
        raise RuntimeError(f"{symbol}/{policy}: {proc.stderr[-3000:]}")
    payload = json.loads((out / "result.json").read_text())
    entry = {
        "symbol": symbol,
        "side": side,
        "family": family,
        "artifact": str(artifact),
    }
    candidates = []
    for candidate in payload["candidates"]:
        if candidate["family"] not in beam.EXIT_TO_PATH:
            continue
        row = beam._candidate_discovery_summary(candidate, "generic", entry)
        row["state_policy"] = policy
        candidates.append(row)
    return {
        **entry,
        "state_policy": policy,
        "artifact_root": str(base),
        "candidates": candidates,
    }


def _public(row: dict[str, Any], reveal: bool) -> dict[str, Any]:
    public = beam._public_candidate(row, reveal)
    public["state_policy"] = row["state_policy"]
    return public


def freeze_candidates(
    candidates: list[dict[str, Any]], width: int
) -> list[dict[str, Any]]:
    """Discovery-only freeze boundary; final fields are ignored by rank."""
    return sorted(candidates, key=beam._exit_rank)[:width]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--entry-artifact", type=Path, action="append", required=True)
    ap.add_argument("--npz-dir", type=Path, required=True)
    ap.add_argument("--output-root", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--exit-beam-width", type=int, default=8)
    args = ap.parse_args()
    args.output_root.mkdir(parents=True, exist_ok=False)
    policies = [row.name for row in state_grid.policy_grid()]
    artifacts = [path.resolve() for path in args.entry_artifact]
    raw_results: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=max(1, args.workers)
    ) as pool:
        futures = {
            pool.submit(
                _run_one,
                artifact,
                args.npz_dir.resolve(),
                args.output_root.resolve(),
                policy,
            ): (artifact, policy)
            for artifact in artifacts
            for policy in policies
        }
        for future in concurrent.futures.as_completed(futures):
            artifact, policy = futures[future]
            try:
                row = future.result()
                raw_results.append(row)
                print(
                    json.dumps(
                        {
                            "key": f"{row['symbol']}_{row['side']}",
                            "policy": policy,
                            "candidates": len(row["candidates"]),
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
            except Exception as exc:
                errors.append(
                    {
                        "artifact": str(artifact),
                        "policy": policy,
                        "error": f"{type(exc).__name__}:{exc}",
                    }
                )
    grouped: dict[str, list[dict[str, Any]]] = {}
    meta: dict[str, dict[str, Any]] = {}
    for row in raw_results:
        grouped.setdefault(row["artifact"], []).extend(row["candidates"])
        meta[row["artifact"]] = row
    results = []
    exact = []
    for entry_artifact, candidates in grouped.items():
        frozen = freeze_candidates(candidates, args.exit_beam_width)
        public = [_public(row, True) for row in frozen]
        info = meta[entry_artifact]
        strict = [row for row in public if row["all_folds_strict"]]
        entry_result = {
            "symbol": info["symbol"],
            "side": info["side"],
            "entry_family": info["family"],
            "entry_artifact": entry_artifact,
            "candidate_count": len(candidates),
            "policy_count": len(policies),
            "frozen_state_exit_beam": public,
            "strict_survivors": strict,
            "policy_discovery_leaderboard": [
                _public(row, False)
                for row in sorted(candidates, key=beam._exit_rank)[:50]
            ],
        }
        digest = hashlib.sha256(entry_artifact.encode()).hexdigest()[:10]
        entry_dir = (
            args.output_root
            / f"{info['symbol']}_{info['side']}"
            / f"{info['family']}_{digest}"
        )
        compact = entry_dir / "compact_result.json"
        compact.write_text(
            json.dumps(entry_result, indent=2, sort_keys=True, allow_nan=False)
            + "\n"
        )
        results.append(
            {
                key: value
                for key, value in entry_result.items()
                if key not in {
                    "frozen_state_exit_beam",
                    "policy_discovery_leaderboard",
                }
            }
            | {"compact_result": str(compact)}
        )
        exact.extend(
            {
                "symbol": info["symbol"],
                "side": info["side"],
                "entry_family": info["family"],
                "entry_artifact": entry_artifact,
                "state_policy": row["state_policy"],
                "exit_family": row["exit_family"],
                "exit_params": row["exit_params"],
            }
            for row in strict
        )
    manifest = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "campaign": "STATE_AWARE_ENTRY_EXIT_BEAM",
        "preregistered_grid": [
            {
                **vars(row),
                "scale": list(row.scale),
                "min_gap_completed_1h_bars": list(
                    row.min_gap_completed_1h_bars
                ),
                "cap_mult": list(row.cap_mult),
            }
            for row in state_grid.policy_grid()
        ],
        "contracts": {
            "market_regime_features": False,
            "symbol_specific_thresholds": False,
            "entry_family_blends": False,
            "source_e02_state_exogenous": True,
            "selection": "discovery folds only",
            "final_fold": "revealed after top-eight exit freeze",
            "hard_capacity_usd": 16000,
            "base_unit_and_bh_usd": 2000,
            "hard_max_mult": 8,
            "long_short_isolated": True,
            "exact_replay": "strict all-fold survivors only",
            "live_or_npz_changes": False,
        },
        "families": FAMILIES.split(","),
        "entry_results": results,
        "exact_replay_queue": exact,
        "errors": errors,
    }
    (args.output_root / "campaign_result.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )
    print(
        json.dumps(
            {
                "output": str(args.output_root),
                "entries": len(results),
                "errors": len(errors),
                "exact": len(exact),
            },
            sort_keys=True,
        )
    )
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())

