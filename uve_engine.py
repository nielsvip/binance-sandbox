# -*- coding: utf-8 -*-
"""uve_engine.py — Core Unified Vectorized Execution Engine.

Vectorized precomputations + sequential bar processing for 100% bit-exact parity between backtests and live trading.

Parity gates added 2026-05-27:
  UVE_NOLOSS_ENABLED   — block loss exits unless R1/hopeless/max_hold fires (mirrors UNIVERSAL_NOLOSS_GATE)
  UVE_R1_WINDOW_BARS   — DC-channel emergency exit in newborn window (mirrors R1_DC_LOW4 logic)
  UVE_HOPELESS_BARS    — consecutive bars outside DC channel triggers DC_HOPELESS exit
  UVE_MAX_HOLD_BARS    — absolute hold limit prevents infinite no-exit hold under NOLOSS
"""
from __future__ import annotations
import numpy as np
from dataclasses import dataclass
from typing import Dict, List, Tuple, Any

@dataclass
class TradeEvent:
    idx: int
    ts: float
    type: str
    price: float
    qty: float
    reason: str

def evaluate_uve_signals(npz: Dict[str, np.ndarray], is_long: bool, mode: str, config: Any = None) -> Dict[str, np.ndarray]:
    close_field = "close_5m" if mode == "tradier" else "close_3m"
    close = np.asarray(npz.get(close_field, npz.get("close", np.zeros(0, dtype=np.float32))), dtype=np.float32)
    n = len(close)
    atr = np.asarray(npz.get("atr_15m", np.zeros(n, dtype=np.float32)), dtype=np.float32)
    wt1_1h = np.asarray(npz.get("wt1_1h", np.zeros(n, dtype=np.float32)), dtype=np.float32)
    wt2_1h = np.asarray(npz.get("wt2_1h", np.zeros(n, dtype=np.float32)), dtype=np.float32)
    wt1_4h = np.asarray(npz.get("wt1_4h", np.zeros(n, dtype=np.float32)), dtype=np.float32)
    wt2_4h = np.asarray(npz.get("wt2_4h", np.zeros(n, dtype=np.float32)), dtype=np.float32)
    wt1_D = np.asarray(npz.get("wt1_D", np.zeros(n, dtype=np.float32)), dtype=np.float32)
    wt2_D = np.asarray(npz.get("wt2_D", np.zeros(n, dtype=np.float32)), dtype=np.float32)
    wt1_W = np.asarray(npz.get("wt1_W", np.zeros(n, dtype=np.float32)), dtype=np.float32)
    wt2_W = np.asarray(npz.get("wt2_W", np.zeros(n, dtype=np.float32)), dtype=np.float32)
    # Daily + Weekly trend — never short against the weekly macro trend
    daily_bullish = wt1_D > wt2_D
    daily_bearish = wt1_D < wt2_D
    weekly_bullish = wt1_W > wt2_W
    weekly_bearish = wt1_W < wt2_W
    # LONG: Daily bullish AND (4h OR 1h) — allows entries on dips within uptrend
    htf_bullish = daily_bullish & ((wt1_4h > wt2_4h) | (wt1_1h > wt2_1h))
    # SHORT: Daily AND Weekly both bearish AND (4h OR 1h) — triple confirmation to prevent
    # shorting during corrections inside a bull trend (SNDK style bull-run SHORT disaster)
    htf_bearish = daily_bearish & weekly_bearish & ((wt1_4h < wt2_4h) | (wt1_1h < wt2_1h))
    macro_trend = htf_bullish if is_long else htf_bearish
    stoch_k_15m = np.asarray(npz.get("stoch_k_15m", np.zeros(n, dtype=np.float32)), dtype=np.float32)
    stoch_k_1h = np.asarray(npz.get("stoch_k_1h", np.zeros(n, dtype=np.float32)), dtype=np.float32)
    stoch_thresh = float(getattr(config, "UVE_STOCH_THRESH", 90.0) or 90.0)
    anti_parabolic = (stoch_k_15m < stoch_thresh) & (stoch_k_1h < stoch_thresh)
    wt1_base = np.asarray(npz.get("wt1_3m" if mode == "crypto" else "wt1_5m", np.zeros(n, dtype=np.float32)), dtype=np.float32)
    wt2_base = np.asarray(npz.get("wt2_3m" if mode == "crypto" else "wt2_5m", np.zeros(n, dtype=np.float32)), dtype=np.float32)
    wt1_15m = np.asarray(npz.get("wt1_15m", np.zeros(n, dtype=np.float32)), dtype=np.float32)
    wt2_15m = np.asarray(npz.get("wt2_15m", np.zeros(n, dtype=np.float32)), dtype=np.float32)
    dc_high_15m = np.asarray(npz.get("dc_high_15m", np.zeros(n, dtype=np.float32)), dtype=np.float32)
    dc_low_15m = np.asarray(npz.get("dc_low_15m", np.zeros(n, dtype=np.float32)), dtype=np.float32)
    dc_basis_15m = np.asarray(npz.get("dc_basis_15m", np.zeros(n, dtype=np.float32)), dtype=np.float32)
    strat_mode = getattr(config, "UVE_STRATEGY_MODE", "legacy")
    if strat_mode == "legacy":
        wt_cross = (wt1_base > wt2_base) if is_long else (wt1_base < wt2_base)
        # For LONG: breakout bypasses anti_parabolic (rally entries are valid even when overbought)
        dc_high_15m_prev = np.roll(dc_high_15m, 1); dc_high_15m_prev[0] = dc_high_15m[0]
        dc_low_15m_prev = np.roll(dc_low_15m, 1); dc_low_15m_prev[0] = dc_low_15m[0]
        if is_long:
            is_breakout = (close > dc_high_15m_prev) & (np.roll(close, 1) <= dc_high_15m_prev)
            entry_allowed = macro_trend & (anti_parabolic | is_breakout) & wt_cross
            is_breakout_entry = is_breakout & entry_allowed
        else:
            # SHORT: macro_trend already requires daily_bearish — no breakout bypass
            entry_allowed = macro_trend & anti_parabolic & wt_cross
            is_breakout_entry = np.zeros(n, dtype=bool)
        exit_signal = np.zeros(n, dtype=bool)
    else:
        wt1_base_prev = np.roll(wt1_base, 1)
        wt1_base_prev[0] = wt1_base[0]
        wt2_base_prev = np.roll(wt2_base, 1)
        wt2_base_prev[0] = wt2_base[0]
        if is_long:
            wt_crossover = (wt1_base > wt2_base) & (wt1_base_prev <= wt2_base_prev)
            wt_crossunder = (wt1_base < wt2_base) & (wt1_base_prev >= wt2_base_prev)
            oversold_val = float(getattr(config, "UVE_OVERSOLD_THRES", -40.0) or -40.0)
            # Bottom buy: oversold WT cross with 15m agreement
            buy_bottom = wt_crossover & (wt1_base < oversold_val) & (wt1_15m > wt2_15m)
            bounce_moving_htf = htf_bullish
            reenter_bounce = wt_crossover & (wt1_15m > wt2_15m) & bounce_moving_htf
            dc_high_15m_prev = np.roll(dc_high_15m, 1)
            dc_high_15m_prev[0] = dc_high_15m[0]
            is_breakout = (close > dc_high_15m_prev) & (np.roll(close, 1) <= dc_high_15m_prev)
            breakout_enabled = bool(getattr(config, "UVE_BREAKOUT_ENABLED", True))
            # Breakout entry: valid when daily is bullish (allows entries even when overbought)
            buy_breakout = (is_breakout & daily_bullish) if breakout_enabled else np.zeros(n, dtype=bool)
            entry_allowed = buy_bottom | reenter_bounce | buy_breakout
            is_breakout_entry = buy_breakout & entry_allowed
            overbought_val = float(getattr(config, "UVE_OVERBOUGHT_THRES", 40.0) or 40.0)
            exit_signal = wt_crossunder & (wt1_base > overbought_val)
        else:
            wt_crossover = (wt1_base < wt2_base) & (wt1_base_prev >= wt2_base_prev)
            wt_crossunder = (wt1_base > wt2_base) & (wt1_base_prev <= wt2_base_prev)
            oversold_val = float(getattr(config, "UVE_OVERSOLD_THRES", 40.0) or 40.0)
            # SHORT bottom: overbought cross-under with Daily AND 4h/1h bearish confirmation
            buy_bottom = wt_crossover & (wt1_base > oversold_val) & htf_bearish
            # SHORT reenter: requires Daily bearish AND 15m bearish AND HTF confirms
            reenter_bounce = wt_crossover & (wt1_15m < wt2_15m) & htf_bearish
            dc_low_15m_prev = np.roll(dc_low_15m, 1)
            dc_low_15m_prev[0] = dc_low_15m[0]
            is_breakout = (close < dc_low_15m_prev) & (np.roll(close, 1) >= dc_low_15m_prev)
            breakout_enabled = bool(getattr(config, "UVE_BREAKOUT_ENABLED", True))
            # SHORT breakout: only valid when daily is bearish
            buy_breakout = (is_breakout & daily_bearish) if breakout_enabled else np.zeros(n, dtype=bool)
            entry_allowed = buy_bottom | reenter_bounce | buy_breakout
            is_breakout_entry = buy_breakout & entry_allowed
            overbought_val = float(getattr(config, "UVE_OVERBOUGHT_THRES", -40.0) or -40.0)
            exit_signal = wt_crossunder & (wt1_base < overbought_val)
    return {
        "entry_allowed": entry_allowed,
        "close": close,
        "atr": atr,
        "macro_trend": macro_trend,
        "daily_bullish": daily_bullish,
        "daily_bearish": daily_bearish,
        "exit_signal": exit_signal,
        "is_breakout_entry": is_breakout_entry,
        "dc_basis_15m": dc_basis_15m,
        "dc_high_15m": dc_high_15m,
        "dc_low_15m": dc_low_15m,
    }

