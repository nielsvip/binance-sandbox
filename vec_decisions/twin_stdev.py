"""twin_stdev.py — SHARED scalar+vectorized STDEV band/slope sizing twin.

Seven switches, all in the STDEV_SLOPE_SIZING tab (pilot-skipped, operator
mandates wired anyway):
  STDEV_BAND_MULTIPLIER, STDEV_BULL_SLOPE_BOOST_ENABLED,
  STDEV_BULL_SLOPE_BOOST_MULT, STDEV_SLOPE_LOOKBACK_15M,
  STDEV_SLOPE_LOOKBACK_1H, STDEV_SLOPE_LOOKBACK_4H, STDEV_SLOPE_LOOKBACK_D.

Math mirrors the existing vector implementation exactly:
  lookback ratio + band ratio — v12_quick_engine_fast_v2.py:9782-9795
  favorable-slope gate (boost) — ez_manage.py:27146, tradier_manage.py:28612
  ladder gate (OR)             — tradier_manage.py:28577
  clip-max ladder map          — tradier_manage.py:28583-28589
  enable gate nesting          — v12_quick_engine_fast_v2.py:9751
Live precompute hardcodes 2.5 sigma + fixed windows
(backtest_v8_precompute.py:2100,2117-2118); live sizing reads lrL channel
values only (ez_manage.py:27136-27150, tradier_manage.py:28577-28616).

Modes:
  adjustment (base=None): returns the pure switch factor; the hook does
    ``mult = mult * get(...)``. Inert (exactly 1.0) at defaults.
  full (base given): returns ``clip(base * lb_ratio * bm_ratio * boost,
    smin, smax)`` — bit-mirror of fast_v2:9782-9795 with the boost folded
    inside the clip, matching live slope-mult-before-clip order
    (ez_manage.py:27144-27148).
"""
from __future__ import annotations

from typing import Any, Mapping
import math

import numpy as np

_LB_DEFAULTS = {"D": 180, "4h": 180, "1h": 168, "15m": 96}
_BM_DEFAULT = 2.5
_STDEV_MAX_KEYS = {"D": "STDEV_SLOPE_SIZING_D_MAX", "4h": "STDEV_SLOPE_SIZING_4H_MAX", "1h": "STDEV_SLOPE_SIZING_1H_MAX", "15m": "STDEV_SLOPE_SIZING_15M_MAX"}
_STDEV_MAX_DEFAULTS = {"D": 10.0, "4h": 4.0, "1h": 2.0, "15m": 1.5}


def _num(v: Any, default: float) -> float:
    try:
        f = float(v)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def _is_crypto(cfg: Any) -> bool:
    try:
        return str(getattr(cfg, "MODE", "tradier")).lower() == "crypto"
    except Exception:
        return False


def _tf(cfg: Any) -> str:
    try:
        raw = getattr(cfg, "BAND_SLOPE_SIZING_V2_TF", None)
    except Exception:
        raw = None
    if raw:
        return str(raw)
    return "4h" if _is_crypto(cfg) else "D"


def _ladder_on(cfg: Any) -> bool:
    try:
        stdev_on = bool(getattr(cfg, "STDEV_SLOPE_SIZING_ENABLED", False))
    except Exception:
        stdev_on = False
    try:
        v2_on = bool(getattr(cfg, "BAND_SLOPE_SIZING_V2_ENABLED", False))
    except Exception:
        v2_on = False
    return stdev_on or v2_on


def _lookback_ratio(cfg: Any, tf: str) -> float:
    lb_map = {"D": _num(getattr(cfg, "STDEV_SLOPE_LOOKBACK_D", 180), 180), "4h": _num(getattr(cfg, "STDEV_SLOPE_LOOKBACK_4H", 180), 180), "1h": _num(getattr(cfg, "STDEV_SLOPE_LOOKBACK_1H", 168), 168), "15m": _num(getattr(cfg, "STDEV_SLOPE_LOOKBACK_15M", 96), 96)}
    lb = float(lb_map.get(tf, 180))
    lb_def = float(_LB_DEFAULTS.get(tf, 180))
    if lb > 0 and lb != lb_def:
        return lb_def / lb
    return 1.0


def _band_ratio(cfg: Any) -> float:
    bm = _num(getattr(cfg, "STDEV_BAND_MULTIPLIER", 2.5), 2.5)
    if bm != _BM_DEFAULT and bm > 0:
        return bm / _BM_DEFAULT
    return 1.0


