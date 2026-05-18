"""v8_struct_v4_aggressive.py — multi-path aggressive long-only catcher.

USER MANDATE 2026-05-17 (HANDS_OFF): catch every bottom + sell every top +
reenter continuing rallies, until each of NVDA/GOOGL/MSFT/SNDK clears 4×B&H.
LONG ONLY. Compound capital across trades.

Architecture:
- Multiple parallel ENTRY paths (named A/B/C/...); fire on FIRST armed path
- Multiple parallel EXIT paths (named X1/X2/...); close on FIRST armed path
- PYRAMID: while in long + new HH on D + gain >= MIN_PYRAMID_GAIN, add to position
- Compound sizing: every full close re-bases capital to (capital × (1+return))
- Oracle diagnostic: cross-reference fired entries vs oracle bottoms

Reads ONLY NPZ + vec_paths.structure_hh_hl. No live code, no precompute change.
Sub-floor by design (per-symbol calibration exercise). NOT for live promotion.
"""
from __future__ import annotations

import argparse, datetime, json, sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from v8_vec_structure_sweep import load_npz
from vec_paths.structure_hh_hl import (
    compute_all_structure, _streak_at_value,
    STRUCT_HH, STRUCT_HL, STRUCT_LH, STRUCT_LL,
)

BARS_PER_DAY = 78  # 5m base on stocks


# ════════════════════════════════════════════════════════════════════════════════
# Oracle finder — what the strategy SHOULD have caught
# ════════════════════════════════════════════════════════════════════════════════

def find_oracle_bottoms(close: np.ndarray, ts: np.ndarray, *,
                        fwd_days: int = 20, gain_threshold: float = 0.15) -> List[Dict]:
    n = len(close)
    fwd_bars = int(fwd_days * BARS_PER_DAY)
    out = []
    i = 50
    while i < n - fwd_bars:
        window = close[max(0, i - 10):i + 11]
        if close[i] == window.min() and close[i] > 0:
            fwd_max = close[i:i + fwd_bars].max()
            if (fwd_max / close[i]) - 1 >= gain_threshold:
                peak_off = int(np.argmax(close[i:i + fwd_bars]))
                out.append({
                    "idx_bottom": i, "idx_peak": i + peak_off,
                    "price_bottom": float(close[i]), "price_peak": float(close[i + peak_off]),
                    "gain_pct": (fwd_max / close[i] - 1) * 100,
                    "days_to_peak": peak_off / BARS_PER_DAY,
                })
                i += max(peak_off, 5)
                continue
        i += 1
    return out


# ════════════════════════════════════════════════════════════════════════════════
# Engine config — every knob, every path toggleable
# ════════════════════════════════════════════════════════════════════════════════

