#!/usr/bin/env python3
"""Discovery-only DINO ladder follow-up for the phase-3 top exit.

This research runner freezes ``P3_COMBO_DIV_OR_STRUCTURE`` and its persistent
zero-buffer reclaim contract.  It changes only a compact, preregistered
transformation of each outer fold's already-selected ladder curve.  Candidate
selection consumes folds 1 and 2 only.  The chronological final fold is not
simulated or serialized unless a discovery-strict candidate has first been
frozen.

No live, canonical NPZ, exact queue, or switch-matrix state is written.
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
from typing import Any

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import run_top_exit_reclaim_phase3 as phase3  # noqa: E402
from tools import vec_band_ladder_walkforward as ladder  # noqa: E402
from tools import vec_same_entry_exit_adapter as shared  # noqa: E402


CAMPAIGN = "DINO_PHASE3_JOINT_STABILITY_V1"
FROZEN_EXIT_LABEL = "P3_COMBO_DIV_OR_STRUCTURE"
FROZEN_RECLAIM = "PERSISTENT_ZERO_BUFFER_EACH_AVAILABLE_BASE_BAR"
CAPITAL_CONTRACT = {
    "bh_fixed_unit_usd": 2_000.0,
    "strategy_capacity_usd": 16_000.0,
    "account_solvency_usd": 10_000.0,
}
EXPOSURE_BAND = (70.0, 80.0)


@dataclasses.dataclass(frozen=True)
class EntryProfile:
    """One preregistered cross-fold transformation, never a fold-specific fit."""

    label: str
    trigger: str
    mode: str
    semantics: str
    scale: float
    curve_cap_mult: float
    density_overlay: str = "NONE"
    reclaim_cadence: str = FROZEN_RECLAIM


def preregistered_grid() -> list[EntryProfile]:
    """Return the immutable 54-profile discovery registry.

    The 48 core profiles cross four causal trigger families, two curve shapes,
    target/add semantics, and three coherent scale/cap blocks.  Six additional
    profiles apply one declared completed-4h bullish-state refill overlay only
    to the otherwise sparse structure/target family.
    """

    size_profiles = (
        ("S15_C4", 1.5, 4.0),
        ("S20_C6", 2.0, 6.0),
        ("S267_C8", 8.0 / 3.0, 8.0),
    )
    rows = [
        EntryProfile(
            label=f"CORE_{trigger}_{mode}_{semantics}_{size_label}",
            trigger=trigger,
            mode=mode,
            semantics=semantics,
            scale=scale,
            curve_cap_mult=cap,
        )
        for trigger in ("green", "structure", "union", "wt_state")
        for mode in ("linear", "center_plateau")
        for semantics in ("target", "add")
        for size_label, scale, cap in size_profiles
    ]
    rows.extend(
        EntryProfile(
            label=f"REFILL4H_structure_{mode}_target_{size_label}",
            trigger="structure",
            mode=mode,
            semantics="target",
            scale=scale,
            curve_cap_mult=cap,
            density_overlay="COMPLETED_4H_BULL_WT_REFILL_HALF_CAP",
        )
        for mode in ("linear", "center_plateau")
        for size_label, scale, cap in size_profiles
    )
    assert len(rows) == 54
    assert len({dataclasses.astuple(row) for row in rows}) == len(rows)
    return rows


def transformed_curve(
    base: ladder.Curve, profile: EntryProfile
) -> ladder.Curve:
    """Apply one coherent transformation to a fold's frozen base curve."""

    def sized(value: float) -> float:
        return min(profile.curve_cap_mult, value * profile.scale)

    return ladder.Curve(
        label=f"{base.label}__{profile.label}",
        mode=profile.mode,
        trigger=profile.trigger,
        semantics=profile.semantics,
        stoch_low=base.stoch_low,
        d_bottom=sized(base.d_bottom),
        d_top=sized(base.d_top),
        h4_bottom=sized(base.h4_bottom),
        h4_top=sized(base.h4_top),
        h1_bottom=sized(base.h1_bottom),
        h1_top=sized(base.h1_top),
    )


