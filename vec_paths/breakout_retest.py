"""vec_paths/breakout_retest.py — Rule A breakout-retest entry trigger (vec).

LIVE LOGIC MIRRORED FROM:
  ez_manage.py:35454-35495 (SIMPLIFIED stateless form, per config.py comment).

WHY THIS EXISTS:
  20 RULE_A_RETEST sweep arms (queued 2026-05-18 by Agent A8) were
  producing DROPPED_PHANTOM_KNOB results — the BREAKOUT_RETEST_ARMED_*
  knobs were not in vec_aware_knobs.txt and not referenced anywhere
  in v8_vec_sweep / vec_paths, so toggling them produced byte-identical
  baseline output. This module wires the trigger into the vec engine.

STATELESS SIMPLIFICATION (per config.py:1257 comment block):
  Live impl uses an in-memory `breakout_retest_armed[symbol][side]`
  state dict armed when D/W cross fires. The vec variant arms the
  same bar that the close still satisfies the cross + retest band,
  i.e. checks all conditions on each bar. The arm "window" is then
  enforced by requiring that the arm condition was True in the past
  N daily bars (BREAKOUT_RETEST_ARMED_WINDOW_DAYS).

INPUTS (npz dict, all shape (N,)):
  close, dc_basis_D, atr_D, wt1_D, wt2_D, wt1_W, wt2_W,
  wt1_15m, wt2_15m, wt1_1h, wt2_1h, stoch_k_3m, stoch_k_3m_prev,
  volume_D, relative_volume_D, timestamp / timestamp_D.

KNOBS (read off `config`):
  BREAKOUT_RETEST_ARMED_ENABLED            (bool, default False)
  BREAKOUT_RETEST_ARMED_WINDOW_DAYS        (int,  default 7)
  BREAKOUT_RETEST_ARMED_RETEST_ATR_MULT    (float, default 0.30)
  BREAKOUT_RETEST_ARMED_VOLUME_MULT        (float, default 1.25)
  BREAKOUT_RETEST_ARMED_K_3M_PREV_MAX      (int,   default 30) — LONG fire
  BREAKOUT_RETEST_ARMED_HTF_STACK_MIN      (int,   default 2)  — 1..2 of
                                            (15m, 1h) HTF alignments required
                                            for fire (LONG: both bull; SHORT:
                                            both bear). Default 2 = both,
                                            mirrors live "AND" form. 1 = OR.

RETURNS:
  (long_mask, short_mask) — shape (N,) bool. True on bars where the
  vec engine should treat this as an additive entry trigger (OR'd
  alongside wt_3m_aligned, golden_rule, delta, etc).
"""
from __future__ import annotations

from typing import Any, Dict, Tuple

import numpy as np


def _get(npz: Dict[str, np.ndarray], key: str, n: int, default: float = 0.0) -> np.ndarray:
    arr = npz.get(key)
    if arr is None:
        out = np.full(n, default, dtype=np.float64)
        return out
    a = np.asarray(arr, dtype=np.float64)
    return np.nan_to_num(a, nan=default)


def _trailing_any(mask: np.ndarray, window: int) -> np.ndarray:
    """Return True at bar i iff mask[i-window+1 .. i] has any True. Window>=1."""
    if window <= 1:
        return mask.astype(bool)
    n = mask.shape[0]
    # rolling sum via cumsum; reset on first window
    cs = np.concatenate([[0], np.cumsum(mask.astype(np.int32))])
    # sum over [i-window+1, i] inclusive = cs[i+1] - cs[max(0,i-window+1)]
    start = np.maximum(0, np.arange(n) - window + 1)
    s = cs[np.arange(n) + 1] - cs[start]
    return s > 0


