"""lt_direction_filter_engine.py — per-symbol LT-direction filter backtest.

USER HYPOTHESIS 2026-05-18: v3_no_stop sleeve (pool_sharpe +0.189, 91 syms,
sub-floor) underperforms because it longs downtrending names + shorts uptrending
names. Fix: only long when close_D > sma_200_D, only short when close_D <
sma_200_D. Hypothesis: this lifts pool_sharpe above 1.0.

This is a STANDALONE engine on top of v3_no_stop (long-only) that:
  - adds a SHORT mirror of every entry/exit path (path A_s / B_s / ... X1_s ...)
  - gates entries on a per-bar `close_D > sma_200_D` (longs) / `<` (shorts) filter
  - supports 4 variants via --mode flag:
      CTRL              : long-only, NO LT filter (current v3_no_stop replica)
      A_long_filtered   : long-only, LT-UP gate
      B_dual_filtered   : longs LT-UP + shorts LT-DOWN
      C_short_only      : short-only, LT-DOWN gate

NO live code touched. NO NPZ regen.
All sharpes via per-trade returns, MtM final open trade (NO LIES MANDATE rule 2).
Sub-floor results tagged [DIAGNOSTIC]; ≥100 syms × >1yr × ≥30 tr/sym → [PUBLISHABLE].
"""
from __future__ import annotations

import argparse, csv, datetime, json, sys, time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from v8_vec_structure_sweep import load_npz_slim
from v12_wide_engine import _resolve_npz_path
from vec_paths.structure_hh_hl import (
    compute_all_structure, _streak_at_value, _recent_events_count,
    STRUCT_HH, STRUCT_HL, STRUCT_LH, STRUCT_LL,
)


# ════════════════════════════════════════════════════════════════════════════════
# Extended NPZ loader — slim + LT-filter fields (sma_200_D, rsi_D)
# ════════════════════════════════════════════════════════════════════════════════

def load_npz_for_lt(symbol: str, mode: str, *, start_ts: Optional[int] = None):
    base, ts = load_npz_slim(symbol, mode, start_ts=start_ts)
    p = _resolve_npz_path(symbol, mode)
    extras = ("sma_200_D", "rsi_D")
    with np.load(p, allow_pickle=True) as z:
        ts_full = np.asarray(z["timestamps"])
        if start_ts is not None:
            i0 = int(np.searchsorted(ts_full, start_ts, side="left"))
        else:
            i0 = 0
        for k in extras:
            if k in z.files:
                arr = np.asarray(z[k])
                if arr.ndim == 0 or arr.shape[0] < len(ts_full):
                    continue
                base[k] = arr[i0:]
    return base, ts


# ════════════════════════════════════════════════════════════════════════════════
# Config — mirrors v3_no_stop knobs + LT-filter + side mode
# ════════════════════════════════════════════════════════════════════════════════

@dataclass
class LTCfg:
    # Mode: CTRL / A_long_filtered / B_dual_filtered / C_short_only
    mode_name: str = "CTRL"
    side: str = "LONG_ONLY"        # LONG_ONLY / SHORT_ONLY / BOTH
    lt_filter_enabled: bool = False
    # Capital
    initial_capital: float = 10_000.0
    full_entry_fraction: float = 1.0
    pyramid_add_fraction: float = 0.5
    max_pyramid_levels: int = 5
    pyramid_tf: str = "4h"
    pyramid_also_on_k1h_oversold: bool = True
    pyramid_require_new_pyr_HH: bool = True
    pyramid_min_gain_since_last_pct: float = 3.0
    round_trip_cost_pct: float = 0.0
    cooldown_bars_after_exit: int = 5
    # ── ENTRY paths (v3_no_stop defaults) ───────────────────────────────────
    path_A_struct_enabled: bool = True
    path_A_breakout_tf: str = "D"
    path_A_retest_tf: str = "1h"
    path_A_trigger_tf: str = "15m"
    path_A_min_count: int = 4
    path_A_atr_band: float = 1.0
    path_A_regime_persist_bars: int = 500
    path_B_oversold_enabled: bool = True
    path_B_k15_max: float = 30.0
    path_B_k1h_max: float = 40.0
    path_B_require_wt_bull_15m: bool = True
    path_B_require_reclaim_pivot_lo: bool = True
    path_C_k1h_cross_enabled: bool = True
    path_C_k1h_cross_below: float = 30.0
    path_D_wtD_cross_enabled: bool = True
    path_D_rsi_D_min: float = 40.0
    path_E_sma200_reclaim_enabled: bool = True
    path_E_rsi_D_min: float = 50.0
    path_F_weekly_flip_enabled: bool = True
    path_G_momentum_continuation_enabled: bool = True
    path_G_k1h_cross_min: float = 50.0
    htf_trend_filter_enabled: bool = True
    htf_require_wt_D_bull: bool = True
    htf_require_rsi_D_min: float = 45.0
    # ── EXITS (v3_no_stop = X1/X2/X4/X5 + NO X3 hard stop) ─────────────────
    exit_X1_topcatch_enabled: bool = True
    exit_X1_k15_min: float = 80.0
    exit_X2_trailing_pct: float = 12.0
    exit_X4_daily_bear_wt_enabled: bool = True
    exit_X5_structural_flip_enabled: bool = True
    exit_X5_min_count: int = 3
    exit_X5_window_bars: int = 3


