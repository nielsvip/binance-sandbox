#!/usr/bin/env python3
"""Compiled same-entry E06 prior-regression extreme/reentry screen."""
from __future__ import annotations

import argparse
import ctypes
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

from tools import vec_same_entry_exit_adapter as shared  # noqa: E402
from tools import vec_same_entry_partial_adapter as compiled  # noqa: E402
from tools import vec_top_exit_walkforward as wf  # noqa: E402
from tools import vec_top_exit_campaign as base  # noqa: E402


@dataclasses.dataclass(frozen=True)
class E06Setting:
    lookback: int
    arm_z: float
    exit_z: float
    effective_corr_gate: float

    def result_params(self) -> dict[str, Any]:
        return {
            "timeframe": "4h",
            "lookback": self.lookback,
            "arm_z": self.arm_z,
            "exit_z": self.exit_z,
            "effective_corr_gate": self.effective_corr_gate,
            "registry_rebound_atr_value": self.effective_corr_gate,
            "registry_field_status": (
                "STALE_NAME: function consumes corr_gate, not rebound_atr"
            ),
            "prior_only_regression": True,
        }


def registry_grid() -> list[E06Setting]:
    rows = [
        E06Setting(lookback, arm, exit_z, registry_rebound)
        for lookback in (40, 60, 100, 160)
        for arm in (1.5, 2.0, 2.5, 3.0)
        for exit_z in (0.0, 0.5, 1.0)
        for registry_rebound in (0.25, 0.5, 0.7, 1.0)
    ]
    assert len(rows) == 192
    return rows


def _events(
    data: Any, h4: Any, side: str, setting: E06Setting
) -> compiled.EventArrays:
    event, ref = wf._e06_signal(
        h4,
        1 if side == "LONG" else -1,
        setting.lookback,
        setting.arm_z,
        setting.exit_z,
        setting.effective_corr_gate,
    )
    mapped, refs = base._map_events(len(data.ts), h4, event, ref)
    source = np.zeros(len(data.ts), dtype=np.int64)
    source[np.asarray(h4.event_index, dtype=np.int64)] = np.asarray(
        h4.source_ts, dtype=np.int64
    )
    return compiled.EventArrays(
        np.ascontiguousarray(mapped, dtype=np.uint8),
        source,
        np.ascontiguousarray(refs, dtype=np.float64),
    )