@dataclass
class AggressiveCfg:
    # Capital + sizing
    initial_capital: float = 10_000.0
    full_entry_fraction: float = 1.0          # use 100% of capital each entry
    pyramid_add_fraction: float = 0.5         # each pyramid adds 50% of CURRENT capital
    max_pyramid_levels: int = 3
    round_trip_cost_pct: float = 0.0          # tradier = no commission per memory
    # Cooldown
    cooldown_bars_after_exit: int = 5
    # ─── ENTRY paths (each independently toggleable) ──────────────────────
    # A: HTF struct breakout + retest + multi-series HL alignment (v3 logic)
    path_A_struct_enabled: bool = True
    path_A_breakout_tf: str = "D"
    path_A_retest_tf: str = "1h"
    path_A_trigger_tf: str = "15m"
    path_A_min_count: int = 4
    path_A_atr_band: float = 1.0
    path_A_regime_persist_bars: int = 500
    # B: Oversold reversal — K_15m oversold + WT bull cross 15m + reclaim swing-low
    path_B_oversold_enabled: bool = True
    path_B_k15_max: float = 30.0
    path_B_k1h_max: float = 40.0
    path_B_require_wt_bull_15m: bool = True
    path_B_require_reclaim_pivot_lo: bool = True
    # C: 1h K cross from below — K_1h crosses D_1h from K<30
    path_C_k1h_cross_enabled: bool = True
    path_C_k1h_cross_below: float = 30.0
    # D: Daily WT bullish cross — wt1_D crosses above wt2_D, RSI_D > 40
    path_D_wtD_cross_enabled: bool = True
    path_D_rsi_D_min: float = 40.0
    # E: 200SMA reclaim — close > sma_200_D + RSI_D > 50
    path_E_sma200_reclaim_enabled: bool = True
    path_E_rsi_D_min: float = 50.0
    # F: Weekly bullish flip — wt1_W > wt2_W after being below
    path_F_weekly_flip_enabled: bool = True
    # G: Momentum continuation — K_1h crosses up 50, wt1_1h > wt2_1h, in HTF bull
    path_G_momentum_continuation_enabled: bool = False
    path_G_k1h_cross_min: float = 50.0
    # Universal HTF trend filter — applied to paths B/C/D when enabled
    htf_trend_filter_enabled: bool = False
    htf_require_wt_D_bull: bool = True
    htf_require_rsi_D_min: float = 45.0
    # ─── EXIT paths ───────────────────────────────────────────────────────
    # X1: K_15m overbought + bearish WT cross on 15m (top-catch)
    exit_X1_topcatch_enabled: bool = True
    exit_X1_k15_min: float = 80.0
    # X2: trailing stop from highest close in-trade
    exit_X2_trailing_pct: float = 5.0
    exit_X2_require_profit: bool = True       # True = only trail when in profit; False = trail from peak always
    # X3: hard stop ATR-based (legacy — prefer X7 technical stop)
    exit_X3_hardstop_atr_mult: float = 2.0
    # X7: technical stop — frozen dc_low_<tf> at entry + absolute floor
    exit_X7_tech_stop_enabled: bool = False
    exit_X7_freeze_dc_tf: str = "4h"     # freeze dc_low at this TF at entry; "" or unknown TF disables DC check
    exit_X7_abs_floor_pct: float = -8.0  # absolute max loss % (emergency cap)
    exit_X7_freeze_bb_tf: str = ""       # if set (e.g. "1h"), also freeze bb_lower_<tf> at entry; "" disables
    # X4: bearish D WT cross (HTF protection)
    exit_X4_daily_bear_wt_enabled: bool = True
    # X5: structural flip (v3 logic, simultaneous-flip on trigger TF)
    exit_X5_structural_flip_enabled: bool = True
    exit_X5_min_count: int = 3
    exit_X5_window_bars: int = 3
    # X6: time stop (force close after N bars in trade if gain < threshold)
    exit_X6_time_stop_bars: int = 0           # 0 = disabled
    exit_X6_min_gain_pct: float = 5.0
    # ─── HOLD / CHURN REDUCTION ───────────────────────────────────────────
    min_hold_bars: int = 0                    # min bars before ANY exit (except X7 floor)
    exit_X1_require_k1h_min: float = 0.0     # 0=off; >0 = require K_1h above this for X1
    exit_X1_require_wt_bear_1h: bool = False  # require bear WT cross on 1h (not just 15m) for X1
    exit_X5_min_hold_bars: int = 0            # X5 only fires after this many bars in trade
    # PYRAMID
    pyramid_enabled: bool = True
    pyramid_require_new_D_HH: bool = True
    pyramid_min_gain_since_last_pct: float = 3.0
    pyramid_tf: str = "D"             # which TF's new HH triggers pyramid: D, 4h, 1h, 15m
    pyramid_also_on_k1h_oversold: bool = False  # extra pyramid when K_1h<25 + WT_1h bull cross
    pyramid_on_price_breakout: bool = False    # pyramid when price > highest close since entry
    pyramid_price_breakout_min_gain: float = 1.0  # min % gain for price breakout pyramid
    max_pyramid_capital_mult: float = 3.0     # cap total capital_in_trade at this × initial allocation
    # ─── ANTI-CHURN ──────────────────────────────────────────────────────
    require_above_sma50_D: bool = False       # require close > SMA_50_D for ANY entry
    x4_exit_extended_cooldown: int = 0        # after X4 exit, cooldown = max(normal, this); 0 = off
    # ─── MULTI-TF DC STOP (from sweep: dc_low on 5m/1h best) ─────────
    exit_X7_dc_tfs: str = "5m,1h"            # comma-sep TFs for DC frozen stop; empty = use single freeze_dc_tf


