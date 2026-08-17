#!/usr/bin/env python3
"""Bounded TTD_SHORT regime-exit follow-up.

This is vector research only.  It keeps the frozen $8k band-ladder entry and
tests coherent SHORT cover state machines instead of another Donchian-N grid:

* bullish/correction book: cover a profitable trough only after a WT-confirmed
  ATR rebound;
* confirmed-bear book: arm a lower low, then cover at a structural higher
  bottom;
* adverse book: arm an upside structural break, wait for a lower-timeframe
  pullback/rebound, and use an emergency brake only if price keeps rising
  without that recovery.

Every feature is observed on a completed parent bar.  Signals fill at the next
strictly later RTH availability through ``vec_band_ladder_walkforward``'s
compiled SHORT ledger.  That ledger supplies side isolation, pre-cost
committed-fill-notional dollar-time, hard capacity and persistent stored-bottom
reclaim semantics.
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


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import vec_band_ladder_walkforward as ladder  # noqa: E402
import vec_top_exit_campaign as top  # noqa: E402


CONTRACT = "TTD_SHORT_REGIME_EXIT_V1"
NPZ_SHA256 = "56d874e0784a1e777fd6e69d61fe3b95ae1909fad875c84afe8d0d5d39c9f217"
BASELINE_EXPECTED = {
    1: {"alpha": 15.246442481012892, "tim": 70.22470158647955},
    2: {"alpha": -25.85173009111554, "tim": 77.61480890136636},
}


@dataclasses.dataclass(frozen=True)
class ExitBundle:
    label: str
    families: tuple[str, ...]
    tf: str
    trough_n: int
    giveback_atr: float
    stoch_max: float
    lower_low_n: int
    higher_bottom_wait: int
    adverse_break_n: int
    adverse_buffer_atr: float
    adverse_wait: int
    adverse_trail_atr: float
    emergency_atr: float
    wt_cross_only: bool = False


# Coherent hypotheses, deliberately not a Cartesian parameter grid.
EXIT_BUNDLES = (
    ExitBundle("C_FAST_15M", ("correction",), "15m", 16, .65, 35, 12, 12, 16, .15, 12, .55, 3.5),
    ExitBundle("C_BAL_1H", ("correction",), "1h", 8, .90, 40, 8, 8, 8, .20, 8, .75, 4.0),
    ExitBundle("C_RIDE_1H", ("correction",), "1h", 12, 1.25, 45, 12, 12, 12, .25, 12, 1.00, 4.5),
    ExitBundle("B_HB_FAST_15M", ("bear",), "15m", 16, .75, 35, 16, 16, 16, .20, 16, .65, 4.0),
    ExitBundle("B_HB_BAL_1H", ("bear",), "1h", 8, 1.00, 40, 8, 8, 8, .25, 8, .80, 4.5),
    ExitBundle("B_HB_RIDE_1H", ("bear",), "1h", 12, 1.25, 45, 12, 16, 12, .30, 12, 1.00, 5.0),
    ExitBundle("B_HB_STRICT_4H", ("bear",), "4h", 8, 1.00, 35, 8, 6, 8, .25, 6, .80, 5.0, True),
    ExitBundle("B_HB_STRICT_1H", ("bear",), "1h", 12, 1.25, 35, 12, 12, 12, .30, 12, 1.00, 5.0, True),
    ExitBundle("A_RECOVERY_15M", ("adverse",), "15m", 16, .75, 40, 16, 12, 16, .15, 16, .60, 3.5),
    ExitBundle("A_RECOVERY_1H", ("adverse",), "1h", 8, 1.00, 45, 8, 8, 8, .20, 8, .80, 4.0),
    ExitBundle("ALL_FAST", ("correction", "bear", "adverse"), "15m", 16, .65, 35, 16, 12, 16, .15, 16, .55, 3.5),
    ExitBundle("ALL_BAL", ("correction", "bear", "adverse"), "1h", 8, .90, 40, 8, 8, 8, .20, 8, .75, 4.0),
    ExitBundle("ALL_RIDE", ("correction", "bear", "adverse"), "1h", 12, 1.25, 45, 12, 16, 12, .30, 12, 1.00, 5.0),
)


FROZEN_CURVES = {
    1: ladder.Curve(
        "BLOCK069",
        "linear",
        "green",
        "target",
        20.0,
        3.991529074461716,
        2.14178431096639,
        3.392235688144322,
        2.011622968863513,
        1.0814599494341641,
        .794729056037261,
    ),
    2: ladder.Curve(
        "ARC3_linear_structure_target",
        "linear",
        "structure",
        "target",
        30.0,
        5.0,
        2.0,
        4.0,
        2.0,
        2.0,
        .5,
    ),
}


def _field_on_events(
    data: top.ExecutionData,
    h: top.HTFData,
    name: str,
) -> np.ndarray:
    if name not in data.z.files:
        return np.full(len(h.event_index), np.nan, dtype=np.float64)
    return np.asarray(data.z[name], dtype=np.float64)[
        data.full_indices[h.event_index]
    ]


def _prior(values: np.ndarray, n: int, mode: str) -> np.ndarray:
    return top._rolling_prior(np.asarray(values, dtype=np.float64), n, mode)


def _regime_on(
    data: top.ExecutionData,
    h: top.HTFData,
) -> tuple[np.ndarray, np.ndarray, dict[str, int]]:
    close4 = _field_on_events(data, h, "close_4h")
    ema50 = _field_on_events(data, h, "ema_50_4h")
    wt14 = _field_on_events(data, h, "wt1_4h")
    wt24 = _field_on_events(data, h, "wt2_4h")
    close_d = _field_on_events(data, h, "close_D")
    ema20d = _field_on_events(data, h, "ema_20_D")
    ts4 = _field_on_events(data, h, "timestamp_4h")
    tsd = _field_on_events(data, h, "timestamp_D")
    observed = data.ts[h.event_index]
    finite = (
        np.isfinite(close4)
        & np.isfinite(ema50)
        & np.isfinite(wt14)
        & np.isfinite(wt24)
        & np.isfinite(close_d)
        & np.isfinite(ema20d)
    )
    bull = finite & (close4 >= ema50) & (close_d >= ema20d)
    bear = finite & (close4 < ema50) & (close_d < ema20d) & (wt14 < wt24)
    audit = {
        "timestamp_4h_future_count": int(np.count_nonzero(ts4 > observed)),
        "timestamp_D_future_count": int(np.count_nonzero(tsd > observed)),
    }
    return bull, bear, audit


def build_exit_signal(
    data: top.ExecutionData,
    htfs: dict[str, top.HTFData],
    bundle: ExitBundle,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Precompute a causal structural-cover array for the compiled ledger."""
    h = htfs[bundle.tf]
    n = len(h.close)
    event = np.zeros(n, dtype=np.uint8)
    ref = np.full(n, np.nan, dtype=np.float64)
    bull, bear, causal = _regime_on(data, h)
    wt1 = _field_on_events(data, h, f"wt1_{bundle.tf}")
    wt2 = _field_on_events(data, h, f"wt2_{bundle.tf}")
    stoch = _field_on_events(data, h, f"stoch_k_{bundle.tf}")
    atr = np.asarray(h.atr, dtype=np.float64)
    prior_low = _prior(h.low, bundle.trough_n, "min")
    structure_low = _prior(h.low, bundle.lower_low_n, "min")
    structure_high = _prior(h.high, bundle.adverse_break_n, "max")

    counts = {
        "correction": 0,
        "bear": 0,
        "adverse_recovery": 0,
        "adverse_emergency": 0,
    }
    bear_armed_until = -1
    bear_bottom = math.nan
    adverse_armed_until = -1
    adverse_break = math.nan
    adverse_atr = math.nan
    adverse_bottom = math.nan
    adverse_saw_pullback = False

    warmup = max(
        bundle.trough_n,
        bundle.lower_low_n,
        bundle.adverse_break_n,
        2,
    )
    for j in range(warmup, n):
        if not math.isfinite(atr[j]) or atr[j] <= 0:
            continue
        wt_bull = bool(
            math.isfinite(wt1[j])
            and math.isfinite(wt2[j])
            and wt1[j] > wt2[j]
        )
        wt_cross = bool(
            wt_bull
            and math.isfinite(wt1[j - 1])
            and math.isfinite(wt2[j - 1])
            and wt1[j - 1] <= wt2[j - 1]
        )
        higher_bottom = bool(
            h.low[j] > h.low[j - 1]
            and h.close[j] > h.close[j - 1]
            and (wt_cross if bundle.wt_cross_only else wt_bull)
        )

        correction = bool(
            "correction" in bundle.families
            and bull[j]
            and math.isfinite(prior_low[j])
            and h.close[j] >= prior_low[j] + bundle.giveback_atr * atr[j]
            and (wt_cross or higher_bottom)
            and (
                not math.isfinite(stoch[j])
                or stoch[j] <= bundle.stoch_max
            )
        )

        if (
            "bear" in bundle.families
            and bear[j]
            and math.isfinite(structure_low[j])
            and h.low[j] < structure_low[j]
        ):
            bear_bottom = float(h.low[j])
            bear_armed_until = j + bundle.higher_bottom_wait
        bear_cover = bool(
            "bear" in bundle.families
            and j <= bear_armed_until
            and higher_bottom
            and math.isfinite(bear_bottom)
        )

        if (
            "adverse" in bundle.families
            and j > adverse_armed_until
            and math.isfinite(structure_high[j])
            and h.close[j]
            > structure_high[j] + bundle.adverse_buffer_atr * atr[j]
            and wt_bull
        ):
            adverse_armed_until = j + bundle.adverse_wait
            adverse_break = float(h.close[j])
            adverse_atr = float(atr[j])
            adverse_bottom = float(h.low[j])
            adverse_saw_pullback = False
        adverse_cover = emergency = False
        if j <= adverse_armed_until and math.isfinite(adverse_break):
            adverse_bottom = min(adverse_bottom, float(h.low[j]))
            adverse_saw_pullback |= bool(
                h.close[j] < h.close[j - 1] or h.high[j] < h.high[j - 1]
            )
            adverse_cover = bool(
                adverse_saw_pullback
                and higher_bottom
                and h.close[j]
                >= adverse_bottom + bundle.adverse_trail_atr * atr[j]
            )
            emergency = bool(
                not adverse_saw_pullback
                and h.close[j]
                >= adverse_break + bundle.emergency_atr * adverse_atr
            )

        fired = correction or bear_cover or adverse_cover or emergency
        if fired:
            event[j] = 1
            candidates = [
                x
                for x in (prior_low[j], bear_bottom, adverse_bottom)
                if math.isfinite(x)
            ]
            ref[j] = min(candidates) if candidates else float(h.low[j])
            counts["correction"] += int(correction)
            counts["bear"] += int(bear_cover)
            counts["adverse_recovery"] += int(adverse_cover)
            counts["adverse_emergency"] += int(emergency)
            if bear_cover:
                bear_armed_until = -1
            if adverse_cover or emergency:
                adverse_armed_until = -1

    mapped, refs = top._map_events(len(data.ts), h, event, ref)
    causal.update(
        {
            "decision_tf": bundle.tf,
            "decision_parent_future_count": int(
                np.count_nonzero(h.source_ts > data.ts[h.event_index])
            ),
        }
    )
    return mapped, refs, {"signal_counts": counts, "causality": causal}


