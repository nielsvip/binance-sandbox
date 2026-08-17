#!/usr/bin/env python3
"""Discovery-only beam over frozen single-family entries and causal exits.

The expensive scanners remain in their existing vector/compiled adapters.
This orchestrator deliberately does not combine entry overlays.  It:

1. ranks already-frozen single-family entry artifacts using discovery folds;
2. always retains the plain ladder and DC-tier schedules when available;
3. screens E02, E05, MTF-ATR, peak-giveback, and extended bottom A/B/C exits;
4. ranks exit candidates using discovery folds only; and
5. reveals the untouched final fold only for the frozen exit beam.

No live configuration or matrix cell is changed.  Strict survivors alone are
queued for exact replay.
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
GENERIC_FAMILIES = (
    "E02_GRID,MTF_ATR_TRAIL,"
    "BOTTOM_A_EXT,BOTTOM_B_EXT,BOTTOM_C_EXT"
)
EXIT_TO_PATH = {
    "EXIT_E02_DONCHIAN": "EXIT_E02_DONCHIAN",
    "EXIT_E05_DIVERGENCE_RETEST": "EXIT_E05_DIVERGENCE_RETEST",
    "EXIT_MTF_ATR_TRAIL": "EXIT_MTF_ATR_TRAIL",
    "EXIT_PEAK_GIVEBACK": "EXIT_PEAK_GIVEBACK",
    "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED": "BOTTOM_A_PROTECTIVE_TRAIL",
    "BOTTOM_B_DELAYED_LOWER_TOP_EXTENDED": "BOTTOM_B_DELAYED_LOWER_TOP",
    "BOTTOM_C_DELAYED_EMERGENCY_EXTENDED": "BOTTOM_C_DELAYED_EMERGENCY",
}
EXPOSURE_MIN_PCT = 50.0
EXPOSURE_MAX_PCT = 80.0


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _metric(row: dict[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in row:
            return row[name]
    return default


def _entry_discovery(artifact: Path) -> dict[str, Any]:
    source = _read(artifact / "result.json")
    folds = list(source["outer_folds"])
    if len(folds) < 2:
        raise ValueError(f"{artifact}: nested discovery needs >=2 folds")
    evidence = []
    for fold in folds[:-1]:
        candidate = fold["validation_metrics"]
        # Plain ladder artifacts are themselves the identical-entry E02
        # control, whereas overlay artifacts carry the control explicitly.
        control = fold.get("same_frozen_ladder_e02_control", candidate)
        strategy = float(candidate["capital_return_pct"])
        bh = float(candidate["bh_capital_return_pct"])
        ctrl = float(control["capital_return_pct"])
        tim = float(candidate["exposure_weighted_tim_pct"])
        evidence.append(
            {
                "fold": int(fold["fold"]),
                "validation": list(fold["validation"]),
                "strategy_return_pct": strategy,
                "bh_return_pct": bh,
                "same_entry_e02_return_pct": ctrl,
                "alpha_vs_bh_pp": strategy - bh,
                "alpha_vs_control_pp": strategy - ctrl,
                "tim_pct": tim,
                "future_htf_count": int(
                    candidate.get("future_htf_source_count", 0)
                ),
                "capacity_breach": bool(
                    candidate.get("entry_capacity_breach", False)
                ),
                "insolvent": bool(candidate.get("insolvent", False)),
            }
        )
    strict_count = sum(
        row["alpha_vs_bh_pp"] > 0
        and row["alpha_vs_control_pp"] > 0
        and EXPOSURE_MIN_PCT <= row["tim_pct"] <= EXPOSURE_MAX_PCT
        and row["future_htf_count"] == 0
        and not row["capacity_breach"]
        and not row["insolvent"]
        for row in evidence
    )
    return {
        "symbol": str(source["manifest"]["symbol"]).upper(),
        "side": str(source["manifest"]["side"]).upper(),
        "family": str(source["manifest"].get("family", "ENTRY_LADDER_GREEN")),
        "artifact": str(artifact),
        "discovery_fold_evidence": evidence,
        "discovery_strict_fold_count": strict_count,
        "discovery_all_folds_strict": strict_count == len(evidence),
        "discovery_alpha_vs_bh_pp": sum(
            row["alpha_vs_bh_pp"] for row in evidence
        ),
        "discovery_alpha_vs_control_pp": sum(
            row["alpha_vs_control_pp"] for row in evidence
        ),
        "discovery_max_tim_distance_from_75": max(
            abs(row["tim_pct"] - 75.0) for row in evidence
        ),
    }


def _entry_rank(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        not row["discovery_all_folds_strict"],
        -int(row["discovery_strict_fold_count"]),
        -float(row["discovery_alpha_vs_control_pp"]),
        -float(row["discovery_alpha_vs_bh_pp"]),
        float(row["discovery_max_tim_distance_from_75"]),
        row["family"],
    )


def load_entry_beam(
    component_report: Path,
    dc_tier_report: Path,
    keys: set[str],
    beam_width: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return selected entry rows and the complete discovery-only audit."""
    sources: list[tuple[str, Path]] = []
    for report in (component_report, dc_tier_report):
        for row in _read(report).get("rows", []):
            key = f"{row['symbol'].upper()}_{row['side'].upper()}"
            if key in keys:
                sources.append((str(row["family"]), Path(row["artifact"])))

    # Every component points at its identical plain-ladder control.  Retain one
    # copy per key as an explicit baseline schedule.
    for _, artifact in list(sources):
        manifest = _read(artifact / "result.json")["manifest"]
        control = manifest.get("control_artifact")
        if control:
            sources.append(("ENTRY_LADDER_GREEN", Path(control)))

    deduped: dict[str, tuple[str, Path]] = {}
    for family, artifact in sources:
        deduped[str(artifact.resolve())] = (family, artifact)
    audit = []
    for declared_family, artifact in deduped.values():
        row = _entry_discovery(artifact)
        if row["family"] == "ENTRY_LADDER_GREEN":
            row["family"] = declared_family
        audit.append(row)

    selected: list[dict[str, Any]] = []
    by_key: dict[str, list[dict[str, Any]]] = {}
    for row in audit:
        by_key.setdefault(f"{row['symbol']}_{row['side']}", []).append(row)
    for key in sorted(keys):
        rows = sorted(by_key.get(key, []), key=_entry_rank)
        if not rows:
            raise ValueError(f"no entry artifacts for {key}")
        keep: dict[str, dict[str, Any]] = {
            row["artifact"]: row for row in rows[:beam_width]
        }
        # Force the two requested baseline schedules into the bounded beam.
        for required_family in (
            "ENTRY_LADDER_GREEN",
            "ENTRY_DC_TIER_AUG_ENABLED",
        ):
            match = next(
                (row for row in rows if row["family"] == required_family),
                None,
            )
            if match is not None:
                keep[match["artifact"]] = match
        selected.extend(sorted(keep.values(), key=_entry_rank))
    return selected, sorted(audit, key=lambda row: (
        row["symbol"], row["side"], *_entry_rank(row)
    ))


