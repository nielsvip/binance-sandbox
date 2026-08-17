#!/usr/bin/env python3
"""Compiled same-entry peak-giveback/partial-reduction research screen."""
from __future__ import annotations

import argparse
import ctypes
import dataclasses
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import vec_same_entry_exit_adapter as shared  # noqa: E402
from tools import vec_same_entry_partial_adapter as compiled  # noqa: E402


C_SOURCE = Path(__file__).with_name("vec_same_entry_peak_giveback_scan.c")


@dataclasses.dataclass(frozen=True)
class PeakGivebackSetting:
    arm_gain_pct: float
    giveback_fraction: float
    reduce_fraction: float

    def result_params(self) -> dict[str, Any]:
        return {
            "arm_gain_pct": self.arm_gain_pct,
            "giveback_fraction": self.giveback_fraction,
            "reduce_fraction": self.reduce_fraction,
            "peak_basis": "SIDE_AWARE_PRICE_MFE_SINCE_CURRENT_ENTRY_CYCLE",
            "account_peak_tracking": True,
            "cost_gate": "CURRENT_GAIN_ABOVE_ROUND_TRIP_COST",
            "partial_limit": "ONE_BOUNDED_CLIP_BEFORE_E02_FULL_RESET",
        }


def registry_grid() -> list[PeakGivebackSetting]:
    rows = [
        PeakGivebackSetting(arm, giveback, reduce)
        for arm in (0.5, 1.0, 2.0, 4.0, 8.0)
        for giveback in (0.20, 0.33, 0.50, 0.67)
        for reduce in (0.25, 0.50, 1.0)
    ]
    assert len(rows) == 60
    return rows