def _boost_factor(cfg: Any, slope_day: float, is_long: bool) -> float:
    try:
        enabled = bool(getattr(cfg, "STDEV_BULL_SLOPE_BOOST_ENABLED", False))
    except Exception:
        enabled = False
    if not enabled:
        return 1.0
    if not math.isfinite(slope_day):
        return 1.0
    fav = slope_day > 0 if is_long else slope_day < 0
    if not fav:
        return 1.0
    mult = _num(getattr(cfg, "STDEV_BULL_SLOPE_BOOST_MULT", 1.5), 1.5)
    if mult <= 0:
        return 1.0
    return mult


def _clip_bounds(cfg: Any, tf: str) -> tuple:
    smin = _num(getattr(cfg, "BAND_SLOPE_SIZING_V2_MIN", 0.5), 0.5)
    try:
        stdev_on = bool(getattr(cfg, "STDEV_SLOPE_SIZING_ENABLED", False))
    except Exception:
        stdev_on = False
    if stdev_on:
        smax = _num(getattr(cfg, _STDEV_MAX_KEYS.get(tf, "STDEV_SLOPE_SIZING_D_MAX"), _STDEV_MAX_DEFAULTS.get(tf, 10.0)), _STDEV_MAX_DEFAULTS.get(tf, 10.0))
    else:
        smax = _num(getattr(cfg, "BAND_SLOPE_SIZING_V2_MAX", 2.5), 2.5)
    if not smin > 0:
        smin = 0.5
    if not smax >= smin:
        smax = smin
    return smin, smax


def _slope_key(cfg: Any) -> str:
    return f"lrL_slope_{_tf(cfg)}"


def get(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any, base: Any = None) -> np.ndarray:
    """Vector STDEV switch factor. Inert (ones) at defaults or ladder off."""
    ones = np.ones(int(n), dtype=np.float64)
    if not _ladder_on(cfg):
        return ones
    tf = _tf(cfg)
    lb_ratio = _lookback_ratio(cfg, tf)
    bm_ratio = _band_ratio(cfg)
    try:
        boost_on = bool(getattr(cfg, "STDEV_BULL_SLOPE_BOOST_ENABLED", False))
    except Exception:
        boost_on = False
    if boost_on:
        key = _slope_key(cfg)
        if key in npz:
            try:
                a = np.asarray(npz[key], dtype=float)
                sl = np.full(int(n), 0.0, dtype=float)
                m = min(int(n), a.size)
                sl[:m] = a[:m]
                sl = np.nan_to_num(sl, nan=0.0, posinf=0.0, neginf=0.0)
            except Exception:
                sl = np.zeros(int(n), dtype=float)
        else:
            sl = np.zeros(int(n), dtype=float)
        fav = sl > 0 if is_long else sl < 0
        bmult = _num(getattr(cfg, "STDEV_BULL_SLOPE_BOOST_MULT", 1.5), 1.5)
        if bmult <= 0:
            boost = ones.copy()
        else:
            boost = np.where(fav, bmult, 1.0)
    else:
        boost = ones.copy()
    adj = lb_ratio * bm_ratio
    if base is None:
        out = adj * boost
    else:
        try:
            b = np.asarray(base, dtype=float)
            if b.shape != (int(n),):
                out = adj * boost
            else:
                smin, smax = _clip_bounds(cfg, tf)
                out = np.clip(b * adj * boost, smin, smax)
        except Exception:
            out = adj * boost
    return np.where(np.isfinite(out), out, 1.0)


def get_scalar(indicators: Mapping[str, Any] | None, is_long: bool, cfg: Any, base: Any = None) -> float:
    """Scalar twin of get() for live sizing paths. Inert (1.0) at defaults."""
    if not _ladder_on(cfg):
        return 1.0
    tf = _tf(cfg)
    adj = _lookback_ratio(cfg, tf) * _band_ratio(cfg)
    slope = 0.0
    try:
        if indicators is not None and _slope_key(cfg) in indicators:
            slope = _num(indicators.get(_slope_key(cfg)), 0.0)
    except Exception:
        slope = 0.0
    boost = _boost_factor(cfg, slope, bool(is_long))
    if base is None:
        out = adj * boost
    else:
        try:
            b = float(base)
            if not math.isfinite(b):
                return 1.0
            smin, smax = _clip_bounds(cfg, tf)
            out = min(max(b * adj * boost, smin), smax)
        except (TypeError, ValueError):
            out = adj * boost
    return out if math.isfinite(out) else 1.0
