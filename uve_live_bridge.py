# -*- coding: utf-8 -*-
"""uve_live_bridge.py — Live Adapter for UVE Engine.

Converts live indicator dictionaries into the canonical UVE in-memory NPZ format to produce bit-exact live decisions.
"""
from __future__ import annotations
import numpy as np
from typing import Dict, Any, Optional
from uve_engine import evaluate_uve_signals, simulate_uve

def format_live_npz(indicator_history: Dict[str, list]) -> Dict[str, np.ndarray]:
    npz = {}
    for k, v in indicator_history.items():
        npz[k] = np.asarray(v, dtype=np.float32)
    return npz

def evaluate_live_uve_tick(indicator_history: Dict[str, list], is_long: bool, mode: str) -> Dict[str, Any]:
    npz = format_live_npz(indicator_history)
    signals = evaluate_uve_signals(npz, is_long, mode)
    entry_allowed = bool(signals["entry_allowed"][-1]) if len(signals["entry_allowed"]) > 0 else False
    close = float(signals["close"][-1]) if len(signals["close"]) > 0 else 0.0
    atr = float(signals["atr"][-1]) if len(signals["atr"]) > 0 else 0.0
    return {"entry_allowed": entry_allowed, "close": close, "atr": atr}