# ════════════════════════════════════════════════════════════════════════════════
# Helpers — fetch indicator arrays from NPZ with sane fallbacks
# ════════════════════════════════════════════════════════════════════════════════

def _f(npz, key, n, default=0.0):
    arr = npz.get(key)
    if arr is None:
        return np.full(n, default, dtype=np.float64)
    a = np.asarray(arr, dtype=np.float64)
    if a.shape[0] != n:
        return np.full(n, default, dtype=np.float64)
    return a


def _bull_cross(fast: np.ndarray, slow: np.ndarray) -> np.ndarray:
    """True at bars where fast crosses ABOVE slow."""
    prev_below = np.concatenate(([False], fast[:-1] <= slow[:-1]))
    now_above = fast > slow
    return prev_below & now_above


def _bear_cross(fast: np.ndarray, slow: np.ndarray) -> np.ndarray:
    prev_above = np.concatenate(([False], fast[:-1] >= slow[:-1]))
    now_below = fast < slow
    return prev_above & now_below


def _rolling_max(arr: np.ndarray, w: int) -> np.ndarray:
    """Backward-looking rolling max of width w (uses prefix-max trick approx)."""
    n = len(arr)
    out = np.zeros(n, dtype=arr.dtype)
    if w <= 0:
        return out
    for i in range(n):
        out[i] = arr[max(0, i - w + 1):i + 1].max() if i > 0 else arr[0]
    return out


# ════════════════════════════════════════════════════════════════════════════════
# Per-symbol simulator
# ════════════════════════════════════════════════════════════════════════════════

