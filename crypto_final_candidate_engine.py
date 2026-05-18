"""crypto_final_candidate_engine.py — FINAL deploy-candidate crypto strategy.

USER MANDATE 2026-05-18: build/test the FINAL crypto candidate combining ALL
proven elements into ONE variant:
  - v3_no_stop base (X1/X2/X4/X5 exits, no X3 % stop)
  - + GOLDEN_RULE activation gate (D, 4h)
  - + dc_low_1h hard stop overlay (X6: close if close < dc_low_1h × 0.998)
  - + ABS -20% emergency floor (X7)

This is a STANDALONE engine on top of v8_struct_v4_aggressive (v3_no_stop logic)
that ADDS the activation gate at entry and the dc_low_1h + ABS-20% exits.

No live code touched. No NPZ regen.
Reads ONLY NPZ via load_npz_slim + supplemental fields.
"""
from __future__ import annotations

import argparse, csv, datetime, json, sys, time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from v8_vec_structure_sweep import load_npz_slim  # base slim loader
from v8_vec_sweep import _resolve_npz_path
from vec_paths.structure_hh_hl import (
    compute_all_structure, _streak_at_value,
    STRUCT_HH, STRUCT_HL, STRUCT_LH, STRUCT_LL,
)


# Defaults from golden_rule_htf.py
_DC_EXTENDED_LONG = 0.65
_BB_EXTENDED_LONG = 0.75


# ════════════════════════════════════════════════════════════════════════════════
# Extended NPZ loader — slim + activation/stop fields
# ════════════════════════════════════════════════════════════════════════════════

def load_npz_for_candidate(symbol: str, mode: str, *, start_ts: Optional[int] = None):
    """Load slim base + extra fields needed for activation gate and dc_low_1h."""
    base, ts = load_npz_slim(symbol, mode, start_ts=start_ts)
    # Slim already keeps dc_low_1h (via dc_low_{tf}). We need to ALSO add:
    # bb_pct_b_D, bb_pct_b_4h, dc_position_D, dc_position_4h
    p = _resolve_npz_path(symbol, mode)
    extras = ("bb_pct_b_D", "bb_pct_b_4h", "dc_position_D", "dc_position_4h",
              "rsi_D", "sma_200_D")
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
# Final candidate config
# ════════════════════════════════════════════════════════════════════════════════

@dataclass
class FinalCandidateCfg:
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
    # ── ENTRY paths (v3_no_stop defaults, all on except A/E behavior) ───────
    # Path A: HTF struct breakout + retest (v3 default ON)
    path_A_struct_enabled: bool = True
    path_A_breakout_tf: str = "D"
    path_A_retest_tf: str = "1h"
    path_A_trigger_tf: str = "15m"
    path_A_min_count: int = 4
    path_A_atr_band: float = 1.0
    path_A_regime_persist_bars: int = 500
    # Path B: oversold reversal
    path_B_oversold_enabled: bool = True
    path_B_k15_max: float = 30.0
    path_B_k1h_max: float = 40.0
    path_B_require_wt_bull_15m: bool = True
    path_B_require_reclaim_pivot_lo: bool = True
    # Path C: 1h K cross from below
    path_C_k1h_cross_enabled: bool = True
    path_C_k1h_cross_below: float = 30.0
    # Path D: Daily WT bullish cross
    path_D_wtD_cross_enabled: bool = True
    path_D_rsi_D_min: float = 40.0
    # Path E: 200SMA reclaim
    path_E_sma200_reclaim_enabled: bool = True
    path_E_rsi_D_min: float = 50.0
    # Path F: Weekly bullish flip
    path_F_weekly_flip_enabled: bool = True
    # Path G: Momentum continuation
    path_G_momentum_continuation_enabled: bool = True
    path_G_k1h_cross_min: float = 50.0
    # Universal HTF trend filter (v3_no_stop has this ON)
    htf_trend_filter_enabled: bool = True
    htf_require_wt_D_bull: bool = True
    htf_require_rsi_D_min: float = 45.0
    # ── GOLDEN_RULE activation gate (NEW) ──────────────────────────────────
    golden_rule_activation_enabled: bool = True
    activation_tfs: List[str] = field(default_factory=lambda: ["D", "4h"])
    activation_bb_thr: float = 0.75
    activation_dc_thr: float = 0.65
    # ── EXIT paths ──────────────────────────────────────────────────────────
    # X1: top catch K_15m > 80 + bear WT 15m
    exit_X1_topcatch_enabled: bool = True
    exit_X1_k15_min: float = 80.0
    # X2: trailing
    exit_X2_trailing_pct: float = 12.0
    # X4: Daily bear WT
    exit_X4_daily_bear_wt_enabled: bool = True
    # X5: structural flip on trigger TF
    exit_X5_structural_flip_enabled: bool = True
    exit_X5_min_count: int = 3
    exit_X5_window_bars: int = 3
    # X6: live dc_low_1h × mult hard stop (NEW)
    exit_X6_dc_low_1h_enabled: bool = True
    exit_X6_dc_low_1h_mult: float = 0.998
    # X7: ABS floor (NEW, -20%)
    exit_X7_abs_floor_enabled: bool = True
    exit_X7_abs_floor_pct: float = -20.0