def _run_adapter(
    adapter: str,
    artifact: Path,
    npz_dir: Path,
    out_dir: Path,
    extra: list[str] | None = None,
    emit_event_ledger: bool = False,
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
        "--exposure-min-pct",
        str(EXPOSURE_MIN_PCT),
        "--exposure-max-pct",
        str(EXPOSURE_MAX_PCT),
    ]
    if extra:
        cmd.extend(extra)
    if emit_event_ledger:
        cmd.append("--emit-event-ledger")
    proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True)
    (out_dir.parent / f"{out_dir.name}.stdout.log").write_text(proc.stdout)
    (out_dir.parent / f"{out_dir.name}.stderr.log").write_text(proc.stderr)
    if proc.returncode:
        raise RuntimeError(
            f"{adapter} rc={proc.returncode}: {proc.stderr[-3000:]}"
        )
    return _read(out_dir / "result.json")


def _fold_view(row: dict[str, Any]) -> list[dict[str, Any]]:
    return list(row.get("fold_evidence", []))


def _fold_exit_count(row: dict[str, Any]) -> int:
    """Count completed position lifecycles, never broad exit actions.

    Partial clips, runners and action markers can all increment ``exit_fills``
    without closing a position.  Qualification needs independently tradable
    completed lifecycles.  Old receipts lack this field and therefore fail
    closed until rerun under the repaired adapter contract.
    """
    return int(
        _metric(
            row,
            "real_close_trades",
            "terminal_lifecycle_closes",
            default=0,
        )
    )


