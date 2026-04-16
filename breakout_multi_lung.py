"""breakout_multi_lung.py — NPZ-vectorized port of D4 (ez_breakout_agent.py).

Multi-TF "breathing" composite: each TF is a lung measuring candle pattern,
relative volume, structure (HH/HL), and stochastic K exhaustion.

Composite breath = weighted sum across TFs.
  composite > INHALE threshold → entry
  composite < EXHALE threshold → exit
  slow-lung override: if HTF lungs still inhaling, veto fast-lung exit

CRITICAL: default OFF. Must be sweep-proven (Sharpe > 2 per MEMORY rule) on
full 48-crypto × 4yr and 128-tradier × 2yr before any live wiring.

Origin: ez_breakout_agent.py (70 KB, 2026-03-16, orphaned). Diamond verdict
D4 = EXTRACT. Diff report: DIAMOND_DIFF_2026-04-16.md.
"""
from typing import Tuple

import numpy as np


CRYPTO_LUNGS = [
    ("3m", 0.07), ("15m", 0.13), ("1h", 0.20), ("4h", 0.20),
    ("D", 0.25), ("W", 0.15),
]
MOVER_LUNGS = [
    ("3m", 0.10), ("15m", 0.25), ("1h", 0.35), ("4h", 0.30),
]
STOCK_LUNGS = [
    ("D", 0.55), ("W", 0.35), ("4h", 0.10),
]


def _shift1(a: np.ndarray) -> np.ndarray:
    p = np.roll(a, 1)
    p[0] = a[0]
    return p


