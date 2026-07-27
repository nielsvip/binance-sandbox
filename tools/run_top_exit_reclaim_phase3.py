#!/usr/bin/env python3
"""Causal phase-3 LONG top-harvest screen over immutable ladder entries.

Research only.  Candidate exits consume the already-frozen per-fold ladder
schedule and the identical-entry 4h/N30 E02 control.  Every full exit creates
the same persistent zero-buffer reclaim obligation owned by
``vec_same_entry_exit_adapter.simulate``.

The registry is intentionally compact and preregistered.  It tests evidence
that occurs at/after a peak, plus small OR combinations.  Chandelier trails
are retained only as labeled emergency controls.  A first dc_low4 break is
never a candidate exit.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import vec_same_entry_exit_adapter as shared
from tools import vec_top_exit_campaign as top
from tools import vec_top_exit_walkforward as wf


CAMPAIGN = "TOP_EXIT_RECLAIM_PHASE3_V1"
CAPITAL_CONTRACT = {
    "bh_fixed_unit_usd": 2_000.0,
    "strategy_capacity_usd": 16_000.0,
    "account_solvency_usd": 10_000.0,
}
EXPOSURE_BAND = (70.0, 80.0)


@dataclasses.dataclass(frozen=True)
class RegisteredBook:
    family: str
    label: str
    params: dict[str, Any]
    book: shared.StaticExitBook
    profit_gate_pct: float
    emergency_control: bool = False
    components: tuple[str, ...] = ()


def _static_from_htf(
    data: Any,
    htf: Any,
    *,
    family: str,
    label: str,
    params: dict[str, Any],
    event: np.ndarray,
    reference: np.ndarray,
    profit_gate_pct: float,
) -> RegisteredBook:
    mapped, refs = top._map_events(len(data.ts), htf, event, reference)
    source = {
        int(row): {str(htf.tf): int(ts)}
        for row, ts in zip(htf.event_index, htf.source_ts)
    }
    source_by_row = {
        int(row): source[int(row)]
        for row in np.flatnonzero(mapped)
        if int(row) in source
    }
    return RegisteredBook(
        family,
        label,
        params,
        shared.StaticExitBook(
            label,
            np.ascontiguousarray(mapped, dtype=np.uint8),
            np.ascontiguousarray(refs, dtype=np.float64),
            source_by_row,
            audit={
                "completed_htf_only": True,
                "first_dc_low4_exit": False,
                "peak_evidence_required": True,
            },
        ),
        profit_gate_pct,
    )


def _failed_higher_high(
    h: Any, lookback: int, max_wait: int, failure_atr: float
) -> tuple[np.ndarray, np.ndarray]:
    """Arm a prior-only higher high; exit on a later failed HH + adverse close."""
    prior_high = top._rolling_prior(h.high, lookback, "max")
    event = np.zeros(len(h.close), dtype=np.uint8)
    ref = np.full(len(h.close), np.nan, dtype=np.float64)
    armed = False
    peak = math.nan
    deadline = -1
    for j in range(max(2, lookback), len(h.close)):
        atr = h.atr[j - 1]
        if not (math.isfinite(atr) and atr > 0):
            continue
        if math.isfinite(prior_high[j]) and h.high[j] > prior_high[j]:
            armed = True
            peak = float(h.high[j])
            deadline = j + max_wait
            continue
        if not armed:
            continue
        peak = max(peak, float(h.high[j]))
        failed_hh = (
            h.high[j] < h.high[j - 1]
            and h.low[j] < h.low[j - 1]
            and h.close[j] <= h.high[j] - failure_atr * atr
        )
        if failed_hh:
            event[j] = 1
            ref[j] = peak
            armed = False
        elif j >= deadline:
            armed = False
    return event, ref


def _single_registry(data: Any, htfs: dict[str, Any]) -> list[RegisteredBook]:
    """Return the immutable 36-book phase-3 single-path registry."""
    out: list[RegisteredBook] = []
    h4, hd = htfs["4h"], htfs["D"]

    # Confirmed price/RSI divergence -> break -> rebound -> later rollover.
    for radius in (2, 3):
        for div in (3.0, 8.0):
            for rebound in (0.25, 0.5):
                event, ref = top._e05_signal(
                    h4, 1, radius, div, 0.25, rebound, 12
                )
                out.append(
                    _static_from_htf(
                        data,
                        h4,
                        family="EXIT_E05_DIVERGENCE_RETEST",
                        label=f"P3_E05_P{radius}_D{div:g}_R{rebound:g}",
                        params={
                            "timeframe": "4h",
                            "pivot_radius": radius,
                            "rsi_divergence_min": div,
                            "break_buffer_atr": 0.25,
                            "rebound_atr": rebound,
                            "max_wait_bars": 12,
                        },
                        event=event,
                        reference=ref,
                        profit_gate_pct=0.5,
                    )
                )

    # Structural lower high + lower low, then rebound and failed retest.
    for damage in (0.5, 1.0):
        for rebound in (0.25, 0.5):
            event, ref = wf._e03_signal(h4, 1, 2, damage, rebound, 12)
            out.append(
                _static_from_htf(
                    data,
                    h4,
                    family="EXIT_CONFIRMED_STRUCTURE_RETEST",
                    label=f"P3_E03_D{damage:g}_R{rebound:g}",
                    params={
                        "timeframe": "4h",
                        "confirm_bars": 2,
                        "damage_atr": damage,
                        "rebound_atr": rebound,
                        "max_wait_bars": 12,
                    },
                    event=event,
                    reference=ref,
                    profit_gate_pct=0.5,
                )
            )

    # EMA structural break cannot exit; later retest failure can.
    for ema in (20, 34):
        for rebound in (0.25, 0.5):
            event, ref = top._e04_signal(h4, 1, ema, 0.25, rebound, 12)
            out.append(
                _static_from_htf(
                    data,
                    h4,
                    family="EXIT_BREAK_RETEST_LOWER_TOP",
                    label=f"P3_E04_EMA{ema}_R{rebound:g}",
                    params={
                        "timeframe": "4h",
                        "ema": ema,
                        "break_buffer_atr": 0.25,
                        "rebound_atr": rebound,
                        "max_wait_bars": 12,
                    },
                    event=event,
                    reference=ref,
                    profit_gate_pct=0.5,
                )
            )

    # Completed D/4h RSI/ATR exhaustion, followed by completed 1h damage.
    for arm_tf, arm in (("4h", h4), ("D", hd)):
        for rsi in (65.0, 75.0):
            event, ref = wf._e09_signal(
                arm, htfs["1h"], 1, rsi, 1.0, 24, 1
            )
            out.append(
                _static_from_htf(
                    data,
                    htfs["1h"],
                    family="EXIT_MTF_VOLATILITY_EXHAUSTION",
                    label=f"P3_E09_{arm_tf}_RSI{rsi:g}",
                    params={
                        "arm_timeframe": arm_tf,
                        "trigger_timeframe": "1h",
                        "rsi_threshold": rsi,
                        "extension_atr": 1.0,
                        "expiry_1h_bars": 24,
                    },
                    event=event,
                    reference=ref,
                    profit_gate_pct=0.5,
                )
            )

    # Prior-only regression excursion, then channel re-entry/adverse break.
    for lookback in (60, 100):
        for z_arm, z_exit in ((2.0, 1.0), (2.5, 1.5)):
            event, ref = wf._e06_signal(
                h4, 1, lookback, z_arm, z_exit, 0.7
            )
            out.append(
                _static_from_htf(
                    data,
                    h4,
                    family="EXIT_E06_REGRESSION_RETEST",
                    label=f"P3_E06_N{lookback}_ZA{z_arm:g}_ZE{z_exit:g}",
                    params={
                        "timeframe": "4h",
                        "lookback": lookback,
                        "z_arm": z_arm,
                        "z_exit": z_exit,
                        "correlation_gate": 0.7,
                        "prior_only": True,
                    },
                    event=event,
                    reference=ref,
                    profit_gate_pct=0.5,
                )
            )

    # Failed higher high is independent of oscillator inversion.
    for lookback in (10, 20):
        for failure_atr in (0.25, 0.5):
            event, ref = _failed_higher_high(h4, lookback, 8, failure_atr)
            out.append(
                _static_from_htf(
                    data,
                    h4,
                    family="EXIT_FAILED_HIGHER_HIGH",
                    label=f"P3_FHH_N{lookback}_A{failure_atr:g}",
                    params={
                        "timeframe": "4h",
                        "prior_high_lookback": lookback,
                        "failure_atr": failure_atr,
                        "max_wait_bars": 8,
                    },
                    event=event,
                    reference=ref,
                    profit_gate_pct=0.5,
                )
            )

    # Completed 4h+D WT top roll.  This is not a dc-low break.
    for extreme in (55.0, 65.0, 75.0):
        for velocity in (0.25, 0.5):
            params = shared.WtMtfParams(
                timeframes=("4h", "D"),
                min_against_tfs=1,
                extreme=extreme,
                velocity=velocity,
                recent_extreme_bars=8,
                require_fast_structure=False,
                profit_gate_pct=0.5,
            )
            book = shared.build_wt_mtf_book(data, htfs, params, side="LONG")
            out.append(
                RegisteredBook(
                    "EXIT_WT_DC_TOP_ROLL",
                    f"P3_WT_4hD_X{extreme:g}_V{velocity:g}",
                    dataclasses.asdict(params),
                    book,
                    0.5,
                )
            )

    # Emergency/bottom controls only; they are never promoted as top evidence.
    for tf, n, k in (("4h", 20, 4.0), ("D", 20, 4.0)):
        params = shared.ChandelierParams(tf, n, k, 0.5)
        book = shared.build_chandelier_book(data, htfs, params, side="LONG")
        out.append(
            RegisteredBook(
                "EMERGENCY_CONTROL_CHANDELIER",
                f"P3_EMERGENCY_CHANDELIER_{tf}_N{n}_K{k:g}",
                dataclasses.asdict(params),
                book,
                0.5,
                emergency_control=True,
            )
        )
    assert len(out) == 36
    return out


def union_books(
    label: str, family: str, members: Iterable[RegisteredBook]
) -> RegisteredBook:
    members = tuple(members)
    if len(members) < 2:
        raise ValueError("union requires at least two members")
    n = len(members[0].book.events)
    events = np.zeros(n, dtype=np.uint8)
    refs = np.full(n, np.nan, dtype=np.float64)
    sources: dict[int, dict[str, int]] = {}
    for member in members:
        if len(member.book.events) != n:
            raise ValueError("union member length mismatch")
        fired = np.flatnonzero(member.book.events)
        events[fired] = 1
        for row in fired:
            value = float(member.book.references[row])
            if math.isfinite(value):
                refs[row] = (
                    value
                    if not math.isfinite(refs[row])
                    else max(refs[row], value)
                )
            dst = sources.setdefault(int(row), {})
            for tf, ts in member.book.source_by_row.get(int(row), {}).items():
                dst[tf] = max(int(ts), int(dst.get(tf, 0)))
    return RegisteredBook(
        family,
        label,
        {"members": [m.label for m in members], "logic": "OR_FIRST_CAUSAL_EVENT"},
        shared.StaticExitBook(label, events, refs, sources),
        max(m.profit_gate_pct for m in members),
        False,
        tuple(m.label for m in members),
    )


def registry(data: Any, htfs: dict[str, Any]) -> list[RegisteredBook]:
    singles = _single_registry(data, htfs)
    by_family: dict[str, list[RegisteredBook]] = {}
    for row in singles:
        by_family.setdefault(row.family, []).append(row)
    # Six fixed combinations: no symbol-specific or final-fold selection.
    pairs = (
        ("DIV_OR_STRUCTURE", "EXIT_E05_DIVERGENCE_RETEST", "EXIT_CONFIRMED_STRUCTURE_RETEST"),
        ("DIV_OR_BREAK_RETEST", "EXIT_E05_DIVERGENCE_RETEST", "EXIT_BREAK_RETEST_LOWER_TOP"),
        ("EXHAUST_OR_FHH", "EXIT_MTF_VOLATILITY_EXHAUSTION", "EXIT_FAILED_HIGHER_HIGH"),
        ("REGRESSION_OR_FHH", "EXIT_E06_REGRESSION_RETEST", "EXIT_FAILED_HIGHER_HIGH"),
        ("WT_OR_STRUCTURE", "EXIT_WT_DC_TOP_ROLL", "EXIT_CONFIRMED_STRUCTURE_RETEST"),
        ("WT_OR_DIVERGENCE", "EXIT_WT_DC_TOP_ROLL", "EXIT_E05_DIVERGENCE_RETEST"),
    )
    combos = [
        union_books(
            f"P3_COMBO_{name}",
            "EXIT_TOP_LOGICAL_COMBINATION",
            (by_family[left][0], by_family[right][0]),
        )
        for name, left, right in pairs
    ]
    assert len(singles) == 36 and len(combos) == 6
    return singles + combos


def _fold_evidence(row: dict[str, Any], ctrl: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    return {
        "fold": int(ctx["fold"]),
        "validation": list(ctx["validation"]),
        "strategy_return_pct": float(row["capital_return_pct"]),
        "bh_return_pct": float(row["bh_capital_return_pct"]),
        "same_entry_e02_return_pct": float(ctrl["capital_return_pct"]),
        "alpha_vs_bh_pp": float(row["capital_return_pct"] - row["bh_capital_return_pct"]),
        "alpha_vs_same_entry_e02_pp": float(row["capital_return_pct"] - ctrl["capital_return_pct"]),
        "weighted_tim_pct": float(row["exposure_weighted_tim_pct"]),
        "actual_exit_fills": int(row["exit_fills"]),
        "reclaim_reentries": int(row["reclaim_reentries"]),
        "lower_reentries": int(row["lower_or_higher_reentries"]),
        "bars_flat_beyond_reclaim": int(row["bars_flat_beyond_reclaim"]),
        "future_htf_source_count": int(row["future_htf_source_count"]),
        "minimum_account_equity_usd": float(row["minimum_account_equity_usd"]),
        "peak_notional_usd": float(row["peak_post_fill_notional_usd"]),
        "insolvent": bool(row["insolvent"]),
        "entry_capacity_breach": bool(row["entry_capacity_breach"]),
        "entry_schedule_sha256": row["frozen_entry_schedule_sha256"],
    }


def fold_pass(row: dict[str, Any], emergency_control: bool = False) -> bool:
    return bool(
        not emergency_control
        and row["actual_exit_fills"] > 0
        and row["alpha_vs_bh_pp"] > 0
        and row["alpha_vs_same_entry_e02_pp"] > 0
        and EXPOSURE_BAND[0] <= row["weighted_tim_pct"] <= EXPOSURE_BAND[1]
        and row["bars_flat_beyond_reclaim"] == 0
        and row["future_htf_source_count"] == 0
        and not row["insolvent"]
        and not row["entry_capacity_breach"]
        and row["minimum_account_equity_usd"] > 0
        and row["peak_notional_usd"] <= CAPITAL_CONTRACT["strategy_capacity_usd"] + 1e-6
    )


def screen(artifact: Path, npz_dir: Path) -> dict[str, Any]:
    source, data, htfs, contexts = shared._fold_contexts(
        artifact, npz_dir, fold_mode="nested"
    )
    manifest = source["manifest"]
    if manifest["side"] != "LONG":
        data.z.close()
        raise ValueError("phase-3 top-harvest cohort is LONG only")
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
            side="LONG",
        )
        for ctx in contexts
    ]
    rows = []
    started = time.perf_counter()
    for spec in registry(data, htfs):
        fold_rows = [
            shared.simulate(
                data,
                ctx["signals"],
                ctx["curve"],
                spec.book,
                ctx["left"],
                ctx["right"],
                commission,
                slippage,
                side="LONG",
                profit_gate_pct=spec.profit_gate_pct,
            )
            for ctx in contexts
        ]
        evidence = [
            _fold_evidence(row, control, ctx)
            for row, control, ctx in zip(fold_rows, controls, contexts)
        ]
        gates = [fold_pass(row, spec.emergency_control) for row in evidence]
        rows.append(
            {
                "family": spec.family,
                "label": spec.label,
                "params": spec.params,
                "components": list(spec.components),
                "emergency_control": spec.emergency_control,
                "fold_evidence": evidence,
                "fold_gate_pass": gates,
                "discovery_strict": all(gates[:-1]),
                "all_fold_strict": all(gates),
                "entry_schedule_unchanged": all(
                    row["entry_schedule_sha256"]
                    == control["frozen_entry_schedule_sha256"]
                    for row, control in zip(evidence, controls)
                ),
            }
        )
    # Discovery-only freeze: final values are absent from the ranking tuple.
    rows.sort(
        key=lambda row: (
            row["emergency_control"],
            not row["discovery_strict"],
            -sum(row["fold_gate_pass"][:-1]),
            -min(x["alpha_vs_same_entry_e02_pp"] for x in row["fold_evidence"][:-1]),
            abs(
                np.mean([x["weighted_tim_pct"] for x in row["fold_evidence"][:-1]])
                - 75.0
            ),
            row["label"],
        )
    )
    winner = next(row for row in rows if not row["emergency_control"])
    strict = [row for row in rows if row["all_fold_strict"]]
    if any(not row["entry_schedule_unchanged"] for row in rows):
        data.z.close()
        raise RuntimeError("candidate altered the frozen ladder entry schedule")
    result = {
        "campaign": CAMPAIGN,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "tier": "VEC_RESEARCH_CAUSAL_PARENT_CLOSE",
        "symbol": manifest["symbol"],
        "side": "LONG",
        "source_artifact": str(artifact.resolve()),
        "source_artifact_result_sha256": hashlib.sha256(
            (artifact / "result.json").read_bytes()
        ).hexdigest(),
        "source_npz_sha256": manifest["npz_sha256"],
        "availability_clock": "SYNTHETIC_PARENT_CLOSE_AVAILABILITY_V1",
        "fill_timing": "FIRST_STRICTLY_LATER_AVAILABILITY_BATCH",
        "candidate_count": len(rows),
        "single_count": 36,
        "logical_combination_count": 6,
        "capital_contract": CAPITAL_CONTRACT,
        "costs": {
            "commission_bps_one_way": manifest["commission_bps_one_way"],
            "slippage_bps_one_way": manifest["slippage_bps_one_way"],
        },
        "exposure_gate_pct": list(EXPOSURE_BAND),
        "same_entry_e02_control_folds": controls,
        "frozen_discovery_winner": winner,
        "discovery_strict_count": sum(row["discovery_strict"] for row in rows),
        "strict_survivor_count": len(strict),
        "strict_survivors": strict,
        "exact_replay_allowed": bool(strict),
        "matrix_written": False,
        "promotion_allowed": False,
        "dc_low4_profit_exit_used": False,
        "elapsed_seconds": time.perf_counter() - started,
        "candidates": rows,
    }
    data.z.close()
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifact", type=Path, required=True)
    ap.add_argument("--npz-dir", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    args = ap.parse_args()
    result = screen(args.artifact.resolve(), args.npz_dir.resolve())
    args.out_dir.mkdir(parents=True, exist_ok=False)
    (args.out_dir / "result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )
    print(
        json.dumps(
            {
                "symbol": result["symbol"],
                "candidate_count": result["candidate_count"],
                "discovery_strict_count": result["discovery_strict_count"],
                "strict_survivor_count": result["strict_survivor_count"],
                "winner": result["frozen_discovery_winner"]["label"],
                "output": str(args.out_dir),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
