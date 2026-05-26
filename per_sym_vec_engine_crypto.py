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
    close_3m = sliced_3m["close"]
    n_3m = len(close_3m)
    d_break_up_3m = np.zeros(n_3m, dtype=np.bool_)
    d_break_dn_3m = np.zeros(n_3m, dtype=np.bool_)
    for fld, is_up in [("dc_high_D", True), ("dc_low_D", False), ("bb_upper_D", True), ("bb_lower_D", False)]:
        arr = sliced_3m.get(fld)
        if arr is not None and len(arr) == n_3m:
            prev = np.roll(arr, 1); prev[0] = arr[0]
            if is_up: d_break_up_3m |= (close_3m > prev) & (prev > 0)
            else: d_break_dn_3m |= (close_3m < prev) & (prev > 0)
    out["d_break_up_15m"] = _ss_3m_to_15m(d_break_up_3m, n_15m)
    out["d_break_dn_15m"] = _ss_3m_to_15m(d_break_dn_3m, n_15m)
    if "D" in tf_data and len(tf_data["D"]["close"]) > 0:
        c_d = tf_data["D"]["close"].astype(np.float64)
        def _rm_cs(arr, w):
            if len(arr) < w: return np.zeros_like(arr)
            cs = np.cumsum(arr)
            o_rm = np.zeros_like(arr)
            o_rm[w - 1:] = (cs[w - 1:] - np.concatenate([[0.0], cs[:-w]])) / w
            for idx in range(min(w - 1, len(arr))): o_rm[idx] = arr[:idx + 1].mean()
            return o_rm
        m_d = _rm_cs(c_d, 20)
        rep_long = np.repeat(c_d > m_d, 96)
        rep_short = np.repeat(c_d < m_d, 96)
        if len(rep_long) < n_15m:
            rep_long = np.concatenate([rep_long, np.zeros(n_15m - len(rep_long), dtype=bool)])
            rep_short = np.concatenate([rep_short, np.zeros(n_15m - len(rep_short), dtype=bool)])
        out["d_trend_long"] = rep_long[:n_15m]
        out["d_trend_short"] = rep_short[:n_15m]
    else:
        out["d_trend_long"] = np.ones(n_15m, dtype=bool)
        out["d_trend_short"] = np.ones(n_15m, dtype=bool)
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
    d_trend_long = sig["d_trend_long"]
    d_trend_short = sig["d_trend_short"]
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
    enter_long = enter_long & (~rdt_b | d_trend_long[:, None])
    enter_short = enter_short & (~rdt_b | d_trend_short[:, None])
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
    d_break_up = sig.get("d_break_up_15m", np.zeros(n, dtype=bool))
    d_break_dn = sig.get("d_break_dn_15m", np.zeros(n, dtype=bool))
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
        x_start = entry_i + max(min_hold, 1)
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


