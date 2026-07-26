"""Vectorized, live-parity WT_DC entry scorer and path gates.

This module mirrors the WT_DC-specific part of ``tradier_manage.process_position``.
It deliberately does not combine WT_DC with Golden Rule, ladder, delta, or any
other entry family; those are alternative paths in live code and must be tested
as separate masks before a combination search.
"""

from __future__ import annotations

import numpy as np


def _arr(npz: dict, key: str, n: int, default=np.nan) -> np.ndarray:
    value = npz.get(key)
    if isinstance(value, np.ndarray) and len(value) == n:
        return np.asarray(value, dtype=np.float64)
    return np.full(n, default, dtype=np.float64)


def _cross_masks(npz: dict, tf: str, n: int) -> tuple[np.ndarray, np.ndarray]:
    """Return (bull, bear) for string, signed numeric, or split encodings."""
    cross = npz.get(f"wt_cross_{tf}")
    bull = np.zeros(n, dtype=bool)
    bear = np.zeros(n, dtype=bool)
    if isinstance(cross, np.ndarray) and len(cross) == n:
        a = np.asarray(cross)
        if a.dtype.kind in "biufc":
            finite = np.isfinite(a)
            bull = finite & (a > 0)
            bear = finite & (a < 0)
        else:
            text = np.char.upper(np.char.strip(a.astype(str)))
            bull = text == "BULL"
            bear = text == "BEAR"
    split_bull = npz.get(f"wt_cross_bull_{tf}")
    split_bear = npz.get(f"wt_cross_bear_{tf}")
    if isinstance(split_bull, np.ndarray) and len(split_bull) == n:
        bull |= np.asarray(split_bull).astype(bool)
    if isinstance(split_bear, np.ndarray) and len(split_bear) == n:
        bear |= np.asarray(split_bear).astype(bool)
    return bull, bear


def score_entry_multitf_vec(npz: dict, n: int, is_long: bool) -> np.ndarray:
    """Vector twin of ``wt_dc_entry_scorer.score_entry_multitf``."""
    wt1_d = _arr(npz, "wt1_D", n)
    wt2_d = _arr(npz, "wt2_D", n)
    wt1_4h = _arr(npz, "wt1_4h", n)
    wt2_4h = _arr(npz, "wt2_4h", n)
    dc_1h = _arr(npz, "dc_position_1h", n, 0.5)
    k_5m = _arr(npz, "stoch_k_5m", n, 50.0)
    bull_1h, bear_1h = _cross_masks(npz, "1h", n)
    missing = (
        ~np.isfinite(wt1_d)
        | ~np.isfinite(wt2_d)
        | ~np.isfinite(wt1_4h)
        | ~np.isfinite(wt2_4h)
        | ~np.isfinite(dc_1h)
        | ~np.isfinite(k_5m)
    )
    if is_long:
        score = (
            25.0 * (wt1_d > wt2_d)
            + 25.0 * (wt1_4h > wt2_4h)
            + 30.0 * bull_1h
            + 10.0 * (dc_1h < 0.5)
            + 10.0 * (k_5m < 40.0)
        )
    else:
        score = (
            25.0 * (wt1_d < wt2_d)
            + 25.0 * (wt1_4h < wt2_4h)
            + 30.0 * bear_1h
            + 10.0 * (dc_1h > 0.5)
            + 10.0 * (k_5m > 60.0)
        )
    return np.where(missing, 0.0, score).astype(np.float32)


def compute_wt_dc_entry_vec(
    npz: dict,
    n: int,
    is_long: bool,
    *,
    threshold: float = 45.0,
    htf_gate: str = "none",
    htf_align_required: int = 0,
    combined_stoch_gate: float = 100.0,
    k5m_max_long: float = 100.0,
    k5m_min_short: float = 0.0,
    path_enabled: bool = True,
) -> tuple[np.ndarray, np.ndarray]:
    """Return ``(entry_mask, score)`` for the isolated WT_DC path.

    The mask includes only gates that live applies inside the WT_DC branch.
    It does not include generic execution/capital/risk gates.
    """
    score = score_entry_multitf_vec(npz, n, is_long)
    if not path_enabled:
        return np.zeros(n, dtype=bool), score
    allowed = score >= float(threshold)
    k5 = _arr(npz, "k_5m", n, 50.0)
    if is_long:
        allowed &= k5 <= float(k5m_max_long)
    else:
        allowed &= k5 >= float(k5m_min_short)

    gate = str(htf_gate or "none").strip().lower()
    if gate == "1h":
        w1, w2 = _arr(npz, "wt1_1h", n, 0.0), _arr(npz, "wt2_1h", n, 0.0)
        allowed &= (w1 >= w2) if is_long else (w1 <= w2)
    elif gate in {"4h", "4h_d"}:
        w1, w2 = _arr(npz, "wt1_4h", n, 0.0), _arr(npz, "wt2_4h", n, 0.0)
        allowed &= (w1 >= w2) if is_long else (w1 <= w2)
        if gate == "4h_d":
            d1, d2 = _arr(npz, "wt1_D", n, 0.0), _arr(npz, "wt2_D", n, 0.0)
            allowed &= (d1 >= d2) if is_long else (d1 <= d2)

    required = max(0, int(htf_align_required))
    if required:
        count = np.zeros(n, dtype=np.int8)
        for tf in ("1h", "4h", "D"):
            w1, w2 = _arr(npz, f"wt1_{tf}", n, 0.0), _arr(npz, f"wt2_{tf}", n, 0.0)
            count += ((w1 > w2) if is_long else (w1 < w2)).astype(np.int8)
        allowed &= count >= required

    stoch_gate = float(combined_stoch_gate)
    if stoch_gate < 100.0:
        if is_long:
            allowed &= k5 < stoch_gate
        else:
            allowed &= k5 > (100.0 - stoch_gate)
    return allowed, score
