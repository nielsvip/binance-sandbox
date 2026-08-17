#!/usr/bin/env python3
"""Isolated discovery-frozen HAO_SHORT ladder solvency screen.

This research runner is deliberately narrow:

* it accepts only the hash-bound, versioned HAO recovery NPZ;
* it never writes canonical indicators, config, matrix, or live state;
* it perturbs the *already frozen* per-fold ladder schedule with a bounded,
  preregistered global scale, delivered multiplier cap, and one of three
  monotone per-timeframe reduction profiles;
* it evaluates folds 1-2 first, writes the frozen selection, and only then
  simulates the untouched final fold; and
* it compares two fixed, ex-ante top-exit geometries against identical-entry
  E02 while retaining E02 itself as the solvency baseline.

The E05 and peak-giveback parameters are central values from their existing
bounded registries.  They are not selected on HAO's final fold.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import vec_top_exit_campaign as top  # noqa: E402


EXPECTED_HAO_NPZ_SHA256 = (
    "17907495bd909a1ea7ebcd6c7846134a7e70ae966b0fe7e79c3006107bb36332"
)
VERIFIED_JUMP_TS = "2026-07-14T10:30:00+00:00"
GLOBAL_SCALES = (0.50, 0.625, 0.75, 0.875, 1.0)
DELIVERED_CAPS = (4.0, 5.0, 6.0, 7.0, 8.0)
TF_PROFILES = {
    "EXACT": {"D": 1.0, "4h": 1.0, "1h": 1.0},
    "D75": {"D": 0.75, "4h": 1.0, "1h": 1.0},
    "SLOPE_REDUCED": {"D": 0.75, "4h": 0.875, "1h": 1.0},
}
EXPOSURE_MIN = 70.0
EXPOSURE_MAX = 80.0
DISCOVERY_FOLDS = (1, 2)
FINAL_FOLD = 3


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _install_hash_bound_hao_exception(npz: Path) -> str:
    """Install the existing isolated exception without weakening the contract."""
    actual = _sha256(npz)
    if actual != EXPECTED_HAO_NPZ_SHA256:
        raise RuntimeError(
            f"isolated HAO NPZ hash drift: expected "
            f"{EXPECTED_HAO_NPZ_SHA256}, got {actual}"
        )
    with np.load(npz, allow_pickle=False) as z:
        ts = np.asarray(z["timestamps"], dtype=np.int64)
        close = np.asarray(z["close"], dtype=np.float64)
        jumps = np.abs(np.diff(close) / close[:-1])
        index = int(np.argmax(jumps)) + 1
        jump = float(jumps[index - 1])
        jump_ts = datetime.fromtimestamp(
            int(ts[index]), tz=timezone.utc
        ).isoformat()
    if jump_ts != VERIFIED_JUMP_TS or not 0.889 < jump < 0.891:
        raise RuntimeError(
            f"unrecognized HAO discontinuity: {jump_ts=} {jump=}"
        )
    original = top.audit_npz

    def verified_audit(*args: Any, **kwargs: Any) -> Any:
        audit = original(*args, **kwargs)
        jump_errors = [
            value
            for value in audit.errors
            if value.startswith("unadjusted/corrupt price discontinuity:")
        ]
        other = [value for value in audit.errors if value not in jump_errors]
        if len(jump_errors) == 1 and not other:
            audit.errors = []
            audit.valid = True
            audit.warnings.append(
                "isolated hash/timestamp-bound verified HAO Jul-14 move"
            )
        return audit

    top.audit_npz = verified_audit
    return actual


def preregistered_settings() -> list[dict[str, Any]]:
    """Return the immutable bounded ladder grid in stable order."""
    return [
        {
            "global_scale": scale,
            "delivered_cap_mult": cap,
            "tf_profile": profile,
            "tf_factors": dict(TF_PROFILES[profile]),
        }
        for scale in GLOBAL_SCALES
        for cap in DELIVERED_CAPS
        for profile in TF_PROFILES
    ]


def _candidate_id(exit_family: str, setting: dict[str, Any]) -> str:
    stable = json.dumps(
        {"exit_family": exit_family, "ladder": setting},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(stable.encode()).hexdigest()[:16]


def _scaled_context(
    data: Any,
    htfs: dict[str, Any],
    ctx: dict[str, Any],
    setting: dict[str, Any],
    exit_n: int,
    side: str,
    ladder: Any,
) -> dict[str, Any]:
    """Materialize a setting from frozen trigger/mode/semantics only."""
    curve = ctx["curve"]
    factors = setting["tf_factors"]
    profiled = dataclasses.replace(
        curve,
        label=f"{curve.label}__{setting['tf_profile']}",
        d_bottom=curve.d_bottom * factors["D"],
        d_top=curve.d_top * factors["D"],
        h4_bottom=curve.h4_bottom * factors["4h"],
        h4_top=curve.h4_top * factors["4h"],
        h1_bottom=curve.h1_bottom * factors["1h"],
        h1_top=curve.h1_top * factors["1h"],
    )
    signals = ladder._build_signals(data, htfs, profiled, exit_n, side)
    delivered = np.minimum(
        float(setting["delivered_cap_mult"]),
        np.asarray(signals.entry_mult, dtype=np.float64)
        * float(setting["global_scale"]),
    )
    signals = dataclasses.replace(
        signals,
        entry_mult=np.ascontiguousarray(delivered, dtype=np.float64),
    )
    return {
        **ctx,
        "curve": profiled,
        "signals": signals,
    }


def _normalize(
    row: dict[str, Any],
    control: dict[str, Any],
    fold: int,
    family: str,
) -> dict[str, Any]:
    strategy = float(row["capital_return_pct"])
    bh = float(row["bh_capital_return_pct"])
    e02 = float(control["capital_return_pct"])
    tim = float(row["exposure_weighted_tim_pct"])
    dd = float(row["max_drawdown_account_pct"])
    insolvent = bool(row["insolvent"]) or float(
        row["minimum_account_equity_usd"]
    ) <= 0
    obligations = int(row.get("clip_obligations_unfilled_at_end", 0))
    future = int(row.get("future_htf_source_count", 0))
    actual_exits = int(
        row.get(
            "technical_exit_fills",
            row.get("exit_fills", 0),
        )
    )
    gate = bool(
        family != "EXIT_E02_DONCHIAN"
        and strategy > bh
        and strategy > e02
        and EXPOSURE_MIN <= tim <= EXPOSURE_MAX
        and not insolvent
        and dd < 100.0
        and obligations == 0
        and future == 0
        and actual_exits > 0
    )
    return {
        "fold": fold,
        "strategy_return_pct": strategy,
        "bh_return_pct": bh,
        "same_entry_e02_return_pct": e02,
        "alpha_vs_bh_pp": strategy - bh,
        "alpha_vs_same_entry_e02_pp": strategy - e02,
        "weighted_tim_pct": tim,
        "max_drawdown_account_pct": dd,
        "minimum_account_equity_usd": float(
            row["minimum_account_equity_usd"]
        ),
        "insolvent": insolvent,
        "actual_exit_fills": actual_exits,
        "unfilled_obligations": obligations,
        "future_htf_source_count": future,
        "fold_gate_pass": gate,
        "frozen_entry_schedule_sha256": row[
            "frozen_entry_schedule_sha256"
        ],
    }


def _rank_key(row: dict[str, Any]) -> tuple[Any, ...]:
    """Discovery-only robust ordering; final fields are structurally absent."""
    folds = row["discovery_fold_evidence"]
    strict = sum(bool(f["fold_gate_pass"]) for f in folds)
    solvent = sum(not bool(f["insolvent"]) for f in folds)
    tim_ok = sum(
        EXPOSURE_MIN <= float(f["weighted_tim_pct"]) <= EXPOSURE_MAX
        for f in folds
    )
    min_control = min(float(f["alpha_vs_same_entry_e02_pp"]) for f in folds)
    min_bh = min(float(f["alpha_vs_bh_pp"]) for f in folds)
    max_dd = max(float(f["max_drawdown_account_pct"]) for f in folds)
    max_tim_distance = max(
        abs(float(f["weighted_tim_pct"]) - 75.0) for f in folds
    )
    return (
        -int(strict == len(folds)),
        -strict,
        -solvent,
        -tim_ok,
        -min_control,
        -min_bh,
        max_dd,
        max_tim_distance,
        row["candidate_id"],
    )


def freeze_discovery(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Freeze top-4/family plus top-8 overall using discovery fields only."""
    forbidden = {
        "final_fold_evidence",
        "untouched_final_validation",
        "final_gate_pass",
    }
    if any(forbidden.intersection(row) for row in rows):
        raise ValueError("final-fold field present before discovery freeze")
    by_family: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_family.setdefault(row["exit_family"], []).append(row)
    selected: dict[str, dict[str, Any]] = {}
    for family_rows in by_family.values():
        for row in sorted(family_rows, key=_rank_key)[:4]:
            selected[row["candidate_id"]] = row
    for row in sorted(rows, key=_rank_key)[:8]:
        selected[row["candidate_id"]] = row
    return sorted(selected.values(), key=_rank_key)


