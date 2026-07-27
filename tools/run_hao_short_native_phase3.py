#!/usr/bin/env python3
"""Isolated, causal HAO_SHORT native-entry / E05 research campaign.

The campaign is deliberately incapable of updating canonical indicators,
Tradier configuration, the switch matrix, or path-fleet evidence.  It accepts
only the hash-bound HAO recovery NPZ and the already frozen ladder artifact.

Selection protocol:

* three solvency-safe ladder reductions are fixed from Bible §15.26;
* eight coherent SHORT-native entry filters are screened on discovery folds
  1-2 with E02 and the fixed E05 divergence/break/retest cover;
* every signal fills at the first *strictly later* parent-close availability
  batch with adverse SHORT commission/slippage;
* the discovery freeze is written before the final fold is evaluated; and
* exact v3 is eligible only for a row that passes every discovery and final
  gate.  A rejected row remains gray and cannot emit an exact schedule.

Entry and exit contributions are kept separate: a filtered E02 row is compared
with the same ladder's ungated E02 baseline, while its E05 row is compared with
the identical filtered-entry E02 control.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import math
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import run_hao_solvency_ladder_grid as solvency  # noqa: E402
from tools import vec_band_ladder_walkforward as ladder  # noqa: E402
from tools import vec_same_entry_e05_adapter as e05  # noqa: E402
from tools import vec_same_entry_exit_adapter as shared  # noqa: E402
from tools.research_availability_clock import (  # noqa: E402
    CLOCK_CONTRACT,
    next_strictly_later_index,
)
from tools.research_fill_contract import adverse_fill_price  # noqa: E402


CONTRACT = "HAO_SHORT_NATIVE_PHASE3_V4"
EXPECTED_NPZ_SHA256 = solvency.EXPECTED_HAO_NPZ_SHA256
ACCOUNT_USD = 10_000.0
BASE_USD = 2_000.0
CAPACITY_USD = 16_000.0
EXPOSURE_MIN = 70.0
EXPOSURE_MAX = 80.0
DISCOVERY_FOLDS = (1, 2)
FINAL_FOLD = 3


@dataclasses.dataclass(frozen=True)
class LadderProfile:
    label: str
    global_scale: float
    delivered_cap_mult: float
    tf_profile: str

    def setting(self) -> dict[str, Any]:
        return {
            "global_scale": self.global_scale,
            "delivered_cap_mult": self.delivered_cap_mult,
            "tf_profile": self.tf_profile,
            "tf_factors": dict(solvency.TF_PROFILES[self.tf_profile]),
        }


@dataclasses.dataclass(frozen=True)
class EntryFilter:
    label: str
    confirmation: str
    arm_hours: int
    min_velocity_atr: float = 0.0
    min_acceleration_atr: float = -1.0
    min_atr_ratio: float = 0.0


# These are fixed from the all-fold-solvent rows described in §15.26.  They
# span the high-exposure 1.0/6x result and one lower-risk 0.875/6x alternative
# without reopening the 75-setting ladder search.
LADDER_PROFILES = (
    LadderProfile("L_EXACT_100_CAP6", 1.0, 6.0, "EXACT"),
    LadderProfile("L_D75_100_CAP6", 1.0, 6.0, "D75"),
    LadderProfile("L_EXACT_0875_CAP6", 0.875, 6.0, "EXACT"),
)


# All active filters require the completed D and 4h bear regime.  The component
# labels are explicit so a failed family is still useful gray evidence.
ENTRY_FILTERS = (
    EntryFilter("F01_BEAR_REGIME", "STATE", 120),
    EntryFilter("F02_1H_LHLL", "LHLL", 72),
    EntryFilter("F03_1H_WT_ROLL", "WT_ROLL", 72),
    EntryFilter("F04_LHLL_OR_WT", "LHLL_OR_WT", 96),
    EntryFilter(
        "F05_DOWNSIDE_ACCEL",
        "ATR_ACCEL",
        72,
        min_velocity_atr=0.20,
        min_acceleration_atr=0.05,
        min_atr_ratio=1.05,
    ),
    EntryFilter("F06_FAILED_BULL_RECLAIM", "FAILED_RECLAIM", 120),
    EntryFilter(
        "F07_STRUCT_AND_ACCEL",
        "STRUCT_AND_ACCEL",
        96,
        min_velocity_atr=0.10,
        min_acceleration_atr=0.0,
        min_atr_ratio=0.95,
    ),
    EntryFilter(
        "F08_ANY_CONFIRMED_BEAR",
        "ANY",
        120,
        min_velocity_atr=0.15,
        min_acceleration_atr=0.0,
        min_atr_ratio=1.0,
    ),
)

E05_SETTING = e05.E05Setting(
    pivot_radius=3,
    divergence_min=5.0,
    break_buffer_atr=0.25,
    rebound_atr=0.5,
    max_wait_bars=12,
)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            payload, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def candidate_id(
    ladder_profile: LadderProfile,
    entry_filter: EntryFilter,
    exit_family: str,
) -> str:
    return _sha(
        {
            "contract": CONTRACT,
            "ladder": dataclasses.asdict(ladder_profile),
            "entry": dataclasses.asdict(entry_filter),
            "exit": exit_family,
        }
    )[:16]


def preregistered_candidates() -> list[dict[str, Any]]:
    return [
        {
            "candidate_id": candidate_id(lp, ef, exit_family),
            "ladder": dataclasses.asdict(lp),
            "entry_filter": dataclasses.asdict(ef),
            "exit_family": exit_family,
        }
        for lp in LADDER_PROFILES
        for ef in ENTRY_FILTERS
        for exit_family in ("EXIT_E02_DONCHIAN", "EXIT_E05_DIVERGENCE_RETEST")
    ]


def _aligned(data: Any, htf: Any, key: str) -> np.ndarray:
    n = len(data.ts)
    if key not in data.z.files:
        return np.full(n, np.nan, dtype=np.float64)
    values = np.asarray(data.z[key], dtype=np.float64)[
        data.full_indices[htf.event_index]
    ]
    return ladder.top._align_feature(n, htf, values)


def _prior_rolling_median(values: np.ndarray, lookback: int) -> np.ndarray:
    out = np.full(len(values), np.nan, dtype=np.float64)
    if len(values) <= lookback:
        return out
    windows = np.lib.stride_tricks.sliding_window_view(
        np.asarray(values[:-1], dtype=np.float64), lookback
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        out[lookback:] = np.nanmedian(windows, axis=1)
    return out


def build_entry_gates(
    data: Any,
    htfs: dict[str, Any],
) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    """Return causal, completed-HTF gates and a source-timestamp audit."""
    h1, h4, day = (htfs[x] for x in ("1h", "4h", "D"))
    n = len(data.ts)
    w11, w21 = _aligned(data, h1, "wt1_1h"), _aligned(data, h1, "wt2_1h")
    w14, w24 = _aligned(data, h4, "wt1_4h"), _aligned(data, h4, "wt2_4h")
    w1d, w2d = _aligned(data, day, "wt1_D"), _aligned(data, day, "wt2_D")
    ema4 = _aligned(data, h4, "ema_20_4h")
    emad = _aligned(data, day, "ema_20_D")
    ema1 = _aligned(data, h1, "ema_20_1h")
    close4 = ladder.top._align_feature(n, h4, h4.close)
    closed = ladder.top._align_feature(n, day, day.close)
    atr_med = _prior_rolling_median(h1.atr, 10)
    atr_ratio_h1 = h1.atr / np.where(atr_med > 0, atr_med, np.nan)

    future_sources = 0
    source_checks = 0
    for h in (h1, h4, day):
        for row, source in zip(h.event_index, h.source_ts):
            source_checks += 1
            future_sources += int(int(source) > int(data.ts[int(row)]))
    if future_sources:
        raise RuntimeError(
            f"HAO phase3 found {future_sources} future completed-HTF sources"
        )

    regime = (
        (close4 < ema4)
        & (closed < emad)
        & (w14 < w24)
        & (w1d < w2d)
        & np.isfinite(close4)
        & np.isfinite(closed)
    )
    gates: dict[str, np.ndarray] = {}
    component_counts: dict[str, int] = {}
    for profile in ENTRY_FILTERS:
        armed_until = -1
        gate = np.zeros(n, dtype=np.uint8)
        component_count = 0
        prior_velocity = 0.0
        for j, row0 in enumerate(h1.event_index):
            i = int(row0)
            if j < 12:
                continue
            atr = float(h1.atr[j])
            if not math.isfinite(atr) or atr <= 0:
                continue
            velocity = (float(h1.close[j - 1]) - float(h1.close[j])) / atr
            acceleration = velocity - prior_velocity
            prior_velocity = velocity
            lhll = bool(
                h1.high[j] < h1.high[j - 1]
                and h1.low[j] < h1.low[j - 1]
                and h1.close[j] < h1.low[j - 1]
            )
            wt_roll = bool(
                w11[i] < w21[i]
                and w11[int(h1.event_index[j - 1])]
                >= w21[int(h1.event_index[j - 1])]
                and h1.close[j] < h1.close[j - 1]
            )
            failed_reclaim = bool(
                math.isfinite(ema1[i])
                and h1.high[j - 1]
                >= ema1[int(h1.event_index[j - 1])]
                and h1.close[j] < ema1[i]
                and h1.high[j] < h1.high[j - 1]
            )
            accel = bool(
                velocity >= profile.min_velocity_atr
                and acceleration >= profile.min_acceleration_atr
                and atr_ratio_h1[j] >= profile.min_atr_ratio
            )
            component = {
                "STATE": True,
                "LHLL": lhll,
                "WT_ROLL": wt_roll,
                "LHLL_OR_WT": lhll or wt_roll,
                "ATR_ACCEL": accel,
                "FAILED_RECLAIM": failed_reclaim,
                "STRUCT_AND_ACCEL": lhll and accel,
                "ANY": lhll or wt_roll or failed_reclaim or accel,
            }[profile.confirmation]
            if component and regime[i]:
                component_count += 1
                armed_until = max(
                    armed_until, int(data.ts[i]) + profile.arm_hours * 3600
                )
            right = (
                int(h1.event_index[j + 1])
                if j + 1 < len(h1.event_index)
                else n
            )
            if int(data.ts[i]) <= armed_until:
                active_rows = np.arange(i, right, dtype=np.int64)
                gate[active_rows] = regime[active_rows].astype(np.uint8)
        gates[profile.label] = gate
        component_counts[profile.label] = component_count
    return gates, {
        "clock_contract": getattr(data, "availability_clock", {}),
        "expected_clock_contract": CLOCK_CONTRACT,
        "completed_htf_source_checks": source_checks,
        "future_completed_htf_sources": future_sources,
        "component_event_counts": component_counts,
        "gate_rows": {
            key: int(np.count_nonzero(value)) for key, value in gates.items()
        },
    }


def _exit_arrays(
    data: Any,
    ctx: dict[str, Any],
    e05_events: Any,
    family: str,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if family == "EXIT_E02_DONCHIAN":
        event = np.asarray(ctx["signals"].exit_event, dtype=np.uint8)
        ref = np.asarray(ctx["signals"].exit_ref, dtype=np.float64)
        source = np.asarray(
            ctx.get("_e02_exit_source", np.zeros(len(data.ts))),
            dtype=np.int64,
        )
        return event, ref, source
    if family == "EXIT_E05_DIVERGENCE_RETEST":
        return (
            np.asarray(e05_events.event, dtype=np.uint8),
            np.asarray(e05_events.ref, dtype=np.float64),
            np.asarray(e05_events.source, dtype=np.int64),
        )
    raise ValueError(family)


def should_request_short_reclaim(
    *,
    reclaim_level: float,
    bar_low: float,
    bar_close: float,
    entry_gate: bool,
) -> bool:
    """A veto never becomes a delayed market order above the stored level."""
    return bool(
        entry_gate
        and math.isfinite(reclaim_level)
        and (bar_low <= reclaim_level or bar_close <= reclaim_level)
    )


def simulate(
    data: Any,
    ctx: dict[str, Any],
    entry_gate: np.ndarray,
    e05_events: Any,
    exit_family: str,
    commission_rate: float,
    slippage_rate: float,
    *,
    emit_schedule: bool = False,
) -> dict[str, Any]:
    """Execute one gated HAO ladder on the shared parent-close clock."""
    left, right = int(ctx["left"]), int(ctx["right"])
    if right - left < 100:
        raise ValueError("phase3 fold too short")
    signals = ctx["signals"]
    curve = ctx["curve"]
    exits, exit_ref, exit_source = _exit_arrays(
        data, ctx, e05_events, exit_family
    )
    cash = ACCOUNT_USD
    qty = 0.0  # SHORT quantity is negative.
    average_entry = math.nan
    prior_exit_notional = 0.0
    reclaim_level = math.nan
    reclaim_touched = False
    pending: dict[str, Any] | None = None
    minimum_equity = peak_equity = ACCOUNT_USD
    max_dd = weighted = requested = filled = 0.0
    peak_notional = 0.0
    held = entries = exits_filled = reclaims = ladder_adds = clamps = 0
    vetoed_requests = vetoed_reclaims = future_sources = 0
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
            "signal_i": signal_i,
            "fill_i": int(fill_i),
            "reason": reason,
            "request_notional": request_notional,
            "reference": reference,
            "source": source,
            "absolute_target": absolute_target,
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
                reclaim_level = (
                    min(px, ref) if math.isfinite(ref) else px
                )
                qty = 0.0
                average_entry = math.nan
                reclaim_touched = False
                exits_filled += 1
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
                    average_entry = (
                        px
                        if prior_q <= 0 or not math.isfinite(average_entry)
                        else (average_entry * prior_q + px * add_q)
                        / (prior_q + add_q)
                    )
                    cash += actual - commission_rate * actual
                    qty -= add_q
                    peak_notional = max(peak_notional, abs(qty) * px)
                    entries += 1
                    reclaims += int(item["reason"] == "FILTERED_RECLAIM")
                    ladder_adds += int(item["reason"] == "LADDER_ADD")
                    if emit_schedule:
                        schedule.append(
                            {
                                "type": (
                                    "ENTRY" if prior_q <= 0 else "AUGMENT"
                                ),
                                "reason": item["reason"],
                                "fill_px": px,
                                "quantity": add_q,
                                "requested_notional_usd": want,
                                "filled_notional_usd": actual,
                                "post_fill_notional_usd": abs(qty) * px,
                                "entry_capacity_usd": CAPACITY_USD,
                                "semantics": (
                                    "target"
                                    if item["absolute_target"]
                                    else "add"
                                ),
                                **clock("signal", item["signal_i"]),
                                **clock("fill", i),
                            }
                        )
                    if item["reason"] == "FILTERED_RECLAIM":
                        reclaim_level = math.nan
                        reclaim_touched = False

        equity = cash + qty * close
        minimum_equity = min(minimum_equity, equity)
        peak_equity = max(peak_equity, equity)
        if peak_equity > 0:
            max_dd = max(
                max_dd, 100.0 * (peak_equity - equity) / peak_equity
            )
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
            beyond_reclaim_now = bool(
                float(data.low[i]) <= reclaim_level
                or close <= reclaim_level
            )
            reclaim_touched |= beyond_reclaim_now
            if beyond_reclaim_now:
                if should_request_short_reclaim(
                    reclaim_level=reclaim_level,
                    bar_low=float(data.low[i]),
                    bar_close=close,
                    entry_gate=allowed,
                ):
                    schedule_request(
                        "ENTRY",
                        i,
                        reason="FILTERED_RECLAIM",
                        request_notional=max(
                            BASE_USD, prior_exit_notional
                        ),
                        absolute_target=True,
                    )
                else:
                    vetoed_reclaims += 1
                continue

        mult = float(signals.entry_mult[i])
        if mult <= 0:
            continue
        if not allowed:
            vetoed_requests += 1
            continue
        schedule_request(
            "ENTRY",
            i,
            reason="LADDER_ADD" if active() else "FILTERED_LADDER_ENTRY",
            request_notional=BASE_USD * mult,
            absolute_target=curve.semantics == "target",
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
        "opportunity_benchmark_pct": max(
            0.0, 100.0 * bh_pnl / BASE_USD
        ),
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
        "vetoed_ladder_requests": vetoed_requests,
        "vetoed_reclaim_rows": vetoed_reclaims,
        "reclaim_obligation_open_at_end": math.isfinite(reclaim_level),
        "requested_notional_usd": requested,
        "filled_notional_usd": filled,
        "clamp_count": clamps,
        "future_htf_source_count": future_sources,
        "rows": rows,
        "availability_clock": CLOCK_CONTRACT,
        "fill_contract": "first_strictly_later_availability_adverse_SHORT",
    }
    if emit_schedule:
        result["schedule_events"] = schedule
    return result


def normalize_fold(
    row: dict[str, Any],
    *,
    fold: int,
    baseline_e02: dict[str, Any],
    same_entry_e02: dict[str, Any],
    exit_family: str,
) -> dict[str, Any]:
    strategy = float(row["capital_return_pct"])
    bh = float(row["bh_capital_return_pct"])
    opportunity = max(0.0, bh)
    same_entry = float(same_entry_e02["capital_return_pct"])
    entry_baseline = float(baseline_e02["capital_return_pct"])
    tim = float(row["exposure_weighted_tim_pct"])
    common = bool(
        strategy > opportunity
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
        "bh_return_pct": bh,
        "opportunity_benchmark_pct": opportunity,
        "same_entry_e02_return_pct": same_entry,
        "ungated_ladder_e02_return_pct": entry_baseline,
        "entry_contribution_vs_ungated_e02_pp": strategy - entry_baseline,
        "exit_contribution_vs_same_entry_e02_pp": strategy - same_entry,
        "weighted_tim_pct": tim,
        "binary_tim_pct": float(row["binary_tim_pct"]),
        "max_drawdown_account_pct": float(
            row["max_drawdown_account_pct"]
        ),
        "minimum_account_equity_usd": float(
            row["minimum_account_equity_usd"]
        ),
        "peak_post_fill_notional_usd": float(
            row["peak_post_fill_notional_usd"]
        ),
        "entry_fills": int(row["entry_fills"]),
        "exit_fills": int(row["exit_fills"]),
        "reclaim_reentries": int(row["reclaim_reentries"]),
        "vetoed_ladder_requests": int(row["vetoed_ladder_requests"]),
        "vetoed_reclaim_rows": int(row["vetoed_reclaim_rows"]),
        "future_htf_source_count": int(row["future_htf_source_count"]),
        "solvent": not bool(row["insolvent"]),
        "capacity_ok": not bool(row["entry_capacity_breach"]),
        "tim_gate_pass": EXPOSURE_MIN <= tim <= EXPOSURE_MAX,
        "beats_short_bh_or_cash": strategy > opportunity,
        "beats_same_entry_e02": (
            None
            if exit_family == "EXIT_E02_DONCHIAN"
            else strategy > same_entry
        ),
        "fold_gate_pass": common and exit_gate,
    }


def rank_key(row: dict[str, Any]) -> tuple[Any, ...]:
    folds = row["discovery_fold_evidence"]
    strict = sum(bool(f["fold_gate_pass"]) for f in folds)
    min_bench = min(
        f["strategy_return_pct"] - f["opportunity_benchmark_pct"]
        for f in folds
    )
    min_exit = min(
        f["exit_contribution_vs_same_entry_e02_pp"] for f in folds
    )
    min_entry = min(
        f["entry_contribution_vs_ungated_e02_pp"] for f in folds
    )
    dd = max(f["max_drawdown_account_pct"] for f in folds)
    tim_distance = max(abs(f["weighted_tim_pct"] - 75.0) for f in folds)
    return (
        -int(strict == len(folds)),
        -strict,
        -min_bench,
        -min_exit,
        -min_entry,
        dd,
        tim_distance,
        row["candidate_id"],
    )


def freeze_discovery(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    forbidden = {"untouched_final", "final_gate_pass", "all_folds_strict"}
    if any(forbidden.intersection(row) for row in rows):
        raise ValueError("final-fold field present before phase3 freeze")
    strict = [row for row in rows if row["discovery_all_folds_strict"]]
    # Exact replay must be bounded: freeze at most the best strict candidate.
    # When there is no strict row, retain the best E02 and E05 diagnostics.
    if strict:
        return [sorted(strict, key=rank_key)[0]]
    selected: list[dict[str, Any]] = []
    for family in ("EXIT_E02_DONCHIAN", "EXIT_E05_DIVERGENCE_RETEST"):
        family_rows = [row for row in rows if row["exit_family"] == family]
        if family_rows:
            selected.append(sorted(family_rows, key=rank_key)[0])
    return sorted(selected, key=rank_key)


def final_evaluation_candidates(
    frozen: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return only discovery-strict rows; gray diagnostics cannot reveal FINAL."""
    return [
        item for item in frozen if item["discovery_all_folds_strict"]
    ]