def _signals_with_exit(
    entry: ladder.SignalData,
    exit_event: np.ndarray,
    exit_ref: np.ndarray,
) -> ladder.SignalData:
    return ladder.SignalData(
        entry_mult=entry.entry_mult,
        event_tf=entry.event_tf,
        exit_event=exit_event,
        exit_ref=exit_ref,
        causality=entry.causality,
        entry_source_ts=entry.entry_source_ts,
    )


def _gate(row: dict[str, Any], tim_lo: float, tim_hi: float) -> list[str]:
    failures = []
    if row["deployed_alpha_vs_bh_or_cash_pp"] <= 0:
        failures.append("NOT_ABOVE_BH_OR_CASH")
    tim = row["exposure_weighted_tim_pct"]
    if not tim_lo <= tim <= tim_hi:
        failures.append("TIM_OUTSIDE_65_80")
    if row["fill_ratio"] < .75:
        failures.append("FILL_RATIO")
    if row["bars_flat_beyond_reclaim"]:
        failures.append("FORGOTTEN_RECLAIM")
    if row["insolvent"]:
        failures.append("INSOLVENT")
    if row["entry_capacity_breach"]:
        failures.append("ENTRY_CAPACITY")
    if row["exit_count"] < 2:
        failures.append("INSUFFICIENT_EXITS")
    return failures