def _fold_obligations(row: dict[str, Any]) -> int:
    return int(
        _metric(
            row,
            "unfilled_obligations",
            "clip_obligations_unfilled_at_end",
            default=0,
        )
    )


def _strict_fold(row: dict[str, Any]) -> bool:
    return bool(
        float(row["alpha_vs_bh_pp"]) > 0
        and float(
            _metric(
                row,
                "alpha_vs_same_entry_e02_pp",
                "alpha_vs_control_pp",
                default=0.0,
            )
        )
        > 0
        and EXPOSURE_MIN_PCT
        <= float(_metric(row, "weighted_tim_pct", "tim_pct", default=-1.0))
        <= EXPOSURE_MAX_PCT
        and _fold_exit_count(row) > 0
        and _fold_obligations(row) == 0
        and int(row.get("future_htf_source_count", 0)) == 0
        and int(row.get("bars_flat_beyond_reclaim", 0)) == 0
        and not bool(row.get("entry_capacity_breach", False))
        and not bool(row.get("insolvent", False))
        and float(row.get("emergency_exit_share", 0.0)) <= 0.25
    )


def _candidate_discovery_summary(
    row: dict[str, Any], adapter: str, entry: dict[str, Any]
) -> dict[str, Any]:
    folds = _fold_view(row)
    discovery = folds[:-1]
    if not discovery:
        raise ValueError("candidate lacks nested discovery folds")
    strict = [_strict_fold(fold) for fold in discovery]
    control_alphas = [
        float(
            _metric(
                fold,
                "alpha_vs_same_entry_e02_pp",
                "alpha_vs_control_pp",
                default=0.0,
            )
        )
        for fold in discovery
    ]
    result = {
        "entry_family": entry["family"],
        "entry_artifact": entry["artifact"],
        "exit_family": row["family"],
        "exit_params": row["params"],
        "adapter": adapter,
        "discovery_fold_evidence": discovery,
        "discovery_fold_gate_pass": strict,
        "discovery_strict_fold_count": sum(strict),
        "discovery_all_folds_strict": all(strict),
        "discovery_min_alpha_vs_bh_pp": min(
            float(fold["alpha_vs_bh_pp"]) for fold in discovery
        ),
        "discovery_min_alpha_vs_control_pp": min(control_alphas),
        "discovery_alpha_vs_bh_pp": sum(
            float(fold["alpha_vs_bh_pp"]) for fold in discovery
        ),
        "discovery_alpha_vs_control_pp": sum(control_alphas),
        "discovery_max_tim_distance_from_75": max(
            abs(
                float(
                    _metric(
                        fold, "weighted_tim_pct", "tim_pct", default=-1.0
                    )
                )
                - 75.0
            )
            for fold in discovery
        ),
        "all_fold_max_drawdown_account_pct": float(
            row.get("metrics", {}).get("max_drawdown_account_pct_max", 0.0)
        ),
        "all_fold_clamp_count": int(
            row.get("metrics", {}).get("clamp_count", 0)
        ),
        "all_fold_exit_fills": int(
            row.get("metrics", {}).get("exit_fills", 0)
        ),
        "_source": row,
    }
    return result


def _exit_rank(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        not row["discovery_all_folds_strict"],
        -int(row["discovery_strict_fold_count"]),
        -float(row["discovery_min_alpha_vs_control_pp"]),
        -float(row["discovery_min_alpha_vs_bh_pp"]),
        float(row["discovery_max_tim_distance_from_75"]),
        -float(row["discovery_alpha_vs_control_pp"]),
        row["exit_family"],
    )


