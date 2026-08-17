#!/usr/bin/env python3
"""Nested walk-forward research for causal top exits.

This is an isolated VEC_RESEARCH lane.  It imports the accounting scanner from
``vec_top_exit_campaign.py`` but never writes the production switch matrix,
symbol config, or live state.

Implemented here:

* E03: confirmed HTF lower-high/lower-low (or higher-high/higher-low for SHORT)
  damage arms an obligation; a rebound/retest is observed; exit occurs only on
  the subsequent failed-retest structure bar.
* E09: completed 4h/D RSI+ATR exhaustion arms an obligation; a completed 1h
  adverse structure bar executes the exit.
* E06: prior-only rolling log-price regression excursion arms an obligation;
  a later channel re-entry or adverse bar break executes the exit.
* E08: a completed-HTF MFE threshold activates a monotonic ATR profit lock;
  before activation it cannot exit.

Every evaluated candidate uses E11 lower-price re-entry plus E10 mandatory
zero-buffer reclaim.  Selection is nested: each rolling outer training window
is split into chronological inner folds, a robust candidate is selected using
only those folds, and that exact candidate is frozen for the next outer
validation window.
"""
from __future__ import annotations

import argparse
import dataclasses
import gzip
import json
import math
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import numpy as np

import vec_top_exit_campaign as base


# Source-provenance gaps established by the repaired NPZ audit.  Continuous
# interpolated execution timestamps can otherwise hide these gaps, so timestamp
# continuity alone is not a sufficient validation-quality test.
KNOWN_SOURCE_GAPS: dict[str, tuple[tuple[date, date], ...]] = {
    "VT": ((date(2026, 3, 30), date(2026, 6, 8)),),
}


def _add_months(value: date, months: int) -> date:
    month0 = value.year * 12 + value.month - 1 + months
    year, month_index = divmod(month0, 12)
    return date(year, month_index + 1, 1)


def _epoch(value: date) -> int:
    return int(datetime(value.year, value.month, value.day, tzinfo=timezone.utc).timestamp())


def _slice_execution(
    data: base.ExecutionData,
    start_epoch: int,
    end_epoch: int,
) -> tuple[base.ExecutionData, slice]:
    left = int(np.searchsorted(data.ts, start_epoch, side="left"))
    right = int(np.searchsorted(data.ts, end_epoch, side="left"))
    if right - left < 200:
        raise ValueError(f"window has only {right-left} RTH rows")
    sl = slice(left, right)
    return (
        base.ExecutionData(
            symbol=data.symbol,
            path=data.path,
            ts=np.ascontiguousarray(data.ts[sl]),
            open=np.ascontiguousarray(data.open[sl]),
            high=np.ascontiguousarray(data.high[sl]),
            low=np.ascontiguousarray(data.low[sl]),
            close=np.ascontiguousarray(data.close[sl]),
            synthetic=np.ascontiguousarray(data.synthetic[sl]),
            full_indices=np.arange(right - left, dtype=np.int64),
            z=None,
            contract=data.contract,
        ),
        sl,
    )


def _slice_candidate(candidate: base.ExitCandidate, sl: slice) -> base.ExitCandidate:
    return base.ExitCandidate(
        family=candidate.family,
        label=candidate.label,
        params=candidate.params,
        exit_mode=candidate.exit_mode,
        exit_event=np.ascontiguousarray(candidate.exit_event[sl]),
        raw_stop=np.ascontiguousarray(candidate.raw_stop[sl]),
        struct_ref=np.ascontiguousarray(candidate.struct_ref[sl]),
        atr_exec=np.ascontiguousarray(candidate.atr_exec[sl]),
    )


def _blank_stop(n: int) -> np.ndarray:
    return np.full(n, np.nan, dtype=np.float64)