def simulate_aggressive(symbol: str, mode: str, cfg: AggressiveCfg,
                        *, start_ts: Optional[int] = None,
                        return_events: bool = False) -> Dict[str, Any]:
    try:
        npz, ts = load_npz(symbol, mode, start_ts=start_ts)
    except Exception:
        return {"sym": symbol, "trades": 0, "compound_mult": 1.0, "skip": True}
    close = np.asarray(npz["close"], dtype=np.float64)
    n = len(close)
    if n < 200:
        return {"sym": symbol, "trades": 0, "compound_mult": 1.0, "skip": True}

    # ── Pre-compute all the gates needed across paths ───────────────────────
    structure = compute_all_structure(npz, mode)
    k15 = _f(npz, "stoch_k_15m", n, 50.0)
    d15 = _f(npz, "stoch_d_15m", n, 50.0)
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
    sma200_D = _f(npz, "sma_200_D", n)
    sma50_D = _f(npz, "sma_50_D", n)
    atr_15 = _f(npz, "atr_15m", n, 0.0)
    # X7 technical stop indicators — multi-TF DC stops
    dc_low_4h = _f(npz, f"dc_low_{cfg.exit_X7_freeze_dc_tf}", n, 0.0)
    bb_lower_arr = _f(npz, f"bb_lower_{cfg.exit_X7_freeze_bb_tf}", n, 0.0) if cfg.exit_X7_freeze_bb_tf else np.zeros(n)
    dc_stop_tfs = {}
    if cfg.exit_X7_dc_tfs:
        for tf in cfg.exit_X7_dc_tfs.split(","):
            tf = tf.strip()
            if tf:
                dc_stop_tfs[tf] = _f(npz, f"dc_low_{tf}", n, 0.0)

    # Bull crosses
    bull_wt_15 = _bull_cross(wt1_15, wt2_15)
    bull_wt_D = _bull_cross(wt1_D, wt2_D)
    bear_wt_D = _bear_cross(wt1_D, wt2_D)
    bull_k_1h = _bull_cross(k1h, d1h)
    bull_wt_W = _bull_cross(wt1_W, wt2_W)
    bull_sma200 = _bull_cross(close, sma200_D)

    # Structure-based gates (path A)
    bk_price = structure.get((cfg.path_A_breakout_tf and "price", cfg.path_A_breakout_tf)) if cfg.path_A_struct_enabled else None
    rt_price = structure.get(("price", cfg.path_A_retest_tf)) if cfg.path_A_struct_enabled else None
    bk_is_hh = np.zeros(n, dtype=bool)
    rt_retest_lo_ok = np.zeros(n, dtype=bool)
    if bk_price is not None and rt_price is not None:
        bk_streak = _streak_at_value(bk_price["struct"], int(STRUCT_HH))
        bk_is_hh = bk_streak >= cfg.path_A_regime_persist_bars
        atr_rt = _f(npz, f"atr_{cfg.path_A_retest_tf}", n, 0.0)
        with np.errstate(invalid="ignore", divide="ignore"):
            rt_retest_lo_ok = np.where(atr_rt > 0,
                np.abs(close - rt_price["last_lo"]) / np.maximum(atr_rt, 1e-9) < cfg.path_A_atr_band,
                False).astype(bool)

    # Multi-series HL count on trigger TF for path A
    hl_count = np.zeros(n, dtype=np.int8)
    series_used = 0
    for s in ("price", "wt1", "wt2", "stoch_k", "dc_basis"):
        d = structure.get((s, cfg.path_A_trigger_tf))
        if d is None:
            continue
        hl_count += (d["struct"] == STRUCT_HL).astype(np.int8)
        series_used += 1

    # Last pivot-low on 15m for path B "reclaim swing low"
    tg_price = structure.get(("price", "15m"))
    if tg_price is not None:
        last_pivot_lo = tg_price["last_lo"]
    else:
        last_pivot_lo = np.full(n, np.nan, dtype=np.float64)
    reclaim_ok = np.where(np.isfinite(last_pivot_lo) & (last_pivot_lo > 0),
                          close > last_pivot_lo, False)

    # Pyramid trigger TF
    pyramid_tf_struct = structure.get(("price", cfg.pyramid_tf))
    if pyramid_tf_struct is not None:
        pyr_event = pyramid_tf_struct["pivot_event"]
        new_pyramid_HH = (pyr_event == STRUCT_HH)
    else:
        new_pyramid_HH = np.zeros(n, dtype=bool)
    if cfg.pyramid_also_on_k1h_oversold:
        new_pyramid_HH = new_pyramid_HH | ((k1h < 25.0) & _bull_cross(wt1_1h, wt2_1h))
    # Keep legacy reference for any callers
    new_D_HH = new_pyramid_HH

    # ── ENTRY masks per path ──────────────────────────────────────────────
    entry_A = (bk_is_hh & rt_retest_lo_ok & (hl_count >= cfg.path_A_min_count)) if cfg.path_A_struct_enabled else np.zeros(n, dtype=bool)
    entry_B = np.zeros(n, dtype=bool)
    if cfg.path_B_oversold_enabled:
        m = (k15 < cfg.path_B_k15_max) & (k1h < cfg.path_B_k1h_max)
        if cfg.path_B_require_wt_bull_15m:
            m &= bull_wt_15
        if cfg.path_B_require_reclaim_pivot_lo:
            m &= reclaim_ok
        entry_B = m
    entry_C = (bull_k_1h & (k1h < cfg.path_C_k1h_cross_below)) if cfg.path_C_k1h_cross_enabled else np.zeros(n, dtype=bool)
    entry_D = (bull_wt_D & (rsi_D > cfg.path_D_rsi_D_min)) if cfg.path_D_wtD_cross_enabled else np.zeros(n, dtype=bool)
    entry_E = (bull_sma200 & (rsi_D > cfg.path_E_rsi_D_min)) if cfg.path_E_sma200_reclaim_enabled else np.zeros(n, dtype=bool)
    entry_F = bull_wt_W if cfg.path_F_weekly_flip_enabled else np.zeros(n, dtype=bool)
    # G: momentum continuation entry — only useful in trend
    if cfg.path_G_momentum_continuation_enabled:
        k1h_cross_up = _bull_cross(k1h, np.full(n, cfg.path_G_k1h_cross_min))
        entry_G = k1h_cross_up & (wt1_1h > wt2_1h)
    else:
        entry_G = np.zeros(n, dtype=bool)
    # ── HTF trend filter — masks paths B/C/D when enabled ──────────────────
    if cfg.htf_trend_filter_enabled:
        bull_mask = np.ones(n, dtype=bool)
        if cfg.htf_require_wt_D_bull:
            bull_mask &= (wt1_D > wt2_D)
        if cfg.htf_require_rsi_D_min > 0:
            bull_mask &= (rsi_D >= cfg.htf_require_rsi_D_min)
        entry_B &= bull_mask
        entry_C &= bull_mask
        entry_D &= bull_mask
        entry_G &= bull_mask

    # ── SMA50 regime filter — blocks ALL entries when price < SMA50_D ──
    if cfg.require_above_sma50_D:
        above_sma50 = close > sma50_D
        entry_A &= above_sma50
        entry_B &= above_sma50
        entry_C &= above_sma50
        entry_D &= above_sma50
        entry_E &= above_sma50
        entry_F &= above_sma50
        entry_G &= above_sma50

    # ── EXIT masks per path ───────────────────────────────────────────────
    # X1: top catch (with optional 1h confirmation)
    bear_wt_15 = _bear_cross(wt1_15, wt2_15)
    bear_wt_1h = _bear_cross(wt1_1h, wt2_1h)
    if cfg.exit_X1_topcatch_enabled:
        x1_mask = (k15 > cfg.exit_X1_k15_min) & bear_wt_15
        if cfg.exit_X1_require_k1h_min > 0:
            x1_mask &= (k1h > cfg.exit_X1_require_k1h_min)
        if cfg.exit_X1_require_wt_bear_1h:
            x1_mask &= bear_wt_1h
        exit_X1 = x1_mask
    else:
        exit_X1 = np.zeros(n, dtype=bool)
    # X4
    exit_X4 = bear_wt_D if cfg.exit_X4_daily_bear_wt_enabled else np.zeros(n, dtype=bool)
    # X5: structural flip count on trigger TF
    flip_count = np.zeros(n, dtype=np.int8)
    if cfg.exit_X5_structural_flip_enabled:
        from vec_paths.structure_hh_hl import _recent_events_count
        for s in ("price", "wt1", "wt2", "stoch_k", "dc_basis"):
            d = structure.get((s, cfg.path_A_trigger_tf))
            if d is None:
                continue
            ev = d["pivot_event"]
            bear = (ev == STRUCT_LH) | (ev == STRUCT_LL)
            flip_count += _recent_events_count(bear, cfg.exit_X5_window_bars)
        exit_X5 = flip_count >= cfg.exit_X5_min_count
    else:
        exit_X5 = np.zeros(n, dtype=bool)

    # ── Simulation loop ───────────────────────────────────────────────────
    capital = cfg.initial_capital
    bh_initial_cap = cfg.initial_capital
    pos_qty = 0.0
    avg_entry_price = 0.0
    original_entry_price = 0.0
    initial_capital_in_trade = 0.0
    entry_bar = -1
    highest_close_in_trade = 0.0
    lowest_close_in_trade = 0.0
    pyramid_count = 0
    capital_in_trade = 0.0
    cooldown_until = 0
    frozen_dc_stop = 0.0
    frozen_bb_stop = 0.0
    frozen_dc_stops = {}
    events: List[Dict] = []
    trade_returns: List[float] = []
    trade_worst_dd: List[float] = []  # worst intra-trade DD pct per trade (negative number)
    entry_path_counts: Dict[str, int] = {}
    exit_path_counts: Dict[str, int] = {}

    def _record_event(i, etype, qty, price, reason, value, extra_indicators=None):
        if return_events:
            ind = {
                "k15": round(float(k15[i]), 2),
                "k1h": round(float(k1h[i]), 2),
                "wt1_D": round(float(wt1_D[i]), 4),
                "wt2_D": round(float(wt2_D[i]), 4),
                "wt_D_diff": round(float(wt1_D[i] - wt2_D[i]), 4),
                "rsi_D": round(float(rsi_D[i]), 2),
                "atr15": round(float(atr_15[i]), 4),
            }
            if extra_indicators:
                ind.update(extra_indicators)
            events.append({
                "ts": float(ts[i]), "type": etype, "qty": float(qty),
                "price": float(price), "value": float(value), "reason": reason,
                "indicators": ind,
            })

    for i in range(50, n):
        px = float(close[i])
        if not np.isfinite(px) or px <= 0:
            continue
        if pos_qty == 0.0:
            if i < cooldown_until:
                continue
            # Try entry paths in priority order
            entry_path = None
            if entry_A[i]:
                entry_path = "A"
            elif entry_B[i]:
                entry_path = "B"
            elif entry_C[i]:
                entry_path = "C"
            elif entry_D[i]:
                entry_path = "D"
            elif entry_E[i]:
                entry_path = "E"
            elif entry_F[i]:
                entry_path = "F"
            elif entry_G[i]:
                entry_path = "G"
            if entry_path is not None:
                capital_in_trade = capital * cfg.full_entry_fraction
                initial_capital_in_trade = capital_in_trade
                pos_qty = capital_in_trade / px
                avg_entry_price = px
                original_entry_price = px
                entry_bar = i
                highest_close_in_trade = px
                lowest_close_in_trade = px
                pyramid_count = 0
                frozen_dc_stop = float(dc_low_4h[i]) if cfg.exit_X7_tech_stop_enabled else 0.0
                frozen_bb_stop = float(bb_lower_arr[i]) if (cfg.exit_X7_tech_stop_enabled and cfg.exit_X7_freeze_bb_tf) else 0.0
                frozen_dc_stops = {}
                if cfg.exit_X7_tech_stop_enabled:
                    for tf, arr in dc_stop_tfs.items():
                        v = float(arr[i])
                        if v > 0:
                            frozen_dc_stops[tf] = v
                entry_path_counts[entry_path] = entry_path_counts.get(entry_path, 0) + 1
                _record_event(i, "OPEN", pos_qty, px, f"PATH_{entry_path}", capital_in_trade)
        else:
            # Update trailing high + lowest (for worst intra-trade DD tracking)
            if px > highest_close_in_trade:
                highest_close_in_trade = px
            if px < lowest_close_in_trade or lowest_close_in_trade == 0.0:
                lowest_close_in_trade = px
            # Try PYRAMID first (still in long, new D HH)
            cap_room = initial_capital_in_trade * cfg.max_pyramid_capital_mult - capital_in_trade
            if (cfg.pyramid_enabled
                and pyramid_count < cfg.max_pyramid_levels
                and cap_room > 0
                and new_D_HH[i]
                and (not cfg.pyramid_require_new_D_HH or new_D_HH[i])):
                gain_since_entry = (px - avg_entry_price) / avg_entry_price * 100
                if gain_since_entry >= cfg.pyramid_min_gain_since_last_pct:
                    add_cap = min(capital * cfg.pyramid_add_fraction * (0.7 ** pyramid_count), cap_room)
                    add_qty = add_cap / px
                    new_qty = pos_qty + add_qty
                    avg_entry_price = (avg_entry_price * pos_qty + px * add_qty) / new_qty
                    pos_qty = new_qty
                    capital_in_trade += add_cap
                    pyramid_count += 1
                    _record_event(i, "AUGMENT", add_qty, px, f"PYRAMID_{pyramid_count}", add_cap)
            # Try PYRAMID: price-breakout based (new HH since entry)
            cap_room = initial_capital_in_trade * cfg.max_pyramid_capital_mult - capital_in_trade
            if (cfg.pyramid_enabled and cfg.pyramid_on_price_breakout
                and pyramid_count < cfg.max_pyramid_levels
                and cap_room > 0
                and px >= highest_close_in_trade * 1.001):
                gain_since_entry = (px - avg_entry_price) / avg_entry_price * 100
                if gain_since_entry >= cfg.pyramid_price_breakout_min_gain:
                    add_cap = min(capital * cfg.pyramid_add_fraction * (0.7 ** pyramid_count), cap_room)
                    add_qty = add_cap / px
                    new_qty = pos_qty + add_qty
                    avg_entry_price = (avg_entry_price * pos_qty + px * add_qty) / new_qty
                    pos_qty = new_qty
                    capital_in_trade += add_cap
                    pyramid_count += 1
                    _record_event(i, "AUGMENT", add_qty, px, f"PYR_BREAKOUT_{pyramid_count}", add_cap)
            # Try EXITS in priority order
            bars_in_trade = i - entry_bar
            exit_path = None
            # X7: Technical stop — frozen DC multi-TF + frozen bb + abs floor from ORIGINAL entry (ALWAYS fires)
            if cfg.exit_X7_tech_stop_enabled:
                gain_from_original = (px - original_entry_price) / original_entry_price * 100
                if gain_from_original < 0:
                    _hit_dc = (frozen_dc_stop > 0 and px < frozen_dc_stop)
                    _hit_bb = (frozen_bb_stop > 0 and px < frozen_bb_stop)
                    _hit_abs = (gain_from_original < cfg.exit_X7_abs_floor_pct)
                    _hit_multi_dc = any(px < v for v in frozen_dc_stops.values())
                    if _hit_dc or _hit_bb or _hit_abs or _hit_multi_dc:
                        exit_path = "X7"
            # Hard stop: X3 (legacy ATR-based, also ignores min_hold)
            if exit_path is None and cfg.exit_X3_hardstop_atr_mult > 0 and atr_15[i] > 0:
                stop_px = avg_entry_price - cfg.exit_X3_hardstop_atr_mult * atr_15[i]
                if px < stop_px:
                    exit_path = "X3"
            # Min hold gate — everything below requires min_hold_bars elapsed
            if exit_path is None and bars_in_trade >= cfg.min_hold_bars:
                # Trailing: X2
                if cfg.exit_X2_trailing_pct > 0:
                    trail_px = highest_close_in_trade * (1 - cfg.exit_X2_trailing_pct / 100)
                    if px < trail_px:
                        if not cfg.exit_X2_require_profit or px > avg_entry_price * 1.005 or pyramid_count > 0:
                            exit_path = "X2"
                if exit_path is None and exit_X1[i]:
                    exit_path = "X1"
                if exit_path is None and exit_X4[i]:
                    exit_path = "X4"
                if exit_path is None and exit_X5[i]:
                    if cfg.exit_X5_min_hold_bars <= 0 or bars_in_trade >= cfg.exit_X5_min_hold_bars:
                        exit_path = "X5"
            if exit_path is None and cfg.exit_X6_time_stop_bars > 0:
                if (i - entry_bar) >= cfg.exit_X6_time_stop_bars:
                    gain = (px - avg_entry_price) / avg_entry_price * 100
                    if gain < cfg.exit_X6_min_gain_pct:
                        exit_path = "X6"
            if exit_path is not None:
                ret_pct = (px - avg_entry_price) / avg_entry_price * 100 - cfg.round_trip_cost_pct
                # Compound: capital = capital + capital_in_trade × ret/100
                pnl_dollars = capital_in_trade * (ret_pct / 100)
                capital += pnl_dollars
                trade_returns.append(ret_pct)
                # Worst intra-trade DD = (lowest_close - avg_entry_price) / avg_entry_price * 100
                if avg_entry_price > 0 and lowest_close_in_trade > 0:
                    worst_dd_pct = (lowest_close_in_trade - avg_entry_price) / avg_entry_price * 100
                else:
                    worst_dd_pct = 0.0
                trade_worst_dd.append(worst_dd_pct)
                exit_path_counts[exit_path] = exit_path_counts.get(exit_path, 0) + 1
                _record_event(i, "CLOSE", pos_qty, px, exit_path, pos_qty * px,
                              extra_indicators={"pnl_pct": round(ret_pct, 4),
                                                 "bars_held": int(i - entry_bar),
                                                 "entry_price": round(avg_entry_price, 4)})
                pos_qty = 0.0
                avg_entry_price = 0.0
                original_entry_price = 0.0
                entry_bar = -1
                capital_in_trade = 0.0
                initial_capital_in_trade = 0.0
                cd = cfg.cooldown_bars_after_exit
                if exit_path == "X4" and cfg.x4_exit_extended_cooldown > 0:
                    cd = max(cd, cfg.x4_exit_extended_cooldown)
                cooldown_until = i + cd

    # MtM final open position
    if pos_qty > 0:
        mark = float(close[-1])
        ret_pct = (mark - avg_entry_price) / avg_entry_price * 100 - cfg.round_trip_cost_pct
        capital += capital_in_trade * (ret_pct / 100)
        trade_returns.append(ret_pct)
        if avg_entry_price > 0 and lowest_close_in_trade > 0:
            worst_dd_pct = (lowest_close_in_trade - avg_entry_price) / avg_entry_price * 100
        else:
            worst_dd_pct = 0.0
        trade_worst_dd.append(worst_dd_pct)
        exit_path_counts["MTM"] = exit_path_counts.get("MTM", 0) + 1
        _record_event(n - 1, "CLOSE", pos_qty, mark, "MTM_FINAL", pos_qty * mark,
                      extra_indicators={"pnl_pct": round(ret_pct, 4),
                                        "bars_held": int(n - 1 - entry_bar),
                                        "entry_price": round(avg_entry_price, 4)})

    # B&H multiplier
    bh_mult = float(close[-1]) / float(close[0]) if close[0] > 0 else 1.0
    compound_mult = capital / cfg.initial_capital

    return {
        "sym": symbol,
        "n_bars": n,
        "years": float((ts[-1] - ts[0]) / (365.25 * 24 * 3600)),
        "bh_mult": bh_mult,
        "compound_mult": compound_mult,
        "ratio_vs_bh": compound_mult / bh_mult if bh_mult > 0 else float('inf'),
        "trades": len(trade_returns),
        "wr_pct": sum(1 for r in trade_returns if r > 0) / max(len(trade_returns), 1) * 100,
        "avg_gain_pct": float(np.mean(trade_returns)) if trade_returns else 0.0,
        "entry_paths": entry_path_counts,
        "exit_paths": exit_path_counts,
        "trade_returns": trade_returns,
        "trade_worst_dd": trade_worst_dd,
        "events": events,
    }