def _with_density_overlay(
    data: Any,
    htfs: dict[str, Any],
    signals: ladder.SignalData,
    profile: EntryProfile,
) -> ladder.SignalData:
    entry_mult = np.asarray(signals.entry_mult, dtype=np.float64).copy()
    event_tf = np.asarray(signals.event_tf, dtype=np.uint8).copy()
    causality = json.loads(json.dumps(signals.causality))
    overlay_count = 0
    if profile.density_overlay == "COMPLETED_4H_BULL_WT_REFILL_HALF_CAP":
        h4 = htfs["4h"]
        full_event = data.full_indices[h4.event_index]
        z = data.z
        source_ts = np.asarray(h4.source_ts, dtype=np.int64)
        observed_ts = np.asarray(data.ts[h4.event_index], dtype=np.int64)
        wt1 = np.asarray(z["wt1_4h"], dtype=np.float64)[full_event]
        wt2 = np.asarray(z["wt2_4h"], dtype=np.float64)[full_event]
        stoch = np.asarray(z["stoch_k_4h"], dtype=np.float64)[full_event]
        pct_b = np.asarray(z["lrL_pct_b_4h"], dtype=np.float64)[full_event]
        event = (
            (source_ts <= observed_ts)
            & np.isfinite(wt1)
            & np.isfinite(wt2)
            & np.isfinite(stoch)
            & np.isfinite(pct_b)
            & (wt1 > wt2)
            & (stoch <= 55.0)
            & (pct_b >= 0.0)
            & (pct_b <= 1.25)
        )
        rows = h4.event_index[event]
        entry_mult[rows] = np.maximum(
            entry_mult[rows], profile.curve_cap_mult / 2.0
        )
        event_tf[1, rows] = 1
        overlay_count = int(np.count_nonzero(event))
    elif profile.density_overlay != "NONE":
        raise ValueError(f"unsupported density overlay: {profile.density_overlay}")

    entry_mult = np.minimum(entry_mult, profile.curve_cap_mult)
    causality["joint_stability_profile"] = {
        "curve_cap_mult": profile.curve_cap_mult,
        "density_overlay": profile.density_overlay,
        "density_overlay_events": overlay_count,
        "reclaim_cadence": profile.reclaim_cadence,
    }
    return ladder.SignalData(
        entry_mult=np.ascontiguousarray(entry_mult, dtype=np.float64),
        event_tf=np.ascontiguousarray(event_tf, dtype=np.uint8),
        exit_event=np.asarray(signals.exit_event, dtype=np.uint8),
        exit_ref=np.asarray(signals.exit_ref, dtype=np.float64),
        causality=causality,
        entry_source_ts=signals.entry_source_ts,
    )


def build_profile_signals(
    data: Any,
    htfs: dict[str, Any],
    base: ladder.Curve,
    profile: EntryProfile,
    exit_n: int,
) -> tuple[ladder.Curve, ladder.SignalData]:
    curve = transformed_curve(base, profile)
    signals = ladder._build_signals(data, htfs, curve, exit_n, "LONG")
    return curve, _with_density_overlay(data, htfs, signals, profile)


def fold_pass(evidence: dict[str, Any]) -> bool:
    return bool(
        evidence["actual_exit_fills"] > 0
        and evidence["strategy_return_pct"] > evidence["bh_return_pct"]
        and evidence["strategy_return_pct"]
        > evidence["same_entry_e02_return_pct"]
        and EXPOSURE_BAND[0]
        <= evidence["weighted_tim_pct"]
        <= EXPOSURE_BAND[1]
        and evidence["bars_flat_beyond_reclaim"] == 0
        and evidence["future_htf_source_count"] == 0
        and not evidence["insolvent"]
        and not evidence["entry_capacity_breach"]
        and evidence["minimum_account_equity_usd"] > 0
        and evidence["peak_notional_usd"]
        <= CAPITAL_CONTRACT["strategy_capacity_usd"] + 1e-6
    )


