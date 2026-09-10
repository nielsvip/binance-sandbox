#!/usr/bin/env python3
"""
ez_reentry_vectorized.py — Vectorized reentry evaluation for V8 backtest.

Replaces the 1,177-line scalar evaluate_reentry() with numpy-vectorized
pre-computation. Live trading still uses the scalar path — this is V8 only.

Architecture:
  1. precompute_reentry_signals(npz_data, is_long) → dict of boolean arrays
     Called ONCE per symbol per backtest run. O(N) where N = total bars.
  2. lookup_reentry(precomputed, bar_idx, position_state) → Signal or None
     Called per bar per position. O(1) — just array index lookups.

The scalar evaluate_reentry takes ~3s per symbol per 3m bar (1,177 lines,
15+ sub-functions, await ii(), await price(), safe_fetch_float × hundreds).
The vectorized version targets <1ms effective per bar (vectorized setup
amortized across all bars).

Usage in backtest_v8_engine.py apply_patches():
  if os.environ.get("V8_VECTORIZED_REENTRY", "0") == "1":
      from ez_reentry_vectorized import VectorizedReentryEvaluator
      _vec_eval = VectorizedReentryEvaluator(stores, config)
      ez_manage.evaluate_reentry = _vec_eval.evaluate  # drop-in replacement
"""
import numpy as np
from dataclasses import dataclass
from typing import Dict, Optional, Tuple


@dataclass
class ReentrySignal:
    action: str = "REENTRY"
    reason: str = ""
    conviction: float = 0.0
    quantity: float = 0.0


def _safe_arr(npz: dict, key: str, n: int, default: float = 0.0) -> np.ndarray:
    v = npz.get(key)
    if v is not None and isinstance(v, np.ndarray) and len(v) == n:
        return v.astype(np.float64)
    return np.full(n, default, dtype=np.float64)


def _safe_bool_arr(npz: dict, key: str, n: int, default: bool = False) -> np.ndarray:
    v = npz.get(key)
    if v is not None and isinstance(v, np.ndarray) and len(v) == n:
        return v.astype(bool)
    return np.full(n, default, dtype=bool)


