#!/usr/bin/env python3
"""per_sym_vec_engine_crypto — VARIANT-AXIS vectorized per-symbol crypto backtester.

Goal per user 2026-05-18: "millions of tests per symbol". The legacy per_sym_engine_crypto
is bar-vectorized but variant-scalar: each variant walks the bars in a Python while-loop,
so 100 variants take 100× one walk. This engine vectorizes ACROSS VARIANTS:

  1. Load NPZ + downsample to 15m grid once.
  2. Precompute UNDERLYING scalars (close, wt1_<tf>, wt2_<tf>, dc_high_<tf>, dc_low_<tf>,
     bb_pctb_<tf>) — independent of variant knobs.
  3. For each variant chunk (default 10k):
       a. Build (n_bars, chunk_size) bool entry/exit masks via numpy broadcasting against
          each variant's threshold knobs.
       b. Walk each variant column with a fast inner loop (numba-style, but stays in numpy
          using np.flatnonzero per column). Walk cost dominates at high variant count, so
          we keep the per-trade math vectorized within a single column.
       c. Emit per-variant trade list (kept slim: just pnl_pct + ts).
  4. Per-variant metrics computed vectorized across the chunk.

Constraints (CLAUDE.md):
  - NO LIVE CODE EDITS. NO order routing.
  - All sharpe writes go via metrics_guard.* — DELEGATED to caller (this engine RETURNS
    arrays; caller writes CSV through metrics_guard).
  - Pool-Sharpe ONLY (per-trade returns). NO annualization.
  - Open-position MtM at final bar.

Scope (v1): the 10 most-impactful crypto knobs (MIN_TFS_AGREE_*, ENTRY_MODE, MIN_HOLD,
  COOLDOWN, HARD_LOSS, PEAK_PROTECT, REVERSE_ON_EXIT, REQUIRE_D_TREND, HEDGE_ENABLED).
Out-of-scope v1: full v8 aggregator parity, RZ cascade, wt_dc_hierarchy, augment pyramid,
  nested hedge cycles. Those exist in scalar engine for paranoid top-K verification.

USAGE:
  from per_sym_vec_engine_crypto import sweep_variants
  from per_sym_variant_generator import generate_variants
  variants = generate_variants(n_total=1_000_000, mode='crypto')
  scoreboard = sweep_variants('BTCUSDC', variants, years_back=7.0/365.25)
  top10 = scoreboard.nlargest(10, 'time_weighted_sharpe')
"""
from __future__ import annotations

import math
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Reuse the scalar engine's downsamplers + commission. Replace NPZ loader with allow_pickle
# variant — the scalar load_3m_base trips on `bar_pattern_codes` (object scalar).
from per_sym_engine_crypto import (
    build_tf_data, NPZ_DIR, TF_BARS_3M, COMMISSION_RT_PCT,
)

# Crypto commission already imported (0.08% RT). Default per-trade slippage absorbed by RT.
RT_COMM = COMMISSION_RT_PCT  # in pct units (0.08)


_npz_cache_vec: Dict[str, Dict[str, np.ndarray]] = {}


