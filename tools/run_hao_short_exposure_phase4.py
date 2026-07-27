#!/usr/bin/env python3
"""Sealed HAO_SHORT exposure/persistence phase-4 study.

This campaign starts only from the phase-3 V4 gray diagnostic:
F01_BEAR_REGIME, L_EXACT_0875_CAP6, with fixed E02/E05 exits.  It changes no
entry/exit geometry.  A small preregistered set of coherent exposure policies
tests delivered ladder scale/cap, a staged first position, a resting reclaim
obligation, and at most one completed-1h bearish-impulse add per position.

Discovery folds 1-2 are serialized and frozen before FINAL can be evaluated.
FINAL and an exact schedule audit are structurally unreachable unless one row
passes every discovery gate.  The runner cannot write canonical data, live
configuration, matrix cells, or green fleet evidence.
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
from typing import Any

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import run_hao_short_native_phase3 as phase3  # noqa: E402
from tools import run_hao_solvency_ladder_grid as solvency  # noqa: E402
from tools import vec_band_ladder_walkforward as ladder  # noqa: E402
from tools import vec_same_entry_e05_adapter as e05  # noqa: E402
from tools import vec_same_entry_exit_adapter as shared  # noqa: E402
from tools.research_availability_clock import (  # noqa: E402
    CLOCK_CONTRACT,
    next_strictly_later_index,
)
from tools.research_fill_contract import adverse_fill_price  # noqa: E402


CONTRACT = "HAO_SHORT_EXPOSURE_PHASE4_V1"
SOURCE_PHASE3_CONTRACT = "HAO_SHORT_NATIVE_PHASE3_V4"
EXPECTED_NPZ_SHA256 = solvency.EXPECTED_HAO_NPZ_SHA256
ACCOUNT_USD = 10_000.0
BASE_USD = 2_000.0
CAPACITY_USD = 16_000.0
EXPOSURE_MIN = 70.0
EXPOSURE_MAX = 80.0
DISCOVERY_FOLDS = (1, 2)
FINAL_FOLD = 3
ENTRY_FILTER = phase3.ENTRY_FILTERS[0]
EXIT_FAMILIES = ("EXIT_E02_DONCHIAN", "EXIT_E05_DIVERGENCE_RETEST")


@dataclasses.dataclass(frozen=True)
class ExposurePolicy:
    """One coherent exposure policy; values are position-multiple targets."""

    label: str
    ladder_scale: float
    ladder_cap_mult: float
    initial_seed_mult: float
    impulse_stage_target_mult: float
    resting_reclaim: bool
    impulse_add_mult: float
    max_impulse_adds_per_position: int

    def ladder_setting(self) -> dict[str, Any]:
        return {
            "global_scale": self.ladder_scale,
            "delivered_cap_mult": self.ladder_cap_mult,
            "tf_profile": "EXACT",
            "tf_factors": dict(solvency.TF_PROFILES["EXACT"]),
        }


# This registry was fixed before discovery. P00 is the phase-3 diagnostic.
# The rest are coherent persistence steps, not a Cartesian parameter search.
EXPOSURE_POLICIES = (
    ExposurePolicy("P00_SOURCE_0875_C6", 0.875, 6.0, 0.0, 0.0, False, 0.0, 0),
    ExposurePolicy("P01_SCALE_100_C7", 1.0, 7.0, 0.0, 0.0, False, 0.0, 0),
    ExposurePolicy("P02_SCALE_100_C8", 1.0, 8.0, 0.0, 0.0, False, 0.0, 0),
    ExposurePolicy("P03_SEED4_STAGE6", 0.875, 8.0, 4.0, 6.0, False, 0.0, 0),
    ExposurePolicy("P04_SEED4_STAGE7", 0.875, 8.0, 4.0, 7.0, False, 0.0, 0),
    ExposurePolicy("P05_SEED6_STAGE8", 0.875, 8.0, 6.0, 8.0, False, 0.0, 0),
    ExposurePolicy("P06_SEED7_STAGE8", 0.875, 8.0, 7.0, 8.0, False, 0.0, 0),
    ExposurePolicy("P07_SEED4_STAGE6_REST", 0.875, 8.0, 4.0, 6.0, True, 0.0, 0),
    ExposurePolicy("P08_SEED4_STAGE7_REST", 0.875, 8.0, 4.0, 7.0, True, 0.0, 0),
    ExposurePolicy("P09_SEED6_STAGE8_REST", 0.875, 8.0, 6.0, 8.0, True, 0.0, 0),
    ExposurePolicy("P10_SEED4_STAGE6_REST_ADD1", 0.875, 8.0, 4.0, 6.0, True, 1.0, 1),
    ExposurePolicy("P11_SEED4_STAGE7_REST_ADD1", 0.875, 8.0, 4.0, 7.0, True, 1.0, 1),
    ExposurePolicy("P12_SEED6_STAGE8_REST_ADD1", 0.875, 8.0, 6.0, 8.0, True, 1.0, 1),
)


def _sha(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            payload, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def candidate_id(policy: ExposurePolicy, exit_family: str) -> str:
    return _sha(
        {
            "contract": CONTRACT,
            "entry_filter": ENTRY_FILTER.label,
            "policy": dataclasses.asdict(policy),
            "exit_family": exit_family,
        }
    )[:16]


def preregistered_candidates() -> list[dict[str, Any]]:
    return [
        {
            "candidate_id": candidate_id(policy, family),
            "policy": dataclasses.asdict(policy),
            "entry_filter": ENTRY_FILTER.label,
            "exit_family": family,
        }
        for policy in EXPOSURE_POLICIES
        for family in EXIT_FAMILIES
    ]


def build_bear_impulse(
    data: Any,
    htfs: dict[str, Any],
    f01_gate: np.ndarray,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Completed-1h downside impulse; it can add only to an existing SHORT."""
    h1 = htfs["1h"]
    event = np.zeros(len(data.ts), dtype=np.uint8)
    source = np.zeros(len(data.ts), dtype=np.int64)
    source_checks = future_sources = 0
    for j, row0 in enumerate(h1.event_index):
        i = int(row0)
        source_ts = int(h1.source_ts[j])
        source_checks += 1
        future_sources += int(source_ts > int(data.ts[i]))
        if j < 2:
            continue
        atr = float(h1.atr[j])
        if not math.isfinite(atr) or atr <= 0 or not bool(f01_gate[i]):
            continue
        down_body = (float(h1.open[j]) - float(h1.close[j])) / atr
        lower_structure = bool(
            h1.high[j] < h1.high[j - 1]
            and h1.low[j] < h1.low[j - 1]
            and h1.close[j] < h1.close[j - 1]
        )
        if down_body >= 0.25 and lower_structure:
            event[i] = 1
            source[i] = source_ts
    if future_sources:
        raise RuntimeError(f"phase4 impulse has {future_sources} future sources")
    return event, {
        "completed_1h_source_checks": source_checks,
        "future_completed_1h_sources": future_sources,
        "impulse_event_count": int(np.count_nonzero(event)),
    }


