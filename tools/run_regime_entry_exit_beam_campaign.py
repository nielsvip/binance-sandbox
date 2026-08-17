#!/usr/bin/env python3
"""Separate discovery-only regime exposure × exit beam campaign.

Writes one compact result per frozen entry schedule and a small manifest.
Large raw adapter candidates stay sharded below each entry/policy directory.
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
from tools import regime_conditioned_exposure_grid as regime_grid  # noqa: E402
from tools import run_entry_exit_beam_campaign as beam  # noqa: E402


def _run_adapter(
    adapter: str,
    artifact: Path,
    npz_dir: Path,
    out_dir: Path,
    policy: str,
) -> dict[str, Any]:
    cmd = [
        sys.executable,
        str(ROOT / "tools" / "vec_entry_exit_beam_adapter.py"),
        "--adapter",
        adapter,
        "--artifact",
        str(artifact),
        "--npz-dir",
        str(npz_dir),
        "--out-dir",
        str(out_dir),
        "--regime-policy",
        policy,
        "--exposure-min-pct",
        "70",
        "--exposure-max-pct",
        "80",
    ]
    if adapter == "generic":
        cmd.extend(
            [
                "--families",
                beam.GENERIC_FAMILIES,
                "--fold-mode",
                "nested",
            ]
        )
    proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True)
    (out_dir.parent / f"{adapter}.stdout.log").write_text(proc.stdout)
    (out_dir.parent / f"{adapter}.stderr.log").write_text(proc.stderr)
    if proc.returncode:
        raise RuntimeError(
            f"{adapter}/{policy} rc={proc.returncode}: "
            f"{proc.stderr[-3000:]}"
        )
    return json.loads((out_dir / "result.json").read_text())


def _screen_policy(
    artifact: Path,
    npz_dir: Path,
    output_root: Path,
    policy: str,
) -> dict[str, Any]:
    source = json.loads((artifact / "result.json").read_text())
    symbol = str(source["manifest"]["symbol"]).upper()
    side = str(source["manifest"]["side"]).upper()
    family = str(
        source["manifest"].get("family", "ENTRY_LADDER_GREEN")
    )
    digest = hashlib.sha256(str(artifact).encode()).hexdigest()[:10]
    base = output_root / f"{symbol}_{side}" / f"{family}_{digest}" / policy
    base.mkdir(parents=True, exist_ok=False)
    entry = {
        "symbol": symbol,
        "side": side,
        "family": family,
        "artifact": str(artifact),
    }
    candidates = []
    for adapter in ("generic", "e05", "peak"):
        payload = _run_adapter(
            adapter, artifact, npz_dir, base / adapter, policy
        )
        for candidate in payload["candidates"]:
            if candidate["family"] not in beam.EXIT_TO_PATH:
                continue
            row = beam._candidate_discovery_summary(
                candidate, adapter, entry
            )
            row["regime_policy"] = policy
            candidates.append(row)
    return {
        "symbol": symbol,
        "side": side,
        "entry_family": family,
        "entry_artifact": str(artifact),
        "policy": policy,
        "artifact": str(base),
        "candidates": candidates,
    }


def _public(row: dict[str, Any], reveal: bool) -> dict[str, Any]:
    public = beam._public_candidate(row, reveal)
    public["regime_policy"] = row["regime_policy"]
    return public


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--entry-artifact", type=Path, action="append", required=True)
    ap.add_argument("--npz-dir", type=Path, required=True)
    ap.add_argument("--output-root", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--exit-beam-width", type=int, default=8)
    args = ap.parse_args()
    args.output_root.mkdir(parents=True, exist_ok=False)
    policies = [row.name for row in regime_grid.policy_grid()]
    artifacts = [path for path in args.entry_artifact]
    raw_results = []
    errors = []
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=max(1, args.workers)
    ) as pool:
        future_map = {
            pool.submit(
                _screen_policy,
                artifact,
                args.npz_dir.resolve(),
                args.output_root.resolve(),
                policy,
            ): (artifact, policy)
            for artifact in artifacts
            for policy in policies
        }
        for future in concurrent.futures.as_completed(future_map):
            artifact, policy = future_map[future]
            try:
                row = future.result()
                raw_results.append(row)
                print(
                    json.dumps(
                        {
                            "key": f"{row['symbol']}_{row['side']}",
                            "entry": row["entry_family"],
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
    metadata: dict[str, dict[str, Any]] = {}
    for row in raw_results:
        key = row["entry_artifact"]
        grouped.setdefault(key, []).extend(row["candidates"])
        metadata[key] = row
    results = []
    exact = []
    for entry_artifact, candidates in grouped.items():
        candidates.sort(key=beam._exit_rank)
        frozen = candidates[: args.exit_beam_width]
        meta = metadata[entry_artifact]
        public = [_public(row, True) for row in frozen]
        entry_result = {
            "symbol": meta["symbol"],
            "side": meta["side"],
            "entry_family": meta["entry_family"],
            "entry_artifact": entry_artifact,
            "candidate_count": len(candidates),
            "policy_count": len(policies),
            "frozen_regime_exit_beam": public,
            "strict_survivors": [
                row for row in public if row["all_folds_strict"]
            ],
            "policy_discovery_leaderboard": [
                _public(row, False) for row in candidates[:50]
            ],
        }
        entry_dir = (
            args.output_root
            / f"{meta['symbol']}_{meta['side']}"
            / (
                f"{meta['entry_family']}_"
                f"{hashlib.sha256(entry_artifact.encode()).hexdigest()[:10]}"
            )
        )
        (entry_dir / "compact_result.json").write_text(
            json.dumps(
                entry_result, indent=2, sort_keys=True, allow_nan=False
            )
            + "\n"
        )
        results.append(
            {
                key: value
                for key, value in entry_result.items()
                if key not in {
                    "frozen_regime_exit_beam",
                    "policy_discovery_leaderboard",
                }
            }
            | {"compact_result": str(entry_dir / "compact_result.json")}
        )
        exact.extend(
            {
                "symbol": meta["symbol"],
                "side": meta["side"],
                "entry_family": meta["entry_family"],
                "entry_artifact": entry_artifact,
                "regime_policy": row["regime_policy"],
                "exit_family": row["exit_family"],
                "exit_params": row["exit_params"],
            }
            for row in entry_result["strict_survivors"]
        )
    manifest = {
        "tier": "VEC_REGIME_CONDITIONED_ENTRY_EXIT_BEAM",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "contract": {
            "separate_from_immutable_entry_exit_beam": True,
            "one_entry_family_only": True,
            "policies": policies,
            "fixed_global_completed_regime_classifier": True,
            "symbol_specific_thresholds": False,
            "selection_uses_discovery_only": True,
            "final_fold_evaluation_only": True,
            "every_fold_tim_gate_pct": [70.0, 80.0],
            "bh_capital_usd": 2000.0,
            "strategy_capacity_usd": 16000.0,
            "promotion_allowed": False,
        },
        "entry_schedules": len(results),
        "policy_screens": len(raw_results),
        "results": results,
        "errors": errors,
        "exact_replay_queue": exact,
    }
    (args.output_root / "campaign_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )
    print(
        json.dumps(
            {
                "entries": len(results),
                "policy_screens": len(raw_results),
                "errors": len(errors),
                "exact_queue": len(exact),
                "manifest": str(
                    args.output_root / "campaign_manifest.json"
                ),
            },
            sort_keys=True,
        )
    )
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
