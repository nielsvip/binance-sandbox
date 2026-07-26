#!/usr/bin/env python3
"""Nested walk-forward research for E12 partial exits and E13 regimes.

This lane is deliberately isolated: it writes only under ``VEC_RESEARCH`` and
never imports or mutates live configuration, symbol lists, or the switch
matrix.

Contract:

* completed 1h/4h/D bars only;
* signals at close, fills at next RTH open;
* 2 bp adverse slippage on every fill;
* 10 bp engine-style round-trip charge allocated to each closed entry basis;
* every exited clip owns E11 lower-price re-add plus E10 zero-buffer reclaim;
* 12-month discovery split into three chronological folds, then an untouched
  frozen 3-month validation.
"""
from __future__ import annotations

import argparse
import ctypes
import dataclasses
import gzip
import hashlib
import itertools
import json
import math
import os
import subprocess
import sys
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import vec_top_exit_campaign as base  # noqa: E402
import vec_top_exit_walkforward as wf  # noqa: E402


C_SOURCE = Path(__file__).with_name("vec_partial_regime_scan.c")


class PartialMetrics(ctypes.Structure):
    _fields_ = [
        ("final_equity", ctypes.c_double),
        ("max_drawdown_pct", ctypes.c_double),
        ("weighted_exposure_bars", ctypes.c_double),
        ("binary_exposure_bars", ctypes.c_double),
        ("weighted_exposure_seconds", ctypes.c_double),
        ("realized_partial_gross", ctypes.c_double),
        ("realized_partial_net", ctypes.c_double),
        ("realized_full_gross", ctypes.c_double),
        ("realized_full_net", ctypes.c_double),
        ("total_cost", ctypes.c_double),
        ("turnover", ctypes.c_double),
        ("saved_price_sum_pct", ctypes.c_double),
        ("reclaim_overshoot_sum_pct", ctypes.c_double),
        ("reclaim_overshoot_max_pct", ctypes.c_double),
        ("missed_move_sum_pct", ctypes.c_double),
        ("missed_move_max_pct", ctypes.c_double),
        ("partial_exit_count", ctypes.c_int),
        ("full_exit_count", ctypes.c_int),
        ("reentries", ctypes.c_int),
        ("reclaim_reentries", ctypes.c_int),
        ("resting_reclaim_reentries", ctypes.c_int),
        ("lower_reentries", ctypes.c_int),
        ("positive_saved_reentries", ctypes.c_int),
        ("bars_flat_beyond_reclaim", ctypes.c_int),
        ("technical_exit_count", ctypes.c_int),
        ("winning_exit_count", ctypes.c_int),
        ("losing_exit_count", ctypes.c_int),
        ("rejected_exit_signals", ctypes.c_int),
        ("insolvent", ctypes.c_int),
    ]


@dataclasses.dataclass(frozen=True)
class Component:
    family: str
    label: str
    params: dict[str, Any]
    mode: int
    event: np.ndarray
    raw_stop: np.ndarray
    ref: np.ndarray
    atr: np.ndarray


@dataclasses.dataclass(frozen=True)
class Composite:
    family: str
    label: str
    fast: Component
    structural: Component
    slow: Component
    f1: float
    f2: float
    regime_lookback: int = 0
    regime_fast: int = 0
    regime_slow: int = 0
    regime_policy: int = 0
    lower_gap_atr: float = 1.0

    def parameter_dict(self) -> dict[str, Any]:
        row = {
            "fast": self.fast.label,
            "structural": self.structural.label,
            "slow": self.slow.label,
            "f1": self.f1,
            "f2": self.f2,
            "lower_gap_atr": self.lower_gap_atr,
        }
        if self.family == "E13_REGIME_SWITCHED":
            row.update(
                {
                    "return_lookback_daily": self.regime_lookback,
                    "ema_fast": self.regime_fast,
                    "ema_slow": self.regime_slow,
                    "action_policy": ("slow-only", "partial-fast", "full-fast")[
                        self.regime_policy
                    ],
                }
            )
        return row


