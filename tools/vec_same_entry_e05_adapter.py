#!/usr/bin/env python3
"""Causal same-entry E05 divergence/break/retest vector screen.

E05 is deliberately a research adapter.  Tradier has divergence-related
settings, but no named live state machine equivalent to this sequence:

1. confirm two completed-4h price pivots;
2. arm only when price makes a side-adverse new extreme while prior-only RSI
   diverges by the configured amount;
3. observe a later structural break through the between-pivot level;
4. wait for an ATR-sized rebound/rollover; and
5. exit only on the later rollover bar, never on the first break.

Every candidate consumes the frozen ladder entry schedule and the same
compiled accounting/reclaim implementation used by the E02 control.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import vec_same_entry_e06_adapter as execution  # noqa: E402
from tools import vec_same_entry_exit_adapter as shared  # noqa: E402
from tools import vec_top_exit_campaign as base  # noqa: E402


@dataclasses.dataclass(frozen=True)
class E05Setting:
    pivot_radius: int
    divergence_min: float
    break_buffer_atr: float
    rebound_atr: float
    max_wait_bars: int

    def result_params(self) -> dict[str, Any]:
        return {
            "timeframe": "4h",
            "pivot_radius": self.pivot_radius,
            "rsi_divergence_min": self.divergence_min,
            "break_buffer_atr": self.break_buffer_atr,
            "rebound_atr": self.rebound_atr,
            "max_wait_bars": self.max_wait_bars,
            "confirmed_prior_only_pivots": True,
            "first_break_exit": False,
            "required_sequence": (
                "DIVERGENCE_ARM -> STRUCTURAL_BREAK -> "
                "ATR_REBOUND -> LATER_ROLLOVER_EXIT"
            ),
        }


def registry_grid() -> list[E05Setting]:
    rows = [
        E05Setting(radius, div_min, break_buffer, rebound, max_wait)
        for radius in (2, 3, 5)
        for div_min in (3.0, 5.0, 8.0)
        for break_buffer in (0.0, 0.25)
        for rebound in (0.25, 0.5)
        for max_wait in (8, 12, 20)
    ]
    assert len(rows) == 108
    return rows


def _events(
    data: Any, h4: Any, side: str, setting: E05Setting
) -> execution.compiled.EventArrays:
    event, ref = base._e05_signal(
        h4,
        1 if side == "LONG" else -1,
        setting.pivot_radius,
        setting.divergence_min,
        setting.break_buffer_atr,
        setting.rebound_atr,
        setting.max_wait_bars,
    )
    mapped, refs = base._map_events(len(data.ts), h4, event, ref)
    source = np.zeros(len(data.ts), dtype=np.int64)
    source[np.asarray(h4.event_index, dtype=np.int64)] = np.asarray(
        h4.source_ts, dtype=np.int64
    )
    fired = np.flatnonzero(mapped)
    if len(fired) and np.any(source[fired] > np.asarray(data.ts)[fired]):
        raise RuntimeError("future completed-4h source in E05 event")
    return execution.compiled.EventArrays(
        np.ascontiguousarray(mapped, dtype=np.uint8),
        source,
        np.ascontiguousarray(refs, dtype=np.float64),
    )


def _fold_pass(
    evidence: dict[str, Any], exposure_min: float, exposure_max: float
) -> bool:
    return bool(
        evidence["raw_completed_signal_events"] > 0
        and evidence["actual_exit_fills"] > 0
        and evidence["alpha_vs_bh_pp"] > 0
        and evidence["alpha_vs_same_entry_e02_pp"] > 0
        and exposure_min <= evidence["weighted_tim_pct"] <= exposure_max
        and evidence["unfilled_obligations"] == 0
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
    control_folds = controls
    candidates = []
    started = time.perf_counter()
    for setting in registry_grid():
        events = _events(data, htfs["4h"], side, setting)
        folds = [
            execution._scan(
                data, ctx, events, commission, slippage, side
            )
            for ctx in contexts
        ]
        evidence = []
        for row, ctrl, ctx in zip(folds, control_folds, contexts):
            evidence.append(
                {
                    "fold": ctx["fold"],
                    "validation_window": ctx["validation"],
                    "raw_completed_signal_events": row[
                        "raw_completed_signal_events"
                    ],
                    "actual_exit_fills": row["technical_exit_fills"],
                    "strategy_return_pct": row["capital_return_pct"],
                    "bh_return_pct": row["bh_capital_return_pct"],
                    "same_entry_e02_return_pct": ctrl["capital_return_pct"],
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
        discovery = execution._aggregate(folds[:-1])
        validation = execution._aggregate(folds[-1:])
        control_d = shared._aggregate(control_folds[:-1])
        control_v = shared._aggregate(control_folds[-1:])
        discovery_passes = [
            _fold_pass(row, exposure_min, exposure_max)
            for row in evidence[:-1]
        ]
        validation_pass = _fold_pass(
            evidence[-1], exposure_min, exposure_max
        )
        row = {
            "family": "EXIT_E05_DIVERGENCE_RETEST",
            "params": setting.result_params(),
            "fold_evidence": evidence,
            "fold_gate_pass": discovery_passes + [validation_pass],
            "metrics": execution._aggregate(folds),
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
                "robust_discovery_all_folds": all(discovery_passes),
                "robust_validation_fold": validation_pass,
            },
        }
        candidates.append(row)
    elapsed = time.perf_counter() - started
    candidates.sort(
        key=lambda row: (
            -sum(row["fold_gate_pass"][:-1]),
            not row["nested"]["robust_discovery_all_folds"],
            -sum(
                e["raw_completed_signal_events"] > 0
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
        and metrics["raw_completed_signal_events"] > 0
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
        raise RuntimeError("E05 candidate changed frozen entry schedule")
    payload = {
        "tier": "VEC_RESEARCH_SAME_ENTRY_E05",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "matrix_written": False,
        "promotion_allowed": False,
        "symbol": manifest["symbol"],
        "side": side,
        "source_artifact": str(artifact.resolve()),
        "source_npz_sha256": manifest["npz_sha256"],
        "candidate_count": len(candidates),
        "compiled_grid_elapsed_seconds": elapsed,
        "registry_reconciliation": {
            "live_config_keys": [],
            "live_switch_status": "DISCONNECTED_RESEARCH_ONLY",
            "research_adapter": "tools/vec_same_entry_e05_adapter.py",
            "active_candidate_grid": {
                "pivot_radius": [2, 3, 5],
                "divergence_min": [3.0, 5.0, 8.0],
                "break_buffer_atr": [0.0, 0.25],
                "rebound_atr": [0.25, 0.5],
                "max_wait_bars": [8, 12, 20],
            },
            "stale_queue_contract_grid": {
                "pivot_radius": [2, 3, 4, 6],
                "divergence_min": [3, 5, 8, 12],
                "rebound_atr": [0.25, 0.5, 1.0],
                "max_wait_bars": [6, 12, 18, 24],
                "missing_field": "break_buffer_atr",
            },
            "state_contract": (
                "confirmed prior-only 4h pivot divergence arms; later "
                "structural break changes state but cannot exit; ATR rebound "
                "then later rollover emits the completed-4h exit"
            ),
        },
        "same_adapter_e02_control": control,
        "inert_candidate_count": sum(
            row["metrics"]["raw_completed_signal_events"] == 0
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
        "dc_low4_profit_exit_used": False,
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