def simulate_uve(npz: Dict[str, np.ndarray], is_long: bool, mode: str, config: Any = None) -> Dict[str, Any]:
    signals = evaluate_uve_signals(npz, is_long, mode, config)
    close, entry_allowed, atr = signals["close"], signals["entry_allowed"], signals["atr"]
    exit_signal = signals.get("exit_signal", np.zeros(len(close), dtype=bool))
    is_breakout_entry = signals.get("is_breakout_entry", np.zeros(len(close), dtype=bool))
    dc_basis_15m = signals.get("dc_basis_15m", np.zeros(len(close), dtype=np.float32))
    dc_high_15m = signals.get("dc_high_15m", np.zeros(len(close), dtype=np.float32))
    dc_low_15m = signals.get("dc_low_15m", np.zeros(len(close), dtype=np.float32))
    # Prev-bar DC channels for R1 emergency check (no lookahead)
    dc_high_15m_prev = np.roll(dc_high_15m, 1); dc_high_15m_prev[0] = dc_high_15m[0]
    dc_low_15m_prev = np.roll(dc_low_15m, 1); dc_low_15m_prev[0] = dc_low_15m[0]
    n, events, returns = len(close), [], []
    ts_arr = npz.get("timestamps", npz.get("timestamp_3m", npz.get("timestamp_5m", npz.get("timestamp_15m"))))
    # --- Config params ---
    ppl_gain_pct = float(getattr(config, "UVE_PPL_GAIN_PCT", 1.5) or 1.5)
    atr_mult = float(getattr(config, "UVE_ATR_MULT", 2.0) or 2.0)
    augment_gain_pct = float(getattr(config, "UVE_AUGMENT_GAIN_PCT", 3.0) or 3.0)
    tight_breakout_exit = bool(getattr(config, "UVE_TIGHT_BREAKOUT_EXIT", True))
    slippage_buffer_pct = float(getattr(config, "UVE_BREAKOUT_SLIPPAGE_BUFFER_PCT", 0.1) or 0.1)
    min_cooldown_bars = int(getattr(config, "UVE_MIN_COOLDOWN_BARS", 12) or 12)
    # --- Parity gates (mirrors live UNIVERSAL_NOLOSS_GATE + R1 + DC_HOPELESS) ---
    noloss_enabled = bool(getattr(config, "UVE_NOLOSS_ENABLED", True))
    r1_window_bars = int(getattr(config, "UVE_R1_WINDOW_BARS", 12) or 12)
    hopeless_threshold = int(getattr(config, "UVE_HOPELESS_BARS", 6) or 6)
    # After UVE_MAX_HOLD_BARS bars, MTM-close regardless of NOLOSS (prevents infinite hold)
    max_hold_bars = int(getattr(config, "UVE_MAX_HOLD_BARS", 500) or 500)
    # --- Position state ---
    position_active = False
    entry_price, peak_price, ppl_fired, qty, last_augment_price = 0.0, 0.0, False, 0.0, 0.0
    entered_via_breakout = False
    entry_idx = 0
    hopeless_count = 0
    last_close_idx = -(min_cooldown_bars + 1)
    for i in range(n):
        mark = float(close[i])
        current_atr = float(atr[i]) if i < len(atr) else 0.0
        ts_val = float(ts_arr[i]) if ts_arr is not None and i < len(ts_arr) else float(i)
        if not position_active:
            if (i - last_close_idx >= min_cooldown_bars) and entry_allowed[i]:
                position_active = True
                entry_price, peak_price, ppl_fired, qty, last_augment_price = mark, mark, False, 1.0, mark
                entered_via_breakout = bool(is_breakout_entry[i])
                entry_idx = i
                hopeless_count = 0
                events.append(TradeEvent(idx=i, ts=ts_val, type="OPEN", price=mark, qty=qty, reason="UVE_WT_ENTRY" if not entered_via_breakout else "UVE_BREAKOUT_ENTRY"))
        else:
            gain_pct = (mark - entry_price) / entry_price * 100.0 if is_long else (entry_price - mark) / entry_price * 100.0
            peak_price = max(peak_price, mark) if is_long else min(peak_price, mark)
            age_bars = i - entry_idx
            # PPL step 1: reduce 50% at ppl_gain_pct
            if gain_pct >= ppl_gain_pct and not ppl_fired:
                ppl_fired = True
                qty = qty * 0.5
                events.append(TradeEvent(idx=i, ts=ts_val, type="REDUCE", price=mark, qty=qty, reason="UVE_PPL_STEP1"))
            # Trail / stop
            trail_stop = (peak_price - atr_mult * current_atr) if is_long else (peak_price + atr_mult * current_atr)
            stop_price = (entry_price * 1.0002) if ppl_fired else trail_stop
            # Breakout failure exit
            breakout_failed = False
            if entered_via_breakout and tight_breakout_exit:
                if is_long:
                    breakout_failed = (mark < entry_price * (1.0 - slippage_buffer_pct / 100.0)) or (mark < float(dc_basis_15m[i]))
                else:
                    breakout_failed = (mark > entry_price * (1.0 + slippage_buffer_pct / 100.0)) or (mark > float(dc_basis_15m[i]))
            # Normal exit signals
            should_exit = (mark < stop_price) if is_long else (mark > stop_price)
            should_exit = should_exit or bool(exit_signal[i]) or breakout_failed
            # ---- PARITY GATES ----
            # R1: DC channel emergency exit in newborn window (mirrors R1_DC_LOW4_3M_EMERGENCY)
            r1_fire = False
            if age_bars < r1_window_bars:
                dc_low_prev = float(dc_low_15m_prev[i])
                dc_high_prev = float(dc_high_15m_prev[i])
                if is_long and dc_low_prev > 0 and mark < dc_low_prev:
                    r1_fire = True
                elif not is_long and dc_high_prev > 0 and mark > dc_high_prev:
                    r1_fire = True
            # DC_HOPELESS: price outside DC channel for hopeless_threshold consecutive bars (mirrors DC_HOPELESS_EXIT)
            hopeless_fire = False
            dc_low_cur = float(dc_low_15m[i])
            dc_high_cur = float(dc_high_15m[i])
            if is_long:
                if dc_low_cur > 0 and mark < dc_low_cur:
                    hopeless_count += 1
                    if hopeless_count >= hopeless_threshold:
                        hopeless_fire = True
                else:
                    hopeless_count = 0
            else:
                if dc_high_cur > 0 and mark > dc_high_cur:
                    hopeless_count += 1
                    if hopeless_count >= hopeless_threshold:
                        hopeless_fire = True
                else:
                    hopeless_count = 0
            # Max hold: prevents infinite hold under NOLOSS (no real live equivalent — safety valve)
            max_hold_fire = (age_bars >= max_hold_bars)
            # NOLOSS gate: block loss exits unless a bypass condition fires
            # Mirrors UNIVERSAL_NOLOSS_GATE: R1 / DC_HOPELESS / max_hold bypass the hold
            bypass_noloss = r1_fire or hopeless_fire or max_hold_fire
            if bypass_noloss:
                should_exit = True
            elif noloss_enabled and gain_pct < 0:
                should_exit = False
            if should_exit:
                returns.append(gain_pct)
                reason_str = "UVE_ATR_TRAIL" if not ppl_fired else "UVE_PPL_EXIT"
                if r1_fire:
                    reason_str = "UVE_R1_DC_EMERGENCY"
                elif hopeless_fire:
                    reason_str = "UVE_DC_HOPELESS"
                elif max_hold_fire:
                    reason_str = "UVE_MAX_HOLD_TIMEOUT"
                elif breakout_failed:
                    reason_str = "UVE_BREAKOUT_FAIL_EXIT"
                elif exit_signal[i]:
                    reason_str = "UVE_WT_CORRECTION_EXIT"
                events.append(TradeEvent(idx=i, ts=ts_val, type="CLOSE", price=mark, qty=qty, reason=reason_str))
                position_active = False
                entry_price, peak_price, ppl_fired, qty, entered_via_breakout = 0.0, 0.0, False, 0.0, False
                hopeless_count = 0
                last_close_idx = i
            elif entry_allowed[i] and not ppl_fired:
                augment_gain = (mark - last_augment_price) / last_augment_price * 100.0 if is_long else (last_augment_price - mark) / last_augment_price * 100.0
                if augment_gain >= augment_gain_pct:
                    qty += 0.5
                    last_augment_price = mark
                    events.append(TradeEvent(idx=i, ts=ts_val, type="AUGMENT", price=mark, qty=0.5, reason="UVE_AUGMENT"))
    if position_active:
        mark = float(close[-1])
        ts_val = float(ts_arr[-1]) if ts_arr is not None and len(ts_arr) > 0 else float(n - 1)
        gain_pct = (mark - entry_price) / entry_price * 100.0 if is_long else (entry_price - mark) / entry_price * 100.0
        returns.append(gain_pct)
        events.append(TradeEvent(idx=n - 1, ts=ts_val, type="CLOSE", price=mark, qty=qty, reason="UVE_MTM_FINAL"))
    return {"events": events, "returns": returns}
