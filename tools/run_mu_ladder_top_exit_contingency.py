#!/usr/bin/env python3
"""Small preregistered same-entry top-exit contingency for MU_LONG.

The ladder-only phase is the sole source of ladder selection.  This phase
freezes its five closest exact-compatible candidates, keeps their entry
signals unchanged, and crosses them with 14 causal E03/E06/E09 top-exit
settings.  Only discovery folds 1 and 2 may select a row; fold 3 remains
sealed unless one row passes every discovery gate.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "tools"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
from tools import run_mu_ladder_stability_grid as phase1  # noqa: E402
from tools import vec_band_ladder_walkforward as ladder  # noqa: E402
from tools import vec_top_exit_campaign as exit_base  # noqa: E402
from tools import vec_top_exit_walkforward as exit_wf  # noqa: E402


CONTRACT = "MU_LADDER_TOP_EXIT_CONTINGENCY_PARENT_CLOCK_V3"
MAX_FROZEN_LADDERS = 5


@dataclasses.dataclass(frozen=True)
class ExitSetting:
    family: str
    label: str
    params: dict[str, Any]


def preregistered_exit_settings() -> list[ExitSetting]:
    rows: list[ExitSetting] = []
    for damage in (0.5, 1.0):
        for rebound in (0.5, 1.0):
            rows.append(
                ExitSetting(
                    "E03_CONFIRMED_STRUCTURE_RETEST",
                    f"E03_4h_C2_D{damage:g}_R{rebound:g}_W12",
                    {
                        "confirm_bars": 2,
                        "damage_atr": damage,
                        "rebound_atr": rebound,
                        "max_wait": 12,
                    },
                )
            )
    for lookback in (40, 60, 100):
        for arm_z in (2.0, 2.5):
            rows.append(
                ExitSetting(
                    "E06_REGRESSION_REENTRY",
                    f"E06_4h_N{lookback}_ZA{arm_z:g}_ZE1_R0.7",
                    {
                        "lookback": lookback,
                        "z_arm": arm_z,
                        "z_exit": 1.0,
                        "corr_gate": 0.7,
                    },
                )
            )
    for arm_tf in ("4h", "D"):
        for rsi in (70.0, 75.0):
            rows.append(
                ExitSetting(
                    "E09_MTF_EXHAUSTION_STRUCTURE",
                    f"E09_{arm_tf}_RSI{rsi:g}_X1_W24_C1",
                    {
                        "arm_tf": arm_tf,
                        "rsi_threshold": rsi,
                        "extension_atr": 1.0,
                        "expiry_1h": 24,
                        "confirm_bars": 1,
                    },
                )
            )
    assert len(rows) == 14
    return rows


def _select_frozen_ladders(source: dict[str, Any]) -> list[dict[str, Any]]:
    """Rank using phase-1 discovery only; never inspect an untouched fold."""
    candidates = []
    for row in source["discovery"]:
        if not row["candidate"]["exact_eligible"]:
            continue
        allowed = {"not_above_source_ladder_e02"}
        if not all(
            set(fold["gate_failures"]).issubset(allowed)
            for fold in row["folds"]
        ):
            continue
        candidates.append(row)
    candidates.sort(
        key=lambda row: (
            -min(
                fold["metrics"]["alpha_vs_source_control_pp"]
                for fold in row["folds"]
            ),
            sum(
                abs(fold["metrics"]["exposure_weighted_tim_pct"] - 75.0)
                for fold in row["folds"]
            ),
            row["candidate"]["label"],
        )
    )
    return candidates[:MAX_FROZEN_LADDERS]


def _event_arrays(
    data: Any,
    htfs: dict[str, Any],
    setting: ExitSetting,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, int]:
    n = len(data.ts)
    p = setting.params
    if setting.family == "E03_CONFIRMED_STRUCTURE_RETEST":
        event, ref = exit_wf._e03_signal(
            htfs["4h"],
            1,
            p["confirm_bars"],
            p["damage_atr"],
            p["rebound_atr"],
            p["max_wait"],
        )
        source_htf = htfs["4h"]
    elif setting.family == "E06_REGRESSION_REENTRY":
        event, ref = exit_wf._e06_signal(
            htfs["4h"],
            1,
            p["lookback"],
            p["z_arm"],
            p["z_exit"],
            p["corr_gate"],
        )
        source_htf = htfs["4h"]
    elif setting.family == "E09_MTF_EXHAUSTION_STRUCTURE":
        source_htf = htfs["1h"]
        event, ref = exit_wf._e09_signal(
            htfs[p["arm_tf"]],
            source_htf,
            1,
            p["rsi_threshold"],
            p["extension_atr"],
            p["expiry_1h"],
            p["confirm_bars"],
        )
    else:
        raise ValueError(setting.family)
    mapped, refs = exit_base._map_events(n, source_htf, event, ref)
    source = np.zeros(n, dtype=np.int64)
    source[np.asarray(source_htf.event_index, dtype=np.int64)] = np.asarray(
        source_htf.source_ts, dtype=np.int64
    )
    future = int(
        np.count_nonzero(
            (mapped > 0)
            & (source > 0)
            & (source > np.asarray(data.ts, dtype=np.int64))
        )
    )
    return mapped, refs, source, future


def _signals_with_exit(
    entry: ladder.SignalData,
    event: np.ndarray,
    ref: np.ndarray,
) -> ladder.SignalData:
    return ladder.SignalData(
        entry_mult=entry.entry_mult,
        event_tf=entry.event_tf,
        exit_event=np.ascontiguousarray(event, dtype=np.uint8),
        exit_ref=np.ascontiguousarray(ref, dtype=np.float64),
        causality=entry.causality,
        entry_source_ts=entry.entry_source_ts,
    )


def _gate(
    metrics: dict[str, Any],
    fold: int,
    same_entry_e02_return: float,
    future_count: int,
) -> tuple[bool, list[str]]:
    passed, failures = phase1._gate(metrics, fold)
    metrics["alpha_vs_same_entry_e02_pp"] = (
        metrics["capital_return_pct"] - same_entry_e02_return
    )
    if metrics["alpha_vs_same_entry_e02_pp"] <= 0:
        failures.append("not_above_same_entry_e02")
    if metrics["exit_count"] <= 0:
        failures.append("no_actual_top_exit")
    if future_count:
        failures.append("future_htf_source")
    return not failures, failures


def run(args: argparse.Namespace) -> Path:
    phase1_source = json.loads(Path(args.phase1_result).read_text())
    if phase1_source["summary"]["strict_discovery_survivors"] != 0:
        raise RuntimeError("contingency is only valid after ladder-only failure")
    frozen_ladders = _select_frozen_ladders(phase1_source)
    if len(frozen_ladders) != MAX_FROZEN_LADDERS:
        raise RuntimeError("phase 1 did not yield five bounded near-gate ladders")
    exits = preregistered_exit_settings()
    out = Path(args.out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    prereg = {
        "contract": CONTRACT,
        "created_before_exit_result_access": True,
        "phase1_result": str(Path(args.phase1_result).resolve()),
        "phase1_result_sha256": hashlib.sha256(
            Path(args.phase1_result).read_bytes()
        ).hexdigest(),
        "frozen_ladder_selection_rule": (
            "phase-1 discovery rows; exact-compatible; no failures except "
            "source-control alpha; rank minimum control alpha, TIM distance"
        ),
        "frozen_ladders": [
            {
                "candidate_number": row["candidate_number"],
                "candidate": row["candidate"],
                "phase1_discovery_folds": row["folds"],
            }
            for row in frozen_ladders
        ],
        "exit_settings": [dataclasses.asdict(row) for row in exits],
        "combination_count": len(frozen_ladders) * len(exits),
        "discovery_folds": [1, 2],
        "untouched_final_fold": 3,
        "gates": {
            "phase1_gates_retained": True,
            "positive_alpha_vs_same_entry_e02_each_fold": True,
            "actual_top_exit_each_fold": True,
            "future_htf_count": 0,
        },
        "selection": (
            "discovery only; rank strict rows by minimum alpha versus original "
            "source control, then same-entry E02, then TIM distance"
        ),
        "exact_adapter_status": (
            "required only if untouched final passes; alternative-exit exact "
            "schedule is not emitted by this vector screen"
        ),
        "no_live_canonical_or_matrix_writes": True,
    }
    prereg_path = out / "preregistered_contingency.json"
    prereg_path.write_text(json.dumps(prereg, indent=2, sort_keys=True) + "\n")

    npz_path = Path(args.npz_dir).resolve() / "MU.npz"
    npz_sha = hashlib.sha256(npz_path.read_bytes()).hexdigest()
    if npz_sha != args.npz_sha256:
        raise RuntimeError("immutable parent-clock MU NPZ hash mismatch")
    data = ladder.top._load_execution(
        "MU", Path(args.npz_dir), "2024-01-01", "ladder", "2026-07-25"
    )
    try:
        htfs = {
            tf: ladder.top._compress_htf(data, tf)
            for tf in ("1h", "4h", "D")
        }
        exit_arrays = {
            setting.label: _event_arrays(data, htfs, setting)
            for setting in exits
        }
        results = []
        for frozen in frozen_ladders:
            curve = ladder.Curve(**frozen["candidate"]["curve"])
            entry = ladder._build_signals(data, htfs, curve, 30, "LONG")
            e02_by_fold = {
                int(row["fold"]): float(row["metrics"]["capital_return_pct"])
                for row in frozen["folds"]
            }
            for setting in exits:
                event, ref, _source, future = exit_arrays[setting.label]
                signals = _signals_with_exit(entry, event, ref)
                fold_rows = []
                for fold, start, end in phase1.WINDOWS[:2]:
                    metrics = phase1._simulate(
                        data,
                        signals,
                        phase1.Candidate(
                            label=frozen["candidate"]["label"],
                            curve=curve,
                            effective_cap_x=float(
                                frozen["candidate"]["effective_cap_x"]
                            ),
                            reclaim_cadence=(
                                "close_confirm_next_availability"
                            ),
                            exact_eligible=True,
                        ),
                        ladder._date_index(data, start),
                        ladder._date_index(data, end),
                        args.commission_bps / 10_000.0,
                        args.slippage_bps / 10_000.0,
                    )
                    passed, failures = _gate(
                        metrics, fold, e02_by_fold[fold], future
                    )
                    fold_rows.append(
                        {
                            "fold": fold,
                            "window": [start, end],
                            "metrics": metrics,
                            "gate_pass": passed,
                            "gate_failures": failures,
                        }
                    )
                results.append(
                    {
                        "ladder_candidate_number": frozen[
                            "candidate_number"
                        ],
                        "ladder": frozen["candidate"],
                        "exit": dataclasses.asdict(setting),
                        "future_htf_count": future,
                        "folds": fold_rows,
                        "strict_discovery_survivor": all(
                            row["gate_pass"] for row in fold_rows
                        ),
                    }
                )
        survivors = [
            row for row in results if row["strict_discovery_survivor"]
        ]
        survivors.sort(
            key=lambda row: (
                -min(
                    fold["metrics"]["alpha_vs_source_control_pp"]
                    for fold in row["folds"]
                ),
                -min(
                    fold["metrics"]["alpha_vs_same_entry_e02_pp"]
                    for fold in row["folds"]
                ),
                sum(
                    abs(
                        fold["metrics"]["exposure_weighted_tim_pct"] - 75.0
                    )
                    for fold in row["folds"]
                ),
                row["ladder"]["label"],
                row["exit"]["label"],
            )
        )
        frozen = survivors[0] if survivors else None
        final = None
        if frozen:
            curve = ladder.Curve(**frozen["ladder"]["curve"])
            entry = ladder._build_signals(data, htfs, curve, 30, "LONG")
            setting = next(
                row for row in exits if row.label == frozen["exit"]["label"]
            )
            event, ref, _source, future = exit_arrays[setting.label]
            metrics = phase1._simulate(
                data,
                _signals_with_exit(entry, event, ref),
                phase1.Candidate(
                    label=frozen["ladder"]["label"],
                    curve=curve,
                    effective_cap_x=float(
                        frozen["ladder"]["effective_cap_x"]
                    ),
                    reclaim_cadence="close_confirm_next_availability",
                    exact_eligible=True,
                ),
                ladder._date_index(data, phase1.WINDOWS[2][1]),
                ladder._date_index(data, phase1.WINDOWS[2][2]),
                args.commission_bps / 10_000.0,
                args.slippage_bps / 10_000.0,
            )
            # Same-entry final E02 is deliberately computed only after the
            # frozen discovery selection, so it cannot influence selection.
            e02 = ladder._simulate(
                data,
                entry,
                curve,
                ladder._date_index(data, phase1.WINDOWS[2][1]),
                ladder._date_index(data, phase1.WINDOWS[2][2]),
                args.commission_bps / 10_000.0,
                args.slippage_bps / 10_000.0,
                "LONG",
            )
            passed, failures = _gate(
                metrics, 3, e02["capital_return_pct"], future
            )
            final = {
                "fold": 3,
                "window": list(phase1.WINDOWS[2][1:]),
                "metrics": metrics,
                "same_entry_e02_metrics": e02,
                "gate_pass": passed,
                "gate_failures": failures,
                "exact_replay_required": bool(passed),
            }
        ranked = sorted(
            results,
            key=lambda row: (
                -sum(fold["gate_pass"] for fold in row["folds"]),
                sum(len(fold["gate_failures"]) for fold in row["folds"]),
                -sum(
                    fold["metrics"]["alpha_vs_source_control_pp"]
                    for fold in row["folds"]
                ),
            ),
        )
        payload = {
            "manifest": {
                "contract": CONTRACT,
                "tier": "VEC_RESEARCH",
                "promotion_allowed": False,
                "matrix_written": False,
                "npz": str(npz_path),
                "npz_sha256": npz_sha,
                "preregistered_contingency": str(prereg_path),
                "preregistered_contingency_sha256": hashlib.sha256(
                    prereg_path.read_bytes()
                ).hexdigest(),
            },
            "summary": {
                "combinations": len(results),
                "strict_discovery_survivors": len(survivors),
                "final_opened": final is not None,
                "final_pass": bool(final and final["gate_pass"]),
                "exact_replay_required": bool(
                    final and final["exact_replay_required"]
                ),
                "verdict": (
                    "VECTOR_SURVIVOR_REQUIRES_EXACT_ADAPTER"
                    if final and final["gate_pass"]
                    else "GRAY_NO_ALL_FOLD_SURVIVOR"
                ),
            },
            "frozen_selection": frozen,
            "untouched_final": final,
            "top_discovery_rows": ranked[:20],
            "results": results,
        }
        (out / "result.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n"
        )
        print(json.dumps(payload["summary"], sort_keys=True))
        return out
    finally:
        data.z.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase1-result", type=Path, required=True)
    ap.add_argument("--npz-dir", type=Path, required=True)
    ap.add_argument(
        "--npz-sha256",
        default=(
            "82b9110fac514bc9eddfe4bc50227ab135e4d55783f7c712dc2b85e3eca77def"
        ),
    )
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--commission-bps", type=float, default=5.0)
    ap.add_argument("--slippage-bps", type=float, default=2.0)
    run(ap.parse_args())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