# ════════════════════════════════════════════════════════════════════════════════
# Helpers
# ════════════════════════════════════════════════════════════════════════════════

def _f(npz, key, n, default=0.0):
    arr = npz.get(key)
    if arr is None:
        return np.full(n, default, dtype=np.float64)
    a = np.asarray(arr, dtype=np.float64)
    if a.shape[0] != n:
        return np.full(n, default, dtype=np.float64)
    return a


def _bull_cross(fast, slow):
    prev_below = np.concatenate(([False], fast[:-1] <= slow[:-1]))
    now_above = fast > slow
    return prev_below & now_above


def _bear_cross(fast, slow):
    prev_above = np.concatenate(([False], fast[:-1] >= slow[:-1]))
    now_below = fast < slow
    return prev_above & now_below


# ════════════════════════════════════════════════════════════════════════════════
# Per-symbol simulator — supports LONG, SHORT, BOTH sides
# ════════════════════════════════════════════════════════════════════════════════

def simulate(symbol: str, mode: str, cfg: LTCfg, *, start_ts: Optional[int] = None) -> Dict[str, Any]:
    try:
        npz, ts = load_npz_for_lt(symbol, mode, start_ts=start_ts)
    except Exception as e:
        return {"sym": symbol, "trades": 0, "compound_mult": 1.0, "skip": True, "err": str(e)}
    close = np.asarray(npz["close"], dtype=np.float64)
    n = len(close)
    if n < 200:
        return {"sym": symbol, "trades": 0, "compound_mult": 1.0, "skip": True, "err": "n<200"}

    structure = compute_all_structure(npz, mode)
    k15 = _f(npz, "stoch_k_15m", n, 50.0)
    k1h = _f(npz, "stoch_k_1h", n, 50.0)
    d1h = _f(npz, "stoch_d_1h", n, 50.0)
    wt1_15 = _f(npz, "wt1_15m", n)
    wt2_15 = _f(npz, "wt2_15m", n)
    wt1_1h = _f(npz, "wt1_1h", n)
    wt2_1h = _f(npz, "wt2_1h", n)
    wt1_D = _f(npz, "wt1_D", n)
    wt2_D = _f(npz, "wt2_D", n)
    wt1_W = _f(npz, "wt1_W", n)
    wt2_W = _f(npz, "wt2_W", n)
    rsi_D = _f(npz, "rsi_D", n, 50.0)
    sma200_D = _f(npz, "sma_200_D", n, 0.0)

    bull_wt_15 = _bull_cross(wt1_15, wt2_15)
    bear_wt_15 = _bear_cross(wt1_15, wt2_15)
    bull_wt_1h = _bull_cross(wt1_1h, wt2_1h)
    bear_wt_1h = _bear_cross(wt1_1h, wt2_1h)
    bull_wt_D  = _bull_cross(wt1_D,  wt2_D)
    bear_wt_D  = _bear_cross(wt1_D,  wt2_D)
    bull_wt_W  = _bull_cross(wt1_W,  wt2_W)
    bear_wt_W  = _bear_cross(wt1_W,  wt2_W)
    bull_k_1h  = _bull_cross(k1h, d1h)
    bear_k_1h  = _bear_cross(k1h, d1h)
    bull_sma200 = _bull_cross(close, sma200_D)
    bear_sma200 = _bear_cross(close, sma200_D)

    # ── PER-BAR LT-direction masks ──────────────────────────────────────────
    valid_sma = (sma200_D > 0) & np.isfinite(sma200_D)
    if cfg.lt_filter_enabled:
        lt_up_long  = valid_sma & (close > sma200_D)
        lt_dn_short = valid_sma & (close < sma200_D)
    else:
        lt_up_long  = np.ones(n, dtype=bool)
        lt_dn_short = np.ones(n, dtype=bool)

    # ── LONG path entries (mirrors v8_struct_v4_aggressive v3_no_stop) ─────
    bk_price = structure.get(("price", cfg.path_A_breakout_tf)) if cfg.path_A_struct_enabled else None
    rt_price = structure.get(("price", cfg.path_A_retest_tf)) if cfg.path_A_struct_enabled else None
    bk_is_hh = np.zeros(n, dtype=bool)
    bk_is_ll = np.zeros(n, dtype=bool)
    rt_retest_lo_ok = np.zeros(n, dtype=bool)
    rt_retest_hi_ok = np.zeros(n, dtype=bool)
    if bk_price is not None and rt_price is not None:
        bk_streak_hh = _streak_at_value(bk_price["struct"], int(STRUCT_HH))
        bk_streak_ll = _streak_at_value(bk_price["struct"], int(STRUCT_LL))
        bk_is_hh = bk_streak_hh >= cfg.path_A_regime_persist_bars
        bk_is_ll = bk_streak_ll >= cfg.path_A_regime_persist_bars
        atr_rt = _f(npz, f"atr_{cfg.path_A_retest_tf}", n, 0.0)
        with np.errstate(invalid="ignore", divide="ignore"):
            rt_retest_lo_ok = np.where(atr_rt > 0,
                np.abs(close - rt_price["last_lo"]) / np.maximum(atr_rt, 1e-9) < cfg.path_A_atr_band,
                False).astype(bool)
            rt_retest_hi_ok = np.where(atr_rt > 0,
                np.abs(close - rt_price["last_hi"]) / np.maximum(atr_rt, 1e-9) < cfg.path_A_atr_band,
                False).astype(bool)
    hl_count = np.zeros(n, dtype=np.int8)
    lh_count = np.zeros(n, dtype=np.int8)
    for s in ("price", "wt1", "wt2", "stoch_k", "dc_basis"):
        d = structure.get((s, cfg.path_A_trigger_tf))
        if d is None:
            continue
        hl_count += (d["struct"] == STRUCT_HL).astype(np.int8)
        lh_count += (d["struct"] == STRUCT_LH).astype(np.int8)

    tg_price = structure.get(("price", "15m"))
    last_pivot_lo = tg_price["last_lo"] if tg_price is not None else np.full(n, np.nan)
    last_pivot_hi = tg_price["last_hi"] if tg_price is not None else np.full(n, np.nan)
    reclaim_ok_long = np.where(np.isfinite(last_pivot_lo) & (last_pivot_lo > 0),
                                close > last_pivot_lo, False)
    reclaim_ok_short = np.where(np.isfinite(last_pivot_hi) & (last_pivot_hi > 0),
                                 close < last_pivot_hi, False)

    pyramid_tf_struct = structure.get(("price", cfg.pyramid_tf))
    if pyramid_tf_struct is not None:
        new_pyr_HH = (pyramid_tf_struct["pivot_event"] == STRUCT_HH)
        new_pyr_LL = (pyramid_tf_struct["pivot_event"] == STRUCT_LL)
    else:
        new_pyr_HH = np.zeros(n, dtype=bool)
        new_pyr_LL = np.zeros(n, dtype=bool)
    if cfg.pyramid_also_on_k1h_oversold:
        new_pyr_HH = new_pyr_HH | ((k1h < 25.0) & bull_wt_1h)
        new_pyr_LL = new_pyr_LL | ((k1h > 75.0) & bear_wt_1h)

    # ── ENTRY masks (LONG side) ─────────────────────────────────────────────
    entry_A_L = (bk_is_hh & rt_retest_lo_ok & (hl_count >= cfg.path_A_min_count)) if cfg.path_A_struct_enabled else np.zeros(n, dtype=bool)
    entry_B_L = np.zeros(n, dtype=bool)
    if cfg.path_B_oversold_enabled:
        m = (k15 < cfg.path_B_k15_max) & (k1h < cfg.path_B_k1h_max)
        if cfg.path_B_require_wt_bull_15m:
            m &= bull_wt_15
        if cfg.path_B_require_reclaim_pivot_lo:
            m &= reclaim_ok_long
        entry_B_L = m
    entry_C_L = (bull_k_1h & (k1h < cfg.path_C_k1h_cross_below)) if cfg.path_C_k1h_cross_enabled else np.zeros(n, dtype=bool)
    entry_D_L = (bull_wt_D & (rsi_D > cfg.path_D_rsi_D_min)) if cfg.path_D_wtD_cross_enabled else np.zeros(n, dtype=bool)
    entry_E_L = (bull_sma200 & (rsi_D > cfg.path_E_rsi_D_min)) if cfg.path_E_sma200_reclaim_enabled else np.zeros(n, dtype=bool)
    entry_F_L = bull_wt_W if cfg.path_F_weekly_flip_enabled else np.zeros(n, dtype=bool)
    if cfg.path_G_momentum_continuation_enabled:
        k1h_up = _bull_cross(k1h, np.full(n, cfg.path_G_k1h_cross_min))
        entry_G_L = k1h_up & (wt1_1h > wt2_1h)
    else:
        entry_G_L = np.zeros(n, dtype=bool)

    # ── ENTRY masks (SHORT side, mirror) ────────────────────────────────────
    entry_A_S = (bk_is_ll & rt_retest_hi_ok & (lh_count >= cfg.path_A_min_count)) if cfg.path_A_struct_enabled else np.zeros(n, dtype=bool)
    entry_B_S = np.zeros(n, dtype=bool)
    if cfg.path_B_oversold_enabled:
        m = (k15 > (100 - cfg.path_B_k15_max)) & (k1h > (100 - cfg.path_B_k1h_max))
        if cfg.path_B_require_wt_bull_15m:
            m &= bear_wt_15
        if cfg.path_B_require_reclaim_pivot_lo:
            m &= reclaim_ok_short
        entry_B_S = m
    entry_C_S = (bear_k_1h & (k1h > (100 - cfg.path_C_k1h_cross_below))) if cfg.path_C_k1h_cross_enabled else np.zeros(n, dtype=bool)
    entry_D_S = (bear_wt_D & (rsi_D < (100 - cfg.path_D_rsi_D_min))) if cfg.path_D_wtD_cross_enabled else np.zeros(n, dtype=bool)
    entry_E_S = (bear_sma200 & (rsi_D < (100 - cfg.path_E_rsi_D_min))) if cfg.path_E_sma200_reclaim_enabled else np.zeros(n, dtype=bool)
    entry_F_S = bear_wt_W if cfg.path_F_weekly_flip_enabled else np.zeros(n, dtype=bool)
    if cfg.path_G_momentum_continuation_enabled:
        k1h_dn = _bear_cross(k1h, np.full(n, 100 - cfg.path_G_k1h_cross_min))
        entry_G_S = k1h_dn & (wt1_1h < wt2_1h)
    else:
        entry_G_S = np.zeros(n, dtype=bool)

    # HTF trend filter (B/C/D/G long require D-bull; shorts require D-bear)
    if cfg.htf_trend_filter_enabled:
        bull_mask = np.ones(n, dtype=bool)
        bear_mask = np.ones(n, dtype=bool)
        if cfg.htf_require_wt_D_bull:
            bull_mask &= (wt1_D > wt2_D)
            bear_mask &= (wt1_D < wt2_D)
        if cfg.htf_require_rsi_D_min > 0:
            bull_mask &= (rsi_D >= cfg.htf_require_rsi_D_min)
            bear_mask &= (rsi_D <= (100 - cfg.htf_require_rsi_D_min))
        entry_B_L &= bull_mask; entry_C_L &= bull_mask; entry_D_L &= bull_mask; entry_G_L &= bull_mask
        entry_B_S &= bear_mask; entry_C_S &= bear_mask; entry_D_S &= bear_mask; entry_G_S &= bear_mask

    # ── APPLY LT-direction gate per side ───────────────────────────────────
    entry_A_L &= lt_up_long; entry_B_L &= lt_up_long; entry_C_L &= lt_up_long
    entry_D_L &= lt_up_long; entry_E_L &= lt_up_long; entry_F_L &= lt_up_long; entry_G_L &= lt_up_long

    entry_A_S &= lt_dn_short; entry_B_S &= lt_dn_short; entry_C_S &= lt_dn_short
    entry_D_S &= lt_dn_short; entry_E_S &= lt_dn_short; entry_F_S &= lt_dn_short; entry_G_S &= lt_dn_short

    # ── SIDE gate (LONG_ONLY / SHORT_ONLY / BOTH) ──────────────────────────
    if cfg.side == "LONG_ONLY":
        entry_A_S = np.zeros(n, dtype=bool); entry_B_S = np.zeros(n, dtype=bool)
        entry_C_S = np.zeros(n, dtype=bool); entry_D_S = np.zeros(n, dtype=bool)
        entry_E_S = np.zeros(n, dtype=bool); entry_F_S = np.zeros(n, dtype=bool); entry_G_S = np.zeros(n, dtype=bool)
    elif cfg.side == "SHORT_ONLY":
        entry_A_L = np.zeros(n, dtype=bool); entry_B_L = np.zeros(n, dtype=bool)
        entry_C_L = np.zeros(n, dtype=bool); entry_D_L = np.zeros(n, dtype=bool)
        entry_E_L = np.zeros(n, dtype=bool); entry_F_L = np.zeros(n, dtype=bool); entry_G_L = np.zeros(n, dtype=bool)

    # ── EXIT masks (LONG side fires bearish; SHORT side fires bullish) ─────
    exit_X1_L = ((k15 > cfg.exit_X1_k15_min) & bear_wt_15) if cfg.exit_X1_topcatch_enabled else np.zeros(n, dtype=bool)
    exit_X4_L = bear_wt_D if cfg.exit_X4_daily_bear_wt_enabled else np.zeros(n, dtype=bool)
    exit_X1_S = ((k15 < (100 - cfg.exit_X1_k15_min)) & bull_wt_15) if cfg.exit_X1_topcatch_enabled else np.zeros(n, dtype=bool)
    exit_X4_S = bull_wt_D if cfg.exit_X4_daily_bear_wt_enabled else np.zeros(n, dtype=bool)

    if cfg.exit_X5_structural_flip_enabled:
        bear_flip_count = np.zeros(n, dtype=np.int8)
        bull_flip_count = np.zeros(n, dtype=np.int8)
        for s in ("price", "wt1", "wt2", "stoch_k", "dc_basis"):
            d = structure.get((s, cfg.path_A_trigger_tf))
            if d is None:
                continue
            ev = d["pivot_event"]
            bear_flip_count += _recent_events_count((ev == STRUCT_LH) | (ev == STRUCT_LL), cfg.exit_X5_window_bars)
            bull_flip_count += _recent_events_count((ev == STRUCT_HH) | (ev == STRUCT_HL), cfg.exit_X5_window_bars)
        exit_X5_L = bear_flip_count >= cfg.exit_X5_min_count
        exit_X5_S = bull_flip_count >= cfg.exit_X5_min_count
    else:
        exit_X5_L = np.zeros(n, dtype=bool); exit_X5_S = np.zeros(n, dtype=bool)

    # ── Simulation ─────────────────────────────────────────────────────────
    capital = cfg.initial_capital
    pos_qty = 0.0                       # signed quantity (positive=long, negative=short)
    pos_side = None                     # "L" or "S"
    avg_entry_price = 0.0
    entry_bar = -1
    high_in_trade = 0.0                 # for long trailing
    low_in_trade = 0.0                  # for short trailing
    pyramid_count = 0
    capital_in_trade = 0.0
    cooldown_until = 0
    trade_returns: List[float] = []
    entry_path_counts: Dict[str, int] = {}
    exit_path_counts: Dict[str, int] = {}
    per_sym_long_returns: List[float] = []
    per_sym_short_returns: List[float] = []

    for i in range(50, n):
        px = float(close[i])
        if not np.isfinite(px) or px <= 0:
            continue
        if pos_qty == 0.0:
            if i < cooldown_until:
                continue
            entry_path = None
            new_side = None
            # Long paths
            if entry_A_L[i]: entry_path, new_side = "A_L", "L"
            elif entry_B_L[i]: entry_path, new_side = "B_L", "L"
            elif entry_C_L[i]: entry_path, new_side = "C_L", "L"
            elif entry_D_L[i]: entry_path, new_side = "D_L", "L"
            elif entry_E_L[i]: entry_path, new_side = "E_L", "L"
            elif entry_F_L[i]: entry_path, new_side = "F_L", "L"
            elif entry_G_L[i]: entry_path, new_side = "G_L", "L"
            # Short paths
            elif entry_A_S[i]: entry_path, new_side = "A_S", "S"
            elif entry_B_S[i]: entry_path, new_side = "B_S", "S"
            elif entry_C_S[i]: entry_path, new_side = "C_S", "S"
            elif entry_D_S[i]: entry_path, new_side = "D_S", "S"
            elif entry_E_S[i]: entry_path, new_side = "E_S", "S"
            elif entry_F_S[i]: entry_path, new_side = "F_S", "S"
            elif entry_G_S[i]: entry_path, new_side = "G_S", "S"
            if entry_path is not None:
                capital_in_trade = capital * cfg.full_entry_fraction
                pos_qty = capital_in_trade / px if new_side == "L" else -(capital_in_trade / px)
                pos_side = new_side
                avg_entry_price = px
                entry_bar = i
                high_in_trade = px
                low_in_trade = px
                pyramid_count = 0
                entry_path_counts[entry_path] = entry_path_counts.get(entry_path, 0) + 1
        else:
            # Update trailing peaks
            if px > high_in_trade: high_in_trade = px
            if px < low_in_trade: low_in_trade = px
            # Pyramid (each side has its own structure)
            if pos_side == "L":
                if (pyramid_count < cfg.max_pyramid_levels and new_pyr_HH[i]):
                    gain = (px - avg_entry_price) / avg_entry_price * 100
                    if gain >= cfg.pyramid_min_gain_since_last_pct:
                        add_cap = capital * cfg.pyramid_add_fraction * (0.7 ** pyramid_count)
                        add_qty = add_cap / px
                        new_qty = pos_qty + add_qty
                        avg_entry_price = (avg_entry_price * pos_qty + px * add_qty) / new_qty
                        pos_qty = new_qty
                        capital_in_trade += add_cap
                        pyramid_count += 1
            else:  # SHORT
                if (pyramid_count < cfg.max_pyramid_levels and new_pyr_LL[i]):
                    gain = (avg_entry_price - px) / avg_entry_price * 100
                    if gain >= cfg.pyramid_min_gain_since_last_pct:
                        add_cap = capital * cfg.pyramid_add_fraction * (0.7 ** pyramid_count)
                        add_qty = add_cap / px
                        new_qty_abs = abs(pos_qty) + add_qty
                        avg_entry_price = (avg_entry_price * abs(pos_qty) + px * add_qty) / new_qty_abs
                        pos_qty = -new_qty_abs
                        capital_in_trade += add_cap
                        pyramid_count += 1

            # Exits
            exit_path = None
            if pos_side == "L":
                cur_gain = (px - avg_entry_price) / avg_entry_price * 100
                # X2 trail (only when in profit ≥0.5% or pyramided)
                if cfg.exit_X2_trailing_pct > 0:
                    trail_px = high_in_trade * (1 - cfg.exit_X2_trailing_pct / 100)
                    if px < trail_px and (px > avg_entry_price * 1.005 or pyramid_count > 0):
                        exit_path = "X2"
                if exit_path is None and exit_X1_L[i]: exit_path = "X1"
                if exit_path is None and exit_X4_L[i]: exit_path = "X4"
                if exit_path is None and exit_X5_L[i]: exit_path = "X5"
            else:  # SHORT
                cur_gain = (avg_entry_price - px) / avg_entry_price * 100
                if cfg.exit_X2_trailing_pct > 0:
                    trail_px = low_in_trade * (1 + cfg.exit_X2_trailing_pct / 100)
                    if px > trail_px and (px < avg_entry_price * 0.995 or pyramid_count > 0):
                        exit_path = "X2"
                if exit_path is None and exit_X1_S[i]: exit_path = "X1"
                if exit_path is None and exit_X4_S[i]: exit_path = "X4"
                if exit_path is None and exit_X5_S[i]: exit_path = "X5"

            if exit_path is not None:
                if pos_side == "L":
                    ret_pct = (px - avg_entry_price) / avg_entry_price * 100 - cfg.round_trip_cost_pct
                else:
                    ret_pct = (avg_entry_price - px) / avg_entry_price * 100 - cfg.round_trip_cost_pct
                pnl_dollars = capital_in_trade * (ret_pct / 100)
                capital += pnl_dollars
                trade_returns.append(ret_pct)
                if pos_side == "L":
                    per_sym_long_returns.append(ret_pct)
                else:
                    per_sym_short_returns.append(ret_pct)
                key = f"{exit_path}_{pos_side}"
                exit_path_counts[key] = exit_path_counts.get(key, 0) + 1
                pos_qty = 0.0; pos_side = None
                avg_entry_price = 0.0; entry_bar = -1
                capital_in_trade = 0.0
                cooldown_until = i + cfg.cooldown_bars_after_exit

    # MtM final open (NO LIES MANDATE rule 2)
    if pos_qty != 0.0:
        mark = float(close[-1])
        if pos_side == "L":
            ret_pct = (mark - avg_entry_price) / avg_entry_price * 100 - cfg.round_trip_cost_pct
        else:
            ret_pct = (avg_entry_price - mark) / avg_entry_price * 100 - cfg.round_trip_cost_pct
        capital += capital_in_trade * (ret_pct / 100)
        trade_returns.append(ret_pct)
        if pos_side == "L":
            per_sym_long_returns.append(ret_pct)
        else:
            per_sym_short_returns.append(ret_pct)
        exit_path_counts[f"MTM_FINAL_BAR_NOLIES_{pos_side}"] = exit_path_counts.get(f"MTM_FINAL_BAR_NOLIES_{pos_side}", 0) + 1

    bh_mult = float(close[-1]) / float(close[0]) if close[0] > 0 else 1.0
    compound_mult = capital / cfg.initial_capital
    yrs = float((ts[-1] - ts[0]) / (365.25 * 24 * 3600))

    arr = np.array(trade_returns, dtype=np.float64) if trade_returns else np.array([], dtype=np.float64)
    if len(arr) > 1 and arr.std() > 0:
        sym_sharpe = float(arr.mean() / arr.std())
    else:
        sym_sharpe = 0.0
    if len(arr) > 0:
        eq = np.cumprod(1 + arr / 100)
        peak = np.maximum.accumulate(eq)
        dd = (eq - peak) / peak
        max_dd = float(-dd.min() * 100)
    else:
        max_dd = 0.0

    return {
        "sym": symbol,
        "n_bars": n,
        "years": yrs,
        "bh_mult": bh_mult,
        "compound_mult": compound_mult,
        "ratio_vs_bh": compound_mult / bh_mult if bh_mult > 0 else float('inf'),
        "trades": len(trade_returns),
        "long_trades": len(per_sym_long_returns),
        "short_trades": len(per_sym_short_returns),
        "wr_pct": float(sum(1 for r in trade_returns if r > 0) / max(len(trade_returns), 1) * 100),
        "avg_gain_pct": float(arr.mean()) if len(arr) > 0 else 0.0,
        "sym_sharpe": sym_sharpe,
        "max_dd_pct": max_dd,
        "entry_paths": entry_path_counts,
        "exit_paths": exit_path_counts,
        "trade_returns": trade_returns,
        "long_returns": per_sym_long_returns,
        "short_returns": per_sym_short_returns,
    }