class VectorizedReentryEvaluator:
    """Pre-computes all reentry signals as numpy arrays for O(1) per-bar lookup."""

    def __init__(self, stores: dict, config):
        self.config = config
        self._precomputed: Dict[str, dict] = {}
        for sym, store in stores.items():
            npz = store._data if hasattr(store, '_data') else {}
            n = len(npz.get('close_3m', npz.get('close_5m', np.array([]))))
            if n == 0:
                continue
            self._precomputed[sym] = {
                "long": self._precompute(npz, n, is_long=True),
                "short": self._precompute(npz, n, is_long=False),
                "n": n,
            }

    def _precompute(self, npz: dict, n: int, is_long: bool) -> dict:
        """Vectorize the critical decision blocks from evaluate_reentry."""
        wt1_3m = _safe_arr(npz, 'wt1_3m', n)
        wt2_3m = _safe_arr(npz, 'wt2_3m', n)
        wt1_15m = _safe_arr(npz, 'wt1_15m', n)
        wt2_15m = _safe_arr(npz, 'wt2_15m', n)
        wt1_1h = _safe_arr(npz, 'wt1_1h', n)
        wt2_1h = _safe_arr(npz, 'wt2_1h', n)
        wt1_4h = _safe_arr(npz, 'wt1_4h', n)
        wt2_4h = _safe_arr(npz, 'wt2_4h', n)
        wt1_D = _safe_arr(npz, 'wt1_D', n)
        wt2_D = _safe_arr(npz, 'wt2_D', n)
        wt_vel_15m = _safe_arr(npz, 'wt_velocity_15m', n)
        wt_bull_3m = _safe_bool_arr(npz, 'wt_bullish_3m', n)
        wt_bull_15m = _safe_bool_arr(npz, 'wt_bullish_15m', n)
        wt_bull_1h = _safe_bool_arr(npz, 'wt_bullish_1h', n)
        wt_bull_4h = _safe_bool_arr(npz, 'wt_bullish_4h', n)
        k_3m = _safe_arr(npz, 'stoch_k_3m', n, 50.0)
        d_3m = _safe_arr(npz, 'stoch_d_3m', n, 50.0)
        k_15m = _safe_arr(npz, 'stoch_k_15m', n, 50.0)
        d_15m = _safe_arr(npz, 'stoch_d_15m', n, 50.0)
        k_1h = _safe_arr(npz, 'stoch_k_1h', n, 50.0)
        close = _safe_arr(npz, 'close_3m', n)
        if close.sum() == 0:
            close = _safe_arr(npz, 'close_5m', n)
        ha_3m_str = npz.get('ha_3m')
        dc_high_4h = _safe_arr(npz, 'dc_high_4h', n)
        dc_low_4h = _safe_arr(npz, 'dc_low_4h', n)
        dc_high_15m = _safe_arr(npz, 'dc_high_15m', n)
        dc_low_15m = _safe_arr(npz, 'dc_low_15m', n)
        high_15m = _safe_arr(npz, 'high_15m', n)
        high_15m_prev = np.roll(high_15m, 1); high_15m_prev[0] = high_15m[0]
        low_15m = _safe_arr(npz, 'low_15m', n)
        low_15m_prev = np.roll(low_15m, 1); low_15m_prev[0] = low_15m[0]

        # --- Block 1: WT 2/3 in favor (lines 16146-16177) ---
        if is_long:
            wt_fav = (wt1_3m > wt2_3m).astype(int) + (wt1_15m > wt2_15m).astype(int) + (wt1_1h > wt2_1h).astype(int)
            htf_fav = (wt1_1h > wt2_1h).astype(int) + (wt1_4h > wt2_4h).astype(int) + (wt1_D > wt2_D).astype(int)
        else:
            wt_fav = (wt1_3m < wt2_3m).astype(int) + (wt1_15m < wt2_15m).astype(int) + (wt1_1h < wt2_1h).astype(int)
            htf_fav = (wt1_1h < wt2_1h).astype(int) + (wt1_4h < wt2_4h).astype(int) + (wt1_D < wt2_D).astype(int)
        rally_k15m_max = float(getattr(self.config, 'REENTRY_RALLY_K15M_MAX', 100.0))
        rally_htf_min = int(getattr(self.config, 'REENTRY_RALLY_HTF_MIN', 1))
        wt_2of3_ok = wt_fav >= 2
        if rally_k15m_max < 100.0:
            k15m_ok = (k_15m < rally_k15m_max) if is_long else (k_15m > (100.0 - rally_k15m_max))
            wt_2of3_ok = wt_2of3_ok & k15m_ok
        wt_2of3_ok = wt_2of3_ok & (htf_fav >= rally_htf_min)

        # --- Block 2: BC_156 guaranteed bottom bounce (lines 16178-16220) ---
        if is_long:
            wt15m_bouncing = (wt1_15m < -20) & (wt_vel_15m > 0)
            trend_1h_ok = wt1_1h > wt2_1h
        else:
            wt15m_bouncing = (wt1_15m > 20) & (wt_vel_15m < 0)
            trend_1h_ok = wt1_1h < wt2_1h
        wt_confirm_count = wt_bull_3m.astype(int) + wt_bull_15m.astype(int) + wt_bull_1h.astype(int) + wt_bull_4h.astype(int)
        if not is_long:
            wt_confirm_count = (~wt_bull_3m).astype(int) + (~wt_bull_15m).astype(int) + (~wt_bull_1h).astype(int) + (~wt_bull_4h).astype(int)
        bottom_bounce_ok = wt15m_bouncing & trend_1h_ok & (wt_confirm_count >= 2)
        guaranteed_bottom = bottom_bounce_ok & np.bool_(getattr(self.config, 'LEGACY_REENTRY_GUARANTEED_BOTTOM', False))

        # --- Block 3: Trend gate (lines 16241-16270) ---
        if is_long:
            trend_ok = (wt_bull_3m | (k_3m > d_3m)) & (wt_bull_15m | (k_15m > d_15m) | (high_15m > high_15m_prev))
            bounce_deep = (k_3m < 15) & (k_3m > d_3m)
        else:
            trend_ok = (~wt_bull_3m | (k_3m < d_3m)) & (~wt_bull_15m | (k_15m < d_15m) | (low_15m < low_15m_prev))
            bounce_deep = (k_3m > 85) & (k_3m < d_3m)
        trend_gate_pass = trend_ok | bounce_deep

        # --- Block 4: DC breakout reentry (simplified, lines ~16400-16450) ---
        if is_long:
            dc_breakout = (close > dc_high_15m) & (k_3m > d_3m) & wt_bull_15m
        else:
            dc_breakout = (close < dc_low_15m) & (k_3m < d_3m) & ~wt_bull_15m

        # --- Block 5: DAEMON_PRICE_CROSS with USER 2026-09-03 gates (wt cross, stochk, wt_dc, dc high) ---
        # Must match ez_reentry_daemon.py gating so vector stays in tandem with live/backtest.
        wt1_3m_prev = np.roll(wt1_3m, 1); wt1_3m_prev[0] = wt1_3m[0]
        wt2_3m_prev = np.roll(wt2_3m, 1); wt2_3m_prev[0] = wt2_3m[0]
        stoch_k_3m_arr = k_3m
        wt_dc_15m_arr = _safe_arr(npz, 'wt_dc_15m', n, 0.0)
        wt_dc_15m_prev_arr = np.roll(wt_dc_15m_arr, 1); wt_dc_15m_prev_arr[0] = wt_dc_15m_arr[0]
        dc_high_15m_arr = dc_high_15m
        dc_low_15m_arr = dc_low_15m
        atr_15m_arr = _safe_arr(npz, 'atr_15m', n, 0.0)
        # indicator presence (startup block) — require 6/9 non-zero per bar
        try:
            ind_present = ((wt1_3m != 0).astype(int) + (wt2_3m != 0).astype(int) + (wt1_3m_prev != 0).astype(int) + (wt2_3m_prev != 0).astype(int) + (stoch_k_3m_arr != 0).astype(int) + (wt_dc_15m_arr != 0).astype(int) + (wt_dc_15m_prev_arr != 0).astype(int) + (dc_high_15m_arr != 0).astype(int) + (atr_15m_arr != 0).astype(int))
            ind_ok = ind_present >= 6
        except Exception:
            ind_ok = np.full(n, True, dtype=bool)
        if is_long:
            wt_cross_arr = (wt1_3m > wt2_3m) & (wt1_3m_prev <= wt2_3m_prev)
            stoch_ok_arr = stoch_k_3m_arr < 70
            wt_dc_ok_arr = wt_dc_15m_arr >= (wt_dc_15m_prev_arr - 0.5)
            not_close_dc_arr = (dc_high_15m_arr <= 0) | (atr_15m_arr <= 0) | (close < dc_high_15m_arr - 0.5 * atr_15m_arr)
        else:
            wt_cross_arr = (wt1_3m < wt2_3m) & (wt1_3m_prev >= wt2_3m_prev)
            stoch_ok_arr = stoch_k_3m_arr > 30
            wt_dc_ok_arr = wt_dc_15m_arr <= (wt_dc_15m_prev_arr + 0.5)
            not_close_dc_arr = (dc_low_15m_arr <= 0) | (atr_15m_arr <= 0) | (close > dc_low_15m_arr + 0.5 * atr_15m_arr)
        daemon_price_cross_ok = ind_ok & wt_cross_arr & stoch_ok_arr & wt_dc_ok_arr & not_close_dc_arr

        return {
            "wt_2of3_ok": wt_2of3_ok,
            "guaranteed_bottom": guaranteed_bottom,
            "wt_confirm_count": wt_confirm_count,
            "trend_gate_pass": trend_gate_pass,
            "dc_breakout": dc_breakout,
            "daemon_price_cross_ok": daemon_price_cross_ok,
            "wt_cross_arr": wt_cross_arr,
            "stoch_ok_arr": stoch_ok_arr,
            "wt_fav": wt_fav,
            "htf_fav": htf_fav,
            "close": close,
            "k_3m": k_3m,
            "k_15m": k_15m,
        }

    async def evaluate(self, ctx: dict):
        """Drop-in replacement for ez_manage.evaluate_reentry.
        Uses pre-computed arrays for O(1) lookups instead of 1177 lines of scalar code."""
        position_key = ctx.get('position_key', '')
        symbol = ctx.get('symbol', '')
        gain = ctx.get('gain', 0.0)
        is_long = ctx.get('position_side') == "LONG"
        trade_manager = ctx.get('trade_manager')
        position = trade_manager.positions.get(position_key) if trade_manager else None
        if not position:
            return None
        current_price = float(getattr(position, 'mark_price', 0) or ctx.get('current_price', 0) or 0)
        if current_price <= 0:
            return None
        positionAmt = abs(float(getattr(position, 'positionAmt', 0)))
        position_value = positionAmt * current_price
        if position_value > 2 * float(getattr(self.config, 'MIN_POSITION_SIZE', 55.0)):
            return None
        pre = self._precomputed.get(symbol)
        if not pre:
            return None
        side_key = "long" if is_long else "short"
        s = pre[side_key]
        bar_idx = ctx.get('_v8_bar_idx', 0)
        if bar_idx >= pre["n"]:
            bar_idx = pre["n"] - 1
        start_size = float(getattr(self.config, 'START_POSITION_SIZE', 18.0))
        re_qty = start_size / max(current_price, 1e-9)
        if s["wt_2of3_ok"][bar_idx]:
            return ReentrySignal(
                action="REENTRY",
                reason=f"WT_2of3_REENTRY_{int(s['wt_fav'][bar_idx])}of3_htf{int(s['htf_fav'][bar_idx])}",
                conviction=85.0,
                quantity=re_qty,
            )
        if s["guaranteed_bottom"][bar_idx]:
            return ReentrySignal(
                action="REENTRY",
                reason=f"GUARANTEED_BOTTOM_150pct_vec",
                conviction=85.0,
                quantity=re_qty * 1.5,
            )
        if not s["trend_gate_pass"][bar_idx]:
            return None
        if s["dc_breakout"][bar_idx]:
            return ReentrySignal(
                action="REENTRY",
                reason=f"DC_BREAKOUT_REENTRY_vec",
                conviction=88.0,
                quantity=re_qty,
            )
        if s["daemon_price_cross_ok"][bar_idx]:
            return ReentrySignal(
                action="REENTRY",
                reason=f"DAEMON_PRICE_CROSS_REENTRY_vec_wt_cross_stoch{int(s['k_3m'][bar_idx])}",
                conviction=82.0,
                quantity=re_qty,
            )
        return None
