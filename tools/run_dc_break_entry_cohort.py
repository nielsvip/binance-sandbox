#!/usr/bin/env python3
"""Run the disconnected DC-break registry path as red audit + gray reconstruction."""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools import path_fleet_campaign as fleet  # noqa: E402
from tools.audit_dc_break_entry_wiring import audit_repo  # noqa: E402


FAMILY = "ENTRY_DC_BREAK_ENTRY_ENABLED"


def _claim_exact(root: Path, worker: str) -> dict:
    con = sqlite3.connect(root / "queue.db", timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("BEGIN IMMEDIATE")
    row = con.execute("SELECT * FROM jobs WHERE path_id=?", (FAMILY,)).fetchone()
    if row is None:
        raise RuntimeError(f"missing fleet job {FAMILY}")
    if row["status"] not in {"READY", "ADAPTER_REQUIRED"}:
        raise RuntimeError(f"{FAMILY} cannot start from {row['status']}")
    now = time.time()
    changed = con.execute(
        """UPDATE jobs SET status='RUNNING',claimed_by=?,claimed_at=?,
           heartbeat_at=?,attempts=attempts+1 WHERE id=? AND status=?""",
        (worker, now, now, row["id"], row["status"]),
    ).rowcount
    con.execute("COMMIT")
    if not changed:
        raise RuntimeError("lost exact job claim")
    out = dict(con.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone())
    con.close()
    out["universe"] = json.loads(out.pop("universe_json"))
    out["contract"] = json.loads(out.pop("contract_json"))
    return out


def _controls(root: Path) -> dict[tuple[str, str], str]:
    con = sqlite3.connect(root / "queue.db")
    rows = con.execute(
        """SELECT r.symbol,r.side,r.artifact FROM results r
           JOIN jobs j ON j.id=r.job_id
           WHERE j.path_id='ENTRY_LADDER_GREEN'
             AND r.stage='VEC_UNTOUCHED_OOS'
           ORDER BY r.id"""
    ).fetchall()
    con.close()
    return {(str(symbol), str(side)): str(artifact) for symbol, side, artifact in rows}


def _run_one(control: str, out_root: Path, npz_dir: Path, shortlist: int) -> Path:
    cmd = [
        sys.executable,
        str(ROOT / "tools" / "vec_entry_overlay_walkforward.py"),
        "--control-artifact",
        control,
        "--family",
        FAMILY,
        "--npz-dir",
        str(npz_dir),
        "--out-dir",
        str(out_root),
        "--shortlist",
        str(shortlist),
        "--target-tim-low",
        "70",
        "--target-tim-high",
        "80",
    ]
    proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True)
    if proc.returncode:
        raise RuntimeError(f"overlay rc={proc.returncode}: {proc.stderr[-1200:]}")
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    return Path(payload["artifact"])


