"""
vec_decisions/higher_wt_cross.py — "HIGHER WT CROSS WHILE FLAT → ENTER BIGGER" entry trigger.
2026-05-30 USER MANDATE: if a WT cross-up on ANY of {3m,15m,1h,4h} is HIGHER than the previous
cross on that TF AND we are NOT in a position → ENTER at 150% (or OPEN if never opened). Mirror
for SHORT: a cross-DOWN that is LOWER than the previous. Never sit out a rising-momentum symbol.

Shared scalar+vec core (same pattern as strategy_enhancements._pyramid_fires) so live and backtest
cannot drift. Fields (all in NPZ + live dict, per TF): wt1_<tf>, wt2_<tf>, wt1_<tf>_prev, wt2_<tf>_prev.
TFs default {3m,15m,1h,4h}. Sizing handled by caller: 150% reentry (REENTRY_SIZE_BREAKOUT_MULT) or
fresh OPEN (START_POSITION_SIZE) when never opened.
"""
from __future__ import annotations
from typing import Optional, Tuple
import numpy as np

WT_CROSS_TFS = ("3m", "15m", "1h", "4h")

def _crossed(w1, w2, w1p, w2p, is_long: bool) -> bool:
    """Cross-up this bar (LONG): wt1 was <= wt2, now > wt2. Mirror for SHORT (cross-down)."""
    if is_long:
        return (w1p <= w2p) and (w1 > w2)
    return (w1p >= w2p) and (w1 < w2)

def higher_wt_cross_fires(w1, w2, w1p, w2p, last_cross_level: float, is_long: bool) -> Tuple[bool, Optional[float]]:
    """Per-TF, per-bar: returns (fires, new_cross_level).
    fires = a cross happened AND its level is HIGHER (LONG) / LOWER (SHORT) than the previous cross.
    Caller passes the symbol's last cross level for this TF (None if none yet) and stores new_cross_level.
    NOTE: the caller applies the FLAT (no-position) condition + the 150%/open sizing."""
    if not _crossed(w1, w2, w1p, w2p, is_long):
        return False, last_cross_level
    lvl = float(w1)  # cross level = wt1 at the cross
    if last_cross_level is None:
        return True, lvl  # first cross while flat → enter (and if never opened, OPEN)
    higher = (lvl > last_cross_level) if is_long else (lvl < last_cross_level)
    return bool(higher), lvl

def higher_wt_cross_vec(ind: dict, is_long: bool, tfs=WT_CROSS_TFS) -> np.ndarray:
    """Vectorized: per-bar mask True where a HIGHER (LONG) / LOWER (SHORT) WT cross fires on ANY tf.
    Tracks per-TF running last-cross-level via cumulative max/min over cross bars (no look-ahead:
    uses wt1_<tf>_prev for the prior bar). Same logic as the scalar core, array-form."""
    n = None
    out = None
    for tf in tfs:
        w1 = ind.get(f"wt1_{tf}"); w2 = ind.get(f"wt2_{tf}")
        w1p = ind.get(f"wt1_{tf}_prev"); w2p = ind.get(f"wt2_{tf}_prev")
        if w1 is None or w2 is None or w1p is None or w2p is None:
            continue
        w1 = np.asarray(w1, float); w2 = np.asarray(w2, float)
        w1p = np.asarray(w1p, float); w2p = np.asarray(w2p, float)
        if n is None:
            n = len(w1); out = np.zeros(n, dtype=bool)
        cross = (w1p <= w2p) & (w1 > w2) if is_long else (w1p >= w2p) & (w1 < w2)
        # prior-cross level = wt1 at the IMMEDIATELY PREVIOUS cross (matches scalar: last = lvl, not a running max)
        prior = np.full(n, np.nan)
        last = np.nan
        for i in range(n):
            if cross[i]:
                prior[i] = last
                last = w1[i]
        if is_long:
            higher = cross & (np.isnan(prior) | (w1 > prior))
        else:
            higher = cross & (np.isnan(prior) | (w1 < prior))
        out = out | higher
    if out is None:
        return np.zeros(0, dtype=bool)
    return out