def _candle_scores(o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Vectorized candle pattern scoring. Returns (bull_score, bear_score) per bar."""
    n = len(c)
    if n < 3:
        return np.zeros(n), np.zeros(n)
    body = c - o
    rng = np.maximum(h - l, 1e-9)
    abs_body = np.abs(body)
    upper = h - np.maximum(o, c)
    lower = np.minimum(o, c) - l
    o_p = _shift1(o); c_p = _shift1(c); h_p = _shift1(h); l_p = _shift1(l)
    body_p = c_p - o_p
    abs_body_p = np.abs(body_p)
    rng_p = np.maximum(h_p - l_p, 1e-9)
    # 3-bar lookback for morning/evening star + three soldiers
    o_pp = _shift1(o_p); c_pp = _shift1(c_p)
    body_pp = c_pp - o_pp

    bull_engulf = (body > 0) & (body_p < 0) & (abs_body > abs_body_p * 1.1) & (c > o_p) & (o <= c_p)
    bear_engulf = (body < 0) & (body_p > 0) & (abs_body > abs_body_p * 1.1) & (o > c_p) & (c <= o_p)
    hammer = (abs_body < rng * 0.35) & (lower >= abs_body * 2.0) & (upper < abs_body * 0.5) & (body_p < 0)
    shooting = (abs_body < rng * 0.35) & (upper >= abs_body * 2.0) & (lower < abs_body * 0.5) & (body_p > 0)
    bull_pin = (lower > rng * 0.6) & (abs_body < rng * 0.25) & ~hammer
    bear_pin = (upper > rng * 0.6) & (abs_body < rng * 0.25) & ~shooting
    morning_star = (body_pp < 0) & (abs_body_p < rng_p * 0.2) & (body > 0) & (c > (o_pp + c_pp) * 0.5)
    evening_star = (body_pp > 0) & (abs_body_p < rng_p * 0.2) & (body < 0) & (c < (o_pp + c_pp) * 0.5)
    three_white = (body > 0) & (body_p > 0) & (body_pp > 0) & (c > c_p) & (c_p > c_pp)
    three_black = (body < 0) & (body_p < 0) & (body_pp < 0) & (c < c_p) & (c_p < c_pp)

    bull = np.zeros(n)
    bull = np.where(bull_engulf, 0.6, bull)
    bull = np.where(hammer & (bull == 0), 0.5, bull)
    bull = np.where(morning_star & (bull == 0), 0.55, bull)
    bull = np.where(three_white & (bull == 0), 0.45, bull)
    bull = np.where(bull_pin & (bull == 0), 0.4, bull)

    bear = np.zeros(n)
    bear = np.where(bear_engulf, 0.6, bear)
    bear = np.where(shooting & (bear == 0), 0.5, bear)
    bear = np.where(evening_star & (bear == 0), 0.55, bear)
    bear = np.where(three_black & (bear == 0), 0.45, bear)
    bear = np.where(bear_pin & (bear == 0), 0.4, bear)
    return bull, bear


def _volume_score(v: np.ndarray, lookback: int = 20) -> np.ndarray:
    """Rel volume expanding/contracting score. Returns score per bar in [-0.4, 0.5]."""
    n = len(v)
    if n < 4:
        return np.zeros(n)
    kernel = np.ones(lookback) / lookback
    avg = np.convolve(v, kernel, mode="same")
    avg = np.where(avg > 0, avg, 1.0)
    ratio = v / avg
    v_1 = _shift1(v); v_2 = _shift1(v_1)
    expanding = (v > v_1) & (v_1 > v_2)
    contracting = (v < v_1) & (v_1 < v_2)
    score = np.zeros(n)
    score = np.where(ratio >= 2.5, 0.5, score)
    score = np.where((ratio >= 1.8) & (ratio < 2.5), 0.35, score)
    score = np.where((ratio >= 1.3) & (ratio < 1.8), 0.2 + np.where(expanding, 0.1, 0), score)
    score = np.where((ratio >= 0.5) & (ratio < 0.8), -0.15 + np.where(contracting, -0.1, 0), score)
    score = np.where(ratio < 0.5, -0.3, score)
    return score


def _structure_score(h: np.ndarray, l: np.ndarray, is_long: bool, window: int = 10) -> np.ndarray:
    """HH/HL for long, LL/LH for short. Returns score per bar in [-0.2, 0.3]."""
    n = len(h)
    if n < window:
        return np.zeros(n)
    score = np.zeros(n)
    for i in range(window, n):
        rh = h[i - 3:i + 1].max()
        oh = h[i - 6:i - 3].max() if i - 6 >= 0 else rh
        rl = l[i - 3:i + 1].min()
        ol = l[i - 6:i - 3].min() if i - 6 >= 0 else rl
        if is_long:
            hh, hl = rh > oh, rl > ol
            if hh and hl: score[i] = 0.3
            elif hh: score[i] = 0.15
            elif hl: score[i] = 0.1
            else: score[i] = -0.2
        else:
            ll, lh = rl < ol, rh < oh
            if ll and lh: score[i] = 0.3
            elif ll: score[i] = 0.15
            elif lh: score[i] = 0.1
            else: score[i] = -0.2
    return score


def _stoch_exhaustion(k: np.ndarray, is_long: bool) -> np.ndarray:
    """Stoch K as exhaustion warning. Negative when overextended."""
    if is_long:
        over = np.clip((k - 78) / 22, 0, 1)  # 78..100 → 0..1
        return -over * 0.15
    over = np.clip((22 - k) / 22, 0, 1)
    return -over * 0.15


def _lung_breath(npz: dict, n: int, tf: str, is_long: bool) -> np.ndarray:
    """Single-TF breath score per bar. Returns np.zeros if required fields missing."""
    ok_key = f"open_{tf}"
    if ok_key not in npz:
        return np.zeros(n)
    o = npz[f"open_{tf}"]
    h = npz[f"high_{tf}"]
    l = npz[f"low_{tf}"]
    c = npz[f"close_{tf}"]
    v = npz.get(f"volume_{tf}", np.zeros_like(c))
    if len(o) < n:
        pad = np.full(n - len(o), o[-1] if len(o) else 0.0)
        o = np.concatenate([pad, o]); h = np.concatenate([pad, h])
        l = np.concatenate([pad, l]); c = np.concatenate([pad, c])
        v = np.concatenate([np.zeros(n - len(v)), v])
    o = o[:n]; h = h[:n]; l = l[:n]; c = c[:n]; v = v[:n]
    bull, bear = _candle_scores(o, h, l, c)
    pattern = bull - bear if is_long else bear - bull
    vol = _volume_score(v)
    struct = _structure_score(h, l, is_long)
    k_key = f"stoch_k_{tf}" if tf not in ("D", "W") else f"stoch_k_1h"
    k = npz.get(k_key, np.full(n, 50.0))
    if len(k) < n:
        k = np.concatenate([np.full(n - len(k), 50.0), k])
    k = k[:n]
    exhaustion = _stoch_exhaustion(k, is_long)
    # Combined: pattern is primary, volume confirms, structure supports, exhaustion warns
    # Clamp each to bounded range then sum
    breath = pattern * 0.55 + vol * 0.25 + struct * 0.15 + exhaustion * 0.05
    return np.clip(breath, -1.0, 1.0)


def composite_breath(npz: dict, n: int, is_long: bool, tier: str = "CRYPTO") -> np.ndarray:
    """Weighted sum of per-TF lung breaths. Returns np array size n in [-1, 1]."""
    tier_u = (tier or "CRYPTO").upper()
    lungs = CRYPTO_LUNGS if tier_u == "CRYPTO" else (MOVER_LUNGS if tier_u == "MOVER" else STOCK_LUNGS)
    composite = np.zeros(n)
    total_w = 0.0
    for tf, w in lungs:
        breath = _lung_breath(npz, n, tf, is_long)
        if breath.any():
            composite += breath * w
            total_w += w
    if total_w > 0:
        composite = composite / total_w
    return composite


def slow_lungs_still_inhaling(npz: dict, n: int, is_long: bool, tier: str, threshold: float) -> np.ndarray:
    """HTF-only composite (D + W for crypto/stock, 4h for mover). True = slow lungs OK."""
    tier_u = (tier or "CRYPTO").upper()
    if tier_u == "MOVER":
        slow_tfs = [("4h", 1.0)]
    elif tier_u == "STOCK":
        slow_tfs = [("D", 0.6), ("W", 0.4)]
    else:
        slow_tfs = [("D", 0.6), ("W", 0.4)]
    comp = np.zeros(n); total = 0.0
    for tf, w in slow_tfs:
        b = _lung_breath(npz, n, tf, is_long)
        if b.any():
            comp += b * w; total += w
    if total > 0:
        comp /= total
    return comp > threshold


def multi_lung_entry_signal(npz: dict, n: int, is_long: bool, cfg) -> np.ndarray:
    """Boolean array: True where multi-lung composite >= INHALE threshold."""
    inhale = float(getattr(cfg, "BREAKOUT_MULTI_LUNG_COMPOSITE_INHALE", 0.20))
    tier = str(getattr(cfg, "BREAKOUT_MULTI_LUNG_TIER", "CRYPTO"))
    composite = composite_breath(npz, n, is_long, tier)
    return composite >= inhale


def multi_lung_exit_signal(npz: dict, n: int, is_long: bool, cfg) -> np.ndarray:
    """Boolean array: True where multi-lung composite <= EXHALE threshold AND slow lungs allow."""
    exhale = float(getattr(cfg, "BREAKOUT_MULTI_LUNG_COMPOSITE_EXHALE", -0.10))
    slow_veto = float(getattr(cfg, "BREAKOUT_MULTI_LUNG_SLOW_LUNG_OVERRIDE", 0.15))
    tier = str(getattr(cfg, "BREAKOUT_MULTI_LUNG_TIER", "CRYPTO"))
    composite = composite_breath(npz, n, is_long, tier)
    exhaling = composite <= exhale
    # Slow-lung override: if slow lungs still inhaling, don't exit on fast-lung exhale
    slow_ok = slow_lungs_still_inhaling(npz, n, is_long, tier, slow_veto)
    return exhaling & ~slow_ok


def is_enabled(cfg) -> bool:
    return bool(getattr(cfg, "BREAKOUT_MULTI_LUNG_ENABLED", False))
