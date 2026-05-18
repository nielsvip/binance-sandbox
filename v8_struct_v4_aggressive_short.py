"""v8_struct_v4_aggressive_short.py — multi-path aggressive SHORT-only catcher.

Mirror of v8_struct_v4_aggressive.py with all signals inverted:
- ENTRY paths fire on BEARISH signals (overbought, bear crosses, breakdowns)
- EXIT paths fire on BULLISH signals (bull WT crosses, reclaims)
- PYRAMID on new lows (LL) instead of new highs (HH)
- Gain = (entry_price - current_price) / entry_price (profits when price falls)

Same architecture: multiple parallel entry/exit paths, compound capital across trades.
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


@dataclass
class ShortCfg:
    initial_capital: float = 10_000.0
    full_entry_fraction: float = 1.0
    pyramid_add_fraction: float = 0.8
    max_pyramid_levels: int = 15
    round_trip_cost_pct: float = 0.0
    cooldown_bars_after_exit: int = 20
    # ─── ENTRY paths (bearish signals) ────────────────────────────────────
    # A: HTF struct breakdown + retest from above + multi-series LH alignment
    path_A_struct_enabled: bool = True
    path_A_breakout_tf: str = "D"
    path_A_retest_tf: str = "1h"
    path_A_trigger_tf: str = "15m"
    path_A_min_count: int = 4
    path_A_atr_band: float = 1.0
    path_A_regime_persist_bars: int = 500
    # B: Overbought reversal — K_15m overbought + WT bear cross 15m + break swing-high
    path_B_overbought_enabled: bool = True
    path_B_k15_min: float = 70.0
    path_B_k1h_min: float = 60.0
    path_B_require_wt_bear_15m: bool = True
    path_B_require_break_pivot_hi: bool = True
    # C: 1h K cross DOWN from above — K_1h crosses below D_1h from K>70
    path_C_k1h_cross_enabled: bool = True
    path_C_k1h_cross_above: float = 70.0
    # D: Daily WT bearish cross — wt1_D crosses below wt2_D, RSI_D < 60
    path_D_wtD_cross_enabled: bool = True
    path_D_rsi_D_max: float = 60.0
    # E: 200SMA breakdown — close < sma_200_D + RSI_D < 50
    path_E_sma200_break_enabled: bool = True
    path_E_rsi_D_max: float = 50.0
    # F: Weekly bearish flip — wt1_W < wt2_W after being above
    path_F_weekly_flip_enabled: bool = True
    # G: Momentum continuation SHORT — K_1h crosses down 50, wt1_1h < wt2_1h, in HTF bear
    path_G_momentum_continuation_enabled: bool = True
    path_G_k1h_cross_max: float = 50.0
    # Universal HTF trend filter — applied to paths B/C/D/G when enabled
    htf_trend_filter_enabled: bool = True
    htf_require_wt_D_bear: bool = True
    htf_require_rsi_D_max: float = 55.0
    # ─── EXIT paths (bullish signals force cover) ────────────────────────
    # X1: K_15m oversold + bullish WT cross on 15m (bottom-catch → cover)
    exit_X1_bottomcatch_enabled: bool = False
    exit_X1_k15_max: float = 20.0
    # X2: trailing stop from lowest close in-trade (covers to protect profits)
    exit_X2_trailing_pct: float = 12.0
    exit_X2_require_profit: bool = True
    # X3: hard stop ATR-based (price rallies against)
    exit_X3_hardstop_atr_mult: float = 0
    # X7: technical stop — frozen dc_high at entry + absolute ceiling
    exit_X7_tech_stop_enabled: bool = True
    exit_X7_freeze_dc_tf: str = "4h"
    exit_X7_abs_ceiling_pct: float = 8.0  # max loss % on short (price rallies 8%)
    # X4: bullish D WT cross (HTF protection → cover short)
    exit_X4_daily_bull_wt_enabled: bool = True
    # X5: structural flip (multi-series simultaneous bullish flip → cover)
    exit_X5_structural_flip_enabled: bool = True
    exit_X5_min_count: int = 5
    exit_X5_window_bars: int = 3
    # X6: time stop
    exit_X6_time_stop_bars: int = 0
    exit_X6_min_gain_pct: float = 5.0
    # ─── HOLD / CHURN REDUCTION ───────────────────────────────────────────
    min_hold_bars: int = 48
    exit_X5_min_hold_bars: int = 48
    # PYRAMID (on new lows)
    pyramid_enabled: bool = True
    pyramid_require_new_LL: bool = True
    pyramid_min_gain_since_last_pct: float = 0.5
    pyramid_tf: str = "15m"
    pyramid_also_on_k1h_overbought: bool = True
    pyramid_on_price_breakdown: bool = True
    pyramid_price_breakdown_min_gain: float = 0.5
    # ─── ANTI-CHURN ──────────────────────────────────────────────────────
    require_below_sma50_D: bool = False
    x4_exit_extended_cooldown: int = 0


def _f(npz, key, n, default=0.0):
    arr = npz.get(key)
    if arr is None:
        return np.full(n, default, dtype=np.float64)
    a = np.asarray(arr, dtype=np.float64)
    if a.shape[0] != n:
        return np.full(n, default, dtype=np.float64)
    return a


def _bull_cross(fast: np.ndarray, slow: np.ndarray) -> np.ndarray:
    prev_below = np.concatenate(([False], fast[:-1] <= slow[:-1]))
    now_above = fast > slow
    return prev_below & now_above


def _bear_cross(fast: np.ndarray, slow: np.ndarray) -> np.ndarray:
    prev_above = np.concatenate(([False], fast[:-1] >= slow[:-1]))
    now_below = fast < slow
    return prev_above & now_below


def simulate_aggressive_short(symbol: str, mode: str, cfg: ShortCfg,
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
    # X7: frozen dc_HIGH for short (price must stay below this)
    dc_high_4h = _f(npz, f"dc_high_{cfg.exit_X7_freeze_dc_tf}", n, 0.0)

    # Bear crosses (entries for shorts)
    bear_wt_15 = _bear_cross(wt1_15, wt2_15)
    bear_wt_D = _bear_cross(wt1_D, wt2_D)
    bull_wt_D = _bull_cross(wt1_D, wt2_D)
    bear_k_1h = _bear_cross(k1h, d1h)
    bear_wt_W = _bear_cross(wt1_W, wt2_W)
    bear_sma200 = _bear_cross(close, sma200_D)  # price crosses BELOW sma200
    # Bull crosses (exits for shorts)
    bull_wt_15 = _bull_cross(wt1_15, wt2_15)

    # Structure-based gates (path A — bearish: LL regime on breakout TF)
    bk_price = structure.get(("price", cfg.path_A_breakout_tf)) if cfg.path_A_struct_enabled else None
    rt_price = structure.get(("price", cfg.path_A_retest_tf)) if cfg.path_A_struct_enabled else None
    bk_is_ll = np.zeros(n, dtype=bool)
    rt_retest_hi_ok = np.zeros(n, dtype=bool)
    if bk_price is not None and rt_price is not None:
        bk_streak = _streak_at_value(bk_price["struct"], int(STRUCT_LL))
        bk_is_ll = bk_streak >= cfg.path_A_regime_persist_bars
        atr_rt = _f(npz, f"atr_{cfg.path_A_retest_tf}", n, 0.0)
        with np.errstate(invalid="ignore", divide="ignore"):
            rt_retest_hi_ok = np.where(atr_rt > 0,
                np.abs(close - rt_price["last_hi"]) / np.maximum(atr_rt, 1e-9) < cfg.path_A_atr_band,
                False).astype(bool)

    # Multi-series LH count on trigger TF for path A (bearish structure)
    lh_count = np.zeros(n, dtype=np.int8)
    for s in ("price", "wt1", "wt2", "stoch_k", "dc_basis"):
        d = structure.get((s, cfg.path_A_trigger_tf))
        if d is None:
            continue
        lh_count += (d["struct"] == STRUCT_LH).astype(np.int8)

    # Last pivot-high on 15m for path B "break swing high"
    tg_price = structure.get(("price", "15m"))
    if tg_price is not None:
        last_pivot_hi = tg_price["last_hi"]
    else:
        last_pivot_hi = np.full(n, np.nan, dtype=np.float64)
    break_hi_ok = np.where(np.isfinite(last_pivot_hi) & (last_pivot_hi > 0),
                           close < last_pivot_hi, False)

    # Pyramid trigger TF (new LL for shorts)
    pyramid_tf_struct = structure.get(("price", cfg.pyramid_tf))
    if pyramid_tf_struct is not None:
        pyr_event = pyramid_tf_struct["pivot_event"]
        new_pyramid_LL = (pyr_event == STRUCT_LL)
    else:
        new_pyramid_LL = np.zeros(n, dtype=bool)
    if cfg.pyramid_also_on_k1h_overbought:
        new_pyramid_LL = new_pyramid_LL | ((k1h > 75.0) & _bear_cross(wt1_1h, wt2_1h))

    # ── ENTRY masks per path (ALL BEARISH) ────────────────────────────────
    entry_A = (bk_is_ll & rt_retest_hi_ok & (lh_count >= cfg.path_A_min_count)) if cfg.path_A_struct_enabled else np.zeros(n, dtype=bool)
    entry_B = np.zeros(n, dtype=bool)
    if cfg.path_B_overbought_enabled:
        m = (k15 > cfg.path_B_k15_min) & (k1h > cfg.path_B_k1h_min)
        if cfg.path_B_require_wt_bear_15m:
            m &= bear_wt_15
        if cfg.path_B_require_break_pivot_hi:
            m &= break_hi_ok
        entry_B = m
    entry_C = (bear_k_1h & (k1h > cfg.path_C_k1h_cross_above)) if cfg.path_C_k1h_cross_enabled else np.zeros(n, dtype=bool)
    entry_D = (bear_wt_D & (rsi_D < cfg.path_D_rsi_D_max)) if cfg.path_D_wtD_cross_enabled else np.zeros(n, dtype=bool)
    entry_E = (bear_sma200 & (rsi_D < cfg.path_E_rsi_D_max)) if cfg.path_E_sma200_break_enabled else np.zeros(n, dtype=bool)
    entry_F = bear_wt_W if cfg.path_F_weekly_flip_enabled else np.zeros(n, dtype=bool)
    if cfg.path_G_momentum_continuation_enabled:
        k1h_cross_down = _bear_cross(k1h, np.full(n, cfg.path_G_k1h_cross_max))
        entry_G = k1h_cross_down & (wt1_1h < wt2_1h)
    else:
        entry_G = np.zeros(n, dtype=bool)

    # HTF trend filter — BEARISH: wt1_D < wt2_D and RSI_D < threshold
    if cfg.htf_trend_filter_enabled:
        bear_mask = np.ones(n, dtype=bool)
        if cfg.htf_require_wt_D_bear:
            bear_mask &= (wt1_D < wt2_D)
        if cfg.htf_require_rsi_D_max > 0:
            bear_mask &= (rsi_D <= cfg.htf_require_rsi_D_max)
        entry_B &= bear_mask
        entry_C &= bear_mask
        entry_D &= bear_mask
        entry_G &= bear_mask

    # SMA50 regime filter — blocks ALL entries when price > SMA50_D (for shorts, want BELOW)
    if cfg.require_below_sma50_D:
        below_sma50 = close < sma50_D
        entry_A &= below_sma50
        entry_B &= below_sma50
        entry_C &= below_sma50
        entry_D &= below_sma50
        entry_E &= below_sma50
        entry_F &= below_sma50
        entry_G &= below_sma50

    # ── EXIT masks per path (ALL BULLISH — force cover) ───────────────────
    bull_wt_1h = _bull_cross(wt1_1h, wt2_1h)
    if cfg.exit_X1_bottomcatch_enabled:
        exit_X1 = (k15 < cfg.exit_X1_k15_max) & bull_wt_15
    else:
        exit_X1 = np.zeros(n, dtype=bool)
    # X4: bullish daily WT cross → cover
    exit_X4 = bull_wt_D if cfg.exit_X4_daily_bull_wt_enabled else np.zeros(n, dtype=bool)
    # X5: structural flip — bullish (HH/HL count on trigger TF)
    flip_count = np.zeros(n, dtype=np.int8)
    if cfg.exit_X5_structural_flip_enabled:
        from vec_paths.structure_hh_hl import _recent_events_count
        for s in ("price", "wt1", "wt2", "stoch_k", "dc_basis"):
            d = structure.get((s, cfg.path_A_trigger_tf))
            if d is None:
                continue
            ev = d["pivot_event"]
            bull = (ev == STRUCT_HH) | (ev == STRUCT_HL)
            flip_count += _recent_events_count(bull, cfg.exit_X5_window_bars)
        exit_X5 = flip_count >= cfg.exit_X5_min_count
    else:
        exit_X5 = np.zeros(n, dtype=bool)

    # ── Simulation loop (SHORT positions) ─────────────────────────────────
    capital = cfg.initial_capital
    pos_qty = 0.0
    avg_entry_price = 0.0
    entry_bar = -1
    lowest_close_in_trade = 0.0   # tracks best case for shorts (lower = more profit)
    highest_close_in_trade = 0.0  # tracks worst case (higher = more loss for shorts)
    pyramid_count = 0
    capital_in_trade = 0.0
    cooldown_until = 0
    frozen_dc_stop = 0.0  # dc_high frozen at entry — if price goes above, cover
    events: List[Dict] = []
    trade_returns: List[float] = []
    entry_path_counts: Dict[str, int] = {}
    exit_path_counts: Dict[str, int] = {}

    def _record_event(i, etype, qty, price, reason, value, extra_indicators=None):
        if return_events:
            ind = {
                "k15": round(float(k15[i]), 2),
                "k1h": round(float(k1h[i]), 2),
                "wt1_D": round(float(wt1_D[i]), 4),
                "wt2_D": round(float(wt2_D[i]), 4),
                "rsi_D": round(float(rsi_D[i]), 2),
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
            entry_path = None
            if entry_A[i]:
                entry_path = "A"
            elif entry_D[i]:
                entry_path = "D"
            elif entry_B[i]:
                entry_path = "B"
            elif entry_C[i]:
                entry_path = "C"
            elif entry_E[i]:
                entry_path = "E"
            elif entry_F[i]:
                entry_path = "F"
            elif entry_G[i]:
                entry_path = "G"
            if entry_path is not None:
                capital_in_trade = capital * cfg.full_entry_fraction
                pos_qty = capital_in_trade / px
                avg_entry_price = px
                entry_bar = i
                highest_close_in_trade = px
                lowest_close_in_trade = px
                pyramid_count = 0
                frozen_dc_stop = float(dc_high_4h[i]) if cfg.exit_X7_tech_stop_enabled else 0.0
                entry_path_counts[entry_path] = entry_path_counts.get(entry_path, 0) + 1
                _record_event(i, "OPEN", pos_qty, px, f"PATH_{entry_path}", capital_in_trade)
        else:
            # Update tracking (for shorts: lower=better, higher=worse)
            if px < lowest_close_in_trade:
                lowest_close_in_trade = px
            if px > highest_close_in_trade:
                highest_close_in_trade = px

            # PYRAMID: on new LL (price dropping further = more profit for shorts)
            if (cfg.pyramid_enabled
                and pyramid_count < cfg.max_pyramid_levels
                and new_pyramid_LL[i]):
                # For shorts: gain = (entry - current) / entry
                gain_since_entry = (avg_entry_price - px) / avg_entry_price * 100
                if gain_since_entry >= cfg.pyramid_min_gain_since_last_pct:
                    add_cap = capital * cfg.pyramid_add_fraction * (0.7 ** pyramid_count)
                    add_qty = add_cap / px
                    new_qty = pos_qty + add_qty
                    avg_entry_price = (avg_entry_price * pos_qty + px * add_qty) / new_qty
                    pos_qty = new_qty
                    capital_in_trade += add_cap
                    pyramid_count += 1
                    _record_event(i, "AUGMENT", add_qty, px, f"PYRAMID_{pyramid_count}", add_cap)

            # PYRAMID: price-breakdown (new low since entry)
            if (cfg.pyramid_enabled and cfg.pyramid_on_price_breakdown
                and pyramid_count < cfg.max_pyramid_levels
                and px <= lowest_close_in_trade * 0.999):
                gain_since_entry = (avg_entry_price - px) / avg_entry_price * 100
                if gain_since_entry >= cfg.pyramid_price_breakdown_min_gain:
                    add_cap = capital * cfg.pyramid_add_fraction * (0.7 ** pyramid_count)
                    add_qty = add_cap / px
                    new_qty = pos_qty + add_qty
                    avg_entry_price = (avg_entry_price * pos_qty + px * add_qty) / new_qty
                    pos_qty = new_qty
                    capital_in_trade += add_cap
                    pyramid_count += 1
                    _record_event(i, "AUGMENT", add_qty, px, f"PYR_BREAKDOWN_{pyramid_count}", add_cap)

            # EXITS — for shorts, loss = price going UP
            bars_in_trade = i - entry_bar
            exit_path = None
            # X7: Technical stop — frozen dc_HIGH + absolute ceiling (ALWAYS fires)
            if cfg.exit_X7_tech_stop_enabled:
                cur_gain = (avg_entry_price - px) / avg_entry_price * 100  # negative when price above entry
                if cur_gain < 0:  # losing money (price rallied)
                    _hit_dc = (frozen_dc_stop > 0 and px > frozen_dc_stop)
                    _hit_abs = (cur_gain < -cfg.exit_X7_abs_ceiling_pct)
                    if _hit_dc or _hit_abs:
                        exit_path = "X7"
            # X3: hard stop ATR (price rallies against short)
            if exit_path is None and cfg.exit_X3_hardstop_atr_mult > 0 and atr_15[i] > 0:
                stop_px = avg_entry_price + cfg.exit_X3_hardstop_atr_mult * atr_15[i]
                if px > stop_px:
                    exit_path = "X3"
            # Min hold gate
            if exit_path is None and bars_in_trade >= cfg.min_hold_bars:
                # X2: trailing from lowest (for shorts, cover if price bounces X% from low)
                if cfg.exit_X2_trailing_pct > 0:
                    trail_px = lowest_close_in_trade * (1 + cfg.exit_X2_trailing_pct / 100)
                    if px > trail_px:
                        if not cfg.exit_X2_require_profit or px < avg_entry_price * 0.995 or pyramid_count > 0:
                            exit_path = "X2"
                if exit_path is None and exit_X1[i]:
                    exit_path = "X1"
                if exit_path is None and exit_X4[i]:
                    exit_path = "X4"
                if exit_path is None and exit_X5[i]:
                    if cfg.exit_X5_min_hold_bars <= 0 or bars_in_trade >= cfg.exit_X5_min_hold_bars:
                        exit_path = "X5"
            if exit_path is None and cfg.exit_X6_time_stop_bars > 0:
                if bars_in_trade >= cfg.exit_X6_time_stop_bars:
                    gain = (avg_entry_price - px) / avg_entry_price * 100
                    if gain < cfg.exit_X6_min_gain_pct:
                        exit_path = "X6"

            if exit_path is not None:
                # Short PnL: (entry - exit) / entry
                ret_pct = (avg_entry_price - px) / avg_entry_price * 100 - cfg.round_trip_cost_pct
                pnl_dollars = capital_in_trade * (ret_pct / 100)
                capital += pnl_dollars
                trade_returns.append(ret_pct)
                exit_path_counts[exit_path] = exit_path_counts.get(exit_path, 0) + 1
                _record_event(i, "CLOSE", pos_qty, px, exit_path, pos_qty * px,
                              extra_indicators={"pnl_pct": round(ret_pct, 4),
                                                "bars_held": int(bars_in_trade),
                                                "entry_price": round(avg_entry_price, 4)})
                pos_qty = 0.0
                avg_entry_price = 0.0
                entry_bar = -1
                capital_in_trade = 0.0
                cd = cfg.cooldown_bars_after_exit
                if exit_path == "X4" and cfg.x4_exit_extended_cooldown > 0:
                    cd = max(cd, cfg.x4_exit_extended_cooldown)
                cooldown_until = i + cd

    # MtM final open position
    if pos_qty > 0:
        mark = float(close[-1])
        ret_pct = (avg_entry_price - mark) / avg_entry_price * 100 - cfg.round_trip_cost_pct
        capital += capital_in_trade * (ret_pct / 100)
        trade_returns.append(ret_pct)
        exit_path_counts["MTM"] = exit_path_counts.get("MTM", 0) + 1
        _record_event(n - 1, "CLOSE", pos_qty, mark, "MTM_FINAL", pos_qty * mark,
                      extra_indicators={"pnl_pct": round(ret_pct, 4),
                                        "bars_held": int(n - 1 - entry_bar),
                                        "entry_price": round(avg_entry_price, 4)})

    # B&H SHORT multiplier: (start_price - end_price) / start_price + 1
    # If price dropped 50%, short B&H = 1.5x. If price rose 50%, short B&H = 0.5x
    bh_short_mult = (2.0 - float(close[-1]) / float(close[0])) if close[0] > 0 else 1.0
    compound_mult = capital / cfg.initial_capital

    return {
        "sym": symbol,
        "n_bars": n,
        "years": float((ts[-1] - ts[0]) / (365.25 * 24 * 3600)),
        "bh_mult": bh_short_mult,
        "compound_mult": compound_mult,
        "ratio_vs_bh": compound_mult / max(bh_short_mult, 0.01) if bh_short_mult > 0 else float('inf'),
        "trades": len(trade_returns),
        "wr_pct": sum(1 for r in trade_returns if r > 0) / max(len(trade_returns), 1) * 100,
        "avg_gain_pct": float(np.mean(trade_returns)) if trade_returns else 0.0,
        "entry_paths": entry_path_counts,
        "exit_paths": exit_path_counts,
        "trade_returns": trade_returns,
        "events": events,
    }


def run_eval(cfg: ShortCfg, symbols: List[str], mode: str, start_ts: int,
             label: str = "") -> Dict[str, Any]:
    results = []
    print(f"\n=== {label} ===" if label else "")
    print(f"{'SYM':<7}{'YRS':<6}{'BARS':<8}{'TRADES':<8}{'WR%':<7}{'AVG%/tr':<10}"
          f"{'SH_BH':<10}{'STRAT':<10}{'xBH':<8}{'PATHS'}")
    for sym in symbols:
        r = simulate_aggressive_short(sym, mode, cfg, start_ts=start_ts)
        if r.get("skip"):
            continue
        results.append(r)
        paths = "+".join(f"{k}{v}" for k, v in sorted(r["entry_paths"].items()))
        print(f"{sym:<7}{r['years']:<6.2f}{r['n_bars']:<8}{r['trades']:<8}{r['wr_pct']:<7.1f}"
              f"{r['avg_gain_pct']:<+10.2f}{r['bh_mult']:<10.2f}{r['compound_mult']:<10.2f}"
              f"{r['ratio_vs_bh']:<8.2f}{paths}")
    return {"results": results}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="BTCUSDC,ETHUSDC,SOLUSDC,XRPUSDC")
    ap.add_argument("--mode", default="crypto")
    ap.add_argument("--start", default="2022-01-01")
    ap.add_argument("--label", default="v4_short")
    args = ap.parse_args()
    start_ts = int(datetime.datetime.strptime(args.start, "%Y-%m-%d")
                   .replace(tzinfo=datetime.timezone.utc).timestamp())
    symbols = args.symbols.split(",")
    cfg = ShortCfg()
    run_eval(cfg, symbols, args.mode, start_ts, label=args.label)


if __name__ == "__main__":
    main()
