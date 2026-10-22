"""dc_slope_incentive.py — SHARED scalar+vectorized MTF DC-slope entry incentive.

Four switches, all in the STDEV_SLOPE_SIZING tab:
  DC_SLOPE_INCENTIVE_ENABLED, DC_SLOPE_INCENTIVE_TF,
  DC_SLOPE_INCENTIVE_MIN_PCT, DC_SLOPE_INCENTIVE_ENTRY_MULT.

Idea (USER 2026-10-22): the MTF Donchian rise (dc_low climbing for longs,
dc_high falling for shorts) is an incentive, never a gate. An entry or
reentry that fires on a slope-aligned bar opens bigger (ENTRY_MULT, default
2x) instead of being filtered. Live already sizes rising-DC entries up with
hardcoded adds (ez_manage execute_trade_action, tradier_manage entry
quantizer); this switch is the sweep-tunable twin of that reward.

Reference level is the 30-bar _ant value (same anchor live compares
against), not the 1-bar _prev: _prev flickers on every inside bar while
_ant measures the sustained channel drift entries should be paid for.
Strict inequality, fail-closed: missing/zero _ant never fires.
"""
from __future__ import annotations

from typing import Any
import math

import numpy as np

VALID_TFS = ("15m", "1h", "4h", "D")


def _num(v: Any, default: float) -> float:
    try:
        f = float(v)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def _tf(cfg: Any) -> str:
    try:
        raw = getattr(cfg, "DC_SLOPE_INCENTIVE_TF", "4h")
    except Exception:
        raw = "4h"
    tf = str(raw or "4h").strip()
    return tf if tf in VALID_TFS else ""


def _enabled(cfg: Any) -> bool:
    try:
        return bool(getattr(cfg, "DC_SLOPE_INCENTIVE_ENABLED", False))
    except Exception:
        return False


def _min_pct(cfg: Any) -> float:
    try:
        return _num(getattr(cfg, "DC_SLOPE_INCENTIVE_MIN_PCT", 0.0), 0.0)
    except Exception:
        return 0.0


def _entry_mult(cfg: Any) -> float:
    try:
        m = _num(getattr(cfg, "DC_SLOPE_INCENTIVE_ENTRY_MULT", 2.0), 2.0)
    except Exception:
        m = 2.0
    return m if m > 0 else 1.0


def _arr(npz: Any, key: str, n: int) -> np.ndarray:
    try:
        a = np.asarray(npz[key], dtype=float).reshape(-1)
    except Exception:
        return np.zeros(n, dtype=float)
    if a.size < n:
        out = np.zeros(n, dtype=float)
        out[: a.size] = a
        return out
    return a[:n]


def slope_ok_vec(npz: Any, n: int, is_long: bool, tf: str, min_pct: float) -> np.ndarray:
    """Per-bar slope alignment. LONG: dc_low rising; SHORT: dc_high falling."""
    if tf not in VALID_TFS:
        return np.zeros(n, dtype=bool)
    if is_long:
        cur = _arr(npz, f"dc_low_{tf}", n)
        ant = _arr(npz, f"dc_low_{tf}_ant", n)
        return (ant > 0) & (cur > ant * (1.0 + min_pct / 100.0))
    cur = _arr(npz, f"dc_high_{tf}", n)
    ant = _arr(npz, f"dc_high_{tf}_ant", n)
    return (ant > 0) & (cur < ant * (1.0 - min_pct / 100.0))


def open_mult_vec(npz: Any, n: int, is_long: bool, cfg: Any) -> np.ndarray:
    """Open-dollar multiplier array. Exactly 1.0 everywhere at defaults."""
    one = np.ones(n, dtype=float)
    if not _enabled(cfg):
        return one
    tf = _tf(cfg)
    if not tf:
        return one
    ok = slope_ok_vec(npz, n, is_long, tf, _min_pct(cfg))
    return np.where(ok, _entry_mult(cfg), 1.0)


def slope_ok_live(ind: Any, is_long: bool, tf: str, min_pct: float) -> bool:
    """Scalar twin for the live executors. ind is the indicator mapping."""
    try:
        get = ind.get
    except AttributeError:
        return False
    if tf not in VALID_TFS:
        return False
    try:
        if is_long:
            cur = float(get(f"dc_low_{tf}", 0.0) or 0.0)
            ant = float(get(f"dc_low_{tf}_ant", 0.0) or 0.0)
            return ant > 0 and cur > ant * (1.0 + min_pct / 100.0)
        cur = float(get(f"dc_high_{tf}", 0.0) or 0.0)
        ant = float(get(f"dc_high_{tf}_ant", 0.0) or 0.0)
        return ant > 0 and cur < ant * (1.0 - min_pct / 100.0)
    except (TypeError, ValueError):
        return False


def open_mult_live(ind: Any, is_long: bool, get: Any) -> float:
    """Scalar open multiplier. get(knob, default) is the caller's per-sym getter."""
    try:
        if not bool(get("DC_SLOPE_INCENTIVE_ENABLED", False)):
            return 1.0
        tf = str(get("DC_SLOPE_INCENTIVE_TF", "4h") or "4h").strip()
        min_pct = _num(get("DC_SLOPE_INCENTIVE_MIN_PCT", 0.0), 0.0)
        mult = _num(get("DC_SLOPE_INCENTIVE_ENTRY_MULT", 2.0), 2.0)
    except Exception:
        return 1.0
    if mult <= 0:
        return 1.0
    try:
        ok = slope_ok_live(ind, is_long, tf, min_pct)
    except Exception:
        return 1.0
    return float(mult) if ok else 1.0