# ════════════════════════════════════════════════════════════════════════════════
# Helpers — same shape as v8_struct_v4_aggressive
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
# Per-symbol simulator — extends v8_struct_v4_aggressive logic
# ════════════════════════════════════════════════════════════════════════════════

def simulate_final_candidate(symbol: str, mode: str, cfg: FinalCandidateCfg, *,
                             start_ts: Optional[int] = None) -> Dict[str, Any]:
    try:
        npz, ts = load_npz_for_candidate(symbol, mode, start_ts=start_ts)
    except Exception as e:
        return {"sym": symbol, "trades": 0, "compound_mult": 1.0, "skip": True, "err": str(e)}
    close = np.asarray(npz["close"], dtype=np.float64)
    n = len(close)
    if n < 200:
        return {"sym": symbol, "trades": 0, "compound_mult": 1.0, "skip": True}

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
    sma200_D = _f(npz, "sma_200_D", n)
    atr_15 = _f(npz, "atr_15m", n, 0.0)
    dc_low_1h = _f(npz, "dc_low_1h", n, 0.0)
    bb_pct_b_D = _f(npz, "bb_pct_b_D", n, -1.0)
    bb_pct_b_4h = _f(npz, "bb_pct_b_4h", n, -1.0)
    dc_position_D = _f(npz, "dc_position_D", n, -1.0)
    dc_position_4h = _f(npz, "dc_position_4h", n, -1.0)

    bull_wt_15 = _bull_cross(wt1_15, wt2_15)
    bull_wt_D = _bull_cross(wt1_D, wt2_D)
    bear_wt_D = _bear_cross(wt1_D, wt2_D)
    bull_k_1h = _bull_cross(k1h, d1h)
    bull_wt_W = _bull_cross(wt1_W, wt2_W)
    bull_sma200 = _bull_cross(close, sma200_D)
    bear_wt_15 = _bear_cross(wt1_15, wt2_15)
    bear_wt_1h = _bear_cross(wt1_1h, wt2_1h)

    # Structure-based gates (path A)
    bk_price = structure.get(("price", cfg.path_A_breakout_tf)) if cfg.path_A_struct_enabled else None
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
    hl_count = np.zeros(n, dtype=np.int8)
    for s in ("price", "wt1", "wt2", "stoch_k", "dc_basis"):
        d = structure.get((s, cfg.path_A_trigger_tf))
        if d is None:
            continue
        hl_count += (d["struct"] == STRUCT_HL).astype(np.int8)

    tg_price = structure.get(("price", "15m"))
    last_pivot_lo = tg_price["last_lo"] if tg_price is not None else np.full(n, np.nan)
    reclaim_ok = np.where(np.isfinite(last_pivot_lo) & (last_pivot_lo > 0),
                          close > last_pivot_lo, False)

    pyramid_tf_struct = structure.get(("price", cfg.pyramid_tf))
    if pyramid_tf_struct is not None:
        new_pyramid_HH = (pyramid_tf_struct["pivot_event"] == STRUCT_HH)
    else:
        new_pyramid_HH = np.zeros(n, dtype=bool)
    if cfg.pyramid_also_on_k1h_oversold:
        new_pyramid_HH = new_pyramid_HH | ((k1h < 25.0) & _bull_cross(wt1_1h, wt2_1h))

    # ── ENTRY masks per path ─────────────────────────────────────────────────
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
    if cfg.path_G_momentum_continuation_enabled:
        k1h_cross_up = _bull_cross(k1h, np.full(n, cfg.path_G_k1h_cross_min))
        entry_G = k1h_cross_up & (wt1_1h > wt2_1h)
    else:
        entry_G = np.zeros(n, dtype=bool)
    # HTF trend filter (v3_no_stop has this ON)
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

    # ── GOLDEN_RULE activation gate ─────────────────────────────────────────
    # Activation = at least 1 TF in activation_tfs shows breakout (bb_pctb >= bb_thr OR dc_pos >= dc_thr)
    if cfg.golden_rule_activation_enabled and cfg.activation_tfs:
        act_mask = np.zeros(n, dtype=bool)
        for tf in cfg.activation_tfs:
            bb = npz.get(f"bb_pct_b_{tf}")
            dc = npz.get(f"dc_position_{tf}")
            if bb is not None and bb.shape[0] == n:
                bb_arr = np.asarray(bb, dtype=np.float64)
                act_mask |= (bb_arr >= cfg.activation_bb_thr)
            if dc is not None and dc.shape[0] == n:
                dc_arr = np.asarray(dc, dtype=np.float64)
                act_mask |= (dc_arr >= cfg.activation_dc_thr)
        # Gate ALL entry paths through activation
        entry_A &= act_mask
        entry_B &= act_mask
        entry_C &= act_mask
        entry_D &= act_mask
        entry_E &= act_mask
        entry_F &= act_mask
        entry_G &= act_mask

    # ── EXIT masks (X1, X4, X5) ─────────────────────────────────────────────
    exit_X1 = ((k15 > cfg.exit_X1_k15_min) & bear_wt_15) if cfg.exit_X1_topcatch_enabled else np.zeros(n, dtype=bool)
    exit_X4 = bear_wt_D if cfg.exit_X4_daily_bear_wt_enabled else np.zeros(n, dtype=bool)
    if cfg.exit_X5_structural_flip_enabled:
        from vec_paths.structure_hh_hl import _recent_events_count
        flip_count = np.zeros(n, dtype=np.int8)
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

    # ── Simulation loop ─────────────────────────────────────────────────────
    capital = cfg.initial_capital
    pos_qty = 0.0
    avg_entry_price = 0.0
    entry_bar = -1
    highest_close_in_trade = 0.0
    pyramid_count = 0
    capital_in_trade = 0.0
    cooldown_until = 0
    trade_returns: List[float] = []
    entry_path_counts: Dict[str, int] = {}
    exit_path_counts: Dict[str, int] = {}

    for i in range(50, n):
        px = float(close[i])
        if not np.isfinite(px) or px <= 0:
            continue
        if pos_qty == 0.0:
            if i < cooldown_until:
                continue
            entry_path = None
            if entry_A[i]: entry_path = "A"
            elif entry_B[i]: entry_path = "B"
            elif entry_C[i]: entry_path = "C"
            elif entry_D[i]: entry_path = "D"
            elif entry_E[i]: entry_path = "E"
            elif entry_F[i]: entry_path = "F"
            elif entry_G[i]: entry_path = "G"
            if entry_path is not None:
                capital_in_trade = capital * cfg.full_entry_fraction
                pos_qty = capital_in_trade / px
                avg_entry_price = px
                entry_bar = i
                highest_close_in_trade = px
                pyramid_count = 0
                entry_path_counts[entry_path] = entry_path_counts.get(entry_path, 0) + 1
        else:
            if px > highest_close_in_trade:
                highest_close_in_trade = px
            # Pyramid
            if (pyramid_count < cfg.max_pyramid_levels and new_pyramid_HH[i]):
                gain_since_entry = (px - avg_entry_price) / avg_entry_price * 100
                if gain_since_entry >= cfg.pyramid_min_gain_since_last_pct:
                    add_cap = capital * cfg.pyramid_add_fraction * (0.7 ** pyramid_count)
                    add_qty = add_cap / px
                    new_qty = pos_qty + add_qty
                    avg_entry_price = (avg_entry_price * pos_qty + px * add_qty) / new_qty
                    pos_qty = new_qty
                    capital_in_trade += add_cap
                    pyramid_count += 1

            exit_path = None
            cur_gain = (px - avg_entry_price) / avg_entry_price * 100
            # X7: ABS -20% floor (ALWAYS first; emergency)
            if cfg.exit_X7_abs_floor_enabled and cur_gain <= cfg.exit_X7_abs_floor_pct:
                exit_path = "X7"
            # X6: live dc_low_1h × mult (second; emergency tech stop)
            if exit_path is None and cfg.exit_X6_dc_low_1h_enabled:
                dl = float(dc_low_1h[i])
                if dl > 0 and px < dl * cfg.exit_X6_dc_low_1h_mult:
                    exit_path = "X6"
            # X2: trailing (only when in profit ≥0.5% or pyramided)
            if exit_path is None and cfg.exit_X2_trailing_pct > 0:
                trail_px = highest_close_in_trade * (1 - cfg.exit_X2_trailing_pct / 100)
                if px < trail_px and (px > avg_entry_price * 1.005 or pyramid_count > 0):
                    exit_path = "X2"
            # X1: top catch
            if exit_path is None and exit_X1[i]:
                exit_path = "X1"
            # X4: Daily bear WT
            if exit_path is None and exit_X4[i]:
                exit_path = "X4"
            # X5: structural flip
            if exit_path is None and exit_X5[i]:
                exit_path = "X5"

            if exit_path is not None:
                ret_pct = (px - avg_entry_price) / avg_entry_price * 100 - cfg.round_trip_cost_pct
                pnl_dollars = capital_in_trade * (ret_pct / 100)
                capital += pnl_dollars
                trade_returns.append(ret_pct)
                exit_path_counts[exit_path] = exit_path_counts.get(exit_path, 0) + 1
                pos_qty = 0.0
                avg_entry_price = 0.0
                entry_bar = -1
                capital_in_trade = 0.0
                cooldown_until = i + cfg.cooldown_bars_after_exit

    # MtM final open (NO LIES MANDATE: rule 2)
    if pos_qty > 0:
        mark = float(close[-1])
        ret_pct = (mark - avg_entry_price) / avg_entry_price * 100 - cfg.round_trip_cost_pct
        capital += capital_in_trade * (ret_pct / 100)
        trade_returns.append(ret_pct)
        exit_path_counts["MTM_FINAL_BAR_NOLIES"] = exit_path_counts.get("MTM_FINAL_BAR_NOLIES", 0) + 1

    bh_mult = float(close[-1]) / float(close[0]) if close[0] > 0 else 1.0
    compound_mult = capital / cfg.initial_capital
    yrs = float((ts[-1] - ts[0]) / (365.25 * 24 * 3600))

    arr = np.array(trade_returns, dtype=np.float64) if trade_returns else np.array([], dtype=np.float64)
    if len(arr) > 1 and arr.std() > 0:
        sym_sharpe = float(arr.mean() / arr.std())
    else:
        sym_sharpe = 0.0
    # Per-sym DD on equity curve
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
        "wr_pct": float(sum(1 for r in trade_returns if r > 0) / max(len(trade_returns), 1) * 100),
        "avg_gain_pct": float(arr.mean()) if len(arr) > 0 else 0.0,
        "sym_sharpe": sym_sharpe,
        "max_dd_pct": max_dd,
        "entry_paths": entry_path_counts,
        "exit_paths": exit_path_counts,
        "trade_returns": trade_returns,
    }