# ════════════════════════════════════════════════════════════════════════════════
# CLI / driver
# ════════════════════════════════════════════════════════════════════════════════

def run_eval(cfg: AggressiveCfg, symbols: List[str], mode: str, start_ts: int,
             label: str = "") -> Dict[str, Any]:
    results = []
    print(f"\n=== {label} ===" if label else "")
    print(f"{'SYM':<7}{'YRS':<6}{'BARS':<8}{'TRADES':<8}{'WR%':<7}{'AVG%/tr':<10}"
          f"{'BH×':<10}{'STRAT×':<10}{'×B&H':<8}{'4× target':<12}{'STATUS':<8}{'PATHS'}")
    sums = []
    for sym in symbols:
        r = simulate_aggressive(sym, mode, cfg, start_ts=start_ts)
        results.append(r)
        bh_mult = r["bh_mult"]
        cm = r["compound_mult"]
        ratio = r["ratio_vs_bh"]
        target = 4.0
        status = "✓" if ratio >= target else f"x{ratio:.1f}/4"
        paths = "+".join(f"{k}{v}" for k, v in sorted(r["entry_paths"].items()))
        sums.append({"sym": sym, "bh": bh_mult, "cm": cm, "ratio": ratio, "trades": r["trades"]})
        print(f"{sym:<7}{r['years']:<6.2f}{r['n_bars']:<8}{r['trades']:<8}{r['wr_pct']:<7.1f}"
              f"{r['avg_gain_pct']:<+10.2f}{bh_mult:<10.2f}{cm:<10.2f}{ratio:<8.2f}{target:<12.2f}{status:<8}{paths}")
    return {"results": results, "summary": sums}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="NVDA,GOOGL,MSFT,SNDK")
    ap.add_argument("--mode", default="tradier")
    ap.add_argument("--start", default="2022-01-01")
    ap.add_argument("--label", default="v4_default")
    args = ap.parse_args()
    start_ts = int(datetime.datetime.strptime(args.start, "%Y-%m-%d")
                   .replace(tzinfo=datetime.timezone.utc).timestamp())
    symbols = args.symbols.split(",")
    cfg = AggressiveCfg()
    run_eval(cfg, symbols, args.mode, start_ts, label=args.label)


if __name__ == "__main__":
    main()