def _run_variant_task_worker(args):
    sym, start_ts, variant, years_back = args
    import sys
    from pathlib import Path
    import numpy as np
    import math
    _ROOT = Path(__file__).resolve().parent
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))
    from v8_vec_sweep import load_npz, simulate_one_symbol, SweepConfig
    
    try:
        npz, ts = load_npz(sym, "crypto", start_ts=start_ts)
        _npz_cache = (npz, ts)
    except Exception:
        return None
        
    cfg = SweepConfig()
    
    _VEC_LOCKED_FALSE_KNOBS = (
        "UNIVERSAL_NOLOSS_GATE",
        "VEC_NOLOSS_GATE_ENABLED",
        "HEDGE_SCAN_ENABLED",
        "HEDGE_MODE",
        "OBLIGATORY_HEDGE_ENABLED",
        "NOLOSS_ENABLED",
    )
    
    # 2026-05-26 FIX: setattr ALL keys (not just hasattr ones) so vec_paths/* that
    # read knobs via getattr(config, "K", default) honor dynamic-attr overrides.
    # Bug before: hasattr-filter dropped 80% of variant knobs because variant names
    # come from per_sym_engine_crypto SymParams namespace (ENTRY_MODE, BB_LONG_ENTRY_MAX,
    # HARD_LOSS_PCT, etc.) which are NOT SweepConfig fields. Result: every variant
    # produced identical SweepConfig-default behavior — sweep was a no-op for syms
    # where defaults didn't fire entries (ANKRUSDT, BELUSDT, GRTUSDT, KAVAUSDT, LRCUSDT,
    # etc. — all "no trades" syms in the 4-shard grind).
    for k, v in variant.items():
        if k.startswith("_"):
            continue
        if k == "NOLOSS_ENABLED":
            continue
        try:
            setattr(cfg, k, v)
        except Exception:
            pass

    for locked in _VEC_LOCKED_FALSE_KNOBS:
        try:
            setattr(cfg, locked, False)
        except Exception:
            pass
            
    try:
        long_events, long_rets, _ = simulate_one_symbol(
            sym, "LONG", "crypto", cfg,
            start_ts=start_ts,
            _npz_cache=_npz_cache,
        )
    except Exception as _vec_e_long:
        import os, sys
        if os.environ.get("VEC_DEBUG_EXC") == "1":
            sys.stderr.write(f"[VEC_WORKER_EXC] LONG {sym}: {type(_vec_e_long).__name__}: {_vec_e_long}\n")
        long_events, long_rets = [], []
        
    try:
        short_events, short_rets, _ = simulate_one_symbol(
            sym, "SHORT", "crypto", cfg,
            start_ts=start_ts,
            _npz_cache=_npz_cache,
        )
    except Exception as _vec_e_short:
        import os, sys
        if os.environ.get("VEC_DEBUG_EXC") == "1":
            sys.stderr.write(f"[VEC_WORKER_EXC] SHORT {sym}: {type(_vec_e_short).__name__}: {_vec_e_short}\n")
        short_events, short_rets = [], []
        
    long_ets = [int(ev.ts) for ev in long_events if ev.type in ("CLOSE", "REDUCE", "HEDGE_CLOSE")]
    short_ets = [int(ev.ts) for ev in short_events if ev.type in ("CLOSE", "REDUCE", "HEDGE_CLOSE")]
    
    long_rets = long_rets[:len(long_ets)]
    short_rets = short_rets[:len(short_ets)]
    
    pnls = np.array(long_rets + short_rets, dtype=np.float32)
    ets = np.array(long_ets + short_ets, dtype=np.int64)
    
    if len(pnls) > 0:
        sort_idx = np.argsort(ets)
        pnls = pnls[sort_idx]
        ets = ets[sort_idx]
        
    trades_n = len(pnls)
    ref_ts = int(ts[-1]) if len(ts) else 0
    yrs_for_pyr = max(0.01, float(years_back))
    
    if trades_n >= 2:
        std = float(pnls.std())
        pool_s = float(pnls.mean() / std) if std > 1e-12 else 0.0
        
        decay = 1.0 - np.clip((ref_ts - ets) / (365.25 * 86400.0 * 2.0), 0.0, 1.0)
        decay_sum = decay.sum()
        if decay_sum > 1e-12:
            w = decay / decay_sum
            mean = float((pnls * w).sum())
            var = float((w * (pnls - mean) ** 2).sum())
            std_w = math.sqrt(var)
            tw_s = float(mean / std_w) if std_w > 1e-12 else 0.0
        else:
            tw_s = 0.0
            
        wr = float((pnls > 0).mean() * 100.0)
        avg_g = float(pnls.mean())
        gain_yr = float(pnls.sum()) / yrs_for_pyr
        
        cum = np.cumsum(pnls)
        peaks = np.maximum.accumulate(cum)
        dds = peaks - cum
        dd = float(dds.max()) if len(dds) else 0.0
    elif trades_n == 1:
        pool_s = 0.0
        tw_s = 0.0
        wr = 100.0 if pnls[0] > 0 else 0.0
        avg_g = float(pnls[0])
        gain_yr = float(pnls[0]) / yrs_for_pyr
        dd = 0.0
    else:
        pool_s = 0.0
        tw_s = 0.0
        wr = 0.0
        avg_g = 0.0
        gain_yr = 0.0
        dd = 0.0
        
    return {
        "pool_sharpe": pool_s,
        "time_weighted_sharpe": tw_s,
        "trades": trades_n,
        "win_rate_pct": wr,
        "avg_gain_trade_pct": avg_g,
        "gain_per_yr_pct": gain_yr,
        "max_dd_pct": dd,
        "long_trades": len(long_rets),
        "short_trades": len(short_rets),
    }