# ════════════════════════════════════════════════════════════════════════════════
# Universe / runner
# ════════════════════════════════════════════════════════════════════════════════

import platform
IS_SERVER = platform.system() == "Linux"
BASE = Path("/home/niels/binance-sandbox") if IS_SERVER else Path("/Users/niels/Documents/binance")


def get_universe(universe_path: Optional[str] = None) -> List[str]:
    if universe_path:
        with open(universe_path) as f:
            data = json.load(f)
        return data["symbols"]
    p = BASE / "data" / "struct_v4_universe.json"
    if p.exists():
        with open(p) as f:
            data = json.load(f)
        return data["symbols"]
    raise FileNotFoundError(f"No universe file at {p}")


def mode_to_cfg(mode_name: str) -> LTCfg:
    base = LTCfg(mode_name=mode_name)
    if mode_name == "CTRL":
        base.side = "LONG_ONLY"; base.lt_filter_enabled = False
    elif mode_name == "A_long_filtered":
        base.side = "LONG_ONLY"; base.lt_filter_enabled = True
    elif mode_name == "B_dual_filtered":
        base.side = "BOTH"; base.lt_filter_enabled = True
    elif mode_name == "C_short_only":
        base.side = "SHORT_ONLY"; base.lt_filter_enabled = True
    else:
        raise ValueError(f"Unknown mode {mode_name}")
    return base


