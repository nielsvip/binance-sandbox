# -*- coding: utf-8 -*-
"""uve_engine.py — Core Unified Vectorized Execution Engine.

Vectorized precomputations + sequential bar processing for 100% bit-exact parity between backtests and live trading.
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
    n = len(npz.get("timestamps", npz.get("timestamp_15m", npz.get("timestamp_5m", npz.get("timestamp_3m")))))
    wt1_1h = np.asarray(npz.get("wt1_1h", np.zeros(n, dtype=np.float32)), dtype=np.float32)
    wt2_1h = np.asarray(npz.get("wt2_1h", np.zeros(n, dtype=np.float32)), dtype=np.float32)
    macro_trend = (wt1_1h > wt2_1h) if is_long else (wt1_1h < wt2_1h)
    stoch_k_15m = np.asarray(npz.get("stoch_k_15m", np.zeros(n, dtype=np.float32)), dtype=np.float32)
    stoch_k_1h = np.asarray(npz.get("stoch_k_1h", np.zeros(n, dtype=np.float32)), dtype=np.float32)
    stoch_thresh = float(getattr(config, "UVE_STOCH_THRESH", 90.0) or 90.0)
    anti_parabolic = (stoch_k_15m < stoch_thresh) & (stoch_k_1h < stoch_thresh)
    close_field = "close_5m" if mode == "tradier" else "close_3m"
    close = np.asarray(npz.get(close_field, npz.get("close", np.zeros(n, dtype=np.float32))), dtype=np.float32)
    wt1_base = np.asarray(npz.get("wt1_3m" if mode == "crypto" else "wt1_5m", np.zeros(n, dtype=np.float32)), dtype=np.float32)
    wt2_base = np.asarray(npz.get("wt2_3m" if mode == "crypto" else "wt2_5m", np.zeros(n, dtype=np.float32)), dtype=np.float32)
    wt_cross = (wt1_base > wt2_base) if is_long else (wt1_base < wt2_base)
    entry_allowed = macro_trend & anti_parabolic & wt_cross
    atr = np.asarray(npz.get("atr_15m", np.zeros(n, dtype=np.float32)), dtype=np.float32)
    return {"entry_allowed": entry_allowed, "close": close, "atr": atr, "macro_trend": macro_trend}

def simulate_uve(npz: Dict[str, np.ndarray], is_long: bool, mode: str, config: Any = None) -> Dict[str, Any]:
    signals = evaluate_uve_signals(npz, is_long, mode, config)
    close, entry_allowed, atr = signals["close"], signals["entry_allowed"], signals["atr"]
    n, events, returns = len(close), [], []
    ts_arr = npz.get("timestamps", npz.get("timestamp_3m", npz.get("timestamp_5m", npz.get("timestamp_15m"))))
    position_active, entry_price, peak_price, ppl_fired, qty, last_augment_price = False, 0.0, 0.0, False, 0.0, 0.0
    ppl_gain_pct = float(getattr(config, "UVE_PPL_GAIN_PCT", 1.5) or 1.5)
    atr_mult = float(getattr(config, "UVE_ATR_MULT", 2.0) or 2.0)
    augment_gain_pct = float(getattr(config, "UVE_AUGMENT_GAIN_PCT", 3.0) or 3.0)
    for i in range(n):
        mark, current_atr = float(close[i]), (float(atr[i]) if i < len(atr) else 0.0)
        ts_val = float(ts_arr[i]) if ts_arr is not None and i < len(ts_arr) else float(i)
        if not position_active:
            if entry_allowed[i]:
                position_active, entry_price, peak_price, ppl_fired, qty, last_augment_price = True, mark, mark, False, 1.0, mark
                events.append(TradeEvent(idx=i, ts=ts_val, type="OPEN", price=mark, qty=qty, reason="UVE_WT_ENTRY"))
        else:
            gain_pct = (mark - entry_price) / entry_price * 100.0 if is_long else (entry_price - mark) / entry_price * 100.0
            peak_price = max(peak_price, mark) if is_long else min(peak_price, mark)
            if gain_pct >= ppl_gain_pct and not ppl_fired:
                ppl_fired, qty = True, qty * 0.5
                events.append(TradeEvent(idx=i, ts=ts_val, type="REDUCE", price=mark, qty=qty, reason="UVE_PPL_STEP1"))
            trail_stop = (peak_price - atr_mult * current_atr) if is_long else (peak_price + atr_mult * current_atr)
            stop_price = (entry_price * 1.0002) if ppl_fired else trail_stop
            should_exit = (mark < stop_price) if is_long else (mark > stop_price)
            if should_exit:
                returns.append(gain_pct)
                events.append(TradeEvent(idx=i, ts=ts_val, type="CLOSE", price=mark, qty=qty, reason="UVE_ATR_TRAIL" if not ppl_fired else "UVE_PPL_EXIT"))
                position_active, entry_price, peak_price, ppl_fired, qty = False, 0.0, 0.0, False, 0.0
            elif entry_allowed[i] and not ppl_fired:
                augment_gain = (mark - last_augment_price) / last_augment_price * 100.0 if is_long else (last_augment_price - mark) / last_augment_price * 100.0
                if augment_gain >= augment_gain_pct:
                    qty, last_augment_price = qty + 0.5, mark
                    events.append(TradeEvent(idx=i, ts=ts_val, type="AUGMENT", price=mark, qty=0.5, reason="UVE_AUGMENT"))
    if position_active:
        mark = float(close[-1])
        ts_val = float(ts_arr[-1]) if ts_arr is not None and len(ts_arr) > 0 else float(n - 1)
        gain_pct = (mark - entry_price) / entry_price * 100.0 if is_long else (entry_price - mark) / entry_price * 100.0
        returns.append(gain_pct)
        events.append(TradeEvent(idx=n - 1, ts=ts_val, type="CLOSE", price=mark, qty=qty, reason="UVE_MTM_FINAL"))
    return {"events": events, "returns": returns}