def load_15m_signals(sym: str, years_back: float = 7.0 / 365.25) -> Optional[Dict[str, np.ndarray]]:
    """Loads 3m NPZ, downsamples to 15m grid, returns close_15m + WT/DC/BB resampled.
    Robust to 0-d array object loading (e.g. the `bar_pattern_codes` object scalar). Slices to last `years_back` years. Cached.
    """
    cache_key = f"{sym}__y{years_back:.4f}"
    if cache_key in _npz_cache_vec:
        return _npz_cache_vec[cache_key]
    if _npz_cache_vec:
        _npz_cache_vec.clear()
    p = NPZ_DIR / f"{sym}.npz"
    if not p.exists():
        return None
    try:
        z = np.load(str(p), allow_pickle=True)
    except Exception:
        return None
    full = {}
    for k in z.files:
        try:
            arr = z[k]
            if arr.ndim < 1:
                continue
            full[k] = arr[:]
        except Exception:
            continue
    z.close()
    if not all(k in full for k in ("timestamps", "close_3m")):
        return None
    ts_3m = full["timestamps"].astype(np.int64)
    cutoff = ts_3m[-1] - int(years_back * 365.25 * 86400)
    si_3m = int(np.searchsorted(ts_3m, cutoff))
    sliced_3m = {}
    for k, arr in full.items():
        if isinstance(arr, np.ndarray) and arr.ndim == 1 and len(arr) == len(ts_3m):
            sliced_3m[k] = arr[si_3m:]
        else:
            sliced_3m[k] = arr
    n_3m = len(sliced_3m["close_3m"])
    if n_3m < 10:
        return None
    sliced_3m["open"] = sliced_3m["open_3m"].astype(np.float64)
    sliced_3m["high"] = sliced_3m["high_3m"].astype(np.float64)
    sliced_3m["low"] = sliced_3m["low_3m"].astype(np.float64)
    sliced_3m["close"] = sliced_3m["close_3m"].astype(np.float64)
    sliced_3m["volume"] = sliced_3m.get("volume_3m", np.ones(n_3m)).astype(np.float64)
    sliced_3m["ts"] = ts_3m[si_3m:]
    tf_data = build_tf_data(sliced_3m)
    if "15m" not in tf_data or len(tf_data["15m"]["close"]) < 10:
        return None
    n_15m = len(tf_data["15m"]["close"])
    out = {
        "close_15m": tf_data["15m"]["close"].astype(np.float32),
        "open_15m": tf_data["15m"]["open"].astype(np.float32),
        "high_15m": tf_data["15m"]["high"].astype(np.float32),
        "low_15m": tf_data["15m"]["low"].astype(np.float32),
        "ts_15m": tf_data["15m"]["ts"].astype(np.int64),
        "n_15m": n_15m,
    }
    for tf in ("3m", "15m", "1h", "4h", "D"):
        for fld in (f"wt1_{tf}", f"wt2_{tf}"):
            arr = sliced_3m.get(fld)
            if arr is None or len(arr) == 0:
                out[fld] = np.zeros(n_15m, dtype=np.float32)
            else:
                out[fld] = _ss_3m_to_15m(arr.astype(np.float32), n_15m)
        for fld in (f"dc_high_{tf}", f"dc_low_{tf}"):
            arr = sliced_3m.get(fld)
            if arr is None or len(arr) == 0:
                out[fld] = np.zeros(n_15m, dtype=np.float32)
            else:
                out[fld] = _ss_3m_to_15m(arr.astype(np.float32), n_15m)
        for fld in (f"bb_upper_{tf}", f"bb_lower_{tf}"):
            arr = sliced_3m.get(fld)
            if arr is None or len(arr) == 0:
                out[fld] = np.zeros(n_15m, dtype=np.float32)
            else:
                out[fld] = _ss_3m_to_15m(arr.astype(np.float32), n_15m)
    close_15m = out["close_15m"]
    for tf in ("15m", "1h", "4h", "D"):
        u = out.get(f"bb_upper_{tf}")
        l = out.get(f"bb_lower_{tf}")
        if u is not None and l is not None:
            out[f"bb_pctb_{tf}"] = _bb_pctb(close_15m, u, l)
        else:
            out[f"bb_pctb_{tf}"] = np.full(n_15m, 0.5, dtype=np.float32)
    for tf in ("15m", "1h", "4h", "D"):
        for nm in (f"stoch_k_{tf}", f"atr_{tf}", f"rsi_{tf}"):
            arr = sliced_3m.get(nm)
            if arr is not None and len(arr) > 0:
                out[nm] = _ss_3m_to_15m(arr.astype(np.float32), n_15m)
    out["volume_15m"] = tf_data["15m"].get("volume", np.ones(n_15m, dtype=np.float32)).astype(np.float32)
    _npz_cache_vec[cache_key] = out
    return out