def reclaim_fill_allowed(*, opening_fill_px: float, stored_reference: float) -> bool:
    """SHORT reclaim is never executed above its stored reference."""
    return bool(
        math.isfinite(opening_fill_px)
        and math.isfinite(stored_reference)
        and opening_fill_px <= stored_reference + 1e-12
    )


def simulate(
    data: Any,
    ctx: dict[str, Any],
    entry_gate: np.ndarray,
    impulse_event: np.ndarray,
    impulse_source: np.ndarray,
    e05_events: Any,
    exit_family: str,
    policy: ExposurePolicy,
    commission_rate: float,
    slippage_rate: float,
    *,
    emit_schedule: bool = False,
) -> dict[str, Any]:
    """Execute a policy on the shared completed-parent availability clock."""
    left, right = int(ctx["left"]), int(ctx["right"])
    if right - left < 100:
        raise ValueError("phase4 fold too short")
    signals = ctx["signals"]
    curve = ctx["curve"]
    exits, exit_ref, exit_source = phase3._exit_arrays(
        data, ctx, e05_events, exit_family
    )
    cash = ACCOUNT_USD
    qty = 0.0
    prior_exit_notional = 0.0
    reclaim_level = math.nan
    pending: dict[str, Any] | None = None
    minimum_equity = peak_equity = ACCOUNT_USD
    max_dd = weighted = requested = filled = 0.0
    peak_notional = 0.0
    held = entries = exits_filled = reclaims = ladder_adds = clamps = 0
    impulse_adds = staged_adds = rejected_above_reference = 0
    vetoed_requests = vetoed_reclaims = future_sources = 0
    impulse_adds_this_position = 0
    schedule: list[dict[str, Any]] = []

    def active() -> bool:
        return qty < -1e-12

    def schedule_request(
        kind: str,
        signal_i: int,
        *,
        reason: str,
        request_notional: float = 0.0,
        reference: float = math.nan,
        source: int = 0,
        absolute_target: bool = False,
    ) -> None:
        nonlocal pending
        if pending is not None:
            return
        fill_i = next_strictly_later_index(data.ts, signal_i, right)
        if fill_i is None:
            return
        pending = {
            "kind": kind,
            "signal_i": int(signal_i),
            "fill_i": int(fill_i),
            "reason": reason,
            "request_notional": float(request_notional),
            "reference": float(reference),
            "source": int(source),
            "absolute_target": bool(absolute_target),
        }

    def clock(prefix: str, i: int) -> dict[str, int]:
        return {
            f"{prefix}_ts": int(data.ts[i]),
            f"{prefix}_availability_ts": int(data.ts[i]),
            f"{prefix}_source_ts": int(data.source_ts[i]),
            f"{prefix}_source_row_index": int(data.full_indices[i]),
            f"{prefix}_clock_index": int(i),
        }

    for i in range(left, right):
        op, close = float(data.open[i]), float(data.close[i])
        if pending is not None and i == pending["fill_i"]:
            item = pending
            pending = None
            if item["kind"] == "EXIT" and active():
                px = adverse_fill_price(
                    op,
                    position_side="SHORT",
                    opening=False,
                    slippage_rate=slippage_rate,
                )
                q = abs(qty)
                notional = q * px
                cash -= notional + commission_rate * notional
                prior_exit_notional = min(CAPACITY_USD, notional)
                ref = float(item["reference"])
                reclaim_level = min(px, ref) if math.isfinite(ref) else px
                qty = 0.0
                exits_filled += 1
                impulse_adds_this_position = 0
                if emit_schedule:
                    schedule.append(
                        {
                            "type": "EXIT",
                            "reason": item["reason"],
                            "fill_px": px,
                            "quantity": q,
                            "filled_notional_usd": notional,
                            "position_qty_after_fill": 0.0,
                            "entry_capacity_usd": CAPACITY_USD,
                            **clock("signal", item["signal_i"]),
                            **clock("fill", i),
                        }
                    )
            elif item["kind"] == "ENTRY":
                px = adverse_fill_price(
                    op,
                    position_side="SHORT",
                    opening=True,
                    slippage_rate=slippage_rate,
                )
                if (
                    item["reason"] in {"FILTERED_RECLAIM", "RESTING_RECLAIM"}
                    and not reclaim_fill_allowed(
                        opening_fill_px=px,
                        stored_reference=float(item["reference"]),
                    )
                ):
                    rejected_above_reference += 1
                    continue
                current = abs(qty) * px
                target = float(item["request_notional"])
                want = (
                    max(0.0, target - current)
                    if item["absolute_target"]
                    else target
                )
                actual = min(want, max(0.0, CAPACITY_USD - current))
                requested += max(0.0, want)
                filled += actual
                clamps += int(actual + 1e-9 < want)
                if actual > 0:
                    add_q = actual / px
                    prior_q = abs(qty)
                    cash += actual - commission_rate * actual
                    qty -= add_q
                    peak_notional = max(peak_notional, abs(qty) * px)
                    entries += 1
                    reclaims += int(
                        item["reason"] in {"FILTERED_RECLAIM", "RESTING_RECLAIM"}
                    )
                    ladder_adds += int(
                        item["reason"]
                        in {"LADDER_ADD", "FILTERED_LADDER_ENTRY"}
                    )
                    impulse_adds += int(item["reason"] == "BEAR_IMPULSE_ADD")
                    staged_adds += int(item["reason"] == "BEAR_IMPULSE_STAGE")
                    if item["reason"] == "BEAR_IMPULSE_ADD":
                        impulse_adds_this_position += 1
                    if emit_schedule:
                        schedule.append(
                            {
                                "type": "ENTRY" if prior_q <= 0 else "AUGMENT",
                                "reason": item["reason"],
                                "fill_px": px,
                                "quantity": add_q,
                                "requested_notional_usd": want,
                                "filled_notional_usd": actual,
                                "post_fill_notional_usd": abs(qty) * px,
                                "entry_capacity_usd": CAPACITY_USD,
                                "semantics": (
                                    "target" if item["absolute_target"] else "add"
                                ),
                                **clock("signal", item["signal_i"]),
                                **clock("fill", i),
                            }
                        )
                    if item["reason"] in {
                        "FILTERED_RECLAIM",
                        "RESTING_RECLAIM",
                    }:
                        reclaim_level = math.nan

        equity = cash + qty * close
        minimum_equity = min(minimum_equity, equity)
        peak_equity = max(peak_equity, equity)
        if peak_equity > 0:
            max_dd = max(max_dd, 100.0 * (peak_equity - equity) / peak_equity)
        held += int(active())
        weighted += min(CAPACITY_USD, abs(qty) * close) / CAPACITY_USD
        if pending is not None or i + 1 >= right:
            continue

        if active() and exits[i]:
            source = int(exit_source[i])
            future_sources += int(source > int(data.ts[i]))
            ref = float(exit_ref[i])
            if not math.isfinite(ref):
                ref = float(data.low[i])
            schedule_request(
                "EXIT",
                i,
                reason=exit_family,
                reference=ref,
                source=source,
            )
            continue

        allowed = bool(entry_gate[i])
        if not active() and math.isfinite(reclaim_level):
            touched = bool(float(data.low[i]) <= reclaim_level or close <= reclaim_level)
            reclaim_allowed = allowed or policy.resting_reclaim
            if touched:
                if reclaim_allowed:
                    schedule_request(
                        "ENTRY",
                        i,
                        reason=(
                            "RESTING_RECLAIM"
                            if policy.resting_reclaim
                            else "FILTERED_RECLAIM"
                        ),
                        request_notional=max(BASE_USD, prior_exit_notional),
                        reference=reclaim_level,
                        absolute_target=True,
                    )
                else:
                    vetoed_reclaims += 1
                continue

        mult = float(signals.entry_mult[i])
        if mult > 0:
            if not allowed:
                vetoed_requests += 1
                continue
            target_mult = mult
            if not active() and policy.initial_seed_mult > 0:
                target_mult = max(target_mult, policy.initial_seed_mult)
            schedule_request(
                "ENTRY",
                i,
                reason="LADDER_ADD" if active() else "FILTERED_LADDER_ENTRY",
                request_notional=BASE_USD * target_mult,
                absolute_target=curve.semantics == "target",
            )
            continue

        if active() and allowed and bool(impulse_event[i]):
            source = int(impulse_source[i])
            future_sources += int(source > int(data.ts[i]))
            current_mult = abs(qty) * close / BASE_USD
            if (
                policy.impulse_stage_target_mult > 0
                and current_mult + 1e-9 < policy.impulse_stage_target_mult
            ):
                schedule_request(
                    "ENTRY",
                    i,
                    reason="BEAR_IMPULSE_STAGE",
                    request_notional=BASE_USD * policy.impulse_stage_target_mult,
                    source=source,
                    absolute_target=True,
                )
            elif (
                policy.impulse_add_mult > 0
                and impulse_adds_this_position
                < policy.max_impulse_adds_per_position
            ):
                schedule_request(
                    "ENTRY",
                    i,
                    reason="BEAR_IMPULSE_ADD",
                    request_notional=BASE_USD * policy.impulse_add_mult,
                    source=source,
                    absolute_target=False,
                )

    if active():
        px = adverse_fill_price(
            float(data.close[right - 1]),
            position_side="SHORT",
            opening=False,
            slippage_rate=slippage_rate,
        )
        q = abs(qty)
        notional = q * px
        cash -= notional + commission_rate * notional
        if emit_schedule:
            schedule.append(
                {
                    "type": "EXIT",
                    "reason": "TERMINAL_LIQUIDATION",
                    "fill_px": px,
                    "quantity": q,
                    "filled_notional_usd": notional,
                    "position_qty_after_fill": 0.0,
                    "entry_capacity_usd": CAPACITY_USD,
                    **clock("signal", right - 1),
                    **clock("fill", right - 1),
                }
            )
        qty = 0.0
    minimum_equity = min(minimum_equity, cash)
    pnl = cash - ACCOUNT_USD
    bh_entry = adverse_fill_price(
        float(data.open[left]),
        position_side="SHORT",
        opening=True,
        slippage_rate=slippage_rate,
    )
    bh_exit = adverse_fill_price(
        float(data.close[right - 1]),
        position_side="SHORT",
        opening=False,
        slippage_rate=slippage_rate,
    )
    bh_pnl = (
        BASE_USD * (bh_entry - bh_exit) / bh_entry
        - 2.0 * commission_rate * BASE_USD
    )
    rows = right - left
    result = {
        "capital_return_pct": 100.0 * pnl / BASE_USD,
        "account_return_pct": 100.0 * pnl / ACCOUNT_USD,
        "bh_capital_return_pct": 100.0 * bh_pnl / BASE_USD,
        "opportunity_benchmark_pct": max(0.0, 100.0 * bh_pnl / BASE_USD),
        "alpha_vs_bh_pp": 100.0 * (pnl - bh_pnl) / BASE_USD,
        "binary_tim_pct": 100.0 * held / rows,
        "exposure_weighted_tim_pct": 100.0 * weighted / rows,
        "max_drawdown_account_pct": max_dd,
        "minimum_account_equity_usd": minimum_equity,
        "insolvent": minimum_equity <= 0.0,
        "peak_post_fill_notional_usd": peak_notional,
        "entry_capacity_breach": peak_notional > CAPACITY_USD + 1e-6,
        "entry_fills": entries,
        "exit_fills": exits_filled,
        "reclaim_reentries": reclaims,
        "ladder_adds": ladder_adds,
        "staged_adds": staged_adds,
        "impulse_adds": impulse_adds,
        "vetoed_ladder_requests": vetoed_requests,
        "vetoed_reclaim_rows": vetoed_reclaims,
        "reclaim_above_reference_rejections": rejected_above_reference,
        "reclaim_obligation_open_at_end": math.isfinite(reclaim_level),
        "requested_notional_usd": requested,
        "filled_notional_usd": filled,
        "clamp_count": clamps,
        "future_htf_source_count": future_sources,
        "rows": rows,
        "availability_clock": CLOCK_CONTRACT,
        "fill_contract": "first_strictly_later_availability_adverse_SHORT",
        "reclaim_contract": "current_touch_and_opening_fill_lte_stored_reference",
    }
    if emit_schedule:
        result["schedule_events"] = schedule
    return result