def _validation_view(row: dict[str, Any]) -> dict[str, Any]:
    source = row["_source"]
    fold = _fold_view(source)[-1]
    strict = _strict_fold(fold)
    metrics = source.get("metrics", {})
    aggregate_obligations = int(
        metrics.get("clip_obligations_unfilled_at_end", 0)
    )
    strict = strict and aggregate_obligations == 0
    return {
        "fold_evidence": fold,
        "strict": strict,
        "aggregate_open_reclaim_obligations": aggregate_obligations,
        "entry_schedule_sha256_by_fold": metrics.get(
            "entry_schedule_sha256_by_fold"
        ),
    }


def _public_candidate(row: dict[str, Any], reveal_validation: bool) -> dict[str, Any]:
    public = {key: value for key, value in row.items() if key != "_source"}
    if reveal_validation:
        public["untouched_final_validation"] = _validation_view(row)
        public["all_folds_strict"] = bool(
            public["discovery_all_folds_strict"]
            and public["untouched_final_validation"]["strict"]
        )
    return public


def _campaign_exit_code(errors: list[dict[str, Any]], exact_queue: list[dict[str, Any]]) -> int:
    """Quarantine failed independent entry schedules without losing survivors."""
    return int(bool(errors) and not bool(exact_queue))


def _screen_entry(
    entry: dict[str, Any],
    npz_dir: Path,
    output_root: Path,
    exit_beam_width: int,
    resume: bool = False,
    emit_event_ledger: bool = False,
) -> dict[str, Any]:
    key = f"{entry['symbol']}_{entry['side']}"
    digest = hashlib.sha256(entry["artifact"].encode()).hexdigest()[:10]
    base = output_root / key / f"{entry['family']}_{digest}"
    base.mkdir(parents=True, exist_ok=resume)
    artifact = Path(entry["artifact"])
    payloads = []
    for adapter, extra in (
        (
            "generic",
            ["--families", GENERIC_FAMILIES, "--fold-mode", "nested"],
        ),
        ("e05", None),
        ("peak", None),
    ):
        result_path = base / adapter / "result.json"
        if resume and result_path.exists():
            payload = _read(result_path)
        else:
            payload = _run_adapter(
                adapter,
                artifact,
                npz_dir,
                base / adapter,
                extra,
                emit_event_ledger=emit_event_ledger,
            )
        payloads.append((adapter, payload))
    candidates = []
    for adapter, payload in payloads:
        for candidate in payload["candidates"]:
            if candidate["family"] not in EXIT_TO_PATH:
                continue
            candidates.append(
                _candidate_discovery_summary(candidate, adapter, entry)
            )
    candidates.sort(key=_exit_rank)
    beam = candidates[:exit_beam_width]
    return {
        "symbol": entry["symbol"],
        "side": entry["side"],
        "entry_family": entry["family"],
        "entry_artifact": entry["artifact"],
        "artifact": str(base),
        "candidate_count": len(candidates),
        "discovery_only_rejected": [
            _public_candidate(row, False) for row in candidates[exit_beam_width:]
        ],
        "frozen_exit_beam": [
            _public_candidate(row, True) for row in beam
        ],
        "strict_survivors": [
            _public_candidate(row, True)
            for row in beam
            if _public_candidate(row, True)["all_folds_strict"]
        ],
    }


def _path_job_ids(root: Path) -> dict[str, int]:
    con = sqlite3.connect(root / "queue.db")
    rows = con.execute("SELECT id,path_id FROM jobs").fetchall()
    con.close()
    return {str(path_id): int(job_id) for job_id, path_id in rows}