def _ss_3m_to_15m(arr_3m: np.ndarray, n_15m: int) -> np.ndarray:
    cut = (len(arr_3m) // 5) * 5
    sub = arr_3m[:cut][4::5]
    if len(sub) >= n_15m:
        return sub[:n_15m]
    return np.concatenate([np.zeros(n_15m - len(sub), dtype=arr_3m.dtype), sub])


def _bb_pctb(close: np.ndarray, upper: np.ndarray, lower: np.ndarray) -> np.ndarray:
    diff = upper - lower
    diff_safe = np.where(diff > 1e-8, diff, 1e-8)
    return (close - lower) / diff_safe


def _precompute_signals(sym: str, years_back: float = 7.0 / 365.25) -> Optional[Dict[str, np.ndarray]]:
    return load_15m_signals(sym, years_back)


def _build_tf_signals(sig: Dict[str, np.ndarray]) -> Dict[str, Dict[str, np.ndarray]]:
    n = sig["n_15m"]
    close = sig["close_15m"]
    out: Dict[str, Dict[str, np.ndarray]] = {}
    for tf in ("3m", "15m", "1h", "4h", "D"):
        w1 = sig.get(f"wt1_{tf}", np.zeros(n, dtype=np.float32))
        w2 = sig.get(f"wt2_{tf}", np.zeros(n, dtype=np.float32))
        wt_long = (w1 > w2)
        wt_short = (w1 < w2)
        dc_h = sig.get(f"dc_high_{tf}", np.zeros(n, dtype=np.float32))
        dc_l = sig.get(f"dc_low_{tf}", np.zeros(n, dtype=np.float32))
        prev_h = np.concatenate([[dc_h[0]], dc_h[:-1]])
        prev_l = np.concatenate([[dc_l[0]], dc_l[:-1]])
        dc_long = (close > prev_h) & (prev_h > 0)
        dc_short = (close < prev_l) & (prev_l > 0)
        pctb = sig.get(f"bb_pctb_{tf}", np.full(n, 0.5, dtype=np.float32))
        out[tf] = {
            "wt_long": wt_long.astype(np.bool_),
            "wt_short": wt_short.astype(np.bool_),
            "dc_long": dc_long.astype(np.bool_),
            "dc_short": dc_short.astype(np.bool_),
            "pctb": pctb,
        }
    return out


VEC_KNOBS_USED = {
    "MIN_TFS_AGREE_ENTRY", "MIN_TFS_AGREE_EXIT",
    "ENTRY_MODE",
    "MIN_HOLD_BARS_15m", "COOLDOWN_BARS_15m",
    "REVERSE_ON_EXIT_ENABLED",
    "NOLOSS_ENABLED",
    "HARD_LOSS_PCT_ENABLED", "HARD_LOSS_PCT",
    "PEAK_PROTECT_ENABLED",
    "REQUIRE_D_TREND",
    "BB_LONG_ENTRY_MAX", "BB_SHORT_ENTRY_MIN",
    "BT_RIDICULOUS_HOLD_GUARD_ENABLED", "BT_RIDICULOUS_LOSS_PCT", "BT_RIDICULOUS_HOLD_HOURS",
    "PEAK_GIVEBACK_FIXED_PCT_ENABLED", "PEAK_GIVEBACK_FIXED_DROP_PCT",
}

ENTRY_MODES = ["dc_break", "wt_cross", "bb_extreme", "or"]


def _variants_to_arrays(variants: List[Dict[str, Any]]) -> Dict[str, np.ndarray]:
    n = len(variants)
    out = {
        "MIN_TFS_AGREE_ENTRY": np.array([v.get("MIN_TFS_AGREE_ENTRY", 2) for v in variants], dtype=np.int8),
        "MIN_TFS_AGREE_EXIT": np.array([v.get("MIN_TFS_AGREE_EXIT", 2) for v in variants], dtype=np.int8),
        "ENTRY_MODE_IDX": np.array([ENTRY_MODES.index(v.get("ENTRY_MODE", "or")) for v in variants], dtype=np.int8),
        "MIN_HOLD_BARS": np.array([v.get("MIN_HOLD_BARS_15m", 5) for v in variants], dtype=np.int16),
        "COOLDOWN_BARS": np.array([v.get("COOLDOWN_BARS_15m", 3) for v in variants], dtype=np.int16),
        "REVERSE_ON_EXIT": np.array([bool(v.get("REVERSE_ON_EXIT_ENABLED", False)) for v in variants], dtype=np.bool_),
        "HARD_LOSS_ENABLED": np.array([bool(v.get("HARD_LOSS_PCT_ENABLED", True)) for v in variants], dtype=np.bool_),
        "HARD_LOSS_PCT": np.array([v.get("HARD_LOSS_PCT", 0.5) for v in variants], dtype=np.float32),
        "PEAK_PROTECT_ENABLED": np.array([bool(v.get("PEAK_PROTECT_ENABLED", True)) for v in variants], dtype=np.bool_),
        "REQUIRE_D_TREND": np.array([bool(v.get("REQUIRE_D_TREND", False)) for v in variants], dtype=np.bool_),
        "BB_LONG_ENTRY_MAX": np.array([v.get("BB_LONG_ENTRY_MAX", 0.20) for v in variants], dtype=np.float32),
        "BB_SHORT_ENTRY_MIN": np.array([v.get("BB_SHORT_ENTRY_MIN", 0.80) for v in variants], dtype=np.float32),
        "RIDICULOUS_HOLD_ENABLED": np.array([bool(v.get("BT_RIDICULOUS_HOLD_GUARD_ENABLED", True)) for v in variants], dtype=np.bool_),
        "RIDICULOUS_LOSS_PCT": np.array([v.get("BT_RIDICULOUS_LOSS_PCT", -15.0) for v in variants], dtype=np.float32),
        "RIDICULOUS_HOLD_HOURS": np.array([v.get("BT_RIDICULOUS_HOLD_HOURS", 48.0) for v in variants], dtype=np.float32),
        "PEAK_GIVEBACK_ENABLED": np.array([bool(v.get("PEAK_GIVEBACK_FIXED_PCT_ENABLED", False)) for v in variants], dtype=np.bool_),
        "PEAK_GIVEBACK_DROP_PCT": np.array([v.get("PEAK_GIVEBACK_FIXED_DROP_PCT", 0.5) for v in variants], dtype=np.float32),
        "NOLOSS_ENABLED": np.array([bool(v.get("NOLOSS_ENABLED", True)) for v in variants], dtype=np.bool_),
    }
    return out


def _build_entry_exit_masks_chunk(
    tf_sigs: Dict[str, Dict[str, np.ndarray]],
    sig: Dict[str, np.ndarray],
    vararr: Dict[str, np.ndarray],
    var_slice: slice,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """For a chunk of variants [var_slice], return four (n_bars, chunk_size) bool arrays: enter_long, leave_long, enter_short, leave_short."""
    n = sig["n_15m"]
    decision_tfs = ("15m", "1h", "4h", "D")
    d_wt_long = tf_sigs["D"]["wt_long"]
    d_wt_short = tf_sigs["D"]["wt_short"]
    min_e = vararr["MIN_TFS_AGREE_ENTRY"][var_slice]
    min_x = vararr["MIN_TFS_AGREE_EXIT"][var_slice]
    mode_idx = vararr["ENTRY_MODE_IDX"][var_slice]
    is_dc = (mode_idx == 0)
    is_wt = (mode_idx == 1)
    is_bb = (mode_idx == 2)
    is_or = (mode_idx == 3)
    bb_max = vararr["BB_LONG_ENTRY_MAX"][var_slice]
    bb_min = vararr["BB_SHORT_ENTRY_MIN"][var_slice]
    tf_entries_long = []
    tf_entries_short = []
    tf_exits_long = []
    tf_exits_short = []
    for tf in decision_tfs:
        wt_l = tf_sigs[tf]["wt_long"]
        wt_s = tf_sigs[tf]["wt_short"]
        dc_l = tf_sigs[tf]["dc_long"]
        dc_s = tf_sigs[tf]["dc_short"]
        pctb = tf_sigs[tf]["pctb"]
        bb_ext_l = pctb[:, None] < bb_max[None, :]
        bb_ext_s = pctb[:, None] > bb_min[None, :]
        base_l = (is_dc[None, :] & dc_l[:, None]) | (is_wt[None, :] & wt_l[:, None]) | (is_bb[None, :] & bb_ext_l) | (is_or[None, :] & (dc_l[:, None] | bb_ext_l))
        base_s = (is_dc[None, :] & dc_s[:, None]) | (is_wt[None, :] & wt_s[:, None]) | (is_bb[None, :] & bb_ext_s) | (is_or[None, :] & (dc_s[:, None] | bb_ext_s))
        tf_entries_long.append((wt_l[:, None] & base_l & (pctb[:, None] < 0.95)).astype(np.int8))
        tf_entries_short.append((wt_s[:, None] & base_s & (pctb[:, None] > 0.05)).astype(np.int8))
        tf_exits_long.append((wt_s[:, None] | (pctb[:, None] > 0.95) | dc_s[:, None]).astype(np.int8))
        tf_exits_short.append((wt_l[:, None] | (pctb[:, None] < 0.05) | dc_l[:, None]).astype(np.int8))
    e_long_count = sum(tf_entries_long)
    e_short_count = sum(tf_entries_short)
    x_long_count = sum(tf_exits_long)
    x_short_count = sum(tf_exits_short)
    enter_long = e_long_count >= min_e[None, :]
    enter_short = e_short_count >= min_e[None, :]
    leave_long = x_long_count >= min_x[None, :]
    leave_short = x_short_count >= min_x[None, :]
    rdt = vararr["REQUIRE_D_TREND"][var_slice]
    rdt_b = rdt[None, :]
    enter_long = enter_long & (~rdt_b | d_wt_long[:, None])
    enter_short = enter_short & (~rdt_b | d_wt_short[:, None])
    all_against_long = np.ones(n, dtype=bool)
    all_against_short = np.ones(n, dtype=bool)
    for tf_name in ("3m", "15m", "1h", "4h", "D"):
        w1 = sig.get(f"wt1_{tf_name}")
        w2 = sig.get(f"wt2_{tf_name}")
        if w1 is not None and w2 is not None:
            all_against_long &= (w1 < w2)
            all_against_short &= (w1 > w2)
        else:
            all_against_long = np.zeros(n, dtype=bool)
            all_against_short = np.zeros(n, dtype=bool)
            break
    wt15_1 = sig.get("wt1_15m")
    wt15_2 = sig.get("wt2_15m")
    if wt15_1 is not None and wt15_2 is not None:
        wt15_against_long = wt15_1 < wt15_2
        wt15_against_short = wt15_1 > wt15_2
    else:
        wt15_against_long = np.zeros(n, dtype=bool)
        wt15_against_short = np.zeros(n, dtype=bool)
    close = sig["close_15m"]
    dc_hi_d = sig.get("dc_high_D")
    dc_lo_d = sig.get("dc_low_D")
    bb_up_d = sig.get("bb_upper_D")
    bb_lo_d = sig.get("bb_lower_D")
    d_break_up = np.zeros(n, dtype=bool)
    d_break_dn = np.zeros(n, dtype=bool)
    if dc_hi_d is not None and len(dc_hi_d) == n:
        prev = np.roll(dc_hi_d, 1); prev[0] = dc_hi_d[0]
        d_break_up |= (close > prev) & (prev > 0)
    if dc_lo_d is not None and len(dc_lo_d) == n:
        prev = np.roll(dc_lo_d, 1); prev[0] = dc_lo_d[0]
        d_break_dn |= (close < prev) & (prev > 0)
    if bb_up_d is not None and len(bb_up_d) == n:
        prev = np.roll(bb_up_d, 1); prev[0] = bb_up_d[0]
        d_break_up |= (close > prev) & (prev > 0)
    if bb_lo_d is not None and len(bb_lo_d) == n:
        prev = np.roll(bb_lo_d, 1); prev[0] = bb_lo_d[0]
        d_break_dn |= (close < prev) & (prev > 0)
    wt1_15m = sig.get("wt1_15m")
    bearish_div = np.zeros(n, dtype=bool)
    bullish_div = np.zeros(n, dtype=bool)
    if wt1_15m is not None:
        lb = 20
        from numpy.lib.stride_tricks import sliding_window_view
        price_max_lb = np.full(n, np.nan, dtype=np.float64)
        price_min_lb = np.full(n, np.nan, dtype=np.float64)
        wt_max_lb = np.full(n, np.nan, dtype=np.float64)
        wt_min_lb = np.full(n, np.nan, dtype=np.float64)
        if n >= lb:
            price_max_lb[lb - 1:] = sliding_window_view(close.astype(np.float64), lb).max(axis=1)
            price_min_lb[lb - 1:] = sliding_window_view(close.astype(np.float64), lb).min(axis=1)
            wt_max_lb[lb - 1:] = sliding_window_view(wt1_15m.astype(np.float64), lb).max(axis=1)
            wt_min_lb[lb - 1:] = sliding_window_view(wt1_15m.astype(np.float64), lb).min(axis=1)
        bearish_div = (close >= price_max_lb) & (wt1_15m < wt_max_lb)
        bullish_div = (close <= price_min_lb) & (wt1_15m > wt_min_lb)
    enter_long = enter_long & ~bearish_div[:, None]
    enter_short = enter_short & ~bullish_div[:, None]
    leave_long = leave_long | (all_against_long | wt15_against_long | d_break_dn)[:, None]
    leave_short = leave_short | (all_against_short | wt15_against_short | d_break_up)[:, None]
    return enter_long, leave_long, enter_short, leave_short


def _walk_one_variant(
    enter_long_idx: np.ndarray, leave_long_idx: np.ndarray,
    enter_short_idx: np.ndarray, leave_short_idx: np.ndarray,
    close_15m: np.ndarray, ts_15m: np.ndarray,
    min_hold: int, cooldown: int,
    reverse_on_exit: bool,
    hard_loss_enabled: bool, hard_loss_pct: float,
    peak_protect_enabled: bool,
    peak_giveback_enabled: bool, peak_giveback_drop_pct: float,
    ridiculous_hold_enabled: bool, ridiculous_loss_pct: float, ridiculous_hold_hours: float,
    wt1_15m: np.ndarray, wt2_15m: np.ndarray,
    rt_comm: float,
    enter_long_mask: np.ndarray, enter_short_mask: np.ndarray,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    n = len(close_15m)
    if n < min_hold + 1:
        return np.empty(0, dtype=np.float32), np.empty(0, dtype=np.int64), np.empty(0, dtype=np.int32), np.empty(0, dtype=np.int8)
    pnl_buf: List[float] = []
    ts_buf: List[int] = []
    bars_buf: List[int] = []
    sides_buf: List[int] = []
    i = 0
    el_p = 0
    es_p = 0
    n_el = len(enter_long_idx)
    n_es = len(enter_short_idx)
    n_xl = len(leave_long_idx)
    n_xs = len(leave_short_idx)
    while i < n:
        while el_p < n_el and enter_long_idx[el_p] < i:
            el_p += 1
        while es_p < n_es and enter_short_idx[es_p] < i:
            es_p += 1
        next_long = int(enter_long_idx[el_p]) if el_p < n_el else n + 1
        next_short = int(enter_short_idx[es_p]) if es_p < n_es else n + 1
        if next_long >= n + 1 and next_short >= n + 1:
            break
        if next_long <= next_short:
            entry_i = next_long
            side = 1
        else:
            entry_i = next_short
            side = -1
        if entry_i >= n - 1:
            break
        x_start = entry_i + min_hold
        if x_start >= n:
            break
        if side == 1:
            pos = np.searchsorted(leave_long_idx, x_start, side="left")
            exit_i = int(leave_long_idx[pos]) if pos < n_xl else n - 1
        else:
            pos = np.searchsorted(leave_short_idx, x_start, side="left")
            exit_i = int(leave_short_idx[pos]) if pos < n_xs else n - 1
        ep = float(close_15m[entry_i])
        if ep <= 0:
            i = exit_i + cooldown + 1
            continue
        held_close = close_15m[entry_i:exit_i + 1]
        if side == 1:
            gain_traj = (held_close - ep) / ep * 100.0
        else:
            gain_traj = (ep - held_close) / ep * 100.0
        if hard_loss_enabled and len(gain_traj) > min_hold:
            hl_hit = gain_traj <= -hard_loss_pct
            hl_hit[:min_hold] = False
            hl_idx = np.flatnonzero(hl_hit)
            if len(hl_idx):
                cand = entry_i + int(hl_idx[0])
                if cand < exit_i:
                    exit_i = cand
                    held_close = close_15m[entry_i:exit_i + 1]
                    gain_traj = gain_traj[:exit_i - entry_i + 1]
        if peak_protect_enabled and len(gain_traj) > min_hold:
            had_gain = np.maximum.accumulate(gain_traj) > 0
            if side == 1:
                wt_against = wt1_15m[entry_i:exit_i + 1] < wt2_15m[entry_i:exit_i + 1]
            else:
                wt_against = wt1_15m[entry_i:exit_i + 1] > wt2_15m[entry_i:exit_i + 1]
            pp_hit = wt_against & had_gain
            pp_hit[:min_hold] = False
            pp_idx = np.flatnonzero(pp_hit)
            if len(pp_idx):
                cand = entry_i + int(pp_idx[0])
                if cand < exit_i:
                    exit_i = cand
                    held_close = close_15m[entry_i:exit_i + 1]
                    gain_traj = gain_traj[:exit_i - entry_i + 1]
        if peak_giveback_enabled and len(gain_traj) > min_hold:
            running_peak = np.maximum.accumulate(gain_traj)
            armed = running_peak >= 0.10
            drop = running_peak - gain_traj
            give_hit = armed & (drop >= peak_giveback_drop_pct)
            give_hit[:min_hold] = False
            give_idx = np.flatnonzero(give_hit)
            if len(give_idx):
                cand = entry_i + int(give_idx[0])
                if cand < exit_i:
                    exit_i = cand
                    held_close = close_15m[entry_i:exit_i + 1]
                    gain_traj = gain_traj[:exit_i - entry_i + 1]
        if ridiculous_hold_enabled and len(gain_traj) > min_hold:
            held_secs = (ts_15m[entry_i:exit_i + 1] - ts_15m[entry_i]).astype(np.float64)
            held_hours = held_secs / 3600.0
            rh_hit = (gain_traj <= ridiculous_loss_pct) | (held_hours > ridiculous_hold_hours)
            rh_hit[:min_hold] = False
            rh_idx = np.flatnonzero(rh_hit)
            if len(rh_idx):
                cand = entry_i + int(rh_idx[0])
                if cand < exit_i:
                    exit_i = cand
        xp = float(close_15m[exit_i])
        if xp <= 0:
            i = exit_i + cooldown + 1
            continue
        pnl_gross = (xp - ep) / ep * 100.0 if side == 1 else (ep - xp) / ep * 100.0
        pnl_buf.append(pnl_gross - rt_comm)
        ts_buf.append(int(ts_15m[exit_i]))
        bars_buf.append(exit_i - entry_i)
        sides_buf.append(side)
        if reverse_on_exit and exit_i < n - 1:
            rev_idx = exit_i
            next_side = -side
            if next_side == 1 and enter_long_mask[rev_idx]:
                i = rev_idx
                continue
            elif next_side == -1 and enter_short_mask[rev_idx]:
                i = rev_idx
                continue
        i = exit_i + cooldown + 1
    if len(pnl_buf) == 0 and n > 0:
        ep = float(close_15m[0])
        xp = float(close_15m[-1])
        if ep > 0 and xp > 0:
            pnl_gross = (xp - ep) / ep * 100.0 if sides_buf and sides_buf[-1] == 1 else (ep - xp) / ep * 100.0
            pnl_buf.append(pnl_gross - rt_comm)
            ts_buf.append(int(ts_15m[-1]))
            bars_buf.append(n - 1)
            sides_buf.append(1)
    return np.array(pnl_buf, dtype=np.float32), np.array(ts_buf, dtype=np.int64), np.array(bars_buf, dtype=np.int32), np.array(sides_buf, dtype=np.int8)


def _time_weighted_sharpe(pnls: np.ndarray, ets: np.ndarray, ref_ts: int) -> float:
    if len(pnls) < 2:
        return 0.0
    decay = 1.0 - np.clip((ref_ts - ets) / (365.25 * 86400.0 * 2.0), 0.0, 1.0)
    w = decay / decay.sum()
    mean = float((pnls * w).sum())
    var = float((w * (pnls - mean) ** 2).sum())
    std = math.sqrt(var)
    return float(mean / std) if std > 1e-12 else 0.0


def _max_dd_from_returns(pnls: np.ndarray) -> float:
    if len(pnls) == 0:
        return 0.0
    cum = np.cumsum(pnls)
    peaks = np.maximum.accumulate(cum)
    dds = peaks - cum
    return float(dds.max())


def sweep_variants(
    sym: str,
    variants: List[Dict[str, Any]],
    years_back: float = 7.0 / 365.25,
    chunk_size: int = 10_000,
    verbose: bool = True,
    n_years_for_yr_metrics: Optional[float] = None,
) -> Dict[str, np.ndarray]:
    t0 = time.time()
    sig = _precompute_signals(sym, years_back)
    if sig is None:
        if verbose:
            print(f"[vec_sweep {sym}] NO NPZ or insufficient bars", flush=True)
        return _empty_scoreboard(len(variants))
    tf_sigs = _build_tf_signals(sig)
    if verbose:
        print(f"[vec_sweep {sym}] precompute {time.time()-t0:.2f}s | n_bars={sig['n_15m']}", flush=True)
    n_v = len(variants)
    vararr = _variants_to_arrays(variants)
    pool_sharpe = np.zeros(n_v, dtype=np.float32)
    tw_sharpe = np.zeros(n_v, dtype=np.float32)
    trades = np.zeros(n_v, dtype=np.int32)
    win_rate = np.zeros(n_v, dtype=np.float32)
    avg_gain = np.zeros(n_v, dtype=np.float32)
    gain_per_yr = np.zeros(n_v, dtype=np.float32)
    max_dd = np.zeros(n_v, dtype=np.float32)
    long_trades = np.zeros(n_v, dtype=np.int32)
    short_trades = np.zeros(n_v, dtype=np.int32)
    yrs_for_pyr = float(n_years_for_yr_metrics if n_years_for_yr_metrics is not None else years_back)
    yrs_for_pyr = max(yrs_for_pyr, 1e-6)
    close_15m = sig["close_15m"]
    ts_15m = sig["ts_15m"]
    wt1_15m = sig.get("wt1_15m", np.zeros_like(close_15m))
    wt2_15m = sig.get("wt2_15m", np.zeros_like(close_15m))
    ref_ts = int(ts_15m.max()) if len(ts_15m) else 0
    n_chunks = (n_v + chunk_size - 1) // chunk_size
    for ck in range(n_chunks):
        lo = ck * chunk_size
        hi = min(lo + chunk_size, n_v)
        sl = slice(lo, hi)
        tk = time.time()
        e_l, x_l, e_s, x_s = _build_entry_exit_masks_chunk(tf_sigs, sig, vararr, sl)
        mask_dt = time.time() - tk
        tw = time.time()
        chunk_size_actual = hi - lo
        for j_local in range(chunk_size_actual):
            j_global = lo + j_local
            el_col = e_l[:, j_local]
            es_col = e_s[:, j_local]
            el_idx = np.flatnonzero(el_col)
            es_idx = np.flatnonzero(es_col)
            xl_idx = np.flatnonzero(x_l[:, j_local])
            xs_idx = np.flatnonzero(x_s[:, j_local])
            pnls, ets, bars, sides = _walk_one_variant(
                el_idx, xl_idx, es_idx, xs_idx,
                close_15m, ts_15m,
                int(vararr["MIN_HOLD_BARS"][j_global]), int(vararr["COOLDOWN_BARS"][j_global]),
                bool(vararr["REVERSE_ON_EXIT"][j_global]),
                bool(vararr["HARD_LOSS_ENABLED"][j_global]),
                float(vararr["HARD_LOSS_PCT"][j_global]),
                bool(vararr["PEAK_PROTECT_ENABLED"][j_global]),
                bool(vararr["PEAK_GIVEBACK_ENABLED"][j_global]),
                float(vararr["PEAK_GIVEBACK_DROP_PCT"][j_global]),
                bool(vararr["RIDICULOUS_HOLD_ENABLED"][j_global]),
                float(vararr["RIDICULOUS_LOSS_PCT"][j_global]),
                float(vararr["RIDICULOUS_HOLD_HOURS"][j_global]),
                wt1_15m, wt2_15m,
                RT_COMM,
                el_col, es_col,
            )
            trades[j_global] = len(pnls)
            if len(pnls) >= 2:
                std = float(pnls.std())
                pool_sharpe[j_global] = float(pnls.mean() / std) if std > 1e-12 else 0.0
                tw_sharpe[j_global] = _time_weighted_sharpe(pnls, ets, ref_ts=ref_ts)
                win_rate[j_global] = float((pnls > 0).mean() * 100.0)
                avg_gain[j_global] = float(pnls.mean())
                gain_per_yr[j_global] = float(pnls.sum()) / yrs_for_pyr
                max_dd[j_global] = _max_dd_from_returns(pnls)
                long_trades[j_global] = int((sides == 1).sum())
                short_trades[j_global] = int((sides == -1).sum())
            elif len(pnls) == 1:
                pool_sharpe[j_global] = 0.0
                tw_sharpe[j_global] = 0.0
                win_rate[j_global] = 100.0 if pnls[0] > 0 else 0.0
                avg_gain[j_global] = float(pnls[0])
                gain_per_yr[j_global] = float(pnls[0]) / yrs_for_pyr
                long_trades[j_global] = int((sides == 1).sum())
                short_trades[j_global] = int((sides == -1).sum())
        walk_dt = time.time() - tw
        if verbose:
            elapsed = time.time() - t0
            print(f"  chunk {ck+1}/{n_chunks} ({hi-lo} vars) mask={mask_dt:.2f}s walk={walk_dt:.2f}s | total={elapsed:.1f}s", flush=True)
    if verbose:
        elapsed = time.time() - t0
        rate = n_v / max(elapsed, 1e-6)
        print(f"[vec_sweep {sym}] DONE {n_v} variants in {elapsed:.1f}s ({rate:.0f} variants/s)", flush=True)
    return {
        "pool_sharpe": pool_sharpe,
        "time_weighted_sharpe": tw_sharpe,
        "trades": trades,
        "win_rate_pct": win_rate,
        "avg_gain_trade_pct": avg_gain,
        "gain_per_yr_pct": gain_per_yr,
        "max_dd_pct": max_dd,
        "long_trades": long_trades,
        "short_trades": short_trades,
    }


def _empty_scoreboard(n: int) -> Dict[str, np.ndarray]:
    return {
        "pool_sharpe": np.zeros(n, dtype=np.float32),
        "time_weighted_sharpe": np.zeros(n, dtype=np.float32),
        "trades": np.zeros(n, dtype=np.int32),
        "win_rate_pct": np.zeros(n, dtype=np.float32),
        "avg_gain_trade_pct": np.zeros(n, dtype=np.float32),
        "gain_per_yr_pct": np.zeros(n, dtype=np.float32),
        "max_dd_pct": np.zeros(n, dtype=np.float32),
        "long_trades": np.zeros(n, dtype=np.int32),
        "short_trades": np.zeros(n, dtype=np.int32),
    }


def top_k(scoreboard: Dict[str, np.ndarray], variants: List[Dict[str, Any]],
          k: int = 10, sort_by: str = "time_weighted_sharpe",
          filter_min_trades: int = 0) -> List[Tuple[Dict[str, Any], Dict[str, float]]]:
    n = len(variants)
    indices = np.arange(n)
    if filter_min_trades > 0:
        mask = scoreboard["trades"] >= filter_min_trades
        indices = indices[mask]
    if len(indices) == 0:
        return []
    vals = scoreboard[sort_by][indices]
    top_subset = np.argsort(vals)[::-1][:k]
    g_idx = indices[top_subset]
    out = []
    for idx in g_idx:
        met = {
            "pool_sharpe": float(scoreboard["pool_sharpe"][idx]),
            "time_weighted_sharpe": float(scoreboard["time_weighted_sharpe"][idx]),
            "trades": int(scoreboard["trades"][idx]),
            "win_rate_pct": float(scoreboard["win_rate_pct"][idx]),
            "avg_gain_trade_pct": float(scoreboard["avg_gain_trade_pct"][idx]),
            "gain_per_yr_pct": float(scoreboard["gain_per_yr_pct"][idx]),
            "max_dd_pct": float(scoreboard["max_dd_pct"][idx]),
            "long_trades": int(scoreboard["long_trades"][idx]),
            "short_trades": int(scoreboard["short_trades"][idx]),
        }
        out.append((variants[idx], met))
    return out
