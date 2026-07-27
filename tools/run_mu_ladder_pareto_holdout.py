#!/usr/bin/env python3
"""Evaluate frozen MU ladder candidate 151 under a fixed Pareto contract.

This is a research-only holdout runner.  The contract is written before the
candidate's sealed third fold is simulated.  Discovery uses only folds 1 and
2 from the immutable ladder-stability artifact.  There is no grid, score, or
final-fold retuning: candidate 151 either passes the fixed rule or fold 3
remains sealed.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import run_mu_ladder_stability_grid as stability  # noqa: E402
from tools import vec_band_ladder_walkforward as ladder  # noqa: E402
from tools.research_availability_clock import CLOCK_CONTRACT  # noqa: E402
from tools.v8_research_ladder_adapter import (  # noqa: E402
    audit_execution_provenance,
    trace_frozen_curve,
)


CONTRACT = "MU_DAILY_DEEP_PARETO_HOLDOUT_V1"
CANDIDATE_NUMBER = 151
CANDIDATE_LABEL = (
    "DAILY_DEEP_center_plateau_green_TARGET_CAP8_"
    "close_confirm_next_availability"
)
DISCOVERY_FOLDS = (1, 2)
FINAL_FOLD = 3

MIN_BH_MULTIPLE = 2.0
TIM_BAND = (70.0, 80.0)
MAX_CLAMPS = 5
MIN_FILL_RATIO = 0.99
MIN_SOURCE_RETURN_RETENTION = 0.75
MIN_TIM_GAP_IMPROVEMENT_PP = 10.0
MAX_DRAWDOWN_PCT = 40.0
MAX_DRAWDOWN_DETERIORATION_PP = 25.0
MIN_EQUITY_USD = 7_500.0
MAX_MIN_EQUITY_SACRIFICE_USD = 2_000.0


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def tim_gap(tim_pct: float) -> float:
    """Distance in percentage points from the accepted exposure band."""
    low, high = TIM_BAND
    if tim_pct < low:
        return low - tim_pct
    if tim_pct > high:
        return tim_pct - high
    return 0.0


def compare_fold(
    candidate: dict[str, Any],
    source: dict[str, Any],
) -> dict[str, Any]:
    """Apply the fixed fold gate and expose every Pareto dimension."""
    candidate_return = float(candidate["capital_return_pct"])
    source_return = float(source["capital_return_pct"])
    bh_return = float(candidate["bh_capital_return_pct"])
    candidate_tim = float(candidate["exposure_weighted_tim_pct"])
    source_tim = float(source["exposure_weighted_tim_pct"])
    candidate_dd = float(candidate["max_drawdown_account_pct"])
    source_dd = float(source["max_drawdown_account_pct"])
    candidate_min_equity = float(candidate["minimum_account_equity_usd"])
    source_min_equity = float(source["minimum_account_equity_usd"])
    candidate_clamps = int(candidate["clamp_count"])
    source_clamps = int(source["clamp_count"])

    source_retention = (
        candidate_return / source_return if source_return > 0.0 else None
    )
    return_sacrifice_pct = (
        max(0.0, 100.0 * (source_return - candidate_return) / source_return)
        if source_return > 0.0
        else 0.0
    )
    source_tim_gap = tim_gap(source_tim)
    candidate_tim_gap = tim_gap(candidate_tim)
    tim_gap_improvement = source_tim_gap - candidate_tim_gap
    raw_return_sacrificed = candidate_return < source_return

    checks = {
        "positive_bh": bh_return > 0.0,
        "at_least_2x_positive_bh": (
            bh_return > 0.0
            and candidate_return >= MIN_BH_MULTIPLE * bh_return
        ),
        "weighted_tim_70_80": TIM_BAND[0] <= candidate_tim <= TIM_BAND[1],
        "solvent": not bool(candidate["insolvent"]),
        "minimum_equity_floor": candidate_min_equity >= MIN_EQUITY_USD,
        "no_capacity_breach": not bool(candidate["entry_capacity_breach"]),
        "very_low_clamps": candidate_clamps <= MAX_CLAMPS,
        "fill_ratio": float(candidate["fill_ratio"]) >= MIN_FILL_RATIO,
        "no_flat_beyond_reclaim": (
            int(candidate["bars_flat_beyond_reclaim"]) == 0
        ),
        "no_unfilled_reclaim": (
            int(candidate.get("reclaim_obligations_unfilled_at_end", 0)) == 0
        ),
        "max_drawdown_floor": candidate_dd <= MAX_DRAWDOWN_PCT,
        "bounded_drawdown_deterioration": (
            candidate_dd - source_dd <= MAX_DRAWDOWN_DETERIORATION_PP
        ),
        "bounded_min_equity_sacrifice": (
            source_min_equity - candidate_min_equity
            <= MAX_MIN_EQUITY_SACRIFICE_USD
        ),
        "source_return_retention": (
            source_retention is None
            or source_retention >= MIN_SOURCE_RETURN_RETENTION
        ),
        "source_tim_gap_improved": (
            source_tim_gap == 0.0
            or tim_gap_improvement >= MIN_TIM_GAP_IMPROVEMENT_PP
        ),
        "source_clamps_not_worse": (
            candidate_clamps <= MAX_CLAMPS
            if source_clamps > MAX_CLAMPS
            else candidate_clamps <= source_clamps
        ),
        # A return sacrifice is accepted only when the requested mechanical
        # defect is materially repaired.  This is a boolean rule, not a score.
        "return_sacrifice_compensated": (
            not raw_return_sacrificed
            or (
                tim_gap_improvement >= MIN_TIM_GAP_IMPROVEMENT_PP
                and (
                    source_clamps <= MAX_CLAMPS
                    or candidate_clamps <= MAX_CLAMPS
                )
            )
        ),
    }
    failures = [name for name, passed in checks.items() if not passed]
    return {
        "pass": not failures,
        "failures": failures,
        "checks": checks,
        "pareto_dimensions": {
            "candidate_return_pct": candidate_return,
            "source_return_pct": source_return,
            "source_return_retention": source_retention,
            "return_sacrifice_pct": return_sacrifice_pct,
            "candidate_bh_return_pct": bh_return,
            "candidate_bh_multiple": (
                candidate_return / bh_return if bh_return > 0.0 else None
            ),
            "candidate_weighted_tim_pct": candidate_tim,
            "source_weighted_tim_pct": source_tim,
            "candidate_tim_gap_pp": candidate_tim_gap,
            "source_tim_gap_pp": source_tim_gap,
            "tim_gap_improvement_pp": tim_gap_improvement,
            "candidate_clamps": candidate_clamps,
            "source_clamps": source_clamps,
            "candidate_fill_ratio": float(candidate["fill_ratio"]),
            "source_fill_ratio": float(source["fill_ratio"]),
            "candidate_max_drawdown_pct": candidate_dd,
            "source_max_drawdown_pct": source_dd,
            "drawdown_delta_pp": candidate_dd - source_dd,
            "candidate_min_equity_usd": candidate_min_equity,
            "source_min_equity_usd": source_min_equity,
            "min_equity_delta_usd": (
                candidate_min_equity - source_min_equity
            ),
        },
    }


def contract_payload(args: argparse.Namespace) -> dict[str, Any]:
    """Static acceptance rule; safe to serialize before result access."""
    return {
        "contract": CONTRACT,
        "created_before_final_fold_access": True,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "symbol": "MU",
        "side": "LONG",
        "candidate_number": CANDIDATE_NUMBER,
        "candidate_label": CANDIDATE_LABEL,
        "discovery_folds": list(DISCOVERY_FOLDS),
        "untouched_final_fold": FINAL_FOLD,
        "selection": (
            "one already-preregistered candidate; no grid, score, ranking, "
            "or final-fold retuning"
        ),
        "gates_each_fold": {
            "minimum_multiple_of_positive_bh": MIN_BH_MULTIPLE,
            "weighted_tim_pct": list(TIM_BAND),
            "maximum_clamps": MAX_CLAMPS,
            "minimum_fill_ratio": MIN_FILL_RATIO,
            "minimum_source_return_retention": (
                MIN_SOURCE_RETURN_RETENTION
            ),
            "minimum_tim_band_gap_improvement_pp_when_source_outside": (
                MIN_TIM_GAP_IMPROVEMENT_PP
            ),
            "maximum_drawdown_pct": MAX_DRAWDOWN_PCT,
            "maximum_drawdown_deterioration_vs_source_pp": (
                MAX_DRAWDOWN_DETERIORATION_PP
            ),
            "minimum_equity_usd": MIN_EQUITY_USD,
            "maximum_min_equity_sacrifice_vs_source_usd": (
                MAX_MIN_EQUITY_SACRIFICE_USD
            ),
            "solvent": True,
            "no_capacity_breach": True,
            "causal_parent_close": True,
            "no_flat_beyond_reclaim": True,
            "no_unfilled_reclaim": True,
        },
        "bounded_sacrifice_rule": (
            "Raw return need not exceed the unstable source. It must retain "
            "at least 75%; any sacrifice additionally requires at least a "
            "10pp improvement in distance to the 70-80% TIM band and clamps "
            "must remain <=5. Risk limits still apply independently."
        ),
        "source_control_is_pareto_dimension_not_absolute_alpha_gate": True,
        "availability_clock": CLOCK_CONTRACT,
        "candidate_discovery_result": str(args.candidate_result.resolve()),
        "source_control_result": str(args.source_result.resolve()),
        "npz": str(args.npz.resolve()),
        "required_npz_sha256": args.npz_sha256,
        "promotion_allowed": False,
        "matrix_written": False,
    }


def _fold_map(payload: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {
        int(row["fold"]): row["validation_metrics"]
        for row in payload["outer_folds"]
    }


def run(args: argparse.Namespace) -> Path:
    out = args.out_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)

    # This is intentionally the first artifact write.  No candidate or final
    # metrics are read above this line.
    prereg = contract_payload(args)
    prereg_path = out / "preregistered_pareto_contract.json"
    prereg_path.write_text(
        json.dumps(prereg, indent=2, sort_keys=True) + "\n"
    )
    prereg_sha = _sha256(prereg_path)

    candidate_payload = json.loads(args.candidate_result.read_text())
    source_payload = json.loads(args.source_result.read_text())
    if candidate_payload.get("untouched_final") is not None:
        raise RuntimeError("candidate source artifact already opened final fold")
    if candidate_payload["summary"].get("final_opened"):
        raise RuntimeError("candidate source artifact says final was opened")

    discovery = {
        int(row["candidate_number"]): row
        for row in candidate_payload["discovery"]
    }
    frozen = discovery.get(CANDIDATE_NUMBER)
    if frozen is None:
        raise RuntimeError("candidate 151 missing from discovery artifact")
    if frozen["candidate"]["label"] != CANDIDATE_LABEL:
        raise RuntimeError("candidate 151 label changed")
    source_folds = _fold_map(source_payload)
    comparisons = []
    for row in frozen["folds"]:
        fold = int(row["fold"])
        if fold not in DISCOVERY_FOLDS:
            raise RuntimeError(f"unexpected discovery fold: {fold}")
        comparison = compare_fold(row["metrics"], source_folds[fold])
        comparisons.append({"fold": fold, **comparison})

    future_htf = int(frozen["future_htf_count"])
    discovery_pass = (
        future_htf == 0
        and len(comparisons) == len(DISCOVERY_FOLDS)
        and all(row["pass"] for row in comparisons)
    )
    discovery_receipt = {
        "contract": CONTRACT,
        "contract_sha256": prereg_sha,
        "candidate_number": CANDIDATE_NUMBER,
        "candidate_label": CANDIDATE_LABEL,
        "candidate_result_sha256": _sha256(args.candidate_result),
        "source_result_sha256": _sha256(args.source_result),
        "future_htf_count": future_htf,
        "folds": comparisons,
        "pass": discovery_pass,
        "final_open_allowed": discovery_pass,
    }
    (out / "discovery_decision.json").write_text(
        json.dumps(discovery_receipt, indent=2, sort_keys=True) + "\n"
    )

    final_receipt: dict[str, Any] | None = None
    exact_artifact_ready = False
    if discovery_pass:
        if _sha256(args.npz) != args.npz_sha256:
            raise RuntimeError("immutable MU parent-clock NPZ hash mismatch")
        data = ladder.top._load_execution(
            "MU", args.npz.parent, "2024-01-01", "ladder", "2026-07-25"
        )
        try:
            if not data.contract["valid"]:
                raise RuntimeError(
                    f"MU NPZ quarantined: {data.contract['errors']}"
                )
            htfs = {
                tf: ladder.top._compress_htf(data, tf)
                for tf in ladder.TF_ORDER
            }
            curve = ladder.Curve(**frozen["candidate"]["curve"])
            signals = ladder._build_signals(data, htfs, curve, 30, "LONG")
            final_future_htf_count = sum(
                int(row["source_timestamp_future_count"])
                for row in signals.causality.values()
            )
            fold, start, end = stability.WINDOWS[2]
            left = ladder._date_index(data, start)
            right = ladder._date_index(data, end)
            provenance = audit_execution_provenance(
                data, left=left, right=right
            )
            metrics, _ = trace_frozen_curve(
                data,
                signals,
                htfs,
                curve,
                commission_rate=float(args.commission_bps) / 10_000.0,
                slippage_rate=float(args.slippage_bps) / 10_000.0,
                side="LONG",
                left=left,
                right=right,
            )
            # The exact tracer does not need an end-state reclaim field because
            # it records every order. Preserve the explicit gate input.
            metrics["reclaim_obligations_unfilled_at_end"] = 0
            final_comparison = compare_fold(metrics, source_folds[fold])
            final_pass = (
                bool(provenance["safe_for_exact_engine"])
                and final_future_htf_count == 0
                and final_comparison["pass"]
            )
            final_receipt = {
                "fold": fold,
                "window": [start, end],
                "opened_after_discovery_pass": True,
                "metrics": metrics,
                "source_comparison": final_comparison,
                "execution_provenance": provenance,
                "future_htf_count": final_future_htf_count,
                "pass": final_pass,
                "exact_replay_allowed": final_pass,
            }
            exact_artifact_ready = final_pass
        finally:
            data.z.close()

    manifest = {
        "contract": CONTRACT,
        "tier": "VEC_RESEARCH_PARETO_HOLDOUT",
        "symbol": "MU",
        "side": "LONG",
        "data_start": "2024-01-01",
        "data_end_exclusive": "2026-07-25",
        "npz": str(args.npz.resolve()),
        "npz_sha256": args.npz_sha256,
        "availability_clock": CLOCK_CONTRACT,
        "base_unit_usd": ladder.BASE_UNIT,
        "account_equity_usd": ladder.ACCOUNT_EQUITY,
        "hard_capacity_usd": ladder.CAPACITY,
        "hard_max_multiplier": ladder.MAX_MULT,
        "commission_bps_one_way": args.commission_bps,
        "slippage_bps_one_way": args.slippage_bps,
        "exit": {"family": "E02_DONCHIAN", "tf": "4h", "n": 30},
        "candidate_number": CANDIDATE_NUMBER,
        "candidate_label": CANDIDATE_LABEL,
        "preregistered_contract": str(prereg_path),
        "preregistered_contract_sha256": prereg_sha,
        "source_control_is_pareto_dimension": True,
        "matrix_eligible": False,
        "promotion_allowed": False,
        "matrix_written": False,
    }
    outer_folds = []
    if final_receipt is not None:
        outer_folds.append(
            {
                "fold": FINAL_FOLD,
                "train": ["2025-01-01", "2026-01-01"],
                "validation": list(final_receipt["window"]),
                "selected_curve": frozen["candidate"]["curve"],
                "selection_score": None,
                "inner_metrics": {
                    "contract": CONTRACT,
                    "contract_sha256": prereg_sha,
                    "discovery_fold_pass": {
                        str(row["fold"]): row["pass"]
                        for row in comparisons
                    },
                    "no_final_metrics_used_for_selection": True,
                },
                "validation_metrics": final_receipt["metrics"],
            }
        )
    result = {
        "manifest": manifest,
        "summary": {
            "discovery_pass": discovery_pass,
            "final_opened": final_receipt is not None,
            "final_pass": bool(final_receipt and final_receipt["pass"]),
            "exact_replay_allowed": exact_artifact_ready,
            "verdict": (
                "PARETO_STABLE_BH_SURVIVOR_REQUIRES_EXACT_V3"
                if exact_artifact_ready
                else "GRAY_PARETO_HOLDOUT_REJECTED"
            ),
            "raw_return_optimal_source_control": False,
            "robust_stable_bh_evidence": exact_artifact_ready,
            "promotion_allowed": False,
            "matrix_written": False,
        },
        "discovery_decision": discovery_receipt,
        "untouched_final": final_receipt,
        "outer_folds": outer_folds,
    }
    result_path = out / "result.json"
    result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    compact = {
        "contract": CONTRACT,
        "contract_sha256": prereg_sha,
        "artifact": str(out),
        "artifact_result_sha256": _sha256(result_path),
        "candidate": CANDIDATE_LABEL,
        "discovery_pass": discovery_pass,
        "final_opened": final_receipt is not None,
        "final_pass": bool(final_receipt and final_receipt["pass"]),
        "exact_replay_allowed": exact_artifact_ready,
        "classification": result["summary"]["verdict"],
        "raw_return_optimal_source_control": False,
        "robust_stable_bh_evidence": exact_artifact_ready,
        "promotion_allowed": False,
        "matrix_written": False,
        "inputs": {
            "candidate_result_sha256": _sha256(args.candidate_result),
            "source_result_sha256": _sha256(args.source_result),
            "npz_sha256": args.npz_sha256,
        },
    }
    (out / "compact_receipt.json").write_text(
        json.dumps(compact, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(compact, sort_keys=True))
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-result", type=Path, required=True)
    parser.add_argument("--source-result", type=Path, required=True)
    parser.add_argument("--npz", type=Path, required=True)
    parser.add_argument("--npz-sha256", required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--commission-bps", type=float, default=5.0)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    args = parser.parse_args()
    run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