def _regime_diagnosis(
    data: top.ExecutionData,
    htfs: dict[str, top.HTFData],
    left: int,
    right: int,
) -> dict[str, Any]:
    h = htfs["4h"]
    mask = (h.event_index >= left) & (h.event_index < right)
    chosen = np.flatnonzero(mask)
    if not len(chosen):
        return {}
    bull, bear, causal = _regime_on(data, h)
    close = h.close[chosen]
    returns = np.diff(np.log(close))
    running_low = np.minimum.accumulate(close)
    rebound = 100.0 * (close / running_low - 1.0)
    return {
        "completed_4h_bars": int(len(chosen)),
        "bullish_state_pct": 100.0 * float(np.mean(bull[chosen])),
        "confirmed_bear_state_pct": 100.0 * float(np.mean(bear[chosen])),
        "price_return_pct": 100.0 * (close[-1] / close[0] - 1.0),
        "annualized_4h_log_vol_pct": (
            100.0 * float(np.std(returns)) * math.sqrt(6.5 * 252)
        ),
        "max_rebound_from_running_low_pct": float(np.max(rebound)),
        "up_4h_bar_pct": 100.0 * float(np.mean(np.diff(close) > 0)),
        "causality": causal,
    }


def _simulate(
    data: top.ExecutionData,
    curve: ladder.Curve,
    entry: ladder.SignalData,
    exit_event: np.ndarray,
    exit_ref: np.ndarray,
    left: int,
    right: int,
    commission_bps: float,
    slippage_bps: float,
) -> dict[str, Any]:
    return ladder._simulate_compiled(
        data,
        _signals_with_exit(entry, exit_event, exit_ref),
        curve,
        left,
        right,
        commission_bps / 10_000.0,
        slippage_bps / 10_000.0,
        "SHORT",
    )