def sweep_variants(
    sym: str,
    variants: List[Dict[str, Any]],
    years_back: float = 7.0 / 365.25,
    chunk_size: int = 10_000,
    verbose: bool = True,
    n_years_for_yr_metrics: Optional[float] = None,
) -> Dict[str, np.ndarray]:
    t0 = time.time()
    from v8_vec_sweep import load_npz
    try:
        npz, ts = load_npz(sym, "crypto", start_ts=None)
        if len(ts) < 50:
            if verbose:
                print(f"[vec_sweep {sym}] Insufficient bars", flush=True)
            return _empty_scoreboard(len(variants))
    except Exception:
        if verbose:
            print(f"[vec_sweep {sym}] NO NPZ found", flush=True)
        return _empty_scoreboard(len(variants))
        
    n_v = len(variants)
    pool_sharpe = np.zeros(n_v, dtype=np.float32)
    tw_sharpe = np.zeros(n_v, dtype=np.float32)
    trades = np.zeros(n_v, dtype=np.int32)
    win_rate = np.zeros(n_v, dtype=np.float32)
    avg_gain = np.zeros(n_v, dtype=np.float32)
    gain_per_yr = np.zeros(n_v, dtype=np.float32)
    max_dd = np.zeros(n_v, dtype=np.float32)
    long_trades = np.zeros(n_v, dtype=np.int32)
    short_trades = np.zeros(n_v, dtype=np.int32)
    
    ts_3m = ts.astype(np.int64)
    cutoff = ts_3m[-1] - int(years_back * 365.25 * 86400)
    start_ts = int(cutoff)
    
    import multiprocessing
    # 2026-05-26 FIX: workers count was OOMing on S1 (BrokenProcessPool on every variant
    # when 15+ workers × ~365MB NPZ each + concurrent shards exceeded 31GB RAM). Cap
    # default at 4 and let VEC_SWEEP_WORKERS env var override for ample-memory boxes.
    _cpu = (multiprocessing.cpu_count() or 4) - 1
    _env_w = os.environ.get("VEC_SWEEP_WORKERS")
    if _env_w and _env_w.isdigit():
        n_workers = max(1, int(_env_w))
    else:
        n_workers = max(1, min(4, _cpu))
    if verbose:
        print(f"[vec_sweep {sym}] starting high-parity variant sweep over {n_v} combinations | workers={n_workers} | start_ts={start_ts}", flush=True)
        
    tasks = [(sym, start_ts, var, years_back) for var in variants]
    
    from concurrent.futures import ProcessPoolExecutor, as_completed
    _debug_exc = os.environ.get("VEC_DEBUG_EXC") == "1"
    _none_n = 0
    _exc_n = 0
    _ok_n = 0
    with ProcessPoolExecutor(max_workers=n_workers) as executor:
        futures = {executor.submit(_run_variant_task_worker, t): idx for idx, t in enumerate(tasks)}
        for fut in as_completed(futures):
            idx = futures[fut]
            try:
                res = fut.result()
                if res is not None:
                    pool_sharpe[idx] = res["pool_sharpe"]
                    tw_sharpe[idx] = res["time_weighted_sharpe"]
                    trades[idx] = res["trades"]
                    win_rate[idx] = res["win_rate_pct"]
                    avg_gain[idx] = res["avg_gain_trade_pct"]
                    gain_per_yr[idx] = res["gain_per_yr_pct"]
                    max_dd[idx] = res["max_dd_pct"]
                    long_trades[idx] = res["long_trades"]
                    short_trades[idx] = res["short_trades"]
                    _ok_n += 1
                else:
                    _none_n += 1
                    if _debug_exc:
                        sys.stderr.write(f"[VEC_RESULT_NONE] {sym} idx={idx}\n")
            except Exception as e:
                _exc_n += 1
                if _debug_exc:
                    sys.stderr.write(f"[VEC_RESULT_EXC] {sym} idx={idx}: {type(e).__name__}: {e}\n")
    if verbose:
        print(f"[vec_sweep {sym}] result-collection: ok={_ok_n} none={_none_n} exc={_exc_n}", flush=True)
                
    if verbose:
        print(f"[vec_sweep {sym}] DONE {n_v} variants in {time.time()-t0:.1f}s", flush=True)
        
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