def _evaluate_fold(
    *,
    data: Any,
    htfs: dict[str, Any],
    ctx: dict[str, Any],
    profile: EntryProfile,
    exit_book: shared.ExitBook,
    exit_n: int,
    commission: float,
    slippage: float,
) -> dict[str, Any]:
    curve, signals = build_profile_signals(
        data, htfs, ctx["curve"], profile, exit_n
    )
    control = shared.simulate(
        data,
        signals,
        curve,
        shared.build_e02_book(data, signals, htfs),
        ctx["left"],
        ctx["right"],
        commission,
        slippage,
        side="LONG",
    )
    row = shared.simulate(
        data,
        signals,
        curve,
        exit_book,
        ctx["left"],
        ctx["right"],
        commission,
        slippage,
        side="LONG",
        profit_gate_pct=0.5,
    )
    evidence = phase3._fold_evidence(row, control, ctx)
    evidence.update(
        {
            "candidate_entry_schedule_sha256": row[
                "frozen_entry_schedule_sha256"
            ],
            "same_entry_control_schedule_sha256": control[
                "frozen_entry_schedule_sha256"
            ],
            "entry_schedule_matches_control": bool(
                row["frozen_entry_schedule_sha256"]
                == control["frozen_entry_schedule_sha256"]
            ),
            "entry_request_count": int(row["entry_request_count"]),
            "entry_fills": int(row["entry_fills"]),
            "curve": dataclasses.asdict(curve),
        }
    )
    if not evidence["entry_schedule_matches_control"]:
        raise RuntimeError("candidate and E02 control entry schedules differ")
    return evidence


def _selection_key(row: dict[str, Any]) -> tuple[Any, ...]:
    """Discovery-only ranking; no final-fold value is accepted here."""

    evidence = row["discovery_evidence"]
    return (
        not row["discovery_strict"],
        -sum(row["discovery_fold_gate_pass"]),
        -min(x["alpha_vs_same_entry_e02_pp"] for x in evidence),
        -min(x["alpha_vs_bh_pp"] for x in evidence),
        abs(float(np.mean([x["weighted_tim_pct"] for x in evidence])) - 75.0),
        row["profile"]["label"],
    )