def _select_exit(
    data: top.ExecutionData,
    curve: ladder.Curve,
    entry: ladder.SignalData,
    candidates: dict[str, tuple[np.ndarray, np.ndarray, dict[str, Any]]],
    left: int,
    right: int,
    args: argparse.Namespace,
) -> tuple[str, dict[str, Any], list[dict[str, Any]], float]:
    ranked = []
    options = [
        (
            "E02_N30_CONTROL",
            {
                "label": "E02_N30_CONTROL",
                "families": ["completed_4h_donchian_control"],
                "tf": "4h",
            },
            entry.exit_event,
            entry.exit_ref,
        )
    ] + [
        (
            bundle.label,
            dataclasses.asdict(bundle),
            candidates[bundle.label][0],
            candidates[bundle.label][1],
        )
        for bundle in EXIT_BUNDLES
    ]
    for label, definition, event, ref in options:
        rows = [
            _simulate(
                data,
                curve,
                entry,
                event,
                ref,
                l,
                r,
                args.commission_bps,
                args.slippage_bps,
            )
            for l, r in ladder._inner_slices(left, right)
        ]
        score = ladder._score(
            rows, args.tim_lo, args.tim_hi, args.tim_weight
        )
        ranked.append((score, label, definition, rows))
    ranked.sort(key=lambda row: (-row[0], row[1]))
    score, label, definition, rows = ranked[0]
    return label, definition, rows, score