# ════════════════════════════════════════════════════════════════════════════════
# Universe runner
# ════════════════════════════════════════════════════════════════════════════════

import platform
IS_SERVER = platform.system() == "Linux"
BASE = Path("/home/niels/binance-sandbox") if IS_SERVER else Path("/Users/niels/Documents/binance")


def get_crypto_universe():
    """All USDC/USDT NPZs available."""
    npz_dir = BASE / "backtest_v8" / "indicators"
    syms = []
    for f in npz_dir.glob("*.npz"):
        name = f.stem
        if name.endswith("USDC") or name.endswith("USDT"):
            syms.append(name)
    return sorted(syms)


def run_universe(symbols, cfg: FinalCandidateCfg, start_date: str, label: str):
    start_ts = int(datetime.datetime.strptime(start_date, "%Y-%m-%d")
                   .replace(tzinfo=datetime.timezone.utc).timestamp())
    rows = []
    all_returns = []
    all_exit_paths: Dict[str, int] = {}
    all_entry_paths: Dict[str, int] = {}
    t0 = time.time()
    n_attempted = 0
    n_skipped = 0
    for sym in symbols:
        n_attempted += 1
        try:
            r = simulate_final_candidate(sym, "crypto", cfg, start_ts=start_ts)
        except Exception as e:
            print(f"  ERR {sym}: {e}")
            n_skipped += 1
            continue
        if r.get("skip"):
            n_skipped += 1
            continue
        all_returns.extend(r["trade_returns"])
        for k, v in r["exit_paths"].items():
            all_exit_paths[k] = all_exit_paths.get(k, 0) + v
        for k, v in r["entry_paths"].items():
            all_entry_paths[k] = all_entry_paths.get(k, 0) + v
        rows.append({
            "sym": r["sym"],
            "years": r["years"],
            "trades": r["trades"],
            "wr_pct": r["wr_pct"],
            "avg_gain_trade": r["avg_gain_pct"],
            "bh_mult": r["bh_mult"],
            "compound_mult": r["compound_mult"],
            "ratio_vs_bh": r["ratio_vs_bh"],
            "sym_sharpe": r["sym_sharpe"],
            "max_dd_pct": r["max_dd_pct"],
            "cleared_4x": 1 if r["ratio_vs_bh"] >= 4.0 else 0,
            "beat_bh": 1 if r["compound_mult"] > r["bh_mult"] else 0,
            "x6_fires": r["exit_paths"].get("X6", 0),
            "x7_fires": r["exit_paths"].get("X7", 0),
            "x1_fires": r["exit_paths"].get("X1", 0),
            "x2_fires": r["exit_paths"].get("X2", 0),
            "x4_fires": r["exit_paths"].get("X4", 0),
            "x5_fires": r["exit_paths"].get("X5", 0),
        })
        print(f"  [{n_attempted:>3}/{len(symbols)}] {sym:<14} trades={r['trades']:>4} "
              f"wr={r['wr_pct']:>5.1f}% gain={r['avg_gain_pct']:+6.2f}%/tr "
              f"compound={r['compound_mult']:>6.2f}× bh={r['bh_mult']:>5.2f}× "
              f"ratio={r['ratio_vs_bh']:>5.2f}× dd={r['max_dd_pct']:.1f}%",
              flush=True)
    elapsed = time.time() - t0
    n_syms = len(rows)
    total_trades = sum(r["trades"] for r in rows)

    # Pool Sharpe (canonical metrics_guard standard)
    if len(all_returns) > 1:
        arr = np.array(all_returns, dtype=np.float64)
        pool_sharpe = float(arr.mean() / arr.std()) if arr.std() > 0 else 0.0
        acc_gain_pct = float(arr.sum())  # raw sum of per-trade pct (proxy for total)
        avg_gain_trade = float(arr.mean())
    else:
        pool_sharpe, acc_gain_pct, avg_gain_trade = 0.0, 0.0, 0.0

    # Sym Sharpe (mean of per-sym Sharpe, capped at ±5.0, exclude <30 trades)
    eligible_sym_sharpes = []
    for r in rows:
        if r["trades"] >= 30:
            s = max(-5.0, min(5.0, r["sym_sharpe"]))
            eligible_sym_sharpes.append(s)
    sym_sharpe_avg = float(np.mean(eligible_sym_sharpes)) if eligible_sym_sharpes else 0.0

    yrs = max(r["years"] for r in rows) if rows else 1.0
    # Aggregate compound mult (geometric mean)
    if rows:
        log_compounds = [np.log(max(0.01, r["compound_mult"])) for r in rows]
        geo_mean_compound = float(np.exp(np.mean(log_compounds)))
    else:
        geo_mean_compound = 1.0
    pct_compound_per_sym = (geo_mean_compound - 1.0) * 100
    gain_per_yr = pct_compound_per_sym / yrs if yrs > 0 else 0.0
    gain_sym_yr = gain_per_yr / max(n_syms, 1)

    # Pool max DD (across all combined trades, treat as one stream)
    if len(all_returns) > 0:
        eq = np.cumprod(1 + np.array(all_returns) / 100)
        peak = np.maximum.accumulate(eq)
        dd = (eq - peak) / peak
        pool_max_dd = float(-dd.min() * 100)
    else:
        pool_max_dd = 0.0

    # Per-sym dd average
    avg_dd = float(np.mean([r["max_dd_pct"] for r in rows])) if rows else 0.0

    cleared_4x = sum(1 for r in rows if r["cleared_4x"])
    beat_bh = sum(1 for r in rows if r["beat_bh"])
    losers = sum(1 for r in rows if r["compound_mult"] < 1.0)
    catastrophic = sum(1 for r in rows if r["compound_mult"] < 0.5)

    x6_total = all_exit_paths.get("X6", 0)
    x7_total = all_exit_paths.get("X7", 0)
    x6_pct = x6_total / max(total_trades, 1) * 100
    x7_pct = x7_total / max(total_trades, 1) * 100

    summary = {
        "label": label,
        "start_date": start_date,
        "n_syms": n_syms,
        "years": yrs,
        "trades": total_trades,
        "pool_sharpe": pool_sharpe,
        "sym_sharpe": sym_sharpe_avg,
        "avg_gain_trade": avg_gain_trade,
        "gain_per_yr": gain_per_yr,
        "gain_sym_yr": gain_sym_yr,
        "max_dd_pct": pool_max_dd,
        "avg_dd_pct": avg_dd,
        "compound_geo": geo_mean_compound,
        "beat_bh": beat_bh,
        "cleared_4x": cleared_4x,
        "losers": losers,
        "catastrophic": catastrophic,
        "x6_fires": x6_total,
        "x7_fires": x7_total,
        "x6_pct": x6_pct,
        "x7_pct": x7_pct,
        "exit_paths_total": all_exit_paths,
        "entry_paths_total": all_entry_paths,
        "elapsed_s": elapsed,
        "n_attempted": n_attempted,
        "n_skipped": n_skipped,
    }

    # Sample-floor tag
    if n_syms >= 48 and yrs >= 1.0 and total_trades / max(n_syms, 1) >= 30:
        summary["sample_tag"] = "[PUBLISHABLE]"
    else:
        summary["sample_tag"] = f"[DIAGNOSTIC ONLY · n_syms={n_syms} · years={yrs:.2f}]"

    # Write CSV
    out_dir = BASE / "data" / "sweep_results"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts_label = int(time.time())
    per_sym_csv = out_dir / f"crypto_final_candidate_{label}_{ts_label}_per_sym.csv"
    if rows:
        with open(per_sym_csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
    summary_json = out_dir / f"crypto_final_candidate_{label}_{ts_label}_summary.json"
    with open(summary_json, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    summary["_per_sym_csv"] = str(per_sym_csv)
    summary["_summary_json"] = str(summary_json)
    summary["_rows"] = rows
    # Canonical row through metrics_guard (NO-LIES MANDATE 2026-04-30)
    try:
        import metrics_guard
        canon_csv = out_dir / f"crypto_final_candidate_{label}_{ts_label}.csv"
        row_for_guard = {
            "iter": f"crypto_final_{label}",
            "pool_sharpe": summary["pool_sharpe"],
            "sym_sharpe": summary["sym_sharpe"],
            "avg_gain_trade": summary["avg_gain_trade"],
            "gain_per_yr": summary["gain_per_yr"],
            "gain_sym_yr": summary["gain_sym_yr"],
            "max_dd_pct": summary["max_dd_pct"],
            "trades": summary["trades"],
            "n_syms": summary["n_syms"],
            "years": round(summary["years"], 3),
            "label": label,
            "tier": metrics_guard.tier_name(summary["pool_sharpe"]),
            "compound_geo": round(summary["compound_geo"], 4),
            "beat_bh": summary["beat_bh"],
            "cleared_4x": summary["cleared_4x"],
            "x6_pct": round(summary["x6_pct"], 2),
            "x7_pct": round(summary["x7_pct"], 2),
        }
        metrics_guard.write_sharpe_row(canon_csv, row_for_guard, mode="crypto", append=True)
        summary["_canon_csv"] = str(canon_csv)
        print(f"[CSV] canonical row → {canon_csv}")
    except metrics_guard.FakeMetricRefused as e:
        print(f"[REFUSED] metrics_guard: {e}")
    except Exception as e:
        print(f"[WARN] metrics_guard write failed: {e}")
    return summary


def print_summary(s):
    print("\n" + "═" * 90)
    print(f"  {s['label']} | start={s['start_date']} | {s['n_syms']} syms | {s['elapsed_s']:.0f}s")
    print("═" * 90)
    print(f"  Sample tag      : {s['sample_tag']}")
    print(f"  pool_sharpe     = {s['pool_sharpe']:+.4f}")
    print(f"  sym_sharpe      = {s['sym_sharpe']:+.4f}")
    print(f"  avg_gain_trade  = {s['avg_gain_trade']:+.4f}%")
    print(f"  gain_per_yr     = {s['gain_per_yr']:+.2f}%")
    print(f"  gain_sym_yr     = {s['gain_sym_yr']:+.4f}%")
    print(f"  pool_max_dd     = {s['max_dd_pct']:.2f}%")
    print(f"  avg_dd          = {s['avg_dd_pct']:.2f}%")
    print(f"  trades          = {s['trades']}")
    print(f"  compound_geo    = {s['compound_geo']:.4f}×")
    print(f"  beat_bh         = {s['beat_bh']}/{s['n_syms']}")
    print(f"  cleared_4x      = {s['cleared_4x']}/{s['n_syms']}")
    print(f"  losers (<1×)    = {s['losers']}")
    print(f"  catastrophic    = {s['catastrophic']}")
    print(f"  X6 fires        = {s['x6_fires']} ({s['x6_pct']:.1f}% of trades)")
    print(f"  X7 fires        = {s['x7_fires']} ({s['x7_pct']:.1f}% of trades)")
    print(f"  exit paths      : {s['exit_paths_total']}")
    print(f"  entry paths     : {s['entry_paths_total']}")
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
    ap.add_argument("--start", default="2022-01-01")
    ap.add_argument("--label", default="full")
    ap.add_argument("--baseline", action="store_true", help="Run v3_no_stop baseline (no activation, no X6, no X7)")
    ap.add_argument("--limit", type=int, default=0, help="Limit syms (for smoke test)")
    args = ap.parse_args()

    syms = get_crypto_universe()
    if args.limit:
        syms = syms[:args.limit]
    print(f"Universe: {len(syms)} crypto NPZs")

    cfg = FinalCandidateCfg()
    if args.baseline:
        cfg.golden_rule_activation_enabled = False
        cfg.exit_X6_dc_low_1h_enabled = False
        cfg.exit_X7_abs_floor_enabled = False
        args.label = f"baseline_v3nostop_{args.label}"
    s = run_universe(syms, cfg, args.start, args.label)
    print_summary(s)


if __name__ == "__main__":
    main()