def run(args: argparse.Namespace) -> Path:
    artifact = args.artifact.resolve()
    npz_dir = args.npz_dir.resolve()
    npz_path = npz_dir / "HAO.npz"
    before_hash = solvency._install_hash_bound_hao_exception(npz_path)
    # The ladder module can be imported through two names. Bind the same narrow
    # HAO exception into the loader actually used by _fold_contexts.
    ladder.top.audit_npz = solvency.top.audit_npz
    source, data, htfs, base_contexts = shared._fold_contexts(
        artifact, npz_dir, fold_mode="nested"
    )
    try:
        if source["manifest"]["symbol"] != "HAO":
            raise RuntimeError("phase3 accepts only HAO")
        if source["manifest"]["side"] != "SHORT":
            raise RuntimeError("phase3 accepts only HAO_SHORT")
        if [int(c["fold"]) for c in base_contexts] != [1, 2, 3]:
            raise RuntimeError("phase3 requires frozen folds 1,2,3")
        if before_hash != EXPECTED_NPZ_SHA256:
            raise RuntimeError("unexpected isolated HAO hash")
        if getattr(data, "availability_clock", {}).get("kind") != CLOCK_CONTRACT:
            raise RuntimeError("shared parent-close clock is not active")

        output = args.out_dir.resolve()
        output.mkdir(parents=True, exist_ok=False)
        candidates = preregistered_candidates()
        prereg = {
            "contract": CONTRACT,
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "symbol_side": "HAO_SHORT",
            "source_artifact": str(artifact),
            "source_artifact_result_sha256": _sha256_file(
                artifact / "result.json"
            ),
            "isolated_npz": str(npz_path),
            "isolated_npz_sha256": before_hash,
            "canonical_write_allowed": False,
            "live_write_allowed": False,
            "matrix_write_allowed": False,
            "path_fleet_write_allowed": False,
            "ladder_profiles": [
                dataclasses.asdict(x) for x in LADDER_PROFILES
            ],
            "entry_filters": [
                dataclasses.asdict(x) for x in ENTRY_FILTERS
            ],
            "exit_profiles": {
                "EXIT_E02_DONCHIAN": {
                    "timeframe": "4h",
                    "lookback": 30,
                },
                "EXIT_E05_DIVERGENCE_RETEST": E05_SETTING.result_params(),
            },
            "candidate_count": len(candidates),
            "diagnostic_baselines": (
                "ungated E02 and ungated E05 per ladder/fold; excluded from "
                "selection; FINAL stays sealed unless a discovery-strict row "
                "exists, then only its baselines run after DISCOVERY_FREEZE"
            ),
            "discovery_folds": DISCOVERY_FOLDS,
            "final_fold_sealed_until_freeze": FINAL_FOLD,
            "gates": {
                "each_fold_strategy_gt_max_short_bh_cash": True,
                "non_e02_strategy_gt_same_entry_e02": True,
                "weighted_tim_pct": [EXPOSURE_MIN, EXPOSURE_MAX],
                "minimum_account_equity_usd_gt": 0,
                "max_drawdown_account_pct_lt": 100,
                "peak_post_fill_notional_usd_lte": CAPACITY_USD,
                "future_completed_htf_source_count": 0,
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
                "best strict discovery row; otherwise best gray diagnostic "
                "per exit family; FINAL fields structurally absent"
            ),
            "exact_v3_rule": (
                "emit/replay only after strict discovery freeze and strict "
                "untouched final; never for a gray row"
            ),
        }
        prereg["sha256"] = _sha(prereg)
        (output / "PREREGISTRATION.json").write_text(
            json.dumps(prereg, indent=2, sort_keys=True) + "\n"
        )

        gates, feature_audit = build_entry_gates(data, htfs)
        if feature_audit["future_completed_htf_sources"]:
            raise RuntimeError("future completed HTF source")
        e05_events = e05._events(data, htfs["4h"], "SHORT", E05_SETTING)
        commission = prereg["commission_bps_one_way"] / 10_000.0
        slippage = prereg["slippage_bps_one_way"] / 10_000.0

        contexts: dict[tuple[str, int], dict[str, Any]] = {}
        ungated_controls: dict[tuple[str, int], dict[str, Any]] = {}
        ungated_e05: dict[tuple[str, int], dict[str, Any]] = {}
        for lp in LADDER_PROFILES:
            for ctx in base_contexts:
                scaled = solvency._scaled_context(
                    data,
                    htfs,
                    ctx,
                    lp.setting(),
                    int(source["manifest"]["exit"]["n"]),
                    "SHORT",
                    ladder,
                )
                e02_source = np.zeros(len(data.ts), dtype=np.int64)
                h4 = htfs["4h"]
                e02_source[
                    np.asarray(h4.event_index, dtype=np.int64)
                ] = np.asarray(h4.source_ts, dtype=np.int64)
                scaled["_e02_exit_source"] = e02_source
                key = (lp.label, int(ctx["fold"]))
                contexts[key] = scaled
                if int(ctx["fold"]) in DISCOVERY_FOLDS:
                    all_rows = np.ones(len(data.ts), dtype=np.uint8)
                    ungated_controls[key] = simulate(
                        data,
                        scaled,
                        all_rows,
                        e05_events,
                        "EXIT_E02_DONCHIAN",
                        commission,
                        slippage,
                    )
                    ungated_e05[key] = simulate(
                        data,
                        scaled,
                        all_rows,
                        e05_events,
                        "EXIT_E05_DIVERGENCE_RETEST",
                        commission,
                        slippage,
                    )

        rows: list[dict[str, Any]] = []
        raw_cache: dict[
            tuple[str, str, str, int], dict[str, Any]
        ] = {}
        for lp in LADDER_PROFILES:
            for ef in ENTRY_FILTERS:
                controls: dict[int, dict[str, Any]] = {}
                for fold in DISCOVERY_FOLDS:
                    ctx = contexts[(lp.label, fold)]
                    controls[fold] = simulate(
                        data,
                        ctx,
                        gates[ef.label],
                        e05_events,
                        "EXIT_E02_DONCHIAN",
                        commission,
                        slippage,
                    )
                    raw_cache[
                        (lp.label, ef.label, "EXIT_E02_DONCHIAN", fold)
                    ] = controls[fold]
                for exit_family in (
                    "EXIT_E02_DONCHIAN",
                    "EXIT_E05_DIVERGENCE_RETEST",
                ):
                    evidence = []
                    for fold in DISCOVERY_FOLDS:
                        raw = (
                            controls[fold]
                            if exit_family == "EXIT_E02_DONCHIAN"
                            else simulate(
                                data,
                                contexts[(lp.label, fold)],
                                gates[ef.label],
                                e05_events,
                                exit_family,
                                commission,
                                slippage,
                            )
                        )
                        raw_cache[
                            (lp.label, ef.label, exit_family, fold)
                        ] = raw
                        evidence.append(
                            normalize_fold(
                                raw,
                                fold=fold,
                                baseline_e02=ungated_controls[
                                    (lp.label, fold)
                                ],
                                same_entry_e02=controls[fold],
                                exit_family=exit_family,
                            )
                        )
                    rows.append(
                        {
                            "candidate_id": candidate_id(
                                lp, ef, exit_family
                            ),
                            "ladder": dataclasses.asdict(lp),
                            "entry_filter": dataclasses.asdict(ef),
                            "exit_family": exit_family,
                            "discovery_fold_evidence": evidence,
                            "discovery_all_folds_strict": all(
                                x["fold_gate_pass"] for x in evidence
                            ),
                        }
                    )

        discovery_payload = {
            "preregistration_sha256": prereg["sha256"],
            "contains_final_metrics": False,
            "rows": rows,
        }
        discovery_payload["sha256"] = _sha(discovery_payload)
        (output / "DISCOVERY_GRID.json").write_text(
            json.dumps(discovery_payload, indent=2, sort_keys=True) + "\n"
        )
        frozen = freeze_discovery(rows)
        freeze = {
            "preregistration_sha256": prereg["sha256"],
            "discovery_grid_sha256": discovery_payload["sha256"],
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "selected_on_discovery_only": True,
            "contains_final_metrics": False,
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
        revealed: list[dict[str, Any]] = []
        # FINAL remains entirely unobserved when discovery has no strict row.
        # If a future run has one, evaluate only that frozen strict row and its
        # two indispensable baselines after the persisted freeze.
        for item in discovery_strict:
            lp = next(
                x for x in LADDER_PROFILES if x.label == item["ladder"]["label"]
            )
            ef = next(
                x
                for x in ENTRY_FILTERS
                if x.label == item["entry_filter"]["label"]
            )
            key = (lp.label, FINAL_FOLD)
            ctx = contexts[key]
            all_rows = np.ones(len(data.ts), dtype=np.uint8)
            ungated_controls[key] = simulate(
                data,
                ctx,
                all_rows,
                e05_events,
                "EXIT_E02_DONCHIAN",
                commission,
                slippage,
            )
            ungated_e05[key] = simulate(
                data,
                ctx,
                all_rows,
                e05_events,
                "EXIT_E05_DIVERGENCE_RETEST",
                commission,
                slippage,
            )
            same_entry_e02 = simulate(
                data,
                ctx,
                gates[ef.label],
                e05_events,
                "EXIT_E02_DONCHIAN",
                commission,
                slippage,
            )
            final_raw = simulate(
                data,
                ctx,
                gates[ef.label],
                e05_events,
                item["exit_family"],
                commission,
                slippage,
                emit_schedule=True,
            )
            final = normalize_fold(
                final_raw,
                fold=FINAL_FOLD,
                baseline_e02=ungated_controls[key],
                same_entry_e02=same_entry_e02,
                exit_family=item["exit_family"],
            )
            strict = bool(final["fold_gate_pass"])
            revealed.append(
                {
                    **item,
                    "untouched_final": final,
                    "all_three_folds_strict": strict,
                    "exact_v3_eligible": strict,
                    "final_schedule_sha256": _sha(
                        final_raw["schedule_events"]
                    ),
                    "final_schedule_event_count": len(
                        final_raw["schedule_events"]
                    ),
                }
            )

        strict_survivors = [
            x for x in revealed if x["all_three_folds_strict"]
        ]
        result = {
            "manifest": {
                **prereg,
                "npz_sha256_after": _sha256_file(npz_path),
                "matrix_eligible": False,
                "promotion_allowed": False,
                "canonical_or_live_write": False,
                "exact_replay_allowed": bool(strict_survivors),
                "exact_replay_status": (
                    "STRICT_SURVIVOR_REQUIRES_V3_REPLAY"
                    if strict_survivors
                    else "NOT_RUN_DISCOVERY_GATE_FAILED"
                ),
                "final_fold_evaluated": bool(discovery_strict),
                "final_fold_status": (
                    "EVALUATED_FROZEN_DISCOVERY_STRICT_ONLY"
                    if discovery_strict
                    else "SEALED_NOT_EVALUATED"
                ),
            },
            "feature_audit": feature_audit,
            "ungated_ladder_baselines": [
                {
                    "ladder": dataclasses.asdict(lp),
                    "fold": fold,
                    "e02": ungated_controls[(lp.label, fold)],
                    "e05": ungated_e05[(lp.label, fold)],
                    "e05_exit_contribution_vs_e02_pp": (
                        ungated_e05[(lp.label, fold)][
                            "capital_return_pct"
                        ]
                        - ungated_controls[(lp.label, fold)][
                            "capital_return_pct"
                        ]
                    ),
                }
                for lp in LADDER_PROFILES
                for fold in DISCOVERY_FOLDS
            ],
            "discovery_candidate_count": len(rows),
            "discovery_strict_count": sum(
                x["discovery_all_folds_strict"] for x in rows
            ),
            "frozen_count": len(frozen),
            "all_three_fold_strict_count": len(strict_survivors),
            "frozen_results": revealed,
            "frozen_discovery_only_gray": (
                frozen if not discovery_strict else []
            ),
            "discovery_gray_rows": rows,
        }
        (output / "result.json").write_text(
            json.dumps(
                result, indent=2, sort_keys=True, allow_nan=False
            )
            + "\n"
        )
        print(
            json.dumps(
                {
                    "output": str(output),
                    "discovery_candidates": len(rows),
                    "discovery_strict": result["discovery_strict_count"],
                    "frozen": len(frozen),
                    "all_three_fold_strict": len(strict_survivors),
                    "npz_unchanged": (
                        _sha256_file(npz_path) == before_hash
                    ),
                    "exact_replay_status": result["manifest"][
                        "exact_replay_status"
                    ],
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
    parser.add_argument("--npz-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