def _scan(
    data: Any,
    ctx: dict[str, Any],
    events: compiled.EventArrays,
    commission: float,
    slippage: float,
    side: str,
) -> dict[str, Any]:
    n = len(data.ts)
    zero = np.zeros(n, dtype=np.uint8)
    zero_source = np.zeros(n, dtype=np.int64)
    blank = np.full(n, np.nan, dtype=np.float64)
    out = compiled.PartialMetrics()
    rc = compiled._library().vec_same_entry_partial_scan(
        n, ctx["left"], ctx["right"],
        1 if side == "LONG" else -1,
        0 if ctx["curve"].semantics == "target" else 1,
        np.ascontiguousarray(data.ts, dtype=np.int64),
        np.ascontiguousarray(data.open, dtype=np.float64),
        np.ascontiguousarray(data.high, dtype=np.float64),
        np.ascontiguousarray(data.low, dtype=np.float64),
        np.ascontiguousarray(data.close, dtype=np.float64),
        np.ascontiguousarray(ctx["signals"].entry_mult, dtype=np.float64),
        zero, zero_source, blank,
        2, events.event, events.source, blank, events.ref, zero,
        0.15, 0.0, 0, commission, slippage, ctypes.byref(out),
    )
    if rc:
        raise RuntimeError(f"compiled E06 scan failed with {rc}")
    requested = float(out.requested_notional_usd)
    return {
        "capital_return_pct": float(out.capital_return_pct),
        "bh_capital_return_pct": float(out.bh_capital_return_pct),
        "exposure_weighted_tim_pct": float(out.exposure_weighted_tim_pct),
        "binary_tim_pct": float(out.binary_tim_pct),
        "max_drawdown_account_pct": float(out.max_drawdown_account_pct),
        "minimum_account_equity_usd": float(out.minimum_account_equity_usd),
        "peak_post_fill_notional_usd": float(out.peak_post_fill_notional_usd),
        "requested_notional_usd": requested,
        "filled_notional_usd": float(out.filled_notional_usd),
        "fill_ratio": (
            float(out.filled_notional_usd) / requested
            if requested > 0 else 1.0
        ),
        "realized_partial_pnl_gross_usd": 0.0,
        "realized_partial_pnl_net_usd": 0.0,
        "realized_full_pnl_gross_usd": float(out.realized_full_gross_usd),
        "realized_full_pnl_net_usd": float(out.realized_full_net_usd),
        "unfilled_obligation_notional_usd": float(
            out.unfilled_obligation_notional_usd
        ),
        "insolvent": bool(out.insolvent),
        "entry_capacity_breach": bool(out.entry_capacity_breach),
        "partial_signals": 0,
        "regime_vetoed_partial_signals": 0,
        "partial_exit_fills": 0,
        "full_exit_fills": int(out.full_exit_fills),
        "technical_exit_fills": int(out.technical_exit_fills),
        "entry_fills": int(out.entry_fills),
        "reclaim_reentries": int(out.reclaim_reentries),
        "lower_or_higher_reentries": int(out.lower_reentries),
        "clamp_count": int(out.clamp_count),
        "reclaim_obligations_created": int(
            out.reclaim_obligations_created
        ),
        "reclaim_obligations_filled": int(out.reclaim_obligations_filled),
        "clip_obligations_unfilled_at_end": int(
            out.reclaim_obligations_unfilled_at_end
        ),
        "bars_flat_beyond_reclaim": int(out.bars_flat_beyond_reclaim),
        "future_htf_source_count": int(out.future_htf_count),
        "rows": int(out.rows),
        "raw_completed_signal_events": int(
            np.count_nonzero(events.event[ctx["left"]:ctx["right"]])
        ),
        "frozen_entry_schedule_sha256": shared._entry_schedule_hash(
            data, ctx["signals"], ctx["curve"], ctx["left"], ctx["right"]
        ),
    }


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    result = compiled._aggregate(rows)
    result["raw_completed_signal_events"] = sum(
        row["raw_completed_signal_events"] for row in rows
    )
    return result


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
            data, ctx["signals"], ctx["curve"],
            shared.build_e02_book(data, ctx["signals"], htfs),
            ctx["left"], ctx["right"], commission, slippage, side=side,
        )
        for ctx in contexts
    ]
    control = shared._aggregate(controls)
    candidates = []
    started = time.perf_counter()
    for setting in registry_grid():
        events = _events(data, htfs["4h"], side, setting)
        folds = [
            _scan(data, ctx, events, commission, slippage, side)
            for ctx in contexts
        ]
        discovery = _aggregate(folds[:-1])
        validation = _aggregate(folds[-1:])
        control_d = shared._aggregate(controls[:-1])
        control_v = shared._aggregate(controls[-1:])
        evidence = []
        for row, ctrl, ctx in zip(folds, controls, contexts):
            evidence.append(
                {
                    "fold": ctx["fold"],
                    "raw_completed_signal_events": row[
                        "raw_completed_signal_events"
                    ],
                    "actual_exit_fills": row["technical_exit_fills"],
                    "strategy_return_pct": row["capital_return_pct"],
                    "bh_return_pct": row["bh_capital_return_pct"],
                    "same_entry_e02_return_pct": ctrl["capital_return_pct"],
                    "alpha_vs_bh_pp": (
                        row["capital_return_pct"]-row["bh_capital_return_pct"]
                    ),
                    "alpha_vs_same_entry_e02_pp": (
                        row["capital_return_pct"]-ctrl["capital_return_pct"]
                    ),
                    "weighted_tim_pct": row[
                        "exposure_weighted_tim_pct"
                    ],
                    "unfilled_obligations": row[
                        "clip_obligations_unfilled_at_end"
                    ],
                    "insolvent": row["insolvent"],
                }
            )
        row = {
            "family": "EXIT_E06_REGRESSION_RETEST",
            "params": setting.result_params(),
            "fold_evidence": evidence,
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
            },
        }
        row["nested"]["robust_discovery_all_folds"] = all(
            e["raw_completed_signal_events"] > 0
            and e["actual_exit_fills"] > 0
            and e["alpha_vs_bh_pp"] > 0
            and e["alpha_vs_same_entry_e02_pp"] > 0
            and e["unfilled_obligations"] == 0
            and not e["insolvent"]
            for e in evidence[:-1]
        )
        row["nested"]["robust_validation_fold"] = (
            evidence[-1]["raw_completed_signal_events"] > 0
            and evidence[-1]["actual_exit_fills"] > 0
            and evidence[-1]["alpha_vs_bh_pp"] > 0
            and evidence[-1]["alpha_vs_same_entry_e02_pp"] > 0
            and evidence[-1]["unfilled_obligations"] == 0
            and not evidence[-1]["insolvent"]
        )
        candidates.append(row)
    elapsed = time.perf_counter()-started
    candidates.sort(
        key=lambda row: (
            not row["nested"]["robust_discovery_all_folds"],
            not all(
                evidence["raw_completed_signal_events"] > 0
                and evidence["actual_exit_fills"] > 0
                for evidence in row["fold_evidence"][:-1]
            ),
            not (
                exposure_min
                <= row["nested"]["discovery"]["exposure_weighted_tim_pct"]
                <= exposure_max
            ),
            -row["nested"]["discovery_alpha_vs_same_entry_e02_pp"],
            -row["nested"]["discovery_alpha_vs_bh_pp"],
            -row["nested"]["discovery"]["actual_exit_fills"]
            if "actual_exit_fills" in row["nested"]["discovery"] else 0,
        )
    )
    winner = candidates[0]
    n = winner["nested"]
    m = winner["metrics"]
    survivor = bool(
        n["robust_discovery_all_folds"]
        and n["robust_validation_fold"]
        and exposure_min
        <= n["discovery"]["exposure_weighted_tim_pct"]
        <= exposure_max
        and exposure_min
        <= n["validation"]["exposure_weighted_tim_pct"]
        <= exposure_max
        and m["raw_completed_signal_events"] > 0
        and m["technical_exit_fills"] > 0
        and m["clip_obligations_unfilled_at_end"] == 0
        and m["bars_flat_beyond_reclaim"] == 0
        and m["future_htf_source_count"] == 0
        and m["insolvent_folds"] == 0
        and not m["entry_capacity_breach"]
    )
    schedule = control["entry_schedule_sha256_by_fold"]
    if any(
        row["metrics"]["entry_schedule_sha256_by_fold"] != schedule
        for row in candidates
    ):
        data.z.close()
        raise RuntimeError("E06 candidate changed frozen entry schedule")
    inert_candidates = sum(
        row["metrics"]["raw_completed_signal_events"] == 0
        for row in candidates
    )
    no_fill_candidates = sum(
        row["metrics"]["technical_exit_fills"] == 0 for row in candidates
    )
    payload = {
        "tier": "VEC_RESEARCH_SAME_ENTRY_E06",
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
            "registry_rebound_atr": (
                "STALE/DISCONNECTED: _e06_signal has corr_gate, no ATR rebound"
            ),
            "effective_mapping": (
                "registry values 0.25/0.5/0.7/1.0 are tested as corr_gate"
            ),
            "active_research_lookbacks": [40, 60, 100, 150, 250],
            "registry_lookbacks_tested": [40, 60, 100, 160],
            "active_research_exit_z": [0.75, 1.0, 1.5, 2.0],
            "registry_exit_z_tested": [0.0, 0.5, 1.0],
            "confirmation_semantics": (
                "ACTIVE CODE fires on channel reentry OR the first adverse "
                "completed-4h break; it has no separate post-break retest state"
            ),
        },
        "same_adapter_e02_control": control,
        "inert_candidate_count": inert_candidates,
        "zero_actual_exit_candidate_count": no_fill_candidates,
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
        args.artifact.resolve(), args.npz_dir.resolve(),
        args.exposure_min_pct, args.exposure_max_pct,
    )
    args.out_dir.mkdir(parents=True, exist_ok=False)
    (args.out_dir/"result.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False)+"\n"
    )
    print(
        json.dumps(
            {
                "symbol": payload["symbol"], "side": payload["side"],
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