def _add_result(
    root: Path,
    result_dir: Path,
    *,
    job_id: int,
    symbol: str,
    side: str,
    stage: str,
    status: str,
    strategy: float,
    bh: float,
    control: float,
    tim: float,
    trades: int,
    artifact: str,
    extra: dict,
    untouched_oos: bool = False,
) -> None:
    payload = {
        "job_id": job_id,
        "symbol": symbol,
        "side": side,
        "stage": stage,
        "status": status,
        "strategy_return_pct": strategy,
        "bh_return_pct": bh,
        "same_entry_control_return_pct": control,
        "tim_pct": tim,
        "trades": trades,
        "untouched_oos": untouched_oos,
        "exact_replay": False,
        "future_htf_count": 0,
        "artifact": artifact,
        **extra,
    }
    path = result_dir / f"{symbol}_{side}.{stage}.result.json"
    fleet.atomic_json(path, payload)
    fleet.add_result(root, path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=fleet.DEFAULT_ROOT)
    ap.add_argument("--npz-dir", type=Path, default=fleet.DEFAULT_NPZ)
    ap.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / "data" / "reports" / "vec_research",
    )
    ap.add_argument("--worker", default="dc-break-entry-reconstruction")
    ap.add_argument("--shortlist", type=int, default=24)
    args = ap.parse_args()
    root = args.root.resolve()
    job = _claim_exact(root, args.worker)
    controls = _controls(root)
    wiring = audit_repo()
    if wiring["classification"] != "DISCONNECTED_STALE_REGISTRY_ROW":
        raise RuntimeError(f"wiring changed; reconstruction halted: {wiring}")
    result_dir = root / f"job_{job['id']}_{FAMILY}"
    result_dir.mkdir(parents=True, exist_ok=True)
    cohort = [
        (row["symbol"], "LONG") for row in job["universe"]["top_long"]
    ] + [
        (row["symbol"], "SHORT") for row in job["universe"]["bottom_short"]
    ]
    summary = {
        "job_id": job["id"],
        "path_id": FAMILY,
        "wiring_audit": wiring,
        "screen_status": "RESEARCH_RECONSTRUCTION_NOT_LIVE_PROMOTABLE",
        "grid_candidates": 192,
        "symbols": [],
    }
    errors = 0
    for symbol, side in cohort:
        started = time.time()
        control_artifact = controls.get((symbol, side))
        if not control_artifact:
            raise RuntimeError(f"missing frozen control for {symbol}_{side}")
        control_payload = json.loads(
            (Path(control_artifact) / "result.json").read_text()
        )
        control_agg = control_payload["frozen_oos_aggregate"]
        # Preserve the actual stale-switch behavior as red evidence: the named
        # switch cannot issue a request because no active reader exists.
        _add_result(
            root,
            result_dir,
            job_id=job["id"],
            symbol=symbol,
            side=side,
            stage="WIRING_AUDIT",
            status="RED_DIAGNOSTIC_DISCONNECTED",
            strategy=0.0,
            bh=float(control_agg["bh_capital_return_pct_sum"]),
            control=float(control_agg["capital_return_pct_sum"]),
            tim=0.0,
            trades=0,
            artifact=str(result_dir / "wiring_audit.json"),
            extra={
                "entry_signal_rows": 0,
                "entry_request_count": 0,
                "entry_fill_count": 0,
                "wiring_audit": wiring,
            },
            untouched_oos=False,
        )
        try:
            artifact = _run_one(
                control_artifact,
                args.output_root.resolve(),
                args.npz_dir.resolve(),
                args.shortlist,
            )
            payload = json.loads((artifact / "result.json").read_text())
            agg = payload["aggregate"]
            exit_count = sum(
                int(f["validation_metrics"]["exit_count"])
                for f in payload["outer_folds"]
            )
            status = (
                "VECTOR_SURVIVOR_AWAIT_EXACT"
                if agg["vector_survivor"]
                else "DISCARD_GRAY_RESEARCH_RECONSTRUCTION"
            )
            common_extra = {
                "entry_signal_rows": int(agg["entry_signal_rows"]),
                "entry_request_count": int(agg["entry_request_count"]),
                "entry_fill_count": int(agg["entry_fill_count"]),
                "all_folds_beat_bh": bool(agg["all_folds_beat_bh"]),
                "all_folds_beat_control": bool(agg["all_folds_beat_control"]),
                "all_folds_exposure_policy_pass": bool(
                    agg["all_folds_exposure_policy_pass"]
                ),
                "all_mandatory_reclaim": bool(agg["all_mandatory_reclaim"]),
                "all_capacity_safe": bool(agg["all_capacity_safe"]),
                "research_reconstruction_only": True,
                "normalization_version": "entry_fleet_metric_scope_v1",
            }
            _add_result(
                root,
                result_dir,
                job_id=job["id"],
                symbol=symbol,
                side=side,
                stage="VEC_NESTED_FOLD_AGGREGATE",
                status=status,
                strategy=float(agg["candidate_capital_return_pct_sum"]),
                bh=float(agg["bh_capital_return_pct_sum"]),
                control=float(agg["control_capital_return_pct_sum"]),
                tim=float(agg["weighted_tim_pct"]),
                trades=exit_count,
                artifact=str(artifact),
                extra={
                    **common_extra,
                    "metric_scope": "NESTED_OUTER_VALIDATION_FOLD_AGGREGATE",
                    "return_unit": "SUM_OF_FOLD_CAPITAL_RETURN_PCT",
                    "return_aggregation": (
                        "SUM_ACROSS_OUTER_VALIDATION_FOLDS"
                    ),
                    "tim_unit": "PCT",
                    "tim_aggregation": (
                        "ROW_WEIGHTED_MEAN_ACROSS_OUTER_VALIDATION_FOLDS"
                    ),
                    "trades_unit": "EXIT_FILLS",
                    "trades_aggregation": (
                        "SUM_ACROSS_OUTER_VALIDATION_FOLDS"
                    ),
                    "fold_count": len(payload["outer_folds"]),
                },
                untouched_oos=False,
            )
            final_fold = max(
                payload["outer_folds"],
                key=lambda fold: (
                    int(fold["validation_metrics"].get("end_ts") or -1),
                    int(fold.get("fold") or -1),
                ),
            )
            final_metrics = final_fold["validation_metrics"]
            final_control = final_fold["same_frozen_ladder_e02_control"]
            _add_result(
                root,
                result_dir,
                job_id=job["id"],
                symbol=symbol,
                side=side,
                stage="VEC_UNTOUCHED_OOS",
                status=status,
                strategy=float(final_metrics["capital_return_pct"]),
                bh=float(final_metrics["bh_capital_return_pct"]),
                control=float(final_control["capital_return_pct"]),
                tim=float(final_metrics["exposure_weighted_tim_pct"]),
                trades=int(final_metrics["exit_count"]),
                artifact=str(artifact),
                extra={
                    **common_extra,
                    "metric_scope": (
                        "FINAL_CHRONOLOGICAL_OUTER_VALIDATION_FOLD"
                    ),
                    "return_unit": "CAPITAL_RETURN_PCT",
                    "return_aggregation": "NONE_SINGLE_FOLD",
                    "tim_unit": "PCT",
                    "tim_aggregation": "NONE_SINGLE_FOLD",
                    "trades_unit": "EXIT_FILLS",
                    "trades_aggregation": "NONE_SINGLE_FOLD",
                    "fold_index": final_fold.get("fold"),
                    "validation_window": final_fold.get("validation"),
                    "beats_bh": bool(final_fold.get("beats_bh")),
                    "beats_control": bool(final_fold.get("beats_control")),
                },
                untouched_oos=True,
            )
            summary["symbols"].append(
                {
                    "symbol": symbol,
                    "side": side,
                    "status": status,
                    "artifact": str(artifact),
                    **agg,
                    "seconds": time.time() - started,
                }
            )
        except Exception as exc:
            errors += 1
            summary["symbols"].append(
                {
                    "symbol": symbol,
                    "side": side,
                    "status": "ERROR",
                    "error": f"{type(exc).__name__}:{exc}",
                    "seconds": time.time() - started,
                }
            )
        fleet.heartbeat(root, job["id"], args.worker)
        fleet.atomic_json(result_dir / "summary.partial.json", summary)
    fleet.atomic_json(result_dir / "wiring_audit.json", wiring)
    summary["errors"] = errors
    summary["strict_vector_survivors"] = sum(
        row.get("status") == "VECTOR_SURVIVOR_AWAIT_EXACT"
        for row in summary["symbols"]
    )
    summary_path = result_dir / "summary.json"
    fleet.atomic_json(summary_path, summary)
    fleet.finish(
        root,
        job["id"],
        args.worker,
        summary_path,
        (
            f"disconnected switch proven; reconstructed rows="
            f"{len(summary['symbols']) - errors}; errors={errors}; "
            f"strict survivors={summary['strict_vector_survivors']}"
        ),
    )
    print(json.dumps(summary, sort_keys=True))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