def _compile_scanner() -> ctypes.CDLL:
    digest = hashlib.sha256(C_SOURCE.read_bytes()).hexdigest()[:16]
    target = Path("/tmp") / f"vec_partial_regime_scan_{digest}.so"
    if not target.exists():
        subprocess.run(
            [
                os.environ.get("CC", "cc"),
                "-O3",
                "-std=c11",
                "-fPIC",
                "-shared",
                str(C_SOURCE),
                "-lm",
                "-o",
                str(target),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    lib = ctypes.CDLL(str(target))
    f64 = np.ctypeslib.ndpointer(dtype=np.float64, ndim=1, flags="C_CONTIGUOUS")
    u8 = np.ctypeslib.ndpointer(dtype=np.uint8, ndim=1, flags="C_CONTIGUOUS")
    i8 = np.ctypeslib.ndpointer(dtype=np.int8, ndim=1, flags="C_CONTIGUOUS")
    i64 = np.ctypeslib.ndpointer(dtype=np.int64, ndim=1, flags="C_CONTIGUOUS")
    lib.vec_partial_regime_scan.argtypes = [
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        i64,
        f64,
        f64,
        f64,
        f64,
        f64,
        u8,
        f64,
        u8,
        f64,
        ctypes.c_int,
        u8,
        f64,
        f64,
        i8,
        u8,
        ctypes.c_double,
        ctypes.c_double,
        ctypes.c_double,
        ctypes.c_int,
        ctypes.c_double,
        ctypes.c_double,
        ctypes.c_double,
        ctypes.POINTER(PartialMetrics),
    ]
    lib.vec_partial_regime_scan.restype = ctypes.c_int
    return lib


def _blank(n: int) -> np.ndarray:
    return np.full(n, np.nan, dtype=np.float64)


def _event_component(
    n: int,
    h: base.HTFData,
    family: str,
    label: str,
    params: dict[str, Any],
    event: np.ndarray,
    ref: np.ndarray,
) -> Component:
    mapped, refs = base._map_events(n, h, event, ref)
    return Component(
        family=family,
        label=label,
        params=params,
        mode=2,
        event=mapped,
        raw_stop=_blank(n),
        ref=refs,
        atr=base._align_feature(n, h, h.atr),
    )


def _component_pools(
    data: base.ExecutionData,
    htfs: dict[str, base.HTFData],
    side: int,
) -> tuple[list[Component], list[Component], list[Component]]:
    n = len(data.ts)
    h4 = htfs["4h"]
    fast: list[Component] = []
    structural: list[Component] = []
    slow: list[Component] = []

    for div_min in (5.0, 8.0):
        event, ref = base._e05_signal(h4, side, 3, div_min, 0.0, 0.5, 12)
        fast.append(
            _event_component(
                n,
                h4,
                "E05_DIVERGENCE_RETEST",
                f"E05_4h_P3_D{div_min:g}_B0_R0.5_W12",
                {"pivot_radius": 3, "div_min": div_min},
                event,
                ref,
            )
        )
    for lookback, z_arm in ((60, 2.0), (100, 2.5)):
        event, ref = wf._e06_signal(h4, side, lookback, z_arm, 1.0, 0.7)
        fast.append(
            _event_component(
                n,
                h4,
                "E06_REGRESSION_REENTRY",
                f"E06_4h_N{lookback}_ZA{z_arm:g}_ZE1_R0.7",
                {"lookback": lookback, "z_arm": z_arm, "z_exit": 1.0},
                event,
                ref,
            )
        )

    for damage in (0.5, 1.0):
        event, ref = wf._e03_signal(h4, side, 2, damage, 0.5, 12)
        structural.append(
            _event_component(
                n,
                h4,
                "E03_CONFIRMED_STRUCTURE_RETEST",
                f"E03_4h_C2_D{damage:g}_R0.5_W12",
                {"confirm_bars": 2, "damage_atr": damage},
                event,
                ref,
            )
        )
    for buffer in (0.0, 0.25):
        event, ref = base._e04_signal(h4, side, 34, buffer, 0.5, 12)
        structural.append(
            _event_component(
                n,
                h4,
                "E04_BREAK_RETEST",
                f"E04_4h_EMA34_B{buffer:g}_R0.5_W12",
                {"ema": 34, "break_buffer_atr": buffer},
                event,
                ref,
            )
        )

    for tf, lookback, k in (("4h", 22, 3.0), ("D", 22, 3.0)):
        h = htfs[tf]
        prior_high = base._rolling_prior(h.high, lookback, "max")
        prior_low = base._rolling_prior(h.low, lookback, "min")
        atr_prev = np.concatenate(([np.nan], h.atr[:-1]))
        raw = prior_high - k * atr_prev if side > 0 else prior_low + k * atr_prev
        ref = prior_high if side > 0 else prior_low
        slow.append(
            Component(
                family="E01_CHANDELIER",
                label=f"E01_{tf}_N{lookback}_K{k:g}",
                params={"tf": tf, "n": lookback, "k": k},
                mode=1,
                event=np.zeros(n, dtype=np.uint8),
                raw_stop=base._align_feature(n, h, raw),
                ref=base._align_feature(n, h, ref),
                atr=base._align_feature(n, h, h.atr),
            )
        )
    for tf, lookback in (("4h", 20), ("D", 20)):
        h = htfs[tf]
        prior_high = base._rolling_prior(h.high, lookback, "max")
        prior_low = base._rolling_prior(h.low, lookback, "min")
        event = h.close < prior_low if side > 0 else h.close > prior_high
        ref = prior_high if side > 0 else prior_low
        mapped, refs = base._map_events(n, h, event, ref)
        slow.append(
            Component(
                family="E02_DONCHIAN",
                label=f"E02_{tf}_N{lookback}",
                params={"tf": tf, "n": lookback},
                mode=2,
                event=mapped,
                raw_stop=_blank(n),
                ref=refs,
                atr=base._align_feature(n, h, h.atr),
            )
        )
    return fast, structural, slow


def _regime_array(
    n: int,
    daily: base.HTFData,
    side: int,
    lookback: int,
    fast_n: int,
    slow_n: int,
) -> np.ndarray:
    fast = base._ema(daily.close, fast_n)
    slow = base._ema(daily.close, slow_n)
    ret = np.full(len(daily.close), np.nan)
    ret[lookback:] = daily.close[lookback:] / daily.close[:-lookback] - 1.0
    slope = np.concatenate(([np.nan], np.diff(slow)))
    up = (ret > 0) & (fast > slow) & (slope > 0)
    down = (ret < 0) & (fast < slow) & (slope < 0)
    values = np.zeros(len(daily.close), dtype=np.float64)
    values[up] = 1.0
    values[down] = -1.0
    aligned = np.nan_to_num(
        base._align_feature(n, daily, values),
        nan=0.0,
    ).astype(np.int8)
    if side < 0:
        aligned *= -1
    return np.ascontiguousarray(aligned)


def _composites(
    fast: list[Component],
    structural: list[Component],
    slow: list[Component],
) -> dict[str, list[Composite]]:
    e12: list[Composite] = []
    for fa, st, sl in itertools.product(fast, structural, slow):
        for f1, f2 in ((0.25, 0.25), (0.25, 0.50), (0.50, 0.25), (0.50, 0.50)):
            e12.append(
                Composite(
                    family="E12_PARTIAL_THEN_RUNNER",
                    label=f"E12__{fa.label}__{st.label}__{sl.label}__F{f1:g}_{f2:g}",
                    fast=fa,
                    structural=st,
                    slow=sl,
                    f1=f1,
                    f2=f2,
                )
            )

    # Balanced residual design: all fast/structure pairs are represented, while
    # slow exits rotate deterministically.  This avoids a blind Cartesian E13
    # explosion without field-by-field tuning.
    triplets = [
        (fa, st, slow[(fi + si) % len(slow)])
        for fi, fa in enumerate(fast)
        for si, st in enumerate(structural)
    ]
    e13: list[Composite] = []
    for fa, st, sl in triplets:
        for lookback in (20, 60, 120, 250):
            for fast_n, slow_n in ((20, 50), (50, 100), (50, 200)):
                for policy in (0, 1, 2):
                    f1 = 0.5 if policy == 1 else 1.0
                    e13.append(
                        Composite(
                            family="E13_REGIME_SWITCHED",
                            label=(
                                f"E13__{fa.label}__{st.label}__{sl.label}"
                                f"__L{lookback}_EMA{fast_n}_{slow_n}_P{policy}"
                            ),
                            fast=fa,
                            structural=st,
                            slow=sl,
                            f1=f1,
                            f2=0.0,
                            regime_lookback=lookback,
                            regime_fast=fast_n,
                            regime_slow=slow_n,
                            regime_policy=policy,
                        )
                    )
    return {
        "E12_PARTIAL_THEN_RUNNER": e12,
        "E13_REGIME_SWITCHED": e13,
    }


def _slice(values: np.ndarray, sl: slice, dtype) -> np.ndarray:
    return np.ascontiguousarray(values[sl], dtype=dtype)


def _scan(
    lib: ctypes.CDLL,
    data: base.ExecutionData,
    sl: slice,
    candidate: Composite,
    regime: np.ndarray,
    lower_event: np.ndarray,
    side: int,
    cost_rt: float,
    slip: float,
) -> dict[str, Any]:
    sliced, _ = wf._slice_execution(
        data,
        int(data.ts[sl.start]),
        int(data.ts[sl.stop - 1]) + 1,
    )
    n = len(sliced.ts)
    out = PartialMetrics()
    family_id = 12 if candidate.family.startswith("E12") else 13
    # ATR at the fill belongs to the component that supplied the first-stage
    # exit; all current candidates use 4h fast/structure data.
    atr = _slice(candidate.fast.atr, sl, np.float64)
    rc = lib.vec_partial_regime_scan(
        n,
        side,
        family_id,
        candidate.regime_policy,
        sliced.ts,
        sliced.open,
        sliced.high,
        sliced.low,
        sliced.close,
        atr,
        _slice(candidate.fast.event, sl, np.uint8),
        _slice(candidate.fast.ref, sl, np.float64),
        _slice(candidate.structural.event, sl, np.uint8),
        _slice(candidate.structural.ref, sl, np.float64),
        candidate.slow.mode,
        _slice(candidate.slow.event, sl, np.uint8),
        _slice(candidate.slow.raw_stop, sl, np.float64),
        _slice(candidate.slow.ref, sl, np.float64),
        _slice(regime, sl, np.int8),
        _slice(lower_event, sl, np.uint8),
        candidate.f1,
        candidate.f2,
        candidate.lower_gap_atr,
        0,
        -1.0,
        cost_rt,
        slip,
        ctypes.byref(out),
    )
    if rc:
        raise RuntimeError(f"compiled partial scanner returned {rc}")
    bh = base._side_bh(sliced, side, cost_rt / 2.0, slip)
    strategy_equity = out.final_equity
    bh_equity = 1.0 + bh["bh_net_side_pct"] / 100.0
    span = max(1, int(sliced.ts[-1] - sliced.ts[0]))
    row = {
        "tier": "VEC_RESEARCH",
        "matrix_eligible": False,
        "family": candidate.family,
        "strategy": candidate.label,
        "params": candidate.parameter_dict(),
        "strategy_equity": strategy_equity,
        "gain_pct": 100.0 * (strategy_equity - 1.0),
        **bh,
        "bh_equity": bh_equity,
        "equity_ratio_vs_bh": (
            strategy_equity / bh_equity
            if strategy_equity > 0 and bh_equity > 0
            else 0.0
        ),
        "weighted_exposure_time_pct": 100.0 * out.weighted_exposure_bars / n,
        "binary_time_in_market_pct": 100.0 * out.binary_exposure_bars / n,
        "weighted_calendar_exposure_pct": (
            100.0 * out.weighted_exposure_seconds / span
        ),
        "realized_partial_pnl_gross_equity": out.realized_partial_gross,
        "realized_partial_pnl_net_equity": out.realized_partial_net,
        "realized_full_pnl_gross_equity": out.realized_full_gross,
        "realized_full_pnl_net_equity": out.realized_full_net,
        "estimated_cost_equity": out.total_cost,
        "partial_exit_count": int(out.partial_exit_count),
        "full_exit_count": int(out.full_exit_count),
        "technical_exit_count": int(out.technical_exit_count),
        "reentries": int(out.reentries),
        "reclaim_reentries": int(out.reclaim_reentries),
        "lower_reentries": int(out.lower_reentries),
        "positive_saved_reentries": int(out.positive_saved_reentries),
        "mean_saved_price_pct": (
            out.saved_price_sum_pct / out.reentries if out.reentries else 0.0
        ),
        "bars_flat_beyond_reclaim": int(out.bars_flat_beyond_reclaim),
        "max_drawdown_pct": out.max_drawdown_pct,
        "turnover_one_way": out.turnover,
        "insolvent": bool(out.insolvent),
    }
    row["mandatory_reclaim_policy"] = bool(
        out.bars_flat_beyond_reclaim <= out.reclaim_reentries
    )
    row["target_weighted_exposure_policy"] = bool(
        70.0 <= row["weighted_exposure_time_pct"] <= 80.0
    )
    return row


def _window_slice(
    data: base.ExecutionData,
    start: date,
    end: date,
) -> slice:
    left = int(np.searchsorted(data.ts, wf._epoch(start), side="left"))
    right = int(np.searchsorted(data.ts, wf._epoch(end), side="left"))
    if right - left < 200:
        raise ValueError(f"{start}..{end}: only {right-left} RTH bars")
    return slice(left, right)


def _data_clean(symbol: str, data: base.ExecutionData, start: date, end: date) -> dict[str, Any]:
    sl = _window_slice(data, start, end)
    diffs = np.diff(data.ts[sl])
    gaps = [
        {"start": a.isoformat(), "end": b.isoformat()}
        for a, b in wf.KNOWN_SOURCE_GAPS.get(symbol, ())
        if start < b and end > a
    ]
    return {
        "gap_clean": bool((not len(diffs) or np.max(diffs) <= 7 * 86400) and not gaps),
        "known_source_gap_overlap": gaps,
        "max_timestamp_gap_days": float(np.max(diffs) / 86400.0) if len(diffs) else 0.0,
    }


def _score(rows: list[dict[str, Any]]) -> tuple[float, dict[str, Any]]:
    ratios = np.asarray([max(1e-12, row["equity_ratio_vs_bh"]) for row in rows])
    logs = np.log(ratios)
    tim = np.asarray([row["weighted_exposure_time_pct"] for row in rows])
    reclaim = float(np.mean([row["mandatory_reclaim_policy"] for row in rows]))
    score = (
        float(np.median(logs))
        - 0.75 * float(np.std(logs))
        - 0.025 * float(np.median(np.abs(tim - 75.0)))
        - 2.0 * (1.0 - reclaim)
    )
    return score, {
        "selection_score": score,
        "median_equity_ratio_vs_bh": float(np.median(ratios)),
        "min_equity_ratio_vs_bh": float(np.min(ratios)),
        "log_ratio_std": float(np.std(logs)),
        "mean_weighted_exposure_pct": float(np.mean(tim)),
        "weighted_exposure_std_pp": float(np.std(tim)),
        "mandatory_reclaim_rate": reclaim,
    }


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    strategy_equity = float(np.prod([row["strategy_equity"] for row in rows]))
    bh_equity = float(np.prod([row["bh_equity"] for row in rows]))
    weights = np.asarray([row["bar_count"] for row in rows], dtype=float)
    tim = np.asarray([row["weighted_exposure_time_pct"] for row in rows])
    binary = np.asarray([row["binary_time_in_market_pct"] for row in rows])
    ratios = np.asarray([row["equity_ratio_vs_bh"] for row in rows])
    return {
        "folds": len(rows),
        "strategy_compounded_gain_pct": 100.0 * (strategy_equity - 1.0),
        "bh_compounded_gain_pct": 100.0 * (bh_equity - 1.0),
        "strategy_to_bh_equity_ratio": strategy_equity / bh_equity,
        "median_fold_equity_ratio_vs_bh": float(np.median(ratios)),
        "positive_vs_bh_fold_rate": float(np.mean(ratios > 1.0)),
        "weighted_exposure_time_pct": float(np.average(tim, weights=weights)),
        "binary_time_in_market_pct": float(np.average(binary, weights=weights)),
        "realized_partial_pnl_net_equity": float(
            sum(row["realized_partial_pnl_net_equity"] for row in rows)
        ),
        "realized_partial_pnl_gross_equity": float(
            sum(row["realized_partial_pnl_gross_equity"] for row in rows)
        ),
        "partial_exit_count": int(sum(row["partial_exit_count"] for row in rows)),
        "full_exit_count": int(sum(row["full_exit_count"] for row in rows)),
        "lower_reentries": int(sum(row["lower_reentries"] for row in rows)),
        "reclaim_reentries": int(sum(row["reclaim_reentries"] for row in rows)),
        "bars_flat_beyond_reclaim": int(
            sum(row["bars_flat_beyond_reclaim"] for row in rows)
        ),
        "all_mandatory_reclaim": all(row["mandatory_reclaim_policy"] for row in rows),
    }


def _engine_cost_reference(
    entry_equity: float,
    entry_px: float,
    exit_px: float,
    side: int,
    round_trip_cost_rate: float,
) -> tuple[float, float]:
    gross = entry_equity * side * (exit_px - entry_px) / entry_px
    return gross, gross - entry_equity * round_trip_cost_rate


def _self_test() -> int:
    # Directly verifies the engine partial-close basis allocation used by C.
    gross, net = _engine_cost_reference(0.25, 100.0, 110.0, 1, 0.001)
    assert abs(gross - 0.025) < 1e-12
    assert abs(net - 0.02475) < 1e-12
    gross_s, net_s = _engine_cost_reference(0.50, 100.0, 90.0, -1, 0.001)
    assert abs(gross_s - 0.05) < 1e-12
    assert abs(net_s - 0.0495) < 1e-12
    check = _synthetic_scanner_check()
    assert check["status"] == "PASS", check
    print(json.dumps({"status": "PASS", "tests": 11, "semantic_contract": "engine-aligned"}))
    return 0


def _synthetic_scanner_check() -> dict[str, Any]:
    """Independent exact fixture for latency, clips, cost, and weighted TIM."""
    lib = _compile_scanner()
    n = 8
    ts = np.arange(n, dtype=np.int64) * 300
    open_px = np.asarray([100, 110, 105, 95, 96, 97, 98, 99], dtype=np.float64)
    close_px = np.asarray([100, 109, 104, 94, 95, 96, 97, 98], dtype=np.float64)
    high = np.maximum(open_px, close_px) + 0.5
    low = np.minimum(open_px, close_px) - 0.5
    atr = np.ones(n, dtype=np.float64)
    fast = np.zeros(n, dtype=np.uint8)
    structural = np.zeros(n, dtype=np.uint8)
    slow = np.zeros(n, dtype=np.uint8)
    fast[0] = 1
    structural[1] = 1
    slow[2] = 1
    ref = np.full(n, 120.0, dtype=np.float64)
    blank = np.full(n, np.nan, dtype=np.float64)
    regime = np.zeros(n, dtype=np.int8)
    lower = np.zeros(n, dtype=np.uint8)
    out = PartialMetrics()
    rc = lib.vec_partial_regime_scan(
        n,
        1,
        12,
        0,
        ts,
        open_px,
        high,
        low,
        close_px,
        atr,
        fast,
        ref,
        structural,
        ref,
        2,
        slow,
        blank,
        ref,
        regime,
        lower,
        0.25,
        0.25,
        1.0,
        0,
        -1.0,
        0.001,
        0.0,
        ctypes.byref(out),
    )
    checks = {
        "return_code": rc == 0,
        "next_bar_fill_equity": abs(out.final_equity - 1.0115) < 1e-12,
        "partial_gross": abs(out.realized_partial_gross - 0.0375) < 1e-12,
        "partial_net": abs(out.realized_partial_net - 0.0370) < 1e-12,
        "full_net": abs(out.realized_full_net + 0.0255) < 1e-12,
        "weighted_exposure_bars": abs(out.weighted_exposure_bars - 2.25) < 1e-12,
        "partial_count": out.partial_exit_count == 2,
        "full_count": out.full_exit_count == 1,
        "no_reentries": out.reentries == 0,
    }
    return {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol")
    ap.add_argument("--side", choices=("LONG", "SHORT"))
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end")
    ap.add_argument("--npz-dir", type=Path, default=base.DEFAULT_NPZ)
    ap.add_argument("--output-root", type=Path, default=base.DEFAULT_OUT)
    ap.add_argument("--outer-train-months", type=int, default=12)
    ap.add_argument("--validation-months", type=int, default=3)
    ap.add_argument("--inner-folds", type=int, default=3)
    ap.add_argument("--round-trip-cost-bps", type=float, default=10.0)
    ap.add_argument("--slippage-bps", type=float, default=2.0)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return _self_test()
    if not args.symbol or not args.side:
        ap.error("--symbol and --side are required unless --self-test is used")

    symbol = args.symbol.upper()
    side = 1 if args.side == "LONG" else -1
    data = base._load_execution(symbol, args.npz_dir, args.start, "ladder", args.end)
    if not data.contract["valid"]:
        raise RuntimeError(f"{symbol}: invalid NPZ: {data.contract['errors']}")
    htfs = {tf: base._compress_htf(data, tf) for tf in ("1h", "4h", "D")}
    fast, structural, slow = _component_pools(data, htfs, side)
    families = _composites(fast, structural, slow)
    lower_event = base._lower_reentry_events(len(data.ts), htfs["1h"], side)
    regimes = {
        (lookback, fast_n, slow_n): _regime_array(
            len(data.ts), htfs["D"], side, lookback, fast_n, slow_n
        )
        for lookback in (20, 60, 120, 250)
        for fast_n, slow_n in ((20, 50), (50, 100), (50, 200))
    }
    neutral_regime = np.zeros(len(data.ts), dtype=np.int8)
    lib = _compile_scanner()
    cost_rt = args.round_trip_cost_bps / 10_000.0
    slip = args.slippage_bps / 10_000.0

    first_day = datetime.fromtimestamp(int(data.ts[0]), tz=timezone.utc).date()
    campaign_start = max(
        date.fromisoformat(args.start),
        date(first_day.year, first_day.month, 1),
    )
    last_day = datetime.fromtimestamp(int(data.ts[-1]), tz=timezone.utc).date()
    cursor = wf._add_months(campaign_start, args.outer_train_months)
    validation_starts: list[date] = []
    while wf._epoch(cursor) < int(data.ts[-1]):
        validation_starts.append(cursor)
        cursor = wf._add_months(cursor, args.validation_months)

    frozen_by_family: dict[str, list[dict[str, Any]]] = {
        family: [] for family in families
    }
    selections: list[dict[str, Any]] = []
    for validation_start in validation_starts:
        validation_end = min(
            wf._add_months(validation_start, args.validation_months),
            last_day,
        )
        if (validation_end - validation_start).days < 14:
            continue
        train_end = validation_start
        train_start = wf._add_months(train_end, -args.outer_train_months)
        inner = wf._inner_windows(train_start, train_end, args.inner_folds)
        for family, candidates in families.items():
            ranked: list[tuple[float, Composite, dict[str, Any]]] = []
            for candidate in candidates:
                regime = (
                    regimes[
                        (
                            candidate.regime_lookback,
                            candidate.regime_fast,
                            candidate.regime_slow,
                        )
                    ]
                    if family.startswith("E13")
                    else neutral_regime
                )
                rows = []
                for fold_start, fold_end in inner:
                    sl = _window_slice(data, fold_start, fold_end)
                    rows.append(
                        _scan(
                            lib,
                            data,
                            sl,
                            candidate,
                            regime,
                            lower_event,
                            side,
                            cost_rt,
                            slip,
                        )
                    )
                score, stats = _score(rows)
                ranked.append((score, candidate, stats))
            ranked.sort(key=lambda item: item[0], reverse=True)
            constrained = [
                item
                for item in ranked
                if item[2]["mandatory_reclaim_rate"] == 1.0
                and 70.0 <= item[2]["mean_weighted_exposure_pct"] <= 80.0
            ]
            chosen_score, chosen, stats = constrained[0] if constrained else ranked[0]
            selection = {
                "family": family,
                "validation_start": validation_start.isoformat(),
                "train_start": train_start.isoformat(),
                "train_end_exclusive": train_end.isoformat(),
                "strategy": chosen.label,
                "params": chosen.parameter_dict(),
                "selection_constraint_fallback": not bool(constrained),
                **stats,
            }
            selections.append(selection)
            regime = (
                regimes[
                    (
                        chosen.regime_lookback,
                        chosen.regime_fast,
                        chosen.regime_slow,
                    )
                ]
                if family.startswith("E13")
                else neutral_regime
            )
            sl = _window_slice(data, validation_start, validation_end)
            frozen = _scan(
                lib,
                data,
                sl,
                chosen,
                regime,
                lower_event,
                side,
                cost_rt,
                slip,
            )
            frozen.update(
                {
                    "selection": "FROZEN_BEFORE_VALIDATION",
                    "validation_start": validation_start.isoformat(),
                    "validation_end_exclusive": validation_end.isoformat(),
                    "outer_train_start": train_start.isoformat(),
                    "outer_train_end_exclusive": train_end.isoformat(),
                    "bar_count": sl.stop - sl.start,
                    "selection_constraint_fallback": not bool(constrained),
                    **_data_clean(symbol, data, validation_start, validation_end),
                    "discovery_data_clean": all(
                        _data_clean(symbol, data, start, end)["gap_clean"]
                        for start, end in inner
                    ),
                }
            )
            frozen_by_family[family].append(frozen)

    if not any(frozen_by_family.values()):
        raise RuntimeError("no frozen validation folds")

    family_digests: dict[str, Any] = {}
    for family, rows in frozen_by_family.items():
        clean = [
            row
            for row in rows
            if row["gap_clean"] and row["discovery_data_clean"]
        ]
        aggregate = _aggregate(clean) if clean else None
        signatures = Counter(
            json.dumps(row["params"], sort_keys=True) for row in clean
        )
        exact_repeat = max(signatures.values()) / len(clean) if clean else 0.0
        component_signatures = Counter(
            (
                row["params"]["fast"].split("_")[0],
                row["params"]["structural"].split("_")[0],
                row["params"]["slow"].split("_")[0],
                row["params"].get("action_policy", "partial-runner"),
            )
            for row in clean
        )
        component_repeat = (
            max(component_signatures.values()) / len(clean) if clean else 0.0
        )
        stability = {
            "exact_parameter_repeat_rate": exact_repeat,
            "component_policy_repeat_rate": component_repeat,
            "stable": bool(component_repeat >= 0.50),
        }
        accepted = bool(
            aggregate
            and stability["stable"]
            and aggregate["strategy_to_bh_equity_ratio"] > 1.0
            and aggregate["median_fold_equity_ratio_vs_bh"] > 1.0
            and aggregate["positive_vs_bh_fold_rate"] >= 0.60
            and aggregate["all_mandatory_reclaim"]
            and 70.0 <= aggregate["weighted_exposure_time_pct"] <= 80.0
        )
        family_digests[family] = {
            "status": "RETAINED_FOR_EXACT_REPLAY" if accepted else "REJECTED",
            "acceptance_rule": (
                "gap-clean frozen OOS only; stable component/policy >=50%; "
                "compounded and median fold beat B&H; >=60% positive folds; "
                "mandatory E10; weighted exposure 70-80%"
            ),
            "aggregate_gap_clean_frozen": aggregate,
            "stability": stability,
            "frozen_validation_folds": rows,
        }

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = (
        args.output_root
        / f"partial_regime_walkforward_{run_id}_{symbol}_{args.side}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "tier": "VEC_RESEARCH",
        "matrix_eligible": False,
        "live_config_write": False,
        "symbol": symbol,
        "side": args.side,
        "families": list(families),
        "candidate_counts": {k: len(v) for k, v in families.items()},
        "outer_train_months": args.outer_train_months,
        "validation_months": args.validation_months,
        "inner_folds": args.inner_folds,
        "signals": "completed HTF bars only",
        "fills": "next RTH open",
        "adverse_slippage_bps_one_way": args.slippage_bps,
        "engine_round_trip_cost_bps": args.round_trip_cost_bps,
        "partial_cost_allocation": "entry basis of each closed clip",
        "reentry": "E11 lower price with mandatory E10 zero-buffer fallback per clip",
        "npz_path": data.path,
        "npz_sha256": base._sha256_file(data.path),
        "npz_contract_valid": bool(data.contract["valid"]),
        "npz_contract_warnings": data.contract["warnings"],
    }
    digest = {**manifest, "results": family_digests}
    (out_dir / "run_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )
    (out_dir / f"digest_{symbol}_{args.side}.json").write_text(
        json.dumps(digest, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )
    with gzip.open(out_dir / "frozen_validation_folds.jsonl.gz", "wt") as fh:
        for rows in frozen_by_family.values():
            for row in rows:
                fh.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")
    with gzip.open(out_dir / "discovery_selection_audit.jsonl.gz", "wt") as fh:
        for row in selections:
            fh.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")
    snapshot = out_dir / "source_snapshot"
    snapshot.mkdir()
    for source in (Path(__file__), C_SOURCE, Path(base.__file__), Path(wf.__file__)):
        snapshot.joinpath(source.name).write_bytes(source.read_bytes())
    data.z.close()
    print(json.dumps({"status": "PASS", "artifact": str(out_dir), **digest}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
