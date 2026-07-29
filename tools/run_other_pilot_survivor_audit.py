#!/usr/bin/env python3
"""Bounded, leak-safe rescreen for the non-MU Tradier pilot keys.

This driver deliberately treats the band ladder as vector research only.  It
uses folds 1-2 to choose one *meta profile* (capacity and exit horizon), then
executes that profile once on the untouched third fold.  Losing profiles never
touch fold 3.  A passing vector holdout is still gray until the ordinary
``backtest_v8_engine`` decision path and the live state machine produce an
independent parity receipt; the private research schedule is not accepted as
"exact".

Selection is based on return per pre-cost dollar committed, relative to the
better of side-aware B&H and cash.  Ratios with |B&H| below 20 percentage
points are telemetry only and cannot rank a candidate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np


ROOT = Path(__file__).resolve().parent.parent
LADDER = ROOT / "tools" / "vec_band_ladder_walkforward.py"
DEFAULT_KEYS = (
    "TTD_SHORT",
    "ACN_SHORT",
    "NVDA_LONG",
    "VT_LONG",
    "LAC_SHORT",
)
DEFAULT_CAPACITIES = (4_000, 8_000)
DEFAULT_EXIT_NS = (20, 30, 45)
CONTRACT = "OTHER_PILOT_DEPLOYED_ALPHA_V1"


def _utc(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), timezone.utc).isoformat()


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _load_expected(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    payload = json.loads(path.read_text())
    return payload.get("keys", payload)


def audit_npz(
    key: str,
    npz_dir: Path,
    expected: dict[str, Any],
    max_age_hours: float,
    now: datetime,
) -> dict[str, Any]:
    symbol, side = key.rsplit("_", 1)
    path = npz_dir / f"{symbol}.npz"
    row: dict[str, Any] = {
        "key": key,
        "symbol": symbol,
        "side": side,
        "path": str(path),
        "valid": False,
        "errors": [],
    }
    if not path.exists():
        row["errors"].append("NPZ_MISSING")
        return row
    sha = _hash(path)
    row["sha256"] = sha
    try:
        with np.load(path, allow_pickle=False) as z:
            names = set(z.files)
            ts_key = "timestamps" if "timestamps" in names else "timestamp"
            ts = np.asarray(z[ts_key], dtype=np.int64)
            synthetic = (
                np.asarray(z["synthetic_5m"], dtype=np.uint8).astype(bool)
                if "synthetic_5m" in names
                else np.zeros(len(ts), dtype=bool)
            )
            parent_present = "synthetic_5m_parent_close_ts" in names
            row.update(
                {
                    "rows": int(len(ts)),
                    "first_ts": int(ts[0]),
                    "first_utc": _utc(int(ts[0])),
                    "last_ts": int(ts[-1]),
                    "last_utc": _utc(int(ts[-1])),
                    "arrays": len(names),
                    "synthetic_rows": int(synthetic.sum()),
                    "parent_clock_present": parent_present,
                }
            )
            if len(ts) < 1_000:
                row["errors"].append("TOO_FEW_ROWS")
            if np.any(np.diff(ts) < 0):
                row["errors"].append("TIMESTAMPS_NOT_MONOTONE")
            if synthetic.any() and not parent_present:
                row["errors"].append("SYNTHETIC_PARENT_CLOCK_MISSING")
            if parent_present:
                parent = np.asarray(
                    z["synthetic_5m_parent_close_ts"], dtype=np.int64
                )
                if len(parent) != len(ts):
                    row["errors"].append("PARENT_CLOCK_LENGTH_MISMATCH")
                elif synthetic.any() and np.any(parent[synthetic] < ts[synthetic]):
                    row["errors"].append("PARENT_CLOCK_PRECEDES_CHILD")
    except Exception as exc:  # fail closed, and retain the exact reason
        row["errors"].append(f"NPZ_READ_ERROR:{type(exc).__name__}:{exc}")
        return row

    last_dt = datetime.fromtimestamp(row["last_ts"], timezone.utc)
    row["age_hours"] = (now - last_dt).total_seconds() / 3600.0
    if row["age_hours"] > max_age_hours:
        row["errors"].append("NPZ_TOO_OLD_FOR_RESCREEN")

    exp = expected.get(key, expected.get(symbol, {}))
    row["expected"] = exp
    prefix = str(exp.get("sha256_prefix", exp.get("sha256", ""))).lower()
    if prefix and not sha.startswith(prefix):
        row["errors"].append("FROZEN_SHA_MISMATCH")
    if exp.get("rows") is not None and int(exp["rows"]) != row.get("rows"):
        row["errors"].append("FROZEN_ROW_COUNT_MISMATCH")
    if exp.get("last_ts") is not None and int(exp["last_ts"]) != row.get("last_ts"):
        row["errors"].append("FROZEN_LAST_TS_MISMATCH")
    if exp.get("parent_clock") is True and not row.get("parent_clock_present"):
        row["errors"].append("FROZEN_PARENT_CLOCK_MISMATCH")
    row["valid"] = not row["errors"]
    return row


def _profile_command(
    *,
    symbol: str,
    side: str,
    npz_dir: Path,
    out_dir: Path,
    capacity: int,
    exit_n: int,
    end: str | None,
    random_curves: int,
    seed: int,
    tim_lo: float,
    tim_hi: float,
    tim_weight: float,
) -> list[str]:
    command = [
        sys.executable,
        str(LADDER),
        "--symbol",
        symbol,
        "--side",
        side,
        "--npz-dir",
        str(npz_dir),
        "--out-dir",
        str(out_dir),
        "--start",
        "2024-01-01",
        "--capacity",
        str(capacity),
        "--exit-n",
        str(exit_n),
        "--random-curves",
        str(random_curves),
        "--seed",
        str(seed),
        "--tim-lo",
        str(tim_lo),
        "--tim-hi",
        str(tim_hi),
        "--tim-weight",
        str(tim_weight),
        "--no-replay-spec",
    ]
    if end:
        command.extend(["--end", end])
    return command


def _run_profile(command: list[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    proc = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        capture_output=True,
        env={**os.environ, "PYTHONHASHSEED": "0"},
    )
    call = {
        "command": command,
        "returncode": proc.returncode,
        "stderr_tail": proc.stderr[-4_000:],
    }
    if proc.returncode:
        return call, {"error": "PROFILE_PROCESS_FAILED"}
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    summary = json.loads(lines[-1])
    artifact = Path(summary["artifact"])
    payload = json.loads((artifact / "result.json").read_text())
    call["artifact"] = str(artifact)
    return call, payload


def _tim_distance(value: float, lo: float, hi: float) -> float:
    if value < lo:
        return lo - value
    if value > hi:
        return value - hi
    return 0.0


def fold_gate(
    fold: dict[str, Any],
    tim_lo: float,
    tim_hi: float,
    causality: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metrics = fold["validation_metrics"]
    failures = []
    if metrics["deployed_alpha_vs_bh_or_cash_pp"] <= 0.0:
        failures.append("NOT_ABOVE_SIDE_BH_OR_CASH_ON_DEPLOYED_CAPITAL")
    tim = float(metrics["exposure_weighted_tim_pct"])
    if not (tim_lo <= tim <= tim_hi):
        failures.append("TIM_OUTSIDE_65_80")
    if float(metrics["fill_ratio"]) < 0.75:
        failures.append("FILL_RATIO_BELOW_0.75")
    if int(metrics["bars_flat_beyond_reclaim"]) != 0:
        failures.append("RECLAIM_CONTRACT_BREACH")
    if metrics["insolvent"]:
        failures.append("INSOLVENT")
    if metrics["entry_capacity_breach"]:
        failures.append("ENTRY_CAPACITY_BREACH")
    causal_rows = causality
    if causal_rows is None:
        causal_rows = fold.get("causality", {})
    future = sum(
        int(row.get("source_timestamp_future_count", 0))
        for row in causal_rows.values()
    )
    if future:
        failures.append("FUTURE_HTF")
    return {
        "pass": not failures,
        "failures": failures,
        "deployed_return_pct": metrics["return_on_deployed_pct"],
        "side_bh_return_pct": metrics["bh_return_on_deployed_pct"],
        "benchmark_floor_pct": metrics["benchmark_floor_return_pct"],
        "deployed_alpha_vs_bh_or_cash_pp": metrics[
            "deployed_alpha_vs_bh_or_cash_pp"
        ],
        "ratio": metrics["honest_bh_multiple"],
        "ratio_eligible": metrics["bh_ratio_eligible"],
        "tim_pct": tim,
        "tim_distance_pp": _tim_distance(tim, tim_lo, tim_hi),
        "fill_ratio": metrics["fill_ratio"],
        "clamps": metrics["clamp_count"],
        "fills": metrics["fill_count"],
        "exits": metrics["exit_count"],
        "minimum_equity_usd": metrics["minimum_account_equity_usd"],
        "max_drawdown_account_pct": metrics["max_drawdown_account_pct"],
        "future_htf_count": future,
    }


def _causality_by_fold(payload: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {
        int(row["fold"]): row.get("by_tf", {})
        for row in payload.get("causality_audit", [])
    }


def _rank(profile: dict[str, Any]) -> tuple[Any, ...]:
    gates = profile["discovery_gates"]
    return (
        -sum(g["pass"] for g in gates),
        sum(g["tim_distance_pp"] for g in gates),
        -min(g["deployed_alpha_vs_bh_or_cash_pp"] for g in gates),
        -sum(g["deployed_alpha_vs_bh_or_cash_pp"] for g in gates),
        -min(g["fill_ratio"] for g in gates),
        sum(g["clamps"] for g in gates),
        profile["capacity_usd"],
        profile["exit_n"],
    )


def screen_key(
    key: str,
    source: dict[str, Any],
    args: argparse.Namespace,
    run_dir: Path,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "key": key,
        "source": source,
        "discovery_profiles": [],
        "vector_holdout": None,
        "ordinary_engine_parity": {
            "status": "NOT_RUN",
            "reason": (
                "private research schedule is never ordinary "
                "backtest_v8_engine/live state-machine parity"
            ),
        },
        "promotion_eligible": False,
        "verdict": "DISCARD_GRAY",
    }
    if not source["valid"]:
        result["verdict"] = "SOURCE_BLOCKED_GRAY"
        return result

    symbol, side = key.rsplit("_", 1)
    for capacity in args.capacities:
        for exit_n in args.exit_ns:
            command = _profile_command(
                symbol=symbol,
                side=side,
                npz_dir=Path(args.npz_dir),
                out_dir=run_dir / "discovery",
                capacity=capacity,
                exit_n=exit_n,
                end=args.discovery_end,
                random_curves=args.random_curves,
                seed=args.seed,
                tim_lo=args.tim_lo,
                tim_hi=args.tim_hi,
                tim_weight=args.tim_weight,
            )
            call, payload = _run_profile(command)
            profile: dict[str, Any] = {
                "capacity_usd": capacity,
                "exit_n": exit_n,
                "call": call,
            }
            if "error" in payload:
                profile["error"] = payload["error"]
                result["discovery_profiles"].append(profile)
                continue
            folds = payload["outer_folds"]
            if len(folds) != 2:
                profile["error"] = (
                    f"DISCOVERY_FOLD_COUNT_{len(folds)}_EXPECTED_2"
                )
            else:
                causal = _causality_by_fold(payload)
                profile["discovery_gates"] = [
                    fold_gate(
                        fold,
                        args.tim_lo,
                        args.tim_hi,
                        causal.get(int(fold["fold"])),
                    )
                    for fold in folds
                ]
                profile["discovery_strict"] = all(
                    gate["pass"] for gate in profile["discovery_gates"]
                )
                profile["selected_curves"] = [
                    fold["selected_curve"]["label"] for fold in folds
                ]
                profile["npz_sha256"] = payload["manifest"]["npz_sha256"]
            result["discovery_profiles"].append(profile)

    valid_profiles = [
        row for row in result["discovery_profiles"]
        if "discovery_gates" in row
    ]
    if not valid_profiles:
        result["verdict"] = "SCREEN_ERROR_GRAY"
        return result
    ranked = sorted(valid_profiles, key=_rank)
    nearest = ranked[0]
    result["nearest_discovery_profile"] = nearest
    survivors = [row for row in ranked if row["discovery_strict"]]
    if not survivors:
        result["verdict"] = "NO_DISCOVERY_SURVIVOR_GRAY"
        return result

    selected = survivors[0]
    result["selected_discovery_profile"] = selected
    command = _profile_command(
        symbol=symbol,
        side=side,
        npz_dir=Path(args.npz_dir),
        out_dir=run_dir / "holdout",
        capacity=selected["capacity_usd"],
        exit_n=selected["exit_n"],
        end=None,
        random_curves=args.random_curves,
        seed=args.seed,
        tim_lo=args.tim_lo,
        tim_hi=args.tim_hi,
        tim_weight=args.tim_weight,
    )
    call, payload = _run_profile(command)
    if "error" in payload:
        result["vector_holdout"] = {"call": call, "error": payload["error"]}
        result["verdict"] = "HOLDOUT_ERROR_GRAY"
        return result
    folds = payload["outer_folds"]
    if len(folds) != 3:
        result["vector_holdout"] = {
            "call": call,
            "error": f"HOLDOUT_FOLD_COUNT_{len(folds)}_EXPECTED_3",
        }
        result["verdict"] = "HOLDOUT_ERROR_GRAY"
        return result
    # Reproducibility: the discovery folds rerun byte-semantically under the
    # same source hash and profile before the final fold is consumed.
    causal = _causality_by_fold(payload)
    repeated = [
        fold_gate(
            fold,
            args.tim_lo,
            args.tim_hi,
            causal.get(int(fold["fold"])),
        )
        for fold in folds[:2]
    ]
    holdout_gate = fold_gate(
        folds[2],
        args.tim_lo,
        args.tim_hi,
        causal.get(int(folds[2]["fold"])),
    )
    result["vector_holdout"] = {
        "call": call,
        "npz_sha256": payload["manifest"]["npz_sha256"],
        "discovery_repeat_gates": repeated,
        "discovery_repeat_pass": all(g["pass"] for g in repeated),
        "holdout_fold": 3,
        "selected_curve": folds[2]["selected_curve"]["label"],
        "gate": holdout_gate,
    }
    if not all(g["pass"] for g in repeated):
        result["verdict"] = "DISCOVERY_NOT_REPRODUCIBLE_GRAY"
    elif not holdout_gate["pass"]:
        result["verdict"] = "HOLDOUT_FAILED_GRAY"
    else:
        result["verdict"] = "VECTOR_SURVIVOR_ORDINARY_PARITY_REQUIRED_GRAY"
        result["ordinary_engine_parity"]["status"] = "REQUIRED"
    return result


def _nearest_key(results: list[dict[str, Any]]) -> str | None:
    candidates = [
        row for row in results
        if row.get("nearest_discovery_profile", {}).get("discovery_gates")
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda row: _rank(row["nearest_discovery_profile"]),
    )["key"]


def run(args: argparse.Namespace) -> Path:
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.out_dir)
    run_dir = out_dir / f"other_pilot_survivor_{stamp}"
    run_dir.mkdir(parents=True, exist_ok=False)
    expected = _load_expected(
        Path(args.expected_manifest) if args.expected_manifest else None
    )
    sources = [
        audit_npz(
            key,
            Path(args.npz_dir),
            expected,
            args.max_age_hours,
            now,
        )
        for key in args.keys
    ]
    results = [
        screen_key(source["key"], source, args, run_dir)
        for source in sources
    ]
    nearest = _nearest_key(results)
    payload = {
        "contract": CONTRACT,
        "created_utc": now.isoformat(),
        "manifest": {
            "keys": args.keys,
            "npz_dir": args.npz_dir,
            "expected_manifest": args.expected_manifest,
            "source_max_age_hours": args.max_age_hours,
            "discovery_end_exclusive": args.discovery_end,
            "untouched_holdout": "outer_fold_3_only_after_discovery_freeze",
            "capacities_usd": args.capacities,
            "exit_ns": args.exit_ns,
            "random_curves": args.random_curves,
            "seed": args.seed,
            "tim_band_pct": [args.tim_lo, args.tim_hi],
            "selection_metric": (
                "return_on_deployed_pct - "
                "max(side_aware_bh_return_on_deployed_pct, 0)"
            ),
            "deployed_capital": (
                "pre-cost committed/requested dollar-time averaged over every "
                "window bar; TIM is reported separately"
            ),
            "bh_ratio_abs_floor_pp": 20.0,
            "private_schedule_exact_is_live_parity": False,
            "ordinary_engine_required_for_promotion": True,
        },
        "sources": sources,
        "results": results,
        "summary": {
            "source_valid": sum(row["valid"] for row in sources),
            "source_blocked": sum(not row["valid"] for row in sources),
            "discovery_survivors": sum(
                bool(row.get("selected_discovery_profile")) for row in results
            ),
            "vector_holdout_survivors": sum(
                row["verdict"]
                == "VECTOR_SURVIVOR_ORDINARY_PARITY_REQUIRED_GRAY"
                for row in results
            ),
            "ordinary_engine_live_parity_pass": 0,
            "promotions": 0,
            "nearest_key": nearest,
        },
    }
    receipt = run_dir / "result.json"
    receipt.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )
    compact = ROOT / "data" / "reports" / "vec_research"
    compact.mkdir(parents=True, exist_ok=True)
    compact_path = compact / "OTHER_PILOT_SURVIVOR_RECEIPT_20260729.json"
    compact_payload = {
        "contract": CONTRACT,
        "created_utc": payload["created_utc"],
        "artifact": str(run_dir),
        "summary": payload["summary"],
        "rows": [
            {
                "key": row["key"],
                "source_valid": row["source"]["valid"],
                "source_errors": row["source"]["errors"],
                "verdict": row["verdict"],
                "nearest_discovery_profile": row.get(
                    "nearest_discovery_profile"
                ),
                "vector_holdout": row.get("vector_holdout"),
                "ordinary_engine_parity": row["ordinary_engine_parity"],
                "promotion_eligible": False,
            }
            for row in results
        ],
    }
    compact_path.write_text(
        json.dumps(
            compact_payload, indent=2, sort_keys=True, allow_nan=False
        )
        + "\n"
    )
    print(json.dumps({"artifact": str(run_dir), **payload["summary"]}))
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keys", default=",".join(DEFAULT_KEYS))
    parser.add_argument("--npz-dir", required=True)
    parser.add_argument("--expected-manifest")
    parser.add_argument(
        "--out-dir",
        default=str(ROOT / "data" / "reports" / "vec_research"),
    )
    parser.add_argument("--max-age-hours", type=float, default=168.0)
    parser.add_argument("--discovery-end", default="2026-01-01")
    parser.add_argument("--capacities", default="4000,8000")
    parser.add_argument("--exit-ns", default="20,30,45")
    parser.add_argument("--random-curves", type=int, default=80)
    parser.add_argument("--seed", type=int, default=20260729)
    parser.add_argument("--tim-lo", type=float, default=65.0)
    parser.add_argument("--tim-hi", type=float, default=80.0)
    parser.add_argument("--tim-weight", type=float, default=4.0)
    args = parser.parse_args()
    args.keys = [
        value.strip().upper()
        for value in str(args.keys).split(",")
        if value.strip()
    ]
    args.capacities = [
        int(value) for value in str(args.capacities).split(",")
    ]
    args.exit_ns = [int(value) for value in str(args.exit_ns).split(",")]
    if args.tim_lo > args.tim_hi:
        raise SystemExit("--tim-lo must be <= --tim-hi")
    if not args.capacities or min(args.capacities) < 2_000:
        raise SystemExit("capacities must be >= $2,000")
    run(args)


if __name__ == "__main__":
    main()