def _e03_signal(
    h: base.HTFData,
    side: int,
    confirm_bars: int,
    damage_atr: float,
    rebound_atr: float,
    max_wait: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Arm on confirmed structure damage; exit on failed rebound/retest.

    The arm bar cannot also exit.  The rebound bar cannot also exit.  This
    explicitly prevents the old failure mode where the favorable retest bar was
    itself treated as the exit.
    """
    n = len(h.close)
    event = np.zeros(n, dtype=np.uint8)
    ref = np.full(n, np.nan, dtype=np.float64)
    state = 0  # 0 unarmed, 1 waiting rebound, 2 waiting failed retest
    wait = 0
    post_extreme = math.nan
    retest_extreme = math.nan

    for j in range(max(2, confirm_bars), n):
        atr = h.atr[j - 1]
        if not (math.isfinite(atr) and atr > 0):
            continue
        if state == 0:
            start = j - confirm_bars + 1
            if side > 0:
                structure = bool(
                    np.all(h.high[start : j + 1] < h.high[start - 1 : j])
                    and np.all(h.low[start : j + 1] < h.low[start - 1 : j])
                )
                damage = (np.max(h.high[max(0, j - 12) : j + 1]) - h.close[j]) / atr
            else:
                structure = bool(
                    np.all(h.high[start : j + 1] > h.high[start - 1 : j])
                    and np.all(h.low[start : j + 1] > h.low[start - 1 : j])
                )
                damage = (h.close[j] - np.min(h.low[max(0, j - 12) : j + 1])) / atr
            if structure and damage >= damage_atr:
                state = 1
                wait = 0
                post_extreme = h.low[j] if side > 0 else h.high[j]
                retest_extreme = h.high[j] if side > 0 else h.low[j]
            continue

        wait += 1
        if side > 0:
            post_extreme = min(post_extreme, h.low[j])
            retest_extreme = max(retest_extreme, h.high[j])
            rebound = h.high[j] - post_extreme >= rebound_atr * atr
            failed = (
                state == 2
                and h.high[j] < h.high[j - 1]
                and h.low[j] < h.low[j - 1]
                and h.close[j] < h.low[j - 1]
            )
            invalid = h.close[j] > retest_extreme + 0.5 * atr
        else:
            post_extreme = max(post_extreme, h.high[j])
            retest_extreme = min(retest_extreme, h.low[j])
            rebound = post_extreme - h.low[j] >= rebound_atr * atr
            failed = (
                state == 2
                and h.high[j] > h.high[j - 1]
                and h.low[j] > h.low[j - 1]
                and h.close[j] > h.high[j - 1]
            )
            invalid = h.close[j] < retest_extreme - 0.5 * atr

        if failed:
            event[j] = 1
            ref[j] = retest_extreme
            state = 0
        elif state == 1 and rebound:
            # A distinct later bar must confirm failure.
            state = 2
        elif invalid or wait >= max_wait:
            state = 0
    return event, ref


def _e03_candidates(
    data: base.ExecutionData,
    h: base.HTFData,
    side: int,
    quick: bool,
) -> Iterator[base.ExitCandidate]:
    n = len(data.ts)
    for confirm_bars in ((2,) if quick else (2, 3)):
        for damage_atr in ((1.0,) if quick else (0.5, 1.0, 1.5)):
            for rebound_atr in ((0.5,) if quick else (0.25, 0.5, 1.0)):
                for max_wait in ((12,) if quick else (8, 12, 20)):
                    event, ref = _e03_signal(
                        h, side, confirm_bars, damage_atr, rebound_atr, max_wait
                    )
                    mapped, refs = base._map_events(n, h, event, ref)
                    yield base.ExitCandidate(
                        family="E03_CONFIRMED_STRUCTURE_RETEST",
                        label=(
                            f"E03_4h_C{confirm_bars}_D{damage_atr:g}_"
                            f"R{rebound_atr:g}_W{max_wait}"
                        ),
                        params={
                            "tf": "4h",
                            "confirm_bars": confirm_bars,
                            "damage_atr": damage_atr,
                            "rebound_atr": rebound_atr,
                            "max_wait": max_wait,
                        },
                        exit_mode=2,
                        exit_event=mapped,
                        raw_stop=_blank_stop(n),
                        struct_ref=refs,
                        atr_exec=base._align_feature(n, h, h.atr),
                    )


def _e09_signal(
    arm_h: base.HTFData,
    trigger_h: base.HTFData,
    side: int,
    rsi_threshold: float,
    extension_atr: float,
    expiry_1h: int,
    confirm_bars: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Completed HTF exhaustion arm followed by completed 1h structure trigger."""
    ema = base._ema(arm_h.close, 20)
    arm_event = np.zeros(len(arm_h.close), dtype=np.uint8)
    for j in range(1, len(arm_h.close)):
        atr = arm_h.atr[j - 1]
        if not (math.isfinite(atr) and atr > 0 and math.isfinite(arm_h.rsi[j])):
            continue
        if side > 0:
            arm_event[j] = (
                arm_h.rsi[j] >= rsi_threshold
                and arm_h.close[j] >= ema[j] + extension_atr * atr
            )
        else:
            arm_event[j] = (
                arm_h.rsi[j] <= 100.0 - rsi_threshold
                and arm_h.close[j] <= ema[j] - extension_atr * atr
            )

    # Match each completed 1h bar to the latest already-completed arm bar.
    arm_slot = np.searchsorted(arm_h.source_ts, trigger_h.source_ts, side="right") - 1
    event = np.zeros(len(trigger_h.close), dtype=np.uint8)
    ref = np.full(len(trigger_h.close), np.nan, dtype=np.float64)
    armed_until = -1
    armed_extreme = math.nan
    last_arm_slot = -1
    for j in range(max(2, confirm_bars), len(trigger_h.close)):
        slot = int(arm_slot[j])
        if slot >= 0 and slot != last_arm_slot:
            # Process all newly available HTF closes, preserving causality.
            begin = max(last_arm_slot + 1, 0)
            newly_armed = np.flatnonzero(arm_event[begin : slot + 1])
            if len(newly_armed):
                armed_until = j + expiry_1h
                armed_extreme = (
                    trigger_h.high[j] if side > 0 else trigger_h.low[j]
                )
            last_arm_slot = slot
        if j > armed_until:
            continue
        if side > 0:
            armed_extreme = max(armed_extreme, trigger_h.high[j])
            start = j - confirm_bars + 1
            trigger = bool(
                np.all(trigger_h.high[start : j + 1] < trigger_h.high[start - 1 : j])
                and np.all(trigger_h.low[start : j + 1] < trigger_h.low[start - 1 : j])
                and trigger_h.close[j] < trigger_h.low[j - 1]
            )
        else:
            armed_extreme = min(armed_extreme, trigger_h.low[j])
            start = j - confirm_bars + 1
            trigger = bool(
                np.all(trigger_h.high[start : j + 1] > trigger_h.high[start - 1 : j])
                and np.all(trigger_h.low[start : j + 1] > trigger_h.low[start - 1 : j])
                and trigger_h.close[j] > trigger_h.high[j - 1]
            )
        if trigger:
            event[j] = 1
            ref[j] = armed_extreme
            armed_until = -1
    return event, ref


def _e09_candidates(
    data: base.ExecutionData,
    htfs: dict[str, base.HTFData],
    side: int,
    quick: bool,
) -> Iterator[base.ExitCandidate]:
    n = len(data.ts)
    trigger = htfs["1h"]
    for arm_tf in ("4h", "D"):
        arm = htfs[arm_tf]
        for rsi_threshold in ((70.0,) if quick else (65.0, 70.0, 75.0)):
            for extension_atr in ((1.0,) if quick else (0.5, 1.0, 1.5)):
                for expiry in ((24,) if quick else (12, 24, 40)):
                    for confirm_bars in ((1,) if quick else (1, 2)):
                        event, ref = _e09_signal(
                            arm,
                            trigger,
                            side,
                            rsi_threshold,
                            extension_atr,
                            expiry,
                            confirm_bars,
                        )
                        mapped, refs = base._map_events(n, trigger, event, ref)
                        yield base.ExitCandidate(
                            family="E09_MTF_EXHAUSTION_STRUCTURE",
                            label=(
                                f"E09_{arm_tf}_RSI{rsi_threshold:g}_X{extension_atr:g}_"
                                f"W{expiry}_C{confirm_bars}"
                            ),
                            params={
                                "arm_tf": arm_tf,
                                "trigger_tf": "1h",
                                "rsi_threshold": rsi_threshold,
                                "extension_atr": extension_atr,
                                "expiry_1h": expiry,
                                "confirm_bars": confirm_bars,
                            },
                            exit_mode=2,
                            exit_event=mapped,
                            raw_stop=_blank_stop(n),
                            struct_ref=refs,
                            atr_exec=base._align_feature(n, arm, arm.atr),
                        )


def _rolling_regression_prior(
    close: np.ndarray,
    lookback: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Prior-only log OLS prediction, residual sigma, slope, and correlation."""
    n = len(close)
    center = np.full(n, np.nan, dtype=np.float64)
    sigma = np.full(n, np.nan, dtype=np.float64)
    slope = np.full(n, np.nan, dtype=np.float64)
    corr = np.full(n, np.nan, dtype=np.float64)
    if n <= lookback:
        return center, sigma, slope, corr
    y = np.lib.stride_tricks.sliding_window_view(np.log(close[:-1]), lookback)
    x = np.arange(lookback, dtype=np.float64)
    x_centered = x - x.mean()
    x_ss = float(np.dot(x_centered, x_centered))
    y_mean = y.mean(axis=1)
    y_centered = y - y_mean[:, None]
    slopes = (y_centered @ x_centered) / x_ss
    intercept = y_mean - slopes * x.mean()
    fitted = intercept[:, None] + slopes[:, None] * x
    residual = y - fitted
    sigmas = np.sqrt(np.mean(residual * residual, axis=1))
    y_ss = np.sum(y_centered * y_centered, axis=1)
    correlations = np.divide(
        y_centered @ x_centered,
        np.sqrt(y_ss * x_ss),
        out=np.zeros(len(y), dtype=np.float64),
        where=y_ss > 0,
    )
    center[lookback:] = intercept + slopes * lookback
    sigma[lookback:] = sigmas
    slope[lookback:] = slopes
    corr[lookback:] = correlations
    return center, sigma, slope, corr


def _e06_signal(
    h: base.HTFData,
    side: int,
    lookback: int,
    z_arm: float,
    z_exit: float,
    corr_gate: float,
) -> tuple[np.ndarray, np.ndarray]:
    center, sigma, slope, corr = _rolling_regression_prior(h.close, lookback)
    z = np.divide(
        np.log(h.close) - center,
        sigma,
        out=np.full(len(h.close), np.nan, dtype=np.float64),
        where=sigma > 1e-12,
    )
    event = np.zeros(len(h.close), dtype=np.uint8)
    ref = np.full(len(h.close), np.nan, dtype=np.float64)
    armed = False
    extreme = math.nan
    for j in range(lookback, len(h.close)):
        if not (
            math.isfinite(z[j])
            and math.isfinite(slope[j])
            and math.isfinite(corr[j])
        ):
            continue
        if not armed:
            arm = (
                slope[j] > 0 and corr[j] >= corr_gate and z[j] >= z_arm
                if side > 0
                else slope[j] < 0 and corr[j] <= -corr_gate and z[j] <= -z_arm
            )
            if arm:
                armed = True
                extreme = h.high[j] if side > 0 else h.low[j]
            continue
        if side > 0:
            extreme = max(extreme, h.high[j])
            channel_reentry = z[j] <= z_exit
            adverse_break = (
                math.isfinite(z[j - 1])
                and z[j] < z[j - 1]
                and h.close[j] < h.low[j - 1]
            )
        else:
            extreme = min(extreme, h.low[j])
            channel_reentry = z[j] >= -z_exit
            adverse_break = (
                math.isfinite(z[j - 1])
                and z[j] > z[j - 1]
                and h.close[j] > h.high[j - 1]
            )
        if channel_reentry or adverse_break:
            event[j] = 1
            ref[j] = extreme
            armed = False
    return event, ref


def _e06_candidates(
    data: base.ExecutionData,
    h: base.HTFData,
    side: int,
    quick: bool,
) -> Iterator[base.ExitCandidate]:
    n = len(data.ts)
    for lookback in ((60,) if quick else (40, 60, 100, 150, 250)):
        for z_arm in ((2.0,) if quick else (1.5, 2.0, 2.5, 3.0)):
            for z_exit in ((1.0,) if quick else (0.75, 1.0, 1.5, 2.0)):
                if z_exit >= z_arm:
                    continue
                for corr_gate in ((0.7,) if quick else (0.5, 0.7, 0.85)):
                    event, ref = _e06_signal(
                        h, side, lookback, z_arm, z_exit, corr_gate
                    )
                    mapped, refs = base._map_events(n, h, event, ref)
                    yield base.ExitCandidate(
                        family="E06_REGRESSION_REENTRY",
                        label=(
                            f"E06_4h_N{lookback}_ZA{z_arm:g}_"
                            f"ZE{z_exit:g}_R{corr_gate:g}"
                        ),
                        params={
                            "tf": "4h",
                            "lookback": lookback,
                            "z_arm": z_arm,
                            "z_exit": z_exit,
                            "corr_gate": corr_gate,
                            "prior_only": True,
                        },
                        exit_mode=2,
                        exit_event=mapped,
                        raw_stop=_blank_stop(n),
                        struct_ref=refs,
                        atr_exec=base._align_feature(n, h, h.atr),
                    )


def _e08_candidates(
    data: base.ExecutionData,
    htfs: dict[str, base.HTFData],
    side: int,
    quick: bool,
) -> Iterator[base.ExitCandidate]:
    """Entry-dependent MFE lock; state is owned by the compiled scanner."""
    del side  # Scanner mirrors the state machine from its explicit side input.
    n = len(data.ts)
    for tf in ("4h", "D"):
        h = htfs[tf]
        completed = np.zeros(n, dtype=np.uint8)
        completed[h.event_index] = 1
        atr_exec = base._align_feature(n, h, h.atr)
        for activation_q in ((4.0,) if quick else (2.0, 3.0, 4.0, 6.0, 8.0)):
            for tight_k in ((2.5,) if quick else (1.5, 2.0, 2.5, 3.0, 3.5)):
                yield base.ExitCandidate(
                    family="E08_MFE_PROFIT_LOCK",
                    label=f"E08_{tf}_Q{activation_q:g}_K{tight_k:g}",
                    params={
                        "tf": tf,
                        "activation_mfe_atr": activation_q,
                        "tight_chandelier_atr": tight_k,
                        "inactive_before_activation": True,
                    },
                    exit_mode=3,
                    exit_event=np.ascontiguousarray(completed),
                    # Mode 3 interprets these as scalar parameter arrays.
                    raw_stop=np.full(n, activation_q, dtype=np.float64),
                    struct_ref=np.full(n, tight_k, dtype=np.float64),
                    atr_exec=atr_exec,
                )


def _candidate_iter(
    data: base.ExecutionData,
    htfs: dict[str, base.HTFData],
    side: int,
    families: set[str],
    quick: bool,
) -> Iterator[base.ExitCandidate]:
    if "E03" in families:
        yield from _e03_candidates(data, htfs["4h"], side, quick)
    if "E06" in families:
        yield from _e06_candidates(data, htfs["4h"], side, quick)
    if "E08" in families:
        yield from _e08_candidates(data, htfs, side, quick)
    if "E09" in families:
        yield from _e09_candidates(data, htfs, side, quick)


def _reentries(quick: bool) -> list[tuple[str, int, float, float]]:
    gaps = (1.0,) if quick else (0.5, 1.0, 1.5, 2.0)
    return [(f"E11_G{gap:g}+E10_RB0", 2, 0.0, gap) for gap in gaps]


def _window_scan(
    lib,
    data: base.ExecutionData,
    candidate: base.ExitCandidate,
    lower_event: np.ndarray,
    side: int,
    reentry: tuple[str, int, float, float],
    start_epoch: int,
    end_epoch: int,
    cost_rate: float,
    slip_rate: float,
) -> dict[str, Any]:
    sliced, sl = _slice_execution(data, start_epoch, end_epoch)
    sliced_candidate = _slice_candidate(candidate, sl)
    bh = base._side_bh(sliced, side, cost_rate, slip_rate)
    row = base._scan(
        lib,
        sliced,
        sliced_candidate,
        np.ascontiguousarray(lower_event[sl]),
        side,
        reentry,
        cost_rate,
        slip_rate,
        bh,
    )
    row["start_epoch"] = int(sliced.ts[0])
    row["end_epoch"] = int(sliced.ts[-1])
    row["strategy_equity"] = 1.0 + row["gain_pct"] / 100.0
    row["bh_equity"] = 1.0 + row["bh_net_side_pct"] / 100.0
    row["equity_ratio_vs_bh"] = (
        row["strategy_equity"] / row["bh_equity"]
        if row["strategy_equity"] > 0 and row["bh_equity"] > 0
        else 0.0
    )
    diffs = np.diff(sliced.ts)
    row["max_data_gap_days"] = float(np.max(diffs) / 86400.0) if len(diffs) else 0.0
    # Use the requested window bounds, not the first/last surviving rows.  A
    # source outage can remove the beginning of a window entirely.
    start_day = datetime.fromtimestamp(start_epoch, tz=timezone.utc).date()
    end_day = datetime.fromtimestamp(end_epoch, tz=timezone.utc).date()
    source_gap_overlap = [
        {"start": gap_start.isoformat(), "end": gap_end.isoformat()}
        for gap_start, gap_end in KNOWN_SOURCE_GAPS.get(data.symbol, ())
        if start_day < gap_end and end_day > gap_start
    ]
    row["known_source_gap_overlap"] = source_gap_overlap
    row["gap_clean"] = bool(
        row["max_data_gap_days"] <= 7.0 and not source_gap_overlap
    )
    return row


def _selection_score(rows: list[dict[str, Any]]) -> tuple[float, dict[str, Any]]:
    ratios = np.asarray([max(1e-12, row["equity_ratio_vs_bh"]) for row in rows])
    log_ratios = np.log(ratios)
    tim = np.asarray([row["tim_rth_pct"] for row in rows])
    mandatory_rate = float(np.mean([row["mandatory_reclaim_policy"] for row in rows]))
    target_rate = float(np.mean([(70.0 <= x <= 80.0) for x in tim]))
    # Robustness matters more than one spectacular inner period.  TIM misses and
    # any reclaim-policy breach are penalized before frozen validation is seen.
    score = (
        float(np.median(log_ratios))
        - 0.50 * float(np.std(log_ratios))
        - 0.035 * float(np.median(np.abs(tim - 75.0)))
        - 2.0 * (1.0 - mandatory_rate)
        - 0.50 * (1.0 - target_rate)
    )
    stats = {
        "selection_score": score,
        "median_equity_ratio_vs_bh": float(np.median(ratios)),
        "min_equity_ratio_vs_bh": float(np.min(ratios)),
        "max_equity_ratio_vs_bh": float(np.max(ratios)),
        "log_equity_ratio_std": float(np.std(log_ratios)),
        "mean_tim_rth_pct": float(np.mean(tim)),
        "tim_std_pp": float(np.std(tim)),
        "mandatory_reclaim_rate": mandatory_rate,
        "target_tim_rate": target_rate,
    }
    return score, stats


def _inner_windows(train_start: date, train_end: date, folds: int) -> list[tuple[date, date]]:
    total_months = (train_end.year - train_start.year) * 12 + train_end.month - train_start.month
    if total_months % folds:
        raise ValueError("outer training months must divide evenly by inner folds")
    width = total_months // folds
    return [
        (_add_months(train_start, width * i), _add_months(train_start, width * (i + 1)))
        for i in range(folds)
    ]


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).date().isoformat()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--side", choices=("LONG", "SHORT"), required=True)
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end")
    ap.add_argument("--npz-dir", type=Path, default=base.DEFAULT_NPZ)
    ap.add_argument("--output-root", type=Path, default=base.DEFAULT_OUT)
    ap.add_argument("--families", default="E03,E06,E08,E09")
    ap.add_argument("--outer-train-months", type=int, default=12)
    ap.add_argument("--validation-months", type=int, default=3)
    ap.add_argument("--inner-folds", type=int, default=3)
    ap.add_argument("--cost-bps", type=float, default=0.0)
    ap.add_argument("--slippage-bps", type=float, default=2.5)
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()

    symbol = args.symbol.upper()
    side = 1 if args.side == "LONG" else -1
    data = base._load_execution(symbol, args.npz_dir, args.start, "ladder", args.end)
    if not data.contract["valid"]:
        raise RuntimeError(f"{symbol}: invalid NPZ contract: {data.contract['errors']}")
    lib = base._compile_scanner()
    htfs = {tf: base._compress_htf(data, tf) for tf in ("1h", "4h", "D")}
    lower_event = base._lower_reentry_events(len(data.ts), htfs["1h"], side)
    families = {x.strip().upper() for x in args.families.split(",") if x.strip()}
    candidates = list(_candidate_iter(data, htfs, side, families, args.quick))
    reentries = _reentries(args.quick)
    combinations = [(c, r) for c in candidates for r in reentries]
    if not combinations:
        raise RuntimeError("no candidates generated")

    cost_rate = args.cost_bps / 10_000.0
    slip_rate = args.slippage_bps / 10_000.0
    data_end = datetime.fromtimestamp(int(data.ts[-1]), tz=timezone.utc).date()
    requested_start = date.fromisoformat(args.start)
    first_data_day = datetime.fromtimestamp(int(data.ts[0]), tz=timezone.utc).date()
    first_data_month = date(first_data_day.year, first_data_day.month, 1)
    campaign_start = max(requested_start, first_data_month)
    first_validation = _add_months(campaign_start, args.outer_train_months)
    validation_starts: list[date] = []
    cursor = first_validation
    while _epoch(cursor) < int(data.ts[-1]):
        validation_starts.append(cursor)
        cursor = _add_months(cursor, args.validation_months)

    outer_results: list[dict[str, Any]] = []
    selection_rows: list[dict[str, Any]] = []
    for validation_start in validation_starts:
        validation_end = min(
            _add_months(validation_start, args.validation_months),
            data_end,
        )
        if (validation_end - validation_start).days < 14:
            continue
        train_end = validation_start
        train_start = _add_months(train_end, -args.outer_train_months)
        inner = _inner_windows(train_start, train_end, args.inner_folds)
        ranked: list[tuple[float, base.ExitCandidate, tuple[str, int, float, float], dict[str, Any]]] = []
        for candidate, reentry in combinations:
            rows = [
                _window_scan(
                    lib,
                    data,
                    candidate,
                    lower_event,
                    side,
                    reentry,
                    _epoch(start),
                    _epoch(end),
                    cost_rate,
                    slip_rate,
                )
                for start, end in inner
            ]
            score, stats = _selection_score(rows)
            summary = {
                "outer_validation_start": validation_start.isoformat(),
                "strategy": candidate.label,
                "family": candidate.family,
                "params": candidate.params,
                "reentry": reentry[0],
                **stats,
            }
            ranked.append((score, candidate, reentry, summary))
        ranked.sort(key=lambda item: item[0], reverse=True)
        constrained = [
            item
            for item in ranked
            if item[3]["mandatory_reclaim_rate"] == 1.0
            and 70.0 <= item[3]["mean_tim_rth_pct"] <= 80.0
            and item[3]["target_tim_rate"] >= (2.0 / 3.0)
        ]
        chosen_score, chosen_candidate, chosen_reentry, chosen_summary = (
            constrained[0] if constrained else ranked[0]
        )
        chosen_summary["selection_constraint_fallback"] = not bool(constrained)

        # Parameter stability: count families and exact configs among near-best
        # discovery choices; validation remains unseen until after this point.
        stability_pool = constrained if constrained else ranked
        top_k = stability_pool[: min(10, len(stability_pool))]
        chosen_summary["selection_rank_margin"] = (
            chosen_score - stability_pool[1][0] if len(stability_pool) > 1 else None
        )
        chosen_summary["top10_family_counts"] = dict(
            Counter(item[1].family for item in top_k)
        )
        chosen_summary["top10_reentry_counts"] = dict(
            Counter(item[2][0] for item in top_k)
        )
        chosen_summary["top10_strategies"] = [item[1].label for item in top_k]
        # All candidates use the same bars in an inner fold, so inspecting the
        # chosen rows is sufficient to disclose selection-data provenance.
        chosen_inner_rows = [
            _window_scan(
                lib,
                data,
                chosen_candidate,
                lower_event,
                side,
                chosen_reentry,
                _epoch(start),
                _epoch(end),
                cost_rate,
                slip_rate,
            )
            for start, end in inner
        ]
        chosen_summary["discovery_data_clean"] = all(
            row["gap_clean"] for row in chosen_inner_rows
        )
        chosen_summary["discovery_gap_overlaps"] = [
            overlap
            for row in chosen_inner_rows
            for overlap in row["known_source_gap_overlap"]
        ]
        selection_rows.append(chosen_summary)

        frozen = _window_scan(
            lib,
            data,
            chosen_candidate,
            lower_event,
            side,
            chosen_reentry,
            _epoch(validation_start),
            _epoch(validation_end),
            cost_rate,
            slip_rate,
        )
        frozen.update(
            {
                "selection": "FROZEN_BEFORE_VALIDATION",
                "outer_train_start": train_start.isoformat(),
                "outer_train_end_exclusive": train_end.isoformat(),
                "validation_start": validation_start.isoformat(),
                "validation_end_exclusive": validation_end.isoformat(),
                "inner_folds": [
                    {"start": start.isoformat(), "end_exclusive": end.isoformat()}
                    for start, end in inner
                ],
                "discovery_selection_score": chosen_score,
                "discovery_data_clean": chosen_summary["discovery_data_clean"],
                "discovery_gap_overlaps": chosen_summary["discovery_gap_overlaps"],
                "selection_constraint_fallback": chosen_summary[
                    "selection_constraint_fallback"
                ],
            }
        )
        outer_results.append(frozen)

    if not outer_results:
        raise RuntimeError("no outer validation folds available")

    def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
        strategy_equity = float(np.prod([row["strategy_equity"] for row in rows]))
        bh_equity = float(np.prod([row["bh_equity"] for row in rows]))
        held = sum(
            (row["tim_rth_pct"] / 100.0)
            * (
                np.searchsorted(data.ts, row["end_epoch"], side="right")
                - np.searchsorted(data.ts, row["start_epoch"], side="left")
            )
            for row in rows
        )
        bars = sum(
            np.searchsorted(data.ts, row["end_epoch"], side="right")
            - np.searchsorted(data.ts, row["start_epoch"], side="left")
            for row in rows
        )
        gain = 100.0 * (strategy_equity - 1.0)
        bh_gain = 100.0 * (bh_equity - 1.0)
        return {
            "folds": len(rows),
            "strategy_compounded_gain_pct": gain,
            "bh_compounded_gain_pct": bh_gain,
            "gain_bh_multiple": gain / bh_gain if abs(bh_gain) > 1e-12 else None,
            "strategy_to_bh_equity_ratio": strategy_equity / bh_equity,
            "weighted_tim_rth_pct": 100.0 * held / bars,
            "tim_target_70_80": bool(70.0 <= 100.0 * held / bars <= 80.0),
            "all_mandatory_reclaim": all(row["mandatory_reclaim_policy"] for row in rows),
            "all_frozen": all(row["selection"] == "FROZEN_BEFORE_VALIDATION" for row in rows),
            "total_round_trips": sum(row["round_trips"] for row in rows),
        }

    gap_clean = [
        row
        for row in outer_results
        if row["gap_clean"] and row["discovery_data_clean"]
    ]
    strict_policy = [
        row
        for row in gap_clean
        if not row["selection_constraint_fallback"]
        and row["mandatory_reclaim_policy"]
        and 70.0 <= row["tim_rth_pct"] <= 80.0
    ]
    stability = {
        "selected_family_counts": dict(Counter(row["family"] for row in outer_results)),
        "selected_strategy_counts": dict(Counter(row["strategy"] for row in outer_results)),
        "selected_reentry_counts": dict(Counter(row["reentry"] for row in outer_results)),
        "exact_strategy_repeat_rate": (
            max(Counter(row["strategy"] for row in outer_results).values()) / len(outer_results)
        ),
        "family_repeat_rate": (
            max(Counter(row["family"] for row in outer_results).values()) / len(outer_results)
        ),
    }
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = args.output_root / f"walkforward_top_exit_{run_id}_{symbol}_{args.side}"
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "tier": "VEC_RESEARCH",
        "matrix_eligible": False,
        "live_config_write": False,
        "symbol": symbol,
        "side": args.side,
        "families": sorted(families),
        "candidate_count": len(candidates),
        "combination_count": len(combinations),
        "outer_train_months": args.outer_train_months,
        "validation_months": args.validation_months,
        "inner_folds": args.inner_folds,
        "selection_rule": (
            "median log strategy/B&H equity ratio - stability/TIM/reclaim penalties; "
            "chosen on chronological inner folds only"
        ),
        "fill_timing": "next RTH open",
        "signals": "completed HTF bars only",
        "cost_bps_one_way": args.cost_bps,
        "slippage_bps_one_way": args.slippage_bps,
        "npz_path": data.path,
        "npz_sha256": base._sha256_file(data.path),
        "contract_valid": bool(data.contract["valid"]),
        "contract_warnings": data.contract["warnings"],
        "known_source_gaps": [
            {"start": start.isoformat(), "end": end.isoformat()}
            for start, end in KNOWN_SOURCE_GAPS.get(symbol, ())
        ],
    }
    digest = {
        **manifest,
        "strict_policy_frozen_validation": (
            aggregate(strict_policy) if strict_policy else None
        ),
        "data_clean_frozen_validation": aggregate(gap_clean) if gap_clean else None,
        "all_frozen_validation_including_gaps": aggregate(outer_results),
        "parameter_stability": stability,
        "frozen_validation_folds": outer_results,
        "selection_audit": selection_rows,
    }
    (out_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    (out_dir / f"walkforward_digest_{symbol}_{args.side}.json").write_text(
        json.dumps(digest, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )
    with gzip.open(out_dir / f"frozen_folds_{symbol}_{args.side}.jsonl.gz", "wt") as fh:
        for row in outer_results:
            fh.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")
    snapshot = out_dir / "source_snapshot"
    snapshot.mkdir()
    snapshot.joinpath(Path(__file__).name).write_bytes(Path(__file__).read_bytes())
    snapshot.joinpath(Path(base.__file__).name).write_bytes(Path(base.__file__).read_bytes())
    snapshot.joinpath(base.C_SOURCE.name).write_bytes(base.C_SOURCE.read_bytes())
    data.z.close()
    print(json.dumps({"status": "PASS", "artifact": str(out_dir), **digest}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
