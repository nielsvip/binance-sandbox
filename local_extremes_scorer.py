"""
local_extremes_scorer.py — 100-point multi-indicator entry scorer for local extremes strategy.

LONG: local bottom entry — deeply oversold across all TFs.
SHORT: local top entry — deeply overbought across all TFs.

6 components (100 pts total):
  A. Stoch K multi-TF oversold/overbought   (0-25 pts)
  B. WT oscillator alignment + velocity     (0-20 pts)
  C. DC/BB channel position                 (0-20 pts)
  D. MFI flow multi-TF                      (0-15 pts)
  E. Momentum/trend: HA + LR + WT struct    (0-15 pts)
  F. Volume confirmation                    (0-5  pts)

Size tiers: >=75→$5000, >=60→$2500, >=45→$1000, >=30→$250, >=15→$50, <15→skip.
"""
from __future__ import annotations
from typing import Dict, Tuple, Union

_SIZE_TIERS = [(75, 5000.0), (60, 2500.0), (45, 1000.0), (30, 250.0), (15, 50.0)]


def _f(ind: dict, key: str, default: float = 50.0) -> float:
    v = ind.get(key, default)
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _b(ind: dict, key: str) -> bool:
    v = ind.get(key, 0)
    return bool(v)


def score_local_extremes(ind: Dict, is_long: bool) -> Tuple[float, float, str]:
    """Score an entry candidate from a live indicator dict.

    Returns (score 0-100, size_usd, reason_str).
    Returns (0.0, 0.0, reason) when score < 15 (no trade).
    """
    score = 0.0
    reasons = []

    # ── A. Stoch K oversold/overbought (0-25 pts) ──────────────────────────
    pts_a = 0.0
    k5  = _f(ind, 'stoch_k_5m',  50.0)
    k15 = _f(ind, 'stoch_k_15m', 50.0)
    k1h = _f(ind, 'stoch_k_1h',  50.0)
    k4h = _f(ind, 'stoch_k_4h',  50.0)
    kD  = _f(ind, 'stoch_k_D',   50.0)
    if is_long:
        if k5  < 20: pts_a += 5
        if k15 < 20: pts_a += 6
        if k1h < 25: pts_a += 7
        if k4h < 35: pts_a += 4
        if kD  < 40: pts_a += 3
    else:
        if k5  > 80: pts_a += 5
        if k15 > 80: pts_a += 6
        if k1h > 75: pts_a += 7
        if k4h > 65: pts_a += 4
        if kD  > 60: pts_a += 3
    if pts_a > 0:
        reasons.append(f"K={pts_a:.0f}")
    score += pts_a

    # ── B. WT oscillator alignment + velocity (0-20 pts) ───────────────────
    pts_b = 0.0
    wt1_5m  = _f(ind, 'wt1_5m',  0.0)
    wt2_5m  = _f(ind, 'wt2_5m',  0.0)
    wt1_15m = _f(ind, 'wt1_15m', 0.0)
    wt2_15m = _f(ind, 'wt2_15m', 0.0)
    wt1_1h  = _f(ind, 'wt1_1h',  0.0)
    wt2_1h  = _f(ind, 'wt2_1h',  0.0)
    wt1_4h  = _f(ind, 'wt1_4h',  0.0)
    wt2_4h  = _f(ind, 'wt2_4h',  0.0)
    vel_1h  = _f(ind, 'wt_velocity_1h', 0.0)
    vel_D   = _f(ind, 'wt_velocity_D',  0.0)
    if is_long:
        if wt1_5m  > wt2_5m:  pts_b += 3
        if wt1_15m > wt2_15m: pts_b += 4
        if wt1_1h  > wt2_1h:  pts_b += 5
        if wt1_4h  > wt2_4h:  pts_b += 4
        if vel_1h  > 0:        pts_b += 2
        if vel_D   > 0:        pts_b += 2
    else:
        if wt1_5m  < wt2_5m:  pts_b += 3
        if wt1_15m < wt2_15m: pts_b += 4
        if wt1_1h  < wt2_1h:  pts_b += 5
        if wt1_4h  < wt2_4h:  pts_b += 4
        if vel_1h  < 0:        pts_b += 2
        if vel_D   < 0:        pts_b += 2
    pts_b = min(pts_b, 20.0)
    if pts_b > 0:
        reasons.append(f"WT={pts_b:.0f}")
    score += pts_b

    # ── C. DC/BB channel position (0-20 pts) ───────────────────────────────
    pts_c = 0.0
    dc1h = _f(ind, 'dc_position_1h', 0.5)
    dc4h = _f(ind, 'dc_position_4h', 0.5)
    dcD  = _f(ind, 'dc_position_D',  0.5)
    bb1h = _f(ind, 'bb_pct_b_1h',   0.5)
    bb4h = _f(ind, 'bb_pct_b_4h',   0.5)
    if is_long:
        if dc1h < 0.20: pts_c += 7
        elif dc1h < 0.35: pts_c += 3
        if dc4h < 0.30: pts_c += 5
        elif dc4h < 0.45: pts_c += 2
        if dcD  < 0.40: pts_c += 4
        if bb1h < 0.20: pts_c += 2
        if bb4h < 0.30: pts_c += 2
    else:
        if dc1h > 0.80: pts_c += 7
        elif dc1h > 0.65: pts_c += 3
        if dc4h > 0.70: pts_c += 5
        elif dc4h > 0.55: pts_c += 2
        if dcD  > 0.60: pts_c += 4
        if bb1h > 0.80: pts_c += 2
        if bb4h > 0.70: pts_c += 2
    pts_c = min(pts_c, 20.0)
    if pts_c > 0:
        reasons.append(f"DC={pts_c:.0f}")
    score += pts_c

    # ── D. MFI flow multi-TF (0-15 pts) ────────────────────────────────────
    pts_d = 0.0
    mfi5  = _f(ind, 'mfi_5m',  50.0)
    mfi15 = _f(ind, 'mfi_15m', 50.0)
    mfi1h = _f(ind, 'mfi_1h',  50.0)
    mfi4h = _f(ind, 'mfi_4h',  50.0)
    mfiD  = _f(ind, 'mfi_D',   50.0)
    if is_long:
        if mfi5  < 30: pts_d += 2
        if mfi15 < 30: pts_d += 3
        if mfi1h < 30: pts_d += 5
        if mfi4h < 35: pts_d += 3
        if mfiD  < 40: pts_d += 2
    else:
        if mfi5  > 70: pts_d += 2
        if mfi15 > 70: pts_d += 3
        if mfi1h > 70: pts_d += 5
        if mfi4h > 65: pts_d += 3
        if mfiD  > 60: pts_d += 2
    if pts_d > 0:
        reasons.append(f"MFI={pts_d:.0f}")
    score += pts_d

    # ── E. Momentum/trend: HA + LR + WT struct (0-15 pts) ──────────────────
    pts_e = 0.0
    ha5m  = int(_f(ind, 'ha_5m',  0.0))
    ha15m = int(_f(ind, 'ha_15m', 0.0))
    ha1h  = int(_f(ind, 'ha_1h',  0.0))
    lr1h  = _f(ind, 'lr_trend_1h', 0.0)
    wt_ts_1h = ind.get('wt_trough_structure_1h', 0)
    try:
        wt_ts_1h = int(wt_ts_1h)
    except (TypeError, ValueError):
        wt_ts_1h = 0
    wt_ms_1h = ind.get('wt_momentum_state_1h', 0)
    try:
        wt_ms_1h = int(wt_ms_1h)
    except (TypeError, ValueError):
        wt_ms_1h = 0
    if is_long:
        if ha5m  ==  1: pts_e += 2
        if ha15m ==  1: pts_e += 2
        if ha1h  ==  1: pts_e += 4
        if lr1h  >  0: pts_e += 3
        if wt_ts_1h == 1:  pts_e += 2
        if wt_ms_1h >= 1:  pts_e += 2
    else:
        if ha5m  == -1: pts_e += 2
        if ha15m == -1: pts_e += 2
        if ha1h  == -1: pts_e += 4
        if lr1h  <  0: pts_e += 3
        if wt_ts_1h == -1: pts_e += 2
        if wt_ms_1h <= -1: pts_e += 2
    pts_e = min(pts_e, 15.0)
    if pts_e > 0:
        reasons.append(f"MOM={pts_e:.0f}")
    score += pts_e

    # ── F. Volume confirmation (0-5 pts) ────────────────────────────────────
    pts_f = 0.0
    rvol5  = _f(ind, 'relative_volume_5m',  1.0)
    rvol15 = _f(ind, 'relative_volume_15m', 1.0)
    rvol1h = _f(ind, 'relative_volume_1h',  1.0)
    if rvol5  > 1.5: pts_f += 2
    if rvol15 > 1.2: pts_f += 2
    if rvol1h > 1.2: pts_f += 1
    pts_f = min(pts_f, 5.0)
    if pts_f > 0:
        reasons.append(f"VOL={pts_f:.0f}")
    score += pts_f

    score = min(score, 100.0)
    reason_str = f"score={score:.0f} [{'|'.join(reasons) if reasons else 'none'}]"

    size_usd = 0.0
    for threshold, sz in _SIZE_TIERS:
        if score >= threshold:
            size_usd = sz
            break

    return score, size_usd, reason_str


def size_from_score(score: float) -> float:
    """Return dollar size for a given score. Returns 0.0 if score < 15."""
    for threshold, sz in _SIZE_TIERS:
        if score >= threshold:
            return sz
    return 0.0