def _ingest_selected(root: Path, campaign: dict[str, Any]) -> int:
    """Append the discovery-selected final winner for each entry schedule."""
    from tools import path_fleet_campaign as fleet

    job_ids = _path_job_ids(root)
    count = 0
    ingest_dir = root / "entry_exit_beam_ingest" / campaign["campaign_id"]
    for result in campaign["results"]:
        beam = result.get("frozen_exit_beam", [])
        if not beam:
            continue
        winner = beam[0]
        validation = winner["untouched_final_validation"]
        fold = validation["fold_evidence"]
        path_id = EXIT_TO_PATH[winner["exit_family"]]
        payload = {
            "job_id": job_ids[path_id],
            "symbol": result["symbol"],
            "side": result["side"],
            "stage": "VEC_ENTRY_EXIT_BEAM_UNTOUCHED_OOS",
            "status": (
                "VECTOR_SURVIVOR_EXACT_PENDING"
                if winner["all_folds_strict"]
                else "GRAY_REJECTED"
            ),
            "strategy_return_pct": float(fold["strategy_return_pct"]),
            "bh_return_pct": float(fold["bh_return_pct"]),
            "same_entry_control_return_pct": float(
                fold["same_entry_e02_return_pct"]
            ),
            "tim_pct": float(
                _metric(fold, "weighted_tim_pct", "tim_pct", default=0.0)
            ),
            "trades": _fold_exit_count(fold),
            "untouched_oos": True,
            "exact_replay": False,
            "future_htf_count": int(
                fold.get("future_htf_source_count", 0)
            ),
            "artifact": result["artifact"],
            "metric_scope": "FINAL_CHRONOLOGICAL_OUTER_VALIDATION_FOLD",
            "return_unit": "CAPITAL_RETURN_PCT",
            "return_aggregation": "NONE_SINGLE_FOLD",
            "tim_unit": "PCT",
            "tim_aggregation": "NONE_SINGLE_FOLD",
            "trades_unit": "ACTUAL_TECHNICAL_EXIT_FILLS",
            "trades_aggregation": "NONE_SINGLE_FOLD",
            "entry_family": winner["entry_family"],
            "entry_artifact": winner["entry_artifact"],
            "exit_family": winner["exit_family"],
            "params": winner["exit_params"],
            "discovery_fold_evidence": winner[
                "discovery_fold_evidence"
            ],
            "discovery_fold_gate_pass": winner[
                "discovery_fold_gate_pass"
            ],
            "all_folds_strict": winner["all_folds_strict"],
            "open_reclaim_obligations": validation[
                "aggregate_open_reclaim_obligations"
            ],
            "entry_schedule_sha256_by_fold": validation[
                "entry_schedule_sha256_by_fold"
            ],
            "bh_capital_usd": 2000.0,
            "strategy_capacity_usd": 16000.0,
            "costs_included": True,
            "zero_future_htf_required": True,
            "promotion_allowed": False,
            "campaign_id": campaign["campaign_id"],
        }
        path = ingest_dir / (
            f"{result['symbol']}_{result['side']}_"
            f"{winner['entry_family']}_{winner['exit_family']}.json"
        )
        fleet.atomic_json(path, payload)
        fleet.add_result(root, path)
        count += 1
    return count


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--component-report", type=Path, required=True)
    ap.add_argument("--dc-tier-report", type=Path, required=True)
    ap.add_argument("--npz-dir", type=Path, required=True)
    ap.add_argument("--output-root", type=Path, required=True)
    ap.add_argument("--path-fleet-root", type=Path)
    ap.add_argument("--key", action="append", required=True)
    ap.add_argument("--entry-beam-width", type=int, default=2)
    ap.add_argument("--exit-beam-width", type=int, default=8)
    ap.add_argument(
        "--emit-event-ledger",
        action="store_true",
        help="retain causal raw actions on S1 for selected vector chart export",
    )
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--exposure-min-pct", type=float, default=50.0)
    ap.add_argument("--exposure-max-pct", type=float, default=80.0)
    ap.add_argument(
        "--resume",
        action="store_true",
        help="reuse completed per-adapter result.json artifacts",
    )
    args = ap.parse_args()
    if not 0.0 <= args.exposure_min_pct <= args.exposure_max_pct <= 100.0:
        raise ValueError("invalid exposure bounds")
    global EXPOSURE_MIN_PCT, EXPOSURE_MAX_PCT
    EXPOSURE_MIN_PCT = args.exposure_min_pct
    EXPOSURE_MAX_PCT = args.exposure_max_pct
    keys = {key.upper() for key in args.key}
    entries, entry_audit = load_entry_beam(
        args.component_report.resolve(),
        args.dc_tier_report.resolve(),
        keys,
        args.entry_beam_width,
    )
    args.output_root.mkdir(parents=True, exist_ok=args.resume)
    results = []
    errors = []
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=max(1, args.workers)
    ) as pool:
        future_map = {
            pool.submit(
                _screen_entry,
                entry,
                args.npz_dir.resolve(),
                args.output_root.resolve(),
                args.exit_beam_width,
                args.resume,
                args.emit_event_ledger,
            ): entry
            for entry in entries
        }
        for future in concurrent.futures.as_completed(future_map):
            entry = future_map[future]
            try:
                result = future.result()
                results.append(result)
                print(
                    json.dumps(
                        {
                            "key": (
                                f"{result['symbol']}_{result['side']}"
                            ),
                            "entry": result["entry_family"],
                            "candidates": result["candidate_count"],
                            "strict_survivors": len(
                                result["strict_survivors"]
                            ),
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
            except Exception as exc:
                errors.append(
                    {
                        "symbol": entry["symbol"],
                        "side": entry["side"],
                        "entry_family": entry["family"],
                        "error": f"{type(exc).__name__}:{exc}",
                    }
                )
    results.sort(
        key=lambda row: (row["symbol"], row["side"], row["entry_family"])
    )
    campaign_id = args.output_root.name
    payload = {
        "tier": "VEC_ENTRY_EXIT_DISCOVERY_BEAM",
        "campaign_id": campaign_id,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "contract": {
            "entry_overlay_blending": False,
            "one_frozen_entry_schedule_at_a_time": True,
            "entry_selection_uses_discovery_folds_only": True,
            "exit_selection_uses_discovery_folds_only": True,
            "untouched_final_fold_revealed_after_freeze": True,
            "exit_families": sorted(EXIT_TO_PATH),
            "every_fold_tim_gate_pct": [
                EXPOSURE_MIN_PCT,
                EXPOSURE_MAX_PCT,
            ],
            "every_fold_alpha_vs_bh_required": True,
            "every_fold_alpha_vs_identical_entry_e02_required": True,
            "actual_exit_required": True,
            "resting_reclaim_required": True,
            "bh_capital_usd": 2000.0,
            "strategy_capacity_usd": 16000.0,
            "costs_included": True,
            "future_htf_allowed": 0,
            "exact_replay_only_for_strict_survivors": True,
            "matrix_written": False,
            "promotion_allowed": False,
        },
        "keys": sorted(keys),
        "entry_beam_width": args.entry_beam_width,
        "exit_beam_width": args.exit_beam_width,
        "selected_entry_schedules": entries,
        "entry_discovery_audit": entry_audit,
        "results": results,
        "errors": errors,
    }
    payload["exact_replay_queue"] = [
        {
            "symbol": row["symbol"],
            "side": row["side"],
            "entry_family": survivor["entry_family"],
            "entry_artifact": survivor["entry_artifact"],
            "exit_family": survivor["exit_family"],
            "exit_params": survivor["exit_params"],
            "beam_artifact": row["artifact"],
        }
        for row in results
        for survivor in row["strict_survivors"]
    ]
    payload["status"] = (
        "PASS"
        if not errors
        else "PARTIAL_PASS_WITH_QUARANTINED_ENTRY_ERRORS"
        if payload["exact_replay_queue"]
        else "FAILED"
    )
    if args.path_fleet_root and payload["exact_replay_queue"]:
        payload["path_fleet_rows_appended"] = _ingest_selected(
            args.path_fleet_root.resolve(), payload
        )
    else:
        payload["path_fleet_rows_appended"] = 0
    (args.output_root / "campaign_result.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )
    print(
        json.dumps(
            {
                "campaign": campaign_id,
                "entries": len(entries),
                "completed": len(results),
                "errors": len(errors),
                "exact_queue": len(payload["exact_replay_queue"]),
                "fleet_rows": payload["path_fleet_rows_appended"],
            },
            sort_keys=True,
        )
    )
    return _campaign_exit_code(errors, payload["exact_replay_queue"])


if __name__ == "__main__":
    raise SystemExit(main())