def run(
    *,
    phase3_result: Path,
    npz_dir: Path,
) -> dict[str, Any]:
    phase3_source = json.loads(phase3_result.read_text())
    winner = phase3_source["frozen_discovery_winner"]
    if (
        phase3_source["symbol"] != "DINO"
        or phase3_source["side"] != "LONG"
        or winner["label"] != FROZEN_EXIT_LABEL
    ):
        raise ValueError("expected DINO_LONG phase-3 divergence/structure result")
    source_artifact = Path(phase3_source["source_artifact"])
    source, data, htfs, contexts = shared._fold_contexts(
        source_artifact, npz_dir, fold_mode="nested"
    )
    if len(contexts) != 3:
        data.z.close()
        raise RuntimeError("DINO joint-stability contract requires exactly 3 folds")
    manifest = source["manifest"]
    commission = float(manifest["commission_bps_one_way"]) / 10_000.0
    slippage = float(manifest["slippage_bps_one_way"]) / 10_000.0
    exit_n = int(manifest["exit"]["n"])
    exit_book = next(
        row.book
        for row in phase3.registry(data, htfs)
        if row.label == FROZEN_EXIT_LABEL
    )

    started = time.perf_counter()
    candidates: list[dict[str, Any]] = []
    # Deliberately omit contexts[-1]: the final fold is inaccessible here.
    for profile in preregistered_grid():
        evidence = [
            _evaluate_fold(
                data=data,
                htfs=htfs,
                ctx=ctx,
                profile=profile,
                exit_book=exit_book,
                exit_n=exit_n,
                commission=commission,
                slippage=slippage,
            )
            for ctx in contexts[:-1]
        ]
        gates = [fold_pass(row) for row in evidence]
        candidates.append(
            {
                "profile": dataclasses.asdict(profile),
                "discovery_evidence": evidence,
                "discovery_fold_gate_pass": gates,
                "discovery_strict": all(gates),
            }
        )
    candidates.sort(key=_selection_key)
    frozen = candidates[0]
    discovery_survivors = [
        row for row in candidates if row["discovery_strict"]
    ]

    final_evidence = None
    all_fold_strict = False
    if discovery_survivors:
        # Exactly one discovery-frozen profile sees the final fold.
        frozen = discovery_survivors[0]
        profile = EntryProfile(**frozen["profile"])
        final_evidence = _evaluate_fold(
            data=data,
            htfs=htfs,
            ctx=contexts[-1],
            profile=profile,
            exit_book=exit_book,
            exit_n=exit_n,
            commission=commission,
            slippage=slippage,
        )
        all_fold_strict = fold_pass(final_evidence)

    result = {
        "campaign": CAMPAIGN,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "tier": "VEC_RESEARCH_CAUSAL_PARENT_CLOSE",
        "symbol": "DINO",
        "side": "LONG",
        "source_phase3_result": str(phase3_result.resolve()),
        "source_phase3_result_sha256": hashlib.sha256(
            phase3_result.read_bytes()
        ).hexdigest(),
        "source_artifact": str(source_artifact),
        "source_npz_sha256": manifest["npz_sha256"],
        "availability_clock": "SYNTHETIC_PARENT_CLOSE_AVAILABILITY_V1",
        "fill_timing": "FIRST_STRICTLY_LATER_AVAILABILITY_BATCH",
        "frozen_exit_label": FROZEN_EXIT_LABEL,
        "frozen_exit_params": winner["params"],
        "frozen_profit_gate_pct": 0.5,
        "frozen_reclaim_contract": FROZEN_RECLAIM,
        "reclaim_cadence_varied": False,
        "reclaim_cadence_reason": (
            "downsampling a resting reclaim could forget an intrabar touch"
        ),
        "candidate_count": len(candidates),
        "selection_scope": "DISCOVERY_FOLDS_1_AND_2_ONLY",
        "capital_contract": CAPITAL_CONTRACT,
        "costs": {
            "commission_bps_one_way": manifest["commission_bps_one_way"],
            "slippage_bps_one_way": manifest["slippage_bps_one_way"],
        },
        "exposure_gate_pct": list(EXPOSURE_BAND),
        "discovery_strict_count": len(discovery_survivors),
        "frozen_discovery_winner": frozen,
        "final_fold_evaluated": final_evidence is not None,
        "final_evidence": final_evidence,
        "all_fold_strict": all_fold_strict,
        "exact_replay_allowed": all_fold_strict,
        "matrix_written": False,
        "promotion_allowed": False,
        "candidates": candidates,
        "elapsed_seconds": time.perf_counter() - started,
    }
    data.z.close()
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase3-result", type=Path, required=True)
    ap.add_argument("--npz-dir", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    args = ap.parse_args()
    result = run(
        phase3_result=args.phase3_result.resolve(),
        npz_dir=args.npz_dir.resolve(),
    )
    args.out_dir.mkdir(parents=True, exist_ok=False)
    (args.out_dir / "result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )
    print(
        json.dumps(
            {
                "candidate_count": result["candidate_count"],
                "discovery_strict_count": result["discovery_strict_count"],
                "winner": result["frozen_discovery_winner"]["profile"]["label"],
                "final_fold_evaluated": result["final_fold_evaluated"],
                "all_fold_strict": result["all_fold_strict"],
                "output": str(args.out_dir),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