def _library() -> ctypes.CDLL:
    digest = hashlib.sha256(C_SOURCE.read_bytes()).hexdigest()[:16]
    target = Path("/tmp") / f"vec_same_entry_peak_giveback_{digest}.so"
    if not target.exists():
        subprocess.run(
            [
                os.environ.get("CC", "cc"),
                "-O3",
                "-std=c11",
                "-fPIC",
                "-shared",
                str(C_SOURCE),
                "-lm",
                "-o",
                str(target),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    lib = ctypes.CDLL(str(target))
    f64 = np.ctypeslib.ndpointer(
        dtype=np.float64, ndim=1, flags="C_CONTIGUOUS"
    )
    u8 = np.ctypeslib.ndpointer(
        dtype=np.uint8, ndim=1, flags="C_CONTIGUOUS"
    )
    i64 = np.ctypeslib.ndpointer(
        dtype=np.int64, ndim=1, flags="C_CONTIGUOUS"
    )
    lib.vec_same_entry_peak_giveback_scan.argtypes = [
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        i64,
        f64,
        f64,
        f64,
        f64,
        f64,
        ctypes.c_int,
        u8,
        i64,
        f64,
        f64,
        ctypes.c_double,
        ctypes.c_double,
        ctypes.c_double,
        ctypes.c_double,
        ctypes.c_double,
        ctypes.POINTER(compiled.PartialMetrics),
    ]
    lib.vec_same_entry_peak_giveback_scan.restype = ctypes.c_int
    return lib


def _e02_arrays(
    data: Any, htfs: dict[str, Any], signals: Any
) -> compiled.EventArrays:
    book = shared.build_e02_book(data, signals, htfs)
    source = np.zeros(len(data.ts), dtype=np.int64)
    for row, sources in book.source_by_row.items():
        source[row] = max(sources.values(), default=0)
    return compiled.EventArrays(
        np.ascontiguousarray(book.events, dtype=np.uint8),
        source,
        np.ascontiguousarray(book.references, dtype=np.float64),
    )


def _scan(
    data: Any,
    htfs: dict[str, Any],
    ctx: dict[str, Any],
    setting: PeakGivebackSetting,
    commission: float,
    slippage: float,
    side: str,
) -> dict[str, Any]:
    slow = _e02_arrays(data, htfs, ctx["signals"])
    blank = np.full(len(data.ts), np.nan, dtype=np.float64)
    out = compiled.PartialMetrics()
    rc = _library().vec_same_entry_peak_giveback_scan(
        len(data.ts),
        ctx["left"],
        ctx["right"],
        1 if side == "LONG" else -1,
        0 if ctx["curve"].semantics == "target" else 1,
        np.ascontiguousarray(data.ts, dtype=np.int64),
        np.ascontiguousarray(data.open, dtype=np.float64),
        np.ascontiguousarray(data.high, dtype=np.float64),
        np.ascontiguousarray(data.low, dtype=np.float64),
        np.ascontiguousarray(data.close, dtype=np.float64),
        np.ascontiguousarray(ctx["signals"].entry_mult, dtype=np.float64),
        slow.mode,
        slow.event,
        slow.source,
        slow.raw_stop if slow.raw_stop is not None else blank,
        slow.ref,
        setting.arm_gain_pct,
        setting.giveback_fraction,
        setting.reduce_fraction,
        commission,
        slippage,
        ctypes.byref(out),
    )
    if rc:
        raise RuntimeError(f"compiled peak-giveback scan failed with {rc}")
    requested = float(out.requested_notional_usd)
    return {
        "capital_return_pct": float(out.capital_return_pct),
        "bh_capital_return_pct": float(out.bh_capital_return_pct),
        "exposure_weighted_tim_pct": float(out.exposure_weighted_tim_pct),
        "binary_tim_pct": float(out.binary_tim_pct),
        "max_drawdown_account_pct": float(out.max_drawdown_account_pct),
        "minimum_account_equity_usd": float(out.minimum_account_equity_usd),
        "peak_post_fill_notional_usd": float(
            out.peak_post_fill_notional_usd
        ),
        "requested_notional_usd": requested,
        "filled_notional_usd": float(out.filled_notional_usd),
        "fill_ratio": (
            float(out.filled_notional_usd) / requested
            if requested > 0
            else 1.0
        ),
        "realized_partial_pnl_gross_usd": float(
            out.realized_partial_gross_usd
        ),
        "realized_partial_pnl_net_usd": float(
            out.realized_partial_net_usd
        ),
        "realized_full_pnl_gross_usd": float(
            out.realized_full_gross_usd
        ),
        "realized_full_pnl_net_usd": float(out.realized_full_net_usd),
        "unfilled_obligation_notional_usd": float(
            out.unfilled_obligation_notional_usd
        ),
        "insolvent": bool(out.insolvent),
        "entry_capacity_breach": bool(out.entry_capacity_breach),
        "peak_giveback_signals": int(out.partial_signals),
        "partial_signals": int(out.partial_signals),
        "regime_vetoed_partial_signals": 0,
        "partial_exit_fills": int(out.partial_exit_fills),
        "full_exit_fills": int(out.full_exit_fills),
        "technical_exit_fills": int(out.technical_exit_fills),
        "entry_fills": int(out.entry_fills),
        "reclaim_reentries": int(out.reclaim_reentries),
        "lower_or_higher_reentries": int(out.lower_reentries),
        "clamp_count": int(out.clamp_count),
        "reclaim_obligations_created": int(
            out.reclaim_obligations_created
        ),
        "reclaim_obligations_filled": int(
            out.reclaim_obligations_filled
        ),
        "clip_obligations_unfilled_at_end": int(
            out.reclaim_obligations_unfilled_at_end
        ),
        "bars_flat_beyond_reclaim": int(out.bars_flat_beyond_reclaim),
        "future_htf_source_count": int(out.future_htf_count),
        "rows": int(out.rows),
        "frozen_entry_schedule_sha256": shared._entry_schedule_hash(
            data,
            ctx["signals"],
            ctx["curve"],
            ctx["left"],
            ctx["right"],
        ),
    }


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    result = compiled._aggregate(rows)
    result["peak_giveback_signals"] = sum(
        row["peak_giveback_signals"] for row in rows
    )
    return result


def _fold_pass(
    evidence: dict[str, Any], exposure_min: float, exposure_max: float
) -> bool:
    return bool(
        evidence["peak_giveback_signals"] > 0
        and evidence["actual_exit_fills"] > 0
        and evidence["alpha_vs_bh_pp"] > 0
        and evidence["alpha_vs_same_entry_e02_pp"] > 0
        and exposure_min <= evidence["weighted_tim_pct"] <= exposure_max
        and evidence["unfilled_obligations"] == 0
        and evidence["bars_flat_beyond_reclaim"] == 0
        and evidence["future_htf_source_count"] == 0
        and not evidence["entry_capacity_breach"]
        and not evidence["insolvent"]
    )


def screen_artifact(
    artifact: Path, npz_dir: Path, exposure_min: float, exposure_max: float
) -> dict[str, Any]:
    source, data, htfs, contexts = shared._fold_contexts(
        artifact, npz_dir, fold_mode="nested"
    )
    manifest = source["manifest"]
    side = manifest["side"]
    commission = float(manifest["commission_bps_one_way"]) / 10_000.0
    slippage = float(manifest["slippage_bps_one_way"]) / 10_000.0
    controls = [
        shared.simulate(
            data,
            ctx["signals"],
            ctx["curve"],
            shared.build_e02_book(data, ctx["signals"], htfs),
            ctx["left"],
            ctx["right"],
            commission,
            slippage,
            side=side,
        )
        for ctx in contexts
    ]
    control = shared._aggregate(controls)
    candidates = []
    started = time.perf_counter()
    for setting in registry_grid():
        folds = [
            _scan(
                data,
                htfs,
                ctx,
                setting,
                commission,
                slippage,
                side,
            )
            for ctx in contexts
        ]
        evidence = []
        for row, ctrl, ctx in zip(folds, controls, contexts):
            evidence.append(
                {
                    "fold": ctx["fold"],
                    "validation_window": ctx["validation"],
                    "peak_giveback_signals": row[
                        "peak_giveback_signals"
                    ],
                    "partial_exit_fills": row["partial_exit_fills"],
                    "full_exit_fills": row["full_exit_fills"],
                    "actual_exit_fills": row["technical_exit_fills"],
                    "realized_partial_pnl_gross_usd": row[
                        "realized_partial_pnl_gross_usd"
                    ],
                    "realized_partial_pnl_net_usd": row[
                        "realized_partial_pnl_net_usd"
                    ],
                    "realized_full_pnl_net_usd": row[
                        "realized_full_pnl_net_usd"
                    ],
                    "strategy_return_pct": row["capital_return_pct"],
                    "bh_return_pct": row["bh_capital_return_pct"],
                    "same_entry_e02_return_pct": ctrl[
                        "capital_return_pct"
                    ],
                    "alpha_vs_bh_pp": (
                        row["capital_return_pct"]
                        - row["bh_capital_return_pct"]
                    ),
                    "alpha_vs_same_entry_e02_pp": (
                        row["capital_return_pct"] - ctrl["capital_return_pct"]
                    ),
                    "weighted_tim_pct": row[
                        "exposure_weighted_tim_pct"
                    ],
                    "unfilled_obligations": row[
                        "clip_obligations_unfilled_at_end"
                    ],
                    "bars_flat_beyond_reclaim": row[
                        "bars_flat_beyond_reclaim"
                    ],
                    "future_htf_source_count": row[
                        "future_htf_source_count"
                    ],
                    "entry_capacity_breach": row[
                        "entry_capacity_breach"
                    ],
                    "insolvent": row["insolvent"],
                    **shared.chart_event_ledger(row),
                    **shared._causal_action_evidence(row),
                }
            )
        discovery = _aggregate(folds[:-1])
        validation = _aggregate(folds[-1:])
        control_d = shared._aggregate(controls[:-1])
        control_v = shared._aggregate(controls[-1:])
        fold_gates = [
            _fold_pass(row, exposure_min, exposure_max)
            for row in evidence
        ]
        candidates.append(
            {
                "family": "EXIT_PEAK_GIVEBACK",
                "params": setting.result_params(),
                "fold_evidence": evidence,
                "fold_gate_pass": fold_gates,
                "metrics": _aggregate(folds),
                "nested": {
                    "discovery": discovery,
                    "validation": validation,
                    "discovery_alpha_vs_bh_pp": (
                        discovery["capital_return_pct"]
                        - discovery["bh_capital_return_pct"]
                    ),
                    "discovery_alpha_vs_same_entry_e02_pp": (
                        discovery["capital_return_pct"]
                        - control_d["capital_return_pct_sum"]
                    ),
                    "validation_alpha_vs_bh_pp": (
                        validation["capital_return_pct"]
                        - validation["bh_capital_return_pct"]
                    ),
                    "validation_alpha_vs_same_entry_e02_pp": (
                        validation["capital_return_pct"]
                        - control_v["capital_return_pct_sum"]
                    ),
                    "robust_discovery_all_folds": all(fold_gates[:-1]),
                    "robust_validation_fold": fold_gates[-1],
                },
            }
        )
    elapsed = time.perf_counter() - started
    candidates.sort(
        key=lambda row: (
            -sum(row["fold_gate_pass"][:-1]),
            not row["nested"]["robust_discovery_all_folds"],
            -sum(
                e["peak_giveback_signals"] > 0
                and e["actual_exit_fills"] > 0
                for e in row["fold_evidence"][:-1]
            ),
            -row["nested"]["discovery_alpha_vs_same_entry_e02_pp"],
            -row["nested"]["discovery_alpha_vs_bh_pp"],
        )
    )
    winner = candidates[0]
    metrics = winner["metrics"]
    survivor = bool(
        winner["nested"]["robust_discovery_all_folds"]
        and winner["nested"]["robust_validation_fold"]
        and all(winner["fold_gate_pass"])
        and metrics["peak_giveback_signals"] > 0
        and metrics["technical_exit_fills"] > 0
        and metrics["clip_obligations_unfilled_at_end"] == 0
        and metrics["bars_flat_beyond_reclaim"] == 0
        and metrics["future_htf_source_count"] == 0
        and metrics["insolvent_folds"] == 0
        and not metrics["entry_capacity_breach"]
    )
    schedule = control["entry_schedule_sha256_by_fold"]
    if any(
        row["metrics"]["entry_schedule_sha256_by_fold"] != schedule
        for row in candidates
    ):
        data.z.close()
        raise RuntimeError("peak-giveback candidate changed frozen entry")
    payload = {
        "tier": "VEC_RESEARCH_SAME_ENTRY_PEAK_GIVEBACK",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "matrix_written": False,
        "promotion_allowed": False,
        "symbol": manifest["symbol"],
        "side": side,
        "source_artifact": str(artifact.resolve()),
        "source_npz_sha256": manifest["npz_sha256"],
        "candidate_count": len(candidates),
        "compiled_grid_elapsed_seconds": elapsed,
        "wiring_audit": {
            "registry_key": "PEAK_GIVEBACK_ENABLED",
            "registry_key_status": "DISCONNECTED_NAME",
            "live_enable_key": "PEAK_GIVEBACK_PROTECTION_ENABLED",
            "live_settings": [
                "PEAK_GIVEBACK_MIN_PEAK_PCT",
                "PEAK_GIVEBACK_DROP_PCT",
                "PEAK_GIVEBACK_HARD_ZERO_ENABLED",
                "PEAK_GIVEBACK_REQUIRE_NEGATIVE_GAIN",
                "PEAK_GIVEBACK_NEGATIVE_GAIN_FLOOR_PCT",
                "TRADIER_MIN_HOLD_MINUTES",
            ],
            "live_semantics": (
                "full close after absolute percentage-point drop; defaults "
                "require current position gain <= -0.5% after 72h"
            ),
            "research_semantics": (
                "one bounded 25/50% clip or full close after fractional "
                "price-MFE giveback while still above round-trip cost"
            ),
            "equivalence": "NOT_EQUIVALENT_RESEARCH_ONLY",
        },
        "same_adapter_e02_control": control,
        "inert_candidate_count": sum(
            row["metrics"]["peak_giveback_signals"] == 0
            for row in candidates
        ),
        "zero_actual_exit_candidate_count": sum(
            row["metrics"]["technical_exit_fills"] == 0
            for row in candidates
        ),
        "frozen_discovery_winners": [winner],
        "survivor_count": int(survivor),
        "survivors": [winner] if survivor else [],
        "exact_replay_allowed": survivor,
        "candidates": candidates,
    }
    data.z.close()
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--npz-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--exposure-min-pct", type=float, default=70.0)
    parser.add_argument("--exposure-max-pct", type=float, default=80.0)
    args = parser.parse_args()
    payload = screen_artifact(
        args.artifact.resolve(),
        args.npz_dir.resolve(),
        args.exposure_min_pct,
        args.exposure_max_pct,
    )
    args.out_dir.mkdir(parents=True, exist_ok=False)
    (args.out_dir / "result.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )
    print(
        json.dumps(
            {
                "symbol": payload["symbol"],
                "side": payload["side"],
                "candidate_count": payload["candidate_count"],
                "inert": payload["inert_candidate_count"],
                "zero_fills": payload["zero_actual_exit_candidate_count"],
                "survivor_count": payload["survivor_count"],
                "elapsed": payload["compiled_grid_elapsed_seconds"],
                "output": str(args.out_dir),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