def run_universe(symbols: List[str], cfg: LTCfg, start_date: str, label: str, *,
                 npz_mode: str = "tradier") -> Dict[str, Any]:
    start_ts = int(datetime.datetime.strptime(start_date, "%Y-%m-%d")
                   .replace(tzinfo=datetime.timezone.utc).timestamp())
    rows = []
    all_returns = []
    all_long_returns = []
    all_short_returns = []
    all_exit_paths: Dict[str, int] = {}
    all_entry_paths: Dict[str, int] = {}
    t0 = time.time()
    n_attempted = 0
    n_skipped = 0
    for sym in symbols:
        n_attempted += 1
        try:
            r = simulate(sym, npz_mode, cfg, start_ts=start_ts)
        except Exception as e:
            print(f"  ERR {sym}: {e}", flush=True)
            n_skipped += 1
            continue
        if r.get("skip"):
            n_skipped += 1
            continue
        all_returns.extend(r["trade_returns"])
        all_long_returns.extend(r["long_returns"])
        all_short_returns.extend(r["short_returns"])
        for k, v in r["exit_paths"].items():
            all_exit_paths[k] = all_exit_paths.get(k, 0) + v
        for k, v in r["entry_paths"].items():
            all_entry_paths[k] = all_entry_paths.get(k, 0) + v
        rows.append({
            "sym": r["sym"],
            "years": r["years"],
            "trades": r["trades"],
            "long_trades": r["long_trades"],
            "short_trades": r["short_trades"],
            "wr_pct": r["wr_pct"],
            "avg_gain_trade": r["avg_gain_pct"],
            "bh_mult": r["bh_mult"],
            "compound_mult": r["compound_mult"],
            "ratio_vs_bh": r["ratio_vs_bh"],
            "sym_sharpe": r["sym_sharpe"],
            "max_dd_pct": r["max_dd_pct"],
        })
        if n_attempted % 10 == 0 or n_attempted == len(symbols):
            print(f"  [{n_attempted:>3}/{len(symbols)}] {sym:<8} trades={r['trades']:>4} L={r['long_trades']:>3} S={r['short_trades']:>3} sharpe={r['sym_sharpe']:+.3f} dd={r['max_dd_pct']:.1f}% {label}", flush=True)

    elapsed = time.time() - t0
    n_syms = len(rows)
    total_trades = sum(r["trades"] for r in rows)

    # Pool Sharpe
    if len(all_returns) > 1:
        arr = np.array(all_returns, dtype=np.float64)
        pool_sharpe = float(arr.mean() / arr.std()) if arr.std() > 0 else 0.0
        acc_gain_pct = float(arr.sum())
        avg_gain_trade = float(arr.mean())
    else:
        pool_sharpe, acc_gain_pct, avg_gain_trade = 0.0, 0.0, 0.0

    # Pool DD
    if len(all_returns) > 0:
        eq = np.cumprod(1 + np.array(all_returns) / 100)
        peak = np.maximum.accumulate(eq)
        dd = (eq - peak) / peak
        pool_max_dd = float(-dd.min() * 100)
    else:
        pool_max_dd = 0.0

    # Sym Sharpe (≥30 trades; cap ±5)
    eligible_sym_sharpes = []
    for r in rows:
        if r["trades"] >= 30:
            s = max(-5.0, min(5.0, r["sym_sharpe"]))
            eligible_sym_sharpes.append(s)
    sym_sharpe_avg = float(np.mean(eligible_sym_sharpes)) if eligible_sym_sharpes else 0.0

    yrs = rows[0]["years"] if rows else 1.0
    # Aggregate compound geo
    if rows:
        log_compounds = [np.log(max(0.01, r["compound_mult"])) for r in rows]
        geo_mean_compound = float(np.exp(np.mean(log_compounds)))
    else:
        geo_mean_compound = 1.0
    pct_compound_per_sym = (geo_mean_compound - 1.0) * 100
    gain_per_yr = pct_compound_per_sym / yrs if yrs > 0 else 0.0
    gain_sym_yr = gain_per_yr / max(n_syms, 1)

    avg_dd = float(np.mean([r["max_dd_pct"] for r in rows])) if rows else 0.0

    wr_pool = float(sum(1 for r in all_returns if r > 0) / max(len(all_returns), 1) * 100)

    # Long vs short pool sharpes
    if len(all_long_returns) > 1 and np.array(all_long_returns).std() > 0:
        pool_sharpe_long = float(np.mean(all_long_returns) / np.std(all_long_returns))
    else:
        pool_sharpe_long = 0.0
    if len(all_short_returns) > 1 and np.array(all_short_returns).std() > 0:
        pool_sharpe_short = float(np.mean(all_short_returns) / np.std(all_short_returns))
    else:
        pool_sharpe_short = 0.0

    summary = {
        "label": label,
        "mode_name": cfg.mode_name,
        "side": cfg.side,
        "lt_filter_enabled": cfg.lt_filter_enabled,
        "start_date": start_date,
        "n_syms": n_syms,
        "n_attempted": n_attempted,
        "n_skipped": n_skipped,
        "years": yrs,
        "trades": total_trades,
        "long_trades": len(all_long_returns),
        "short_trades": len(all_short_returns),
        "pool_sharpe": pool_sharpe,
        "pool_sharpe_long": pool_sharpe_long,
        "pool_sharpe_short": pool_sharpe_short,
        "sym_sharpe": sym_sharpe_avg,
        "avg_gain_trade": avg_gain_trade,
        "gain_per_yr": gain_per_yr,
        "gain_sym_yr": gain_sym_yr,
        "max_dd_pct": pool_max_dd,
        "avg_dd_pct": avg_dd,
        "compound_geo": geo_mean_compound,
        "wr_pool_pct": wr_pool,
        "exit_paths_total": all_exit_paths,
        "entry_paths_total": all_entry_paths,
        "elapsed_s": elapsed,
    }

    # Sample-floor tag (stocks=100 syms × >1yr × ≥30 tr/sym)
    avg_tr_per_sym = total_trades / max(n_syms, 1)
    if n_syms >= 100 and yrs >= 1.0 and avg_tr_per_sym >= 30:
        summary["sample_tag"] = "[PUBLISHABLE]"
    else:
        summary["sample_tag"] = f"[DIAGNOSTIC ONLY · n_syms={n_syms} · years={yrs:.2f} · tr/sym={avg_tr_per_sym:.1f}]"

    # Write CSVs
    out_dir = BASE / "data" / "sweep_results"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts_label = int(time.time())
    per_sym_csv = out_dir / f"lt_filter_{label}_{ts_label}_per_sym.csv"
    if rows:
        with open(per_sym_csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
    summary_json = out_dir / f"lt_filter_{label}_{ts_label}_summary.json"
    with open(summary_json, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    summary["_per_sym_csv"] = str(per_sym_csv)
    summary["_summary_json"] = str(summary_json)
    summary["_rows"] = rows
    return summary


def print_summary(s):
    print("\n" + "═" * 90)
    print(f"  {s['label']} (mode={s['mode_name']} side={s['side']} LT={s['lt_filter_enabled']}) | start={s['start_date']} | {s['n_syms']} syms | {s['elapsed_s']:.0f}s")
    print("═" * 90)
    print(f"  Sample tag        : {s['sample_tag']}")
    print(f"  pool_sharpe       = {s['pool_sharpe']:+.4f}")
    print(f"  pool_sharpe_long  = {s['pool_sharpe_long']:+.4f}  ({s['long_trades']} long trades)")
    print(f"  pool_sharpe_short = {s['pool_sharpe_short']:+.4f}  ({s['short_trades']} short trades)")
    print(f"  sym_sharpe        = {s['sym_sharpe']:+.4f}")
    print(f"  avg_gain_trade    = {s['avg_gain_trade']:+.4f}%")
    print(f"  gain_per_yr       = {s['gain_per_yr']:+.2f}%")
    print(f"  gain_sym_yr       = {s['gain_sym_yr']:+.4f}%")
    print(f"  wr_pool           = {s['wr_pool_pct']:.2f}%")
    print(f"  pool_max_dd       = {s['max_dd_pct']:.2f}%")
    print(f"  avg_dd            = {s['avg_dd_pct']:.2f}%")
    print(f"  trades            = {s['trades']} ({s['long_trades']}L / {s['short_trades']}S)")
    print(f"  compound_geo      = {s['compound_geo']:.4f}×")
    print()
    print(f"  CANONICAL LINE:")
    print(f"  pool_sharpe={s['pool_sharpe']:.4f} | sym_sharpe={s['sym_sharpe']:.4f} | "
          f"avg_gain_trade={s['avg_gain_trade']:.2f}%/trade | "
          f"gain_per_yr={s['gain_per_yr']:.1f}%/yr | "
          f"gain_sym_yr={s['gain_sym_yr']:.4f}%/sym/yr | "
          f"trades={s['trades']} | dd={s['max_dd_pct']:.1f}% | "
          f"n_syms={s['n_syms']} | years={s['years']:.2f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", required=True, choices=["CTRL", "A_long_filtered", "B_dual_filtered", "C_short_only"])
    ap.add_argument("--start", default="2024-04-01")
    ap.add_argument("--universe", default="")
    ap.add_argument("--label", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--npz-mode", default="tradier")
    args = ap.parse_args()

    syms = get_universe(args.universe or None)
    if args.limit:
        syms = syms[:args.limit]
    cfg = mode_to_cfg(args.mode)
    label = args.label or f"{args.mode}_{args.start}"
    print(f"[{args.mode}] universe={len(syms)} start={args.start} side={cfg.side} LT_filter={cfg.lt_filter_enabled}", flush=True)
    s = run_universe(syms, cfg, args.start, label, npz_mode=args.npz_mode)
    print_summary(s)


if __name__ == "__main__":
    main()
