#!/usr/bin/env python3
"""Run the preregistered qualified-arm, delayed-lower-top exit campaign.

Each job supplies one immutable, side-specific entry artifact and its matching
NPZ root.  The underlying compiled scanner evaluates 960 delayed exits plus
12 rare-emergency overlays.  Candidate ranking is discovery-only; this
orchestrator reports the untouched final fold only after the two family
winners are frozen.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
STAGE = "VEC_BOTTOM_STRUCTURAL_V2_UNTOUCHED_OOS"
PATH_BY_FAMILY = {
    "BOTTOM_B_STRUCTURAL_V2": "BOTTOM_B_DELAYED_LOWER_TOP",
    "BOTTOM_C_STRUCTURAL_V2_EMERGENCY": "BOTTOM_C_DELAYED_EMERGENCY",
}


def _read(path: Path) -> Any:
    return json.loads(path.read_text())


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n"
    )
    temporary.replace(path)


def preregistration() -> dict[str, Any]:
    return {
        "campaign": "BOTTOM_STRUCTURAL_V2",
        "frozen_before_execution": True,
        "entry_contract": "one immutable side-specific schedule per job",
        "selection_partition": "all outer folds except final",
        "final_partition": "last chronological outer fold, untouched until freeze",
        "side_isolation": True,
        "strategy_capacity_usd": 16000.0,
        "bh_capital_usd": 2000.0,
        "costs_included": True,
        "next_rth_fill": True,
        "future_htf_allowed": 0,
        "tim_gate_pct_each_fold": [70.0, 80.0],
        "must_beat_each_fold": ["side_specific_bh", "same_entry_e02"],
        "solvency_required": True,
        "actual_exit_required": True,
        "persistent_reclaim_required": True,
        "families": {
            "B": {
                "candidate_count": 960,
                "arm_timeframes": ["15m", "1h"],
                "arm_profiles": [
                    ["ATR", 0.5],
                    ["ATR", 1.0],
                    ["STDEV", 1.0],
                    ["STDEV", 1.5],
                    ["DC_SUPPORT", 0.0],
                ],
                "confirmation_timeframes": ["5m", "15m", "1h"],
                "confirmation_modes": ["PRICE_ONLY", "WT_ONLY", "AND", "OR"],
                "confirmation_bars": [1, 2],
                "rebound_atr": [0.5, 1.0],
                "wait_hours": [24, 48],
                "break_bar_can_exit": False,
            },
            "C": {
                "base_count": 4,
                "base_selection": "discovery-only B rank",
                "overlays": ["ADVERSE_ATR_6", "ADVERSE_STDEV_7", "CONTINUED_8"],
                "candidate_count": 12,
                "emergency_share_max_each_fold": 0.10,
            },
        },
    }


def _fold_failures(fold: dict[str, Any], emergency: bool) -> list[str]:
    failures = []
    if float(fold["alpha_vs_bh_pp"]) <= 0:
        failures.append("NOT_ABOVE_BH")
    if float(fold["alpha_vs_same_entry_e02_pp"]) <= 0:
        failures.append("NOT_ABOVE_SAME_ENTRY_E02")
    tim = float(fold["weighted_tim_pct"])
    if not 70.0 <= tim <= 80.0:
        failures.append("TIM_OUTSIDE_70_80")
    if int(fold["exit_fills"]) <= 0:
        failures.append("NO_ACTUAL_EXIT")
    if bool(fold["insolvent"]):
        failures.append("INSOLVENT")
    if bool(fold["entry_capacity_breach"]):
        failures.append("CAPACITY_BREACH")
    if int(fold["future_htf_source_count"]) != 0:
        failures.append("FUTURE_HTF")
    if int(fold["bars_flat_beyond_reclaim"]) != 0:
        failures.append("FORGOTTEN_RECLAIM")
    if emergency and float(fold["emergency_exit_share"]) > 0.10:
        failures.append("EMERGENCY_NOT_RARE")
    return failures


def _compact_winner(
    winner: dict[str, Any], *, isolated: bool
) -> dict[str, Any]:
    emergency = winner["family"] == "BOTTOM_C_STRUCTURAL_V2_EMERGENCY"
    fold_evidence = []
    for index, fold in enumerate(winner["fold_evidence"]):
        failures = _fold_failures(fold, emergency)
        fold_evidence.append(
            {
                **fold,
                "partition": (
                    "UNTOUCHED_FINAL"
                    if index == len(winner["fold_evidence"]) - 1
                    else "DISCOVERY"
                ),
                "gate_pass": not failures,
                "failures": failures,
            }
        )
    all_folds_strict = all(row["gate_pass"] for row in fold_evidence)
    parity = winner.get("compiled_python_parity", {})
    exact_ready = (
        all_folds_strict
        and parity.get("status") == "PASS"
        and not isolated
    )
    return {
        "family": winner["family"],
        "params": winner["params"],
        "compiled_python_parity": parity,
        "fold_evidence": fold_evidence,
        "all_folds_strict": all_folds_strict,
        "exact_engine_replay_ready": exact_ready,
        "quarantine_reason": (
            "ISOLATED_VERSIONED_DATA"
            if all_folds_strict and isolated
            else None
        ),
    }


def _run_job(
    job: dict[str, Any], output_root: Path, resume: bool
) -> dict[str, Any]:
    key = str(job["key"]).upper()
    artifact = Path(job["artifact"]).resolve()
    npz_dir = Path(job["npz_dir"]).resolve()
    output = output_root / key
    result_path = output / "result.json"
    if not (resume and result_path.exists()):
        cmd = [
            sys.executable,
            str(ROOT / "tools" / "vec_same_entry_exit_adapter.py"),
            "--artifact",
            str(artifact),
            "--npz-dir",
            str(npz_dir),
            "--out-dir",
            str(output),
            "--families",
            "BOTTOM_V2",
            "--fold-mode",
            "nested",
            "--exposure-min-pct",
            "70",
            "--exposure-max-pct",
            "80",
        ]
        proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True)
        (output_root / f"{key}.stdout.log").write_text(proc.stdout)
        (output_root / f"{key}.stderr.log").write_text(proc.stderr)
        if proc.returncode:
            raise RuntimeError(f"{key} rc={proc.returncode}: {proc.stderr[-3000:]}")
    source = _read(result_path)
    winners = [
        _compact_winner(
            row, isolated=bool(job.get("isolated_versioned_data", False))
        )
        for row in source["frozen_discovery_winners"]
        if row["family"] in PATH_BY_FAMILY
    ]
    if len(winners) != 2:
        raise RuntimeError(f"{key}: expected two frozen family winners")
    return {
        "key": key,
        "symbol": source["symbol"],
        "side": source["side"],
        "artifact": str(output),
        "entry_artifact": source["source_artifact"],
        "source_npz_sha256": source["source_npz_sha256"],
        "isolated_versioned_data": bool(
            job.get("isolated_versioned_data", False)
        ),
        "candidate_count": int(source["candidate_count"]),
        "compiled_elapsed_seconds": float(
            source["compiled_structural_grid_elapsed_seconds"]
        ),
        "winners": winners,
    }


def _job_ids(path_fleet_root: Path) -> dict[str, int]:
    con = sqlite3.connect(path_fleet_root / "queue.db")
    rows = con.execute("SELECT id,path_id FROM jobs").fetchall()
    con.close()
    return {str(path_id): int(job_id) for job_id, path_id in rows}


def _existing_keys(path_fleet_root: Path) -> set[tuple[str, str, str]]:
    con = sqlite3.connect(path_fleet_root / "queue.db")
    rows = con.execute(
        "SELECT symbol,side,payload_json FROM results WHERE stage=?", (STAGE,)
    ).fetchall()
    con.close()
    found = set()
    for symbol, side, raw in rows:
        payload = json.loads(raw)
        found.add((str(symbol), str(side), str(payload.get("v2_family"))))
    return found


def ingest(path_fleet_root: Path, campaign: dict[str, Any]) -> dict[str, int]:
    from tools import path_fleet_campaign as fleet

    job_ids = _job_ids(path_fleet_root)
    existing = _existing_keys(path_fleet_root)
    appended = skipped = 0
    ingest_root = path_fleet_root / "bottom_structural_v2_ingest"
    for result in campaign["results"]:
        for winner in result["winners"]:
            identity = (
                result["symbol"],
                result["side"],
                winner["family"],
            )
            if identity in existing:
                skipped += 1
                continue
            final = winner["fold_evidence"][-1]
            path_id = PATH_BY_FAMILY[winner["family"]]
            payload = {
                "job_id": job_ids[path_id],
                "symbol": result["symbol"],
                "side": result["side"],
                "stage": STAGE,
                "status": (
                    "VECTOR_SURVIVOR_EXACT_PENDING"
                    if winner["exact_engine_replay_ready"]
                    else "GRAY_REJECTED"
                ),
                "strategy_return_pct": float(final["strategy_return_pct"]),
                "bh_return_pct": float(final["bh_return_pct"]),
                "same_entry_control_return_pct": float(
                    final["same_entry_e02_return_pct"]
                ),
                "tim_pct": float(final["weighted_tim_pct"]),
                "trades": int(final["exit_fills"]),
                "untouched_oos": True,
                "exact_replay": False,
                "future_htf_count": int(final["future_htf_source_count"]),
                "artifact": result["artifact"],
                "v2_family": winner["family"],
                "params": winner["params"],
                "fold_evidence": winner["fold_evidence"],
                "all_folds_strict": winner["all_folds_strict"],
                "isolated_versioned_data": result["isolated_versioned_data"],
                "compiled_python_parity": winner["compiled_python_parity"],
                "campaign_id": campaign["campaign_id"],
                "bh_capital_usd": 2000.0,
                "strategy_capacity_usd": 16000.0,
                "costs_included": True,
                "promotion_allowed": False,
            }
            digest = hashlib.sha256(
                json.dumps(identity).encode("utf-8")
            ).hexdigest()[:12]
            path = ingest_root / f"{digest}.json"
            _atomic_json(path, payload)
            fleet.add_result(path_fleet_root, path)
            existing.add(identity)
            appended += 1
    return {"appended": appended, "skipped": skipped}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs-json", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--path-fleet-root", type=Path)
    args = parser.parse_args()
    jobs = list(_read(args.jobs_json))
    args.output_root.mkdir(parents=True, exist_ok=args.resume)
    frozen = preregistration()
    frozen["jobs"] = jobs
    frozen["jobs_sha256"] = hashlib.sha256(
        json.dumps(jobs, sort_keys=True).encode("utf-8")
    ).hexdigest()
    _atomic_json(args.output_root / "preregistered_contract.json", frozen)

    results = []
    errors = []
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=max(1, args.workers)
    ) as pool:
        futures = {
            pool.submit(_run_job, job, args.output_root, args.resume): job
            for job in jobs
        }
        for future in concurrent.futures.as_completed(futures):
            job = futures[future]
            try:
                result = future.result()
                results.append(result)
                print(
                    json.dumps(
                        {
                            "key": result["key"],
                            "candidates": result["candidate_count"],
                            "strict": sum(
                                row["all_folds_strict"]
                                for row in result["winners"]
                            ),
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
            except Exception as exc:
                errors.append(
                    {
                        "key": job.get("key"),
                        "error": f"{type(exc).__name__}:{exc}",
                    }
                )
    results.sort(key=lambda row: row["key"])
    campaign = {
        "campaign_id": args.output_root.name,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "tier": "VEC_BOTTOM_STRUCTURAL_V2",
        "promotion_allowed": False,
        "preregistered_contract": frozen,
        "job_count": len(jobs),
        "completed_jobs": len(results),
        "errors": errors,
        "candidate_evaluations": sum(
            row["candidate_count"] for row in results
        ),
        "strict_survivor_count": sum(
            winner["all_folds_strict"]
            for row in results
            for winner in row["winners"]
        ),
        "exact_replay_queue": [
            {
                "key": row["key"],
                "family": winner["family"],
                "artifact": row["artifact"],
                "params": winner["params"],
            }
            for row in results
            for winner in row["winners"]
            if winner["exact_engine_replay_ready"]
        ],
        "results": results,
    }
    _atomic_json(args.output_root / "campaign_result.json", campaign)
    if args.path_fleet_root:
        receipt = ingest(args.path_fleet_root.resolve(), campaign)
        campaign["fleet_ingest"] = receipt
        _atomic_json(args.output_root / "campaign_result.json", campaign)
    print(
        json.dumps(
            {
                "campaign": campaign["campaign_id"],
                "completed_jobs": len(results),
                "errors": len(errors),
                "candidate_evaluations": campaign["candidate_evaluations"],
                "strict_survivors": campaign["strict_survivor_count"],
                "exact_queue": len(campaign["exact_replay_queue"]),
            },
            sort_keys=True,
        )
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