def _candidate_arrays(
    label: str,
    entry: ladder.SignalData,
    candidates: dict[str, tuple[np.ndarray, np.ndarray, dict[str, Any]]],
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    if label == "E02_N30_CONTROL":
        return (
            entry.exit_event,
            entry.exit_ref,
            {
                "signal_counts": {
                    "completed_4h_donchian_control": int(
                        np.count_nonzero(entry.exit_event)
                    )
                },
                "causality": entry.causality,
            },
        )
    return candidates[label]


def _validation_grid(
    data: top.ExecutionData,
    curve: ladder.Curve,
    entry: ladder.SignalData,
    candidates: dict[str, tuple[np.ndarray, np.ndarray, dict[str, Any]]],
    left: int,
    right: int,
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    definitions = [
        (
            "E02_N30_CONTROL",
            {
                "label": "E02_N30_CONTROL",
                "families": ["completed_4h_donchian_control"],
                "tf": "4h",
            },
        )
    ] + [
        (bundle.label, dataclasses.asdict(bundle))
        for bundle in EXIT_BUNDLES
    ]
    rows = []
    for label, definition in definitions:
        event, ref, audit = _candidate_arrays(
            label, entry, candidates
        )
        metrics = _simulate(
            data,
            curve,
            entry,
            event,
            ref,
            left,
            right,
            args.commission_bps,
            args.slippage_bps,
        )
        failures = _gate(metrics, args.tim_lo, args.tim_hi)
        rows.append(
            {
                "label": label,
                "definition": definition,
                "metrics": metrics,
                "failures": failures,
                "pass": not failures,
                "signal_audit": audit,
            }
        )
    return rows


def _baseline_curve_for_fold(
    fold: int,
    data: top.ExecutionData,
    htfs: dict[str, top.HTFData],
    left: int,
    right: int,
    args: argparse.Namespace,
) -> ladder.Curve:
    if fold in FROZEN_CURVES:
        return FROZEN_CURVES[fold]
    # Fold 3 remains sealed until discovery passes.  Only then select its
    # ladder curve from its own pre-2026 inner training slices.
    ranked = []
    for curve in ladder._curves(args.seed, args.random_curves):
        signals = ladder._build_signals(data, htfs, curve, 30, "SHORT")
        rows = [
            ladder._simulate_compiled(
                data,
                signals,
                curve,
                l,
                r,
                args.commission_bps / 10_000.0,
                args.slippage_bps / 10_000.0,
                "SHORT",
            )
            for l, r in ladder._inner_slices(left, right)
        ]
        ranked.append(
            (
                ladder._score(
                    rows, args.tim_lo, args.tim_hi, args.tim_weight
                ),
                curve.label,
                curve,
            )
        )
    ranked.sort(key=lambda row: (-row[0], row[1]))
    return ranked[0][2]


def run(args: argparse.Namespace) -> Path:
    ladder.BASE_UNIT = 2_000.0
    ladder.CAPACITY = 8_000.0
    ladder.ACCOUNT_EQUITY = 10_000.0
    ladder.MAX_MULT = 4.0
    ladder.TF_ORDER = ("D", "4h", "1h")

    data = top._load_execution(
        "TTD", Path(args.npz_dir), "2024-01-01", "ladder"
    )
    source_hash = hashlib.sha256(Path(data.path).read_bytes()).hexdigest()
    if source_hash != NPZ_SHA256:
        raise RuntimeError(
            f"frozen TTD hash changed: {source_hash} != {NPZ_SHA256}"
        )
    if not data.contract["valid"]:
        raise RuntimeError(f"TTD source quarantined: {data.contract['errors']}")
    htfs = {
        tf: top._compress_htf(data, tf)
        for tf in ("15m", "1h", "4h", "D")
    }
    candidates = {
        bundle.label: build_exit_signal(data, htfs, bundle)
        for bundle in EXIT_BUNDLES
    }

    rows = []
    discovery_pass = True
    windows = ladder._outer_windows("TTD", data)
    for fold, (train_start, validation_start, validation_end) in enumerate(
        windows[:2], 1
    ):
        tl = ladder._date_index(data, train_start)
        tr = ladder._date_index(data, validation_start)
        vl = tr
        vr = ladder._date_index(data, validation_end)
        curve = _baseline_curve_for_fold(
            fold, data, htfs, tl, tr, args
        )
        entry = ladder._build_signals(data, htfs, curve, 30, "SHORT")
        baseline = _simulate(
            data,
            curve,
            entry,
            entry.exit_event,
            entry.exit_ref,
            vl,
            vr,
            args.commission_bps,
            args.slippage_bps,
        )
        expected = BASELINE_EXPECTED[fold]
        reproduction = {
            "alpha_error_pp": (
                baseline["deployed_alpha_vs_bh_or_cash_pp"]
                - expected["alpha"]
            ),
            "tim_error_pp": (
                baseline["exposure_weighted_tim_pct"] - expected["tim"]
            ),
        }
        if max(abs(x) for x in reproduction.values()) > 1e-8:
            raise RuntimeError(
                f"fold {fold} baseline reproduction drift: {reproduction}"
            )
        winner_label, winner, inner, score = _select_exit(
            data, curve, entry, candidates, tl, tr, args
        )
        event, ref, audit = _candidate_arrays(
            winner_label, entry, candidates
        )
        validation = _simulate(
            data,
            curve,
            entry,
            event,
            ref,
            vl,
            vr,
            args.commission_bps,
            args.slippage_bps,
        )
        failures = _gate(validation, args.tim_lo, args.tim_hi)
        validation_grid = _validation_grid(
            data, curve, entry, candidates, vl, vr, args
        )
        discovery_pass &= not failures
        rows.append(
            {
                "fold": fold,
                "train": [train_start, validation_start],
                "validation": [validation_start, validation_end],
                "frozen_entry_curve": dataclasses.asdict(curve),
                "baseline_e02_n30": baseline,
                "baseline_reproduction": reproduction,
                "regime_diagnosis": _regime_diagnosis(
                    data, htfs, vl, vr
                ),
                "selected_exit_bundle": winner,
                "selection_score": score,
                "inner_metrics": inner,
                "exit_signal_audit": audit,
                "validation_metrics": validation,
                "validation_failures": failures,
                "validation_pass": not failures,
                "diagnostic_validation_grid_not_selection_evidence": (
                    validation_grid
                ),
            }
        )

    fold3 = {
        "status": "SEALED",
        "reason": "discovery folds did not both pass every gate",
    }
    if discovery_pass:
        fold, (train_start, validation_start, validation_end) = 3, windows[2]
        tl = ladder._date_index(data, train_start)
        tr = ladder._date_index(data, validation_start)
        vl, vr = tr, ladder._date_index(data, validation_end)
        curve = _baseline_curve_for_fold(
            fold, data, htfs, tl, tr, args
        )
        entry = ladder._build_signals(data, htfs, curve, 30, "SHORT")
        winner_label, winner, inner, score = _select_exit(
            data, curve, entry, candidates, tl, tr, args
        )
        event, ref, audit = _candidate_arrays(
            winner_label, entry, candidates
        )
        validation = _simulate(
            data,
            curve,
            entry,
            event,
            ref,
            vl,
            vr,
            args.commission_bps,
            args.slippage_bps,
        )
        failures = _gate(validation, args.tim_lo, args.tim_hi)
        fold3 = {
            "status": "REVEALED",
            "fold": 3,
            "train": [train_start, validation_start],
            "validation": [validation_start, validation_end],
            "frozen_entry_curve": dataclasses.asdict(curve),
            "selected_exit_bundle": winner,
            "selection_score": score,
            "inner_metrics": inner,
            "exit_signal_audit": audit,
            "regime_diagnosis": _regime_diagnosis(data, htfs, vl, vr),
            "validation_metrics": validation,
            "validation_failures": failures,
            "validation_pass": not failures,
        }

    now = datetime.now(timezone.utc)
    out = Path(args.out_dir) / (
        "ttd_short_regime_exit_" + now.strftime("%Y%m%dT%H%M%SZ")
    )
    out.mkdir(parents=True, exist_ok=False)
    payload = {
        "contract": CONTRACT,
        "created_utc": now.isoformat(),
        "manifest": {
            "tier": "VEC_RESEARCH_GRAY",
            "matrix_eligible": False,
            "live_config_written": False,
            "canonical_engine_cells_written": False,
            "source": data.path,
            "npz_sha256": source_hash,
            "side": "SHORT",
            "capacity_usd": 8_000,
            "base_unit_usd": 2_000,
            "exit_candidate_count": len(EXIT_BUNDLES),
            "exit_search": "coherent regime bundles, not OFAT",
            "metric": (
                "pre-cost committed-fill-notional dollar-time alpha "
                "versus max(side-aware SHORT B&H, cash)"
            ),
            "negative_bh_ratio": "disabled",
            "execution": (
                "completed causal parent signal; next strictly later RTH fill"
            ),
            "reentry": (
                "persistent stored cover/bottom reclaim; zero forgotten bars"
            ),
            "discovery": "folds 1-2",
            "holdout": "fold 3 sealed unless every discovery gate passes",
            "tim_band_pct": [args.tim_lo, args.tim_hi],
        },
        "discovery_folds": rows,
        "discovery_pass": discovery_pass,
        "holdout_fold": fold3,
        "promotion_eligible": False,
        "ordinary_engine_parity": {
            "status": "NOT_RUN",
            "reason": (
                "vector discovery must pass before ordinary-engine/live parity"
            ),
        },
    }
    (out / "result.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )
    compact = (
        ROOT
        / "data/reports/vec_research"
        / "TTD_SHORT_REGIME_EXIT_RECEIPT_20260729.json"
    )
    compact.parent.mkdir(parents=True, exist_ok=True)
    compact.write_text(
        json.dumps(
            {
                "contract": CONTRACT,
                "artifact": str(out),
                "created_utc": payload["created_utc"],
                "npz_sha256": source_hash,
                "discovery_pass": discovery_pass,
                "folds": [
                    {
                        "fold": row["fold"],
                        "baseline": row["baseline_e02_n30"],
                        "regime": row["regime_diagnosis"],
                        "selected_exit_bundle": row[
                            "selected_exit_bundle"
                        ],
                        "validation_metrics": row["validation_metrics"],
                        "validation_failures": row[
                            "validation_failures"
                        ],
                    }
                    for row in rows
                ],
                "holdout_fold": fold3,
                "promotion_eligible": False,
            },
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    )
    data.z.close()
    print(
        json.dumps(
            {
                "artifact": str(out),
                "discovery_pass": discovery_pass,
                "folds": [
                    {
                        "fold": row["fold"],
                        "bundle": row["selected_exit_bundle"]["label"],
                        "alpha_pp": row["validation_metrics"][
                            "deployed_alpha_vs_bh_or_cash_pp"
                        ],
                        "tim_pct": row["validation_metrics"][
                            "exposure_weighted_tim_pct"
                        ],
                        "failures": row["validation_failures"],
                    }
                    for row in rows
                ],
                "holdout": fold3["status"],
            },
            sort_keys=True,
        )
    )
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--npz-dir", required=True)
    parser.add_argument(
        "--out-dir",
        default=str(ROOT / "data/reports/vec_research"),
    )
    parser.add_argument("--random-curves", type=int, default=80)
    parser.add_argument("--seed", type=int, default=20260729)
    parser.add_argument("--commission-bps", type=float, default=0.0)
    parser.add_argument("--slippage-bps", type=float, default=2.5)
    parser.add_argument("--tim-lo", type=float, default=65.0)
    parser.add_argument("--tim-hi", type=float, default=80.0)
    parser.add_argument("--tim-weight", type=float, default=4.0)
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