def scan_e05_candidate(
    e05_module: Any,
    data: Any,
    ctx: dict[str, Any],
    events: Any,
    commission: float,
    slippage: float,
    side: str,
) -> dict[str, Any]:
    """Use E05's validated compiled execution route.

    ``vec_same_entry_e05_adapter`` owns signal construction and intentionally
    delegates accounting to the public module object it imports as
    ``execution``.  Keeping this adapter explicit prevents another accidental
    call to a nonexistent private ``e05._scan`` symbol.
    """
    return e05_module.execution._scan(
        data, ctx, events, commission, slippage, side
    )


def run(args: argparse.Namespace) -> Path:
    npz_dir = args.npz_dir.resolve()
    npz = npz_dir / "HAO.npz"
    npz_hash = _install_hash_bound_hao_exception(npz)

    # Import after the isolated audit hook so every consumer shares it.
    from tools import vec_band_ladder_walkforward as ladder
    from tools import vec_same_entry_e05_adapter as e05
    from tools import vec_same_entry_exit_adapter as shared
    from tools import vec_same_entry_peak_giveback_adapter as peak

    # ``vec_band_ladder_walkforward`` historically imports the campaign module
    # through the bare ``tools/`` path while this runner imports the package
    # name.  They can therefore be two Python module objects.  Bind the narrow
    # exception into the exact loader object consumed by ``_fold_contexts``.
    ladder.top.audit_npz = top.audit_npz
    source, data, htfs, frozen = shared._fold_contexts(
        args.artifact.resolve(), npz_dir, fold_mode="nested"
    )
    if source["manifest"]["symbol"] != "HAO":
        raise RuntimeError("this runner accepts only HAO")
    if source["manifest"]["side"] != "SHORT":
        raise RuntimeError("this runner accepts only HAO_SHORT")
    if [ctx["fold"] for ctx in frozen] != [1, 2, 3]:
        raise RuntimeError("expected exactly frozen folds 1,2,3")

    output = args.out_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    settings = preregistered_settings()
    exit_specs = {
        "EXIT_E02_DONCHIAN": {"timeframe": "4h", "lookback": 30},
        "EXIT_E05_DIVERGENCE_RETEST": {
            "pivot_radius": 3,
            "divergence_min": 5.0,
            "break_buffer_atr": 0.25,
            "rebound_atr": 0.5,
            "max_wait_bars": 12,
        },
        "EXIT_PEAK_GIVEBACK": {
            "arm_gain_pct": 4.0,
            "giveback_fraction": 0.5,
            "reduce_fraction": 1.0,
        },
    }
    prereg = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "symbol": "HAO",
        "side": "SHORT",
        "npz_sha256": npz_hash,
        "canonical_write_allowed": False,
        "live_write_allowed": False,
        "grid": {
            "global_scales": GLOBAL_SCALES,
            "delivered_caps": DELIVERED_CAPS,
            "tf_profiles": TF_PROFILES,
            "ladder_setting_count": len(settings),
        },
        "exit_specs": exit_specs,
        "candidate_count": len(settings) * len(exit_specs),
        "discovery_folds": DISCOVERY_FOLDS,
        "untouched_final_fold": FINAL_FOLD,
        "freeze_rule": "top4_each_exit_union_top8_overall_discovery_only",
        "gates": {
            "each_fold_solvent": True,
            "max_drawdown_account_pct_lt": 100.0,
            "strategy_gt_side_specific_bh": True,
            "strategy_gt_identical_entry_e02": True,
            "weighted_tim_pct": [EXPOSURE_MIN, EXPOSURE_MAX],
            "persistent_e10_e11": True,
            "future_htf_source_count": 0,
        },
    }
    (output / "preregistration.json").write_text(
        json.dumps(prereg, indent=2, sort_keys=True) + "\n"
    )

    commission = (
        float(source["manifest"]["commission_bps_one_way"]) / 10_000.0
    )
    slippage = (
        float(source["manifest"]["slippage_bps_one_way"]) / 10_000.0
    )
    exit_n = int(source["manifest"]["exit"]["n"])
    e05_setting = e05.E05Setting(**exit_specs["EXIT_E05_DIVERGENCE_RETEST"])
    e05_events = e05._events(data, htfs["4h"], "SHORT", e05_setting)
    peak_setting = peak.PeakGivebackSetting(
        **exit_specs["EXIT_PEAK_GIVEBACK"]
    )

    def evaluate_context(
        family: str,
        ctx: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        e02_book = shared.build_e02_book(data, ctx["signals"], htfs)
        control = shared.simulate(
            data,
            ctx["signals"],
            ctx["curve"],
            e02_book,
            ctx["left"],
            ctx["right"],
            commission,
            slippage,
            side="SHORT",
        )
        if family == "EXIT_E02_DONCHIAN":
            candidate = control
        elif family == "EXIT_E05_DIVERGENCE_RETEST":
            candidate = scan_e05_candidate(
                e05,
                data,
                ctx,
                e05_events,
                commission,
                slippage,
                "SHORT",
            )
        elif family == "EXIT_PEAK_GIVEBACK":
            candidate = peak._scan(
                data,
                htfs,
                ctx,
                peak_setting,
                commission,
                slippage,
                "SHORT",
            )
        else:
            raise AssertionError(family)
        return candidate, control

    discovery_rows: list[dict[str, Any]] = []
    context_cache: dict[tuple[int, int], dict[str, Any]] = {}
    for setting_index, setting in enumerate(settings):
        for ctx in frozen[:2]:
            context_cache[(setting_index, ctx["fold"])] = _scaled_context(
                data,
                htfs,
                ctx,
                setting,
                exit_n,
                "SHORT",
                ladder,
            )
        for family in exit_specs:
            evidence = []
            for fold in DISCOVERY_FOLDS:
                candidate, control = evaluate_context(
                    family, context_cache[(setting_index, fold)]
                )
                evidence.append(_normalize(candidate, control, fold, family))
            discovery_rows.append(
                {
                    "candidate_id": _candidate_id(family, setting),
                    "exit_family": family,
                    "ladder_setting_index": setting_index,
                    "ladder_setting": setting,
                    "discovery_fold_evidence": evidence,
                    "discovery_all_folds_strict": all(
                        row["fold_gate_pass"] for row in evidence
                    ),
                }
            )

    frozen_selection = freeze_discovery(discovery_rows)
    freeze_receipt = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "selection_rule": prereg["freeze_rule"],
        "candidate_count": len(discovery_rows),
        "frozen_count": len(frozen_selection),
        "candidate_ids": [row["candidate_id"] for row in frozen_selection],
        "selection_sha256": hashlib.sha256(
            "\n".join(
                row["candidate_id"] for row in frozen_selection
            ).encode()
        ).hexdigest(),
        "discovery_rows": frozen_selection,
        "final_fold_metrics_present": False,
    }
    (output / "discovery_freeze.json").write_text(
        json.dumps(freeze_receipt, indent=2, sort_keys=True) + "\n"
    )

    final_context_cache: dict[int, dict[str, Any]] = {}
    revealed = []
    for row in frozen_selection:
        setting_index = int(row["ladder_setting_index"])
        if setting_index not in final_context_cache:
            final_context_cache[setting_index] = _scaled_context(
                data,
                htfs,
                frozen[2],
                settings[setting_index],
                exit_n,
                "SHORT",
                ladder,
            )
        candidate, control = evaluate_context(
            row["exit_family"], final_context_cache[setting_index]
        )
        final = _normalize(
            candidate, control, FINAL_FOLD, row["exit_family"]
        )
        revealed.append(
            {
                **row,
                "untouched_final_validation": final,
                "all_three_folds_strict": bool(
                    row["discovery_all_folds_strict"]
                    and final["fold_gate_pass"]
                ),
            }
        )

    all_insolvencies = [
        {
            "candidate_id": row["candidate_id"],
            "exit_family": row["exit_family"],
            "ladder_setting": row["ladder_setting"],
            "fold": evidence["fold"],
            "minimum_account_equity_usd": evidence[
                "minimum_account_equity_usd"
            ],
            "max_drawdown_account_pct": evidence[
                "max_drawdown_account_pct"
            ],
            "strategy_return_pct": evidence["strategy_return_pct"],
            "weighted_tim_pct": evidence["weighted_tim_pct"],
        }
        for row in discovery_rows
        for evidence in row["discovery_fold_evidence"]
        if evidence["insolvent"]
    ]
    all_insolvencies.extend(
        {
            "candidate_id": row["candidate_id"],
            "exit_family": row["exit_family"],
            "ladder_setting": row["ladder_setting"],
            "fold": FINAL_FOLD,
            "minimum_account_equity_usd": row[
                "untouched_final_validation"
            ]["minimum_account_equity_usd"],
            "max_drawdown_account_pct": row[
                "untouched_final_validation"
            ]["max_drawdown_account_pct"],
            "strategy_return_pct": row["untouched_final_validation"][
                "strategy_return_pct"
            ],
            "weighted_tim_pct": row["untouched_final_validation"][
                "weighted_tim_pct"
            ],
        }
        for row in revealed
        if row["untouched_final_validation"]["insolvent"]
    )
    result = {
        "manifest": {
            **prereg,
            "source_artifact": str(args.artifact.resolve()),
            "source_artifact_result_sha256": _sha256(
                args.artifact.resolve() / "result.json"
            ),
            "npz_sha256_after": _sha256(npz),
            "matrix_eligible": False,
            "exact_replay_allowed": False,
            "exact_replay_blocker": (
                "isolated HAO final fold contains 15m-derived 5m rows; "
                "no replay spec may be emitted until exact execution delays "
                "containing-15m OHLC to parent-close availability"
            ),
        },
        "discovery_candidate_count": len(discovery_rows),
        "discovery_all_folds_strict_count": sum(
            row["discovery_all_folds_strict"] for row in discovery_rows
        ),
        "frozen_count": len(revealed),
        "all_three_folds_strict_count": sum(
            row["all_three_folds_strict"] for row in revealed
        ),
        "frozen_results": revealed,
        "discovery_ineligible_gray": discovery_rows,
        "insolvency_attribution": all_insolvencies,
    }
    (output / "result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )
    data.z.close()
    print(
        json.dumps(
            {
                "output": str(output),
                "discovery_candidates": len(discovery_rows),
                "discovery_strict": result[
                    "discovery_all_folds_strict_count"
                ],
                "frozen": len(revealed),
                "all_three_folds_strict": result[
                    "all_three_folds_strict_count"
                ],
                "insolvency_rows": len(all_insolvencies),
            },
            sort_keys=True,
        )
    )
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--npz-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