def normalize_fold(
    row: dict[str, Any],
    *,
    fold: int,
    source_phase3: dict[str, Any],
    same_entry_e02: dict[str, Any],
    exit_family: str,
) -> dict[str, Any]:
    strategy = float(row["capital_return_pct"])
    opportunity = max(0.0, float(row["bh_capital_return_pct"]))
    same_entry = float(same_entry_e02["capital_return_pct"])
    source = float(source_phase3["capital_return_pct"])
    tim = float(row["exposure_weighted_tim_pct"])
    common = bool(
        strategy > opportunity
        and strategy > source
        and EXPOSURE_MIN <= tim <= EXPOSURE_MAX
        and not row["insolvent"]
        and not row["entry_capacity_breach"]
        and row["minimum_account_equity_usd"] > 0
        and row["max_drawdown_account_pct"] < 100.0
        and row["entry_fills"] > 0
        and row["exit_fills"] > 0
        and row["future_htf_source_count"] == 0
        and not row["reclaim_obligation_open_at_end"]
    )
    exit_gate = bool(
        exit_family == "EXIT_E02_DONCHIAN" or strategy > same_entry
    )
    return {
        "fold": fold,
        "strategy_return_pct": strategy,
        "bh_return_pct": float(row["bh_capital_return_pct"]),
        "opportunity_benchmark_pct": opportunity,
        "source_phase3_return_pct": source,
        "same_entry_e02_return_pct": same_entry,
        "exposure_contribution_vs_phase3_pp": strategy - source,
        "exit_contribution_vs_same_entry_e02_pp": strategy - same_entry,
        "weighted_tim_pct": tim,
        "binary_tim_pct": float(row["binary_tim_pct"]),
        "max_drawdown_account_pct": float(row["max_drawdown_account_pct"]),
        "minimum_account_equity_usd": float(row["minimum_account_equity_usd"]),
        "peak_post_fill_notional_usd": float(row["peak_post_fill_notional_usd"]),
        "entry_fills": int(row["entry_fills"]),
        "exit_fills": int(row["exit_fills"]),
        "reclaim_reentries": int(row["reclaim_reentries"]),
        "staged_adds": int(row["staged_adds"]),
        "impulse_adds": int(row["impulse_adds"]),
        "reclaim_above_reference_rejections": int(
            row["reclaim_above_reference_rejections"]
        ),
        "future_htf_source_count": int(row["future_htf_source_count"]),
        "solvent": not bool(row["insolvent"]),
        "capacity_ok": not bool(row["entry_capacity_breach"]),
        "tim_gate_pass": EXPOSURE_MIN <= tim <= EXPOSURE_MAX,
        "beats_short_bh_or_cash": strategy > opportunity,
        "beats_phase3_same_exit_control": strategy > source,
        "beats_same_entry_e02": (
            None if exit_family == "EXIT_E02_DONCHIAN" else strategy > same_entry
        ),
        "fold_gate_pass": common and exit_gate,
    }