def evaluate_breakout_retest_vec(
    features: Dict[str, np.ndarray],
    config: Any,
    mode: str = "crypto",
) -> Tuple[np.ndarray, np.ndarray]:
    """Vectorized Rule A breakout-retest trigger.

    Returns (long_mask, short_mask) — shape (N,) bool arrays.
    Both all-False when BREAKOUT_RETEST_ARMED_ENABLED is False.
    """
    if not bool(getattr(config, "BREAKOUT_RETEST_ARMED_ENABLED", False)):
        # find any present array to size output
        n = 0
        for v in features.values():
            if v is not None:
                n = int(np.asarray(v).shape[0])
                break
        zeros = np.zeros(n, dtype=bool)
        return zeros, zeros

    # Resolve N
    n = 0
    for v in features.values():
        if v is not None:
            n = int(np.asarray(v).shape[0])
            break
    if n == 0:
        return np.zeros(0, dtype=bool), np.zeros(0, dtype=bool)

    window_days = int(getattr(config, "BREAKOUT_RETEST_ARMED_WINDOW_DAYS", 7))
    retest_mult = float(getattr(config, "BREAKOUT_RETEST_ARMED_RETEST_ATR_MULT", 0.30))
    vol_mult = float(getattr(config, "BREAKOUT_RETEST_ARMED_VOLUME_MULT", 1.25))
    k3m_prev_max = float(getattr(config, "BREAKOUT_RETEST_ARMED_K_3M_PREV_MAX", 30))
    htf_stack_min = int(getattr(config, "BREAKOUT_RETEST_ARMED_HTF_STACK_MIN", 2))

    close = _get(features, "close", n)
    dc_basis_D = _get(features, "dc_basis_D", n)
    atr_D = _get(features, "atr_D", n)
    wt1_D = _get(features, "wt1_D", n)
    wt2_D = _get(features, "wt2_D", n)
    wt1_W = _get(features, "wt1_W", n)
    wt2_W = _get(features, "wt2_W", n)
    wt1_15m = _get(features, "wt1_15m", n)
    wt2_15m = _get(features, "wt2_15m", n)
    wt1_1h = _get(features, "wt1_1h", n)
    wt2_1h = _get(features, "wt2_1h", n)
    # stoch_k_3m / k_3m fallback chain
    stk_3m = features.get("stoch_k_3m")
    if stk_3m is None:
        stk_3m = features.get("k_3m")
    stk_3m = np.nan_to_num(np.asarray(stk_3m, dtype=np.float64), nan=50.0) if stk_3m is not None else np.full(n, 50.0)
    stk_3m_prev = features.get("stoch_k_3m_prev")
    if stk_3m_prev is None:
        stk_3m_prev = features.get("k_3m_prev")
    if stk_3m_prev is not None:
        stk_3m_prev = np.nan_to_num(np.asarray(stk_3m_prev, dtype=np.float64), nan=50.0)
    else:
        # synthesize prev via shift-1
        stk_3m_prev = np.concatenate([[50.0], stk_3m[:-1]])

    volume_D = _get(features, "volume_D", n)
    rvol_D = _get(features, "relative_volume_D", n, default=1.0)

    # Data validity (matches live `_ra_data_ok`)
    data_ok = (np.abs(wt1_D) > 1e-9) & (np.abs(wt2_D) > 1e-9) & (dc_basis_D > 0) & (atr_D > 0)

    # ARM conditions
    armed_long_now = (wt1_D > wt2_D) & (wt1_W > wt2_W) & (close > dc_basis_D)
    armed_short_now = (wt1_D < wt2_D) & (wt1_W < wt2_W) & (close < dc_basis_D)

    # Volume arm filter (optional — high vol_mult disables; relative_volume_D >= MULT)
    if vol_mult > 0.0:
        vol_ok = rvol_D >= vol_mult
        armed_long_now = armed_long_now & vol_ok
        armed_short_now = armed_short_now & vol_ok

    # WINDOW: any of last `window_days` bars armed (operating on per-bar arrays
    # which already carry stepped daily values, since dc_basis_D / wt_D are D-TF).
    # window in BARS = window_days * bars_per_day; but D-TF arrays are stepped,
    # so window_days steps == window_days unique daily levels. Vec-conservative:
    # use bars-equivalent assumption matching how D-TF arrays are stored
    # (already stepped). Practically: a single arm condition that's true on
    # any bar within the trailing window of daily-bars allows retest fire.
    # Implementation: rolling-any over per-bar mask with a conservative
    # `window_days * 24 * 4` (base-TF 15m) heuristic is overkill; instead
    # we just use a rolling-any of size `max(1, window_days)` which matches
    # the stepped daily values closely enough (one D-step per ~96 bars on
    # 15m, but per-bar the value is constant within a D-step, so window=1
    # over per-bar mask == "armed this D-step"). For multi-D window use
    # ts-aware count below.
    ts_D = features.get("timestamp_D")
    if ts_D is not None and window_days > 1:
        # Compute trailing-any using D-stepped timestamps
        ts_D = np.asarray(ts_D, dtype=np.int64)
        # unique D-steps and the bar index where each step starts
        change = np.concatenate([[True], ts_D[1:] != ts_D[:-1]])
        step_starts = np.where(change)[0]
        # For each bar, find the count of distinct D-steps in trailing window
        # where armed_*_now was true on any bar of that step.
        # Build per-step "any-armed" arrays.
        n_steps = len(step_starts)
        step_armed_long = np.zeros(n_steps, dtype=bool)
        step_armed_short = np.zeros(n_steps, dtype=bool)
        for j in range(n_steps):
            a = step_starts[j]
            b = step_starts[j + 1] if j + 1 < n_steps else n
            if armed_long_now[a:b].any():
                step_armed_long[j] = True
            if armed_short_now[a:b].any():
                step_armed_short[j] = True
        # Now for each bar i in step j, fire-eligible iff any of steps
        # [max(0, j-window_days+1) .. j] had step_armed_*=True.
        # Compute via cumsum on step-level then map back to bars.
        cs_long = np.concatenate([[0], np.cumsum(step_armed_long.astype(np.int32))])
        cs_short = np.concatenate([[0], np.cumsum(step_armed_short.astype(np.int32))])
        # bar→step index map
        bar_step_idx = np.zeros(n, dtype=np.int32)
        for j in range(n_steps):
            a = step_starts[j]
            b = step_starts[j + 1] if j + 1 < n_steps else n
            bar_step_idx[a:b] = j
        win_start = np.maximum(0, bar_step_idx - window_days + 1)
        eligible_long = (cs_long[bar_step_idx + 1] - cs_long[win_start]) > 0
        eligible_short = (cs_short[bar_step_idx + 1] - cs_short[win_start]) > 0
    else:
        # Fall back: per-bar rolling-any with window=1 (== "armed this bar").
        eligible_long = armed_long_now
        eligible_short = armed_short_now

    # RETEST band: |close - dc_basis_D| / atr_D < retest_mult
    with np.errstate(divide="ignore", invalid="ignore"):
        dist_atr = np.where(atr_D > 0, np.abs(close - dc_basis_D) / atr_D, 999.0)
    retest_band = dist_atr < retest_mult

    # K_3M crossover prereq: prev < threshold AND current > prev (rising)
    k3m_xup = (stk_3m_prev < k3m_prev_max) & (stk_3m > stk_3m_prev)
    # SHORT mirror: prev > (100 - threshold) AND current < prev
    k3m_xdn = (stk_3m_prev > (100.0 - k3m_prev_max)) & (stk_3m < stk_3m_prev)

    # HTF stack: 15m + 1h alignment (count how many agree with side)
    bull_15m = wt1_15m > wt2_15m
    bull_1h = wt1_1h > wt2_1h
    bear_15m = wt1_15m < wt2_15m
    bear_1h = wt1_1h < wt2_1h
    long_htf_stack = bull_15m.astype(np.int8) + bull_1h.astype(np.int8)
    short_htf_stack = bear_15m.astype(np.int8) + bear_1h.astype(np.int8)
    htf_long_ok = long_htf_stack >= htf_stack_min
    htf_short_ok = short_htf_stack >= htf_stack_min

    fire_long = data_ok & eligible_long & retest_band & k3m_xup & htf_long_ok
    fire_short = data_ok & eligible_short & retest_band & k3m_xdn & htf_short_ok

    return fire_long.astype(bool), fire_short.astype(bool)