def rank_key(row: dict[str, Any]) -> tuple[Any, ...]:
    folds = row["discovery_fold_evidence"]
    strict = sum(bool(x["fold_gate_pass"]) for x in folds)
    tim_distance = max(abs(float(x["weighted_tim_pct"]) - 75.0) for x in folds)
    min_bench = min(
        float(x["strategy_return_pct"]) - float(x["opportunity_benchmark_pct"])
        for x in folds
    )
    min_source = min(
        float(x["exposure_contribution_vs_phase3_pp"]) for x in folds
    )
    min_exit = min(
        float(x["exit_contribution_vs_same_entry_e02_pp"]) for x in folds
    )
    max_dd = max(float(x["max_drawdown_account_pct"]) for x in folds)
    return (
        -int(strict == len(folds)),
        -strict,
        tim_distance,
        -min_bench,
        -min_source,
        -min_exit,
        max_dd,
        row["candidate_id"],
    )


def freeze_discovery(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    forbidden = {"untouched_final", "final_gate_pass", "exact_v3"}
    if any(forbidden.intersection(row) for row in rows):
        raise ValueError("final/exact field present before phase4 freeze")
    strict = [row for row in rows if row["discovery_all_folds_strict"]]
    if strict:
        return [sorted(strict, key=rank_key)[0]]
    selected = []
    for family in EXIT_FAMILIES:
        family_rows = [row for row in rows if row["exit_family"] == family]
        selected.append(sorted(family_rows, key=rank_key)[0])
    return sorted(selected, key=rank_key)


def final_evaluation_candidates(
    frozen: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [row for row in frozen if row["discovery_all_folds_strict"]]


def run(args: argparse.Namespace) -> Path:
    artifact = args.artifact.resolve()
    phase3_result_path = args.phase3_result.resolve()
    npz_dir = args.npz_dir.resolve()
    npz_path = npz_dir / "HAO.npz"
    phase3_source = json.loads(phase3_result_path.read_text())
    if (
        phase3_source.get("manifest", {}).get("contract")
        != SOURCE_PHASE3_CONTRACT
        or int(phase3_source.get("discovery_strict_count", -1)) != 0
        or phase3_source.get("manifest", {}).get("final_fold_status")
        != "SEALED_NOT_EVALUATED"
    ):
        raise RuntimeError("phase4 requires the accepted sealed phase3 V4 result")
    phase3_gray = phase3_source.get("frozen_discovery_only_gray", [])
    if len(phase3_gray) != 2 or {
        x.get("exit_family") for x in phase3_gray
    } != set(EXIT_FAMILIES):
        raise RuntimeError("phase3 V4 frozen gray diagnostics are not intact")
    for row in phase3_gray:
        if (
            row.get("entry_filter", {}).get("label") != ENTRY_FILTER.label
            or row.get("ladder", {}).get("label") != "L_EXACT_0875_CAP6"
        ):
            raise RuntimeError("phase3 V4 diagnostic identity drift")
    before_hash = solvency._install_hash_bound_hao_exception(npz_path)
    ladder.top.audit_npz = solvency.top.audit_npz
    source, data, htfs, base_contexts = shared._fold_contexts(
        artifact, npz_dir, fold_mode="nested"
    )
    try:
        if source["manifest"]["symbol"] != "HAO" or source["manifest"]["side"] != "SHORT":
            raise RuntimeError("phase4 accepts only HAO_SHORT")
        if [int(c["fold"]) for c in base_contexts] != [1, 2, 3]:
            raise RuntimeError("phase4 requires frozen folds 1,2,3")
        if before_hash != EXPECTED_NPZ_SHA256:
            raise RuntimeError("unexpected isolated HAO hash")
        if getattr(data, "availability_clock", {}).get("kind") != CLOCK_CONTRACT:
            raise RuntimeError("shared parent-close clock is not active")

        output = args.out_dir.resolve()
        output.mkdir(parents=True, exist_ok=False)
        candidates = preregistered_candidates()
        prereg = {
            "contract": CONTRACT,
            "source_phase3_contract": SOURCE_PHASE3_CONTRACT,
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "symbol_side": "HAO_SHORT",
            "source_artifact": str(artifact),
            "source_artifact_result_sha256": _sha256_file(artifact / "result.json"),
            "accepted_phase3_result": str(phase3_result_path),
            "accepted_phase3_result_sha256": _sha256_file(phase3_result_path),
            "isolated_npz": str(npz_path),
            "isolated_npz_sha256": before_hash,
            "canonical_write_allowed": False,
            "live_write_allowed": False,
            "matrix_write_allowed": False,
            "green_fleet_write_allowed": False,
            "fixed_entry_filter": ENTRY_FILTER.label,
            "fixed_source_ladder": "L_EXACT_0875_CAP6",
            "fixed_exits": list(EXIT_FAMILIES),
            "exposure_policies": [dataclasses.asdict(x) for x in EXPOSURE_POLICIES],
            "candidate_count": len(candidates),
            "discovery_folds": DISCOVERY_FOLDS,
            "final_fold_sealed_until_freeze": FINAL_FOLD,
            "gates": {
                "each_fold_strategy_gt_max_short_bh_cash": True,
                "each_fold_strategy_gt_phase3_same_exit_control": True,
                "non_e02_strategy_gt_same_entry_e02": True,
                "weighted_tim_pct": [EXPOSURE_MIN, EXPOSURE_MAX],
                "minimum_account_equity_usd_gt": 0,
                "max_drawdown_account_pct_lt": 100,
                "peak_post_fill_notional_usd_lte": CAPACITY_USD,
                "future_completed_htf_source_count": 0,
                "reclaim_fill_lte_stored_reference": True,
                "first_strictly_later_availability_fill": True,
                "adverse_short_fills_and_costs": True,
            },
            "commission_bps_one_way": float(
                source["manifest"]["commission_bps_one_way"]
            ),
            "slippage_bps_one_way": float(
                source["manifest"]["slippage_bps_one_way"]
            ),
            "availability_clock": data.availability_clock,
            "selection_rule": (
                "best strict discovery row; otherwise best gray diagnostic per "
                "exit family; no FINAL or exact fields for gray rows"
            ),
        }
        prereg["sha256"] = _sha(prereg)
        (output / "PREREGISTRATION.json").write_text(
            json.dumps(prereg, indent=2, sort_keys=True) + "\n"
        )

        gates, feature_audit = phase3.build_entry_gates(data, htfs)
        f01_gate = gates[ENTRY_FILTER.label]
        impulse_event, impulse_audit = build_bear_impulse(data, htfs, f01_gate)
        impulse_source = np.zeros(len(data.ts), dtype=np.int64)
        h1 = htfs["1h"]
        impulse_source[np.asarray(h1.event_index, dtype=np.int64)] = np.asarray(
            h1.source_ts, dtype=np.int64
        )
        e05_events = e05._events(data, htfs["4h"], "SHORT", phase3.E05_SETTING)
        commission = prereg["commission_bps_one_way"] / 10_000.0
        slippage = prereg["slippage_bps_one_way"] / 10_000.0

        contexts: dict[tuple[str, int], dict[str, Any]] = {}
        for policy in EXPOSURE_POLICIES:
            for base in base_contexts:
                ctx = solvency._scaled_context(
                    data,
                    htfs,
                    base,
                    policy.ladder_setting(),
                    int(source["manifest"]["exit"]["n"]),
                    "SHORT",
                    ladder,
                )
                e02_source = np.zeros(len(data.ts), dtype=np.int64)
                h4 = htfs["4h"]
                e02_source[np.asarray(h4.event_index, dtype=np.int64)] = np.asarray(
                    h4.source_ts, dtype=np.int64
                )
                ctx["_e02_exit_source"] = e02_source
                contexts[(policy.label, int(base["fold"]))] = ctx

        source_policy = EXPOSURE_POLICIES[0]
        source_controls: dict[tuple[str, int], dict[str, Any]] = {}
        for family in EXIT_FAMILIES:
            for fold in DISCOVERY_FOLDS:
                source_controls[(family, fold)] = phase3.simulate(
                    data,
                    contexts[(source_policy.label, fold)],
                    f01_gate,
                    e05_events,
                    family,
                    commission,
                    slippage,
                )
                accepted = next(
                    x
                    for x in phase3_gray
                    if x["exit_family"] == family
                )["discovery_fold_evidence"][fold - 1]
                got = source_controls[(family, fold)]
                if (
                    abs(
                        float(got["capital_return_pct"])
                        - float(accepted["strategy_return_pct"])
                    )
                    > 1e-9
                    or abs(
                        float(got["exposure_weighted_tim_pct"])
                        - float(accepted["weighted_tim_pct"])
                    )
                    > 1e-9
                ):
                    raise RuntimeError(
                        f"phase3 V4 source-control parity failed for {family} F{fold}"
                    )

        rows: list[dict[str, Any]] = []
        for policy in EXPOSURE_POLICIES:
            controls: dict[int, dict[str, Any]] = {}
            for fold in DISCOVERY_FOLDS:
                controls[fold] = simulate(
                    data,
                    contexts[(policy.label, fold)],
                    f01_gate,
                    impulse_event,
                    impulse_source,
                    e05_events,
                    "EXIT_E02_DONCHIAN",
                    policy,
                    commission,
                    slippage,
                )
            for family in EXIT_FAMILIES:
                evidence = []
                for fold in DISCOVERY_FOLDS:
                    raw = controls[fold] if family == EXIT_FAMILIES[0] else simulate(
                        data,
                        contexts[(policy.label, fold)],
                        f01_gate,
                        impulse_event,
                        impulse_source,
                        e05_events,
                        family,
                        policy,
                        commission,
                        slippage,
                    )
                    evidence.append(
                        normalize_fold(
                            raw,
                            fold=fold,
                            source_phase3=source_controls[(family, fold)],
                            same_entry_e02=controls[fold],
                            exit_family=family,
                        )
                    )
                rows.append(
                    {
                        "candidate_id": candidate_id(policy, family),
                        "policy": dataclasses.asdict(policy),
                        "entry_filter": ENTRY_FILTER.label,
                        "exit_family": family,
                        "discovery_fold_evidence": evidence,
                        "discovery_all_folds_strict": all(
                            x["fold_gate_pass"] for x in evidence
                        ),
                    }
                )

        discovery = {
            "preregistration_sha256": prereg["sha256"],
            "contains_final_or_exact_metrics": False,
            "rows": rows,
        }
        discovery["sha256"] = _sha(discovery)
        (output / "DISCOVERY_GRID.json").write_text(
            json.dumps(discovery, indent=2, sort_keys=True) + "\n"
        )
        frozen = freeze_discovery(rows)
        freeze = {
            "preregistration_sha256": prereg["sha256"],
            "discovery_grid_sha256": discovery["sha256"],
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "selected_on_discovery_only": True,
            "contains_final_or_exact_metrics": False,
            "strict_discovery_count": sum(
                x["discovery_all_folds_strict"] for x in rows
            ),
            "candidate_ids": [x["candidate_id"] for x in frozen],
            "rows": frozen,
        }
        freeze["sha256"] = _sha(freeze)
        (output / "DISCOVERY_FREEZE.json").write_text(
            json.dumps(freeze, indent=2, sort_keys=True) + "\n"
        )

        discovery_strict = final_evaluation_candidates(frozen)
        revealed = []
        for item in discovery_strict:
            policy = next(
                x for x in EXPOSURE_POLICIES if x.label == item["policy"]["label"]
            )
            fold = FINAL_FOLD
            source_final = phase3.simulate(
                data,
                contexts[(source_policy.label, fold)],
                f01_gate,
                e05_events,
                item["exit_family"],
                commission,
                slippage,
            )
            e02_final = simulate(
                data,
                contexts[(policy.label, fold)],
                f01_gate,
                impulse_event,
                impulse_source,
                e05_events,
                "EXIT_E02_DONCHIAN",
                policy,
                commission,
                slippage,
            )
            final_raw = simulate(
                data,
                contexts[(policy.label, fold)],
                f01_gate,
                impulse_event,
                impulse_source,
                e05_events,
                item["exit_family"],
                policy,
                commission,
                slippage,
                emit_schedule=True,
            )
            final = normalize_fold(
                final_raw,
                fold=fold,
                source_phase3=source_final,
                same_entry_e02=e02_final,
                exit_family=item["exit_family"],
            )
            final_pass = bool(final["fold_gate_pass"])
            exact_v3 = None
            if final_pass:
                events = final_raw["schedule_events"]
                exact_v3 = {
                    "status": "SCHEDULE_FROZEN_REQUIRES_BACKTEST_V8_V3_REPLAY",
                    "eligible": True,
                    "schedule_event_count": len(events),
                    "schedule_sha256": _sha(events),
                    "promotion_allowed": False,
                }
            revealed.append(
                {
                    **item,
                    "untouched_final": final,
                    "all_three_folds_strict": final_pass,
                    "exact_v3": exact_v3,
                }
            )

        result = {
            "manifest": {
                **prereg,
                "npz_sha256_after": _sha256_file(npz_path),
                "matrix_eligible": False,
                "promotion_allowed": False,
                "canonical_or_live_write": False,
                "final_fold_evaluated": bool(discovery_strict),
                "final_fold_status": (
                    "EVALUATED_FROZEN_DISCOVERY_STRICT_ONLY"
                    if discovery_strict
                    else "SEALED_NOT_EVALUATED"
                ),
                "exact_replay_status": (
                    "FROZEN_FINAL_SCHEDULE_REQUIRES_BACKTEST_V8_V3"
                    if any(x["all_three_folds_strict"] for x in revealed)
                    else "NOT_RUN_DISCOVERY_GATE_FAILED"
                ),
            },
            "feature_audit": feature_audit,
            "impulse_audit": impulse_audit,
            "discovery_candidate_count": len(rows),
            "discovery_strict_count": sum(
                x["discovery_all_folds_strict"] for x in rows
            ),
            "frozen_count": len(frozen),
            "all_three_fold_strict_count": sum(
                x["all_three_folds_strict"] for x in revealed
            ),
            "frozen_results": revealed,
            "frozen_discovery_only_gray": frozen if not discovery_strict else [],
            "discovery_gray_rows": rows,
        }
        (output / "result.json").write_text(
            json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n"
        )
        print(
            json.dumps(
                {
                    "output": str(output),
                    "discovery_candidates": len(rows),
                    "discovery_strict": result["discovery_strict_count"],
                    "frozen": len(frozen),
                    "final_fold_evaluated": bool(discovery_strict),
                    "exact_replay_status": result["manifest"][
                        "exact_replay_status"
                    ],
                    "npz_unchanged": _sha256_file(npz_path) == before_hash,
                },
                sort_keys=True,
            )
        )
        return output
    finally:
        data.z.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--phase3-result", type=Path, required=True)
    parser.add_argument("--npz-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
