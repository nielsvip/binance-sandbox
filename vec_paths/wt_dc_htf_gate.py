"""
vec_paths/wt_dc_htf_gate.py — Vectorised WT_DC_HTF_GATE entry block.

Mirrors tradier_manage.py:2751-2762 exactly.

GATE: applied ONLY to the wt_open_ok (WT_3M force-open / WT_DC) entry path.
      GR, DELTA, BB_BREAK, etc. are NOT subject to this gate.

CONFIG KEYS:
    WT_DC_HTF_GATE: str = "none"   # 'none' | '1h' | '4h' | '4h_D'

LOGIC:
    gate = 'none'  → no block (pass all)
    gate = '1h'    → block if wt1_1h < wt2_1h (1h bearish for LONG) / wt1_1h > wt2_1h (1h bullish for SHORT)
    gate = '4h'    → block if wt1_4h < wt2_4h (4h against)
    gate = '4h_D'  → block if wt1_4h < wt2_4h (4h against) OR wt1_D < wt2_D (D against)

RETURNS:
    build_wt_dc_htf_gate_mask(npz, n, is_long, cfg) -> np.ndarray[bool]
        True at bar i = BLOCK this wt_open_ok entry bar.

Note on '1h':
    The live tradier_manage.py only handles '4h' and '4h_D'; '1h' currently
    falls through as no-block. This vec module implements '1h' support so
    sweeps can actually test it. The gate condition is symmetric with the
    existing '4h' logic: block when wt1_1h < wt2_1h (bearish) for LONG,
    wt1_1h > wt2_1h (bullish) for SHORT.
"""
from __future__ import annotations

from typing import Any, Dict

import numpy as np


def build_wt_dc_htf_gate_mask(
    npz: Dict[str, np.ndarray],
    n: int,
    is_long: bool,
    cfg: Any,
) -> np.ndarray:
    """Precompute per-bar WT_DC_HTF_GATE block mask (called once per symbol/side).

    Args:
        npz:      dict of NPZ arrays (field_name → np.ndarray of length n)
        n:        number of bars
        is_long:  True for LONG side
        cfg:      VecConfig (or any object with WT_DC_HTF_GATE attribute)

    Returns:
        block mask — bool np.ndarray of length n.
        True at bar i means: block the wt_open_ok entry at this bar.
    """
    block = np.zeros(n, dtype=bool)
    gate = str(getattr(cfg, "WT_DC_HTF_GATE", "none")).lower().strip()
    if gate == "none" or not gate:
        return block
    def _get(field: str) -> np.ndarray:
        arr = npz.get(field)
        if arr is None:
            return np.zeros(n, dtype=np.float32)
        arr = np.asarray(arr, dtype=np.float32)
        if arr.size != n:
            return np.zeros(n, dtype=np.float32)
        return np.nan_to_num(arr)
    if gate in ("1h",):
        wt1_1h = _get("wt1_1h")
        wt2_1h = _get("wt2_1h")
        if is_long:
            block = wt1_1h < wt2_1h
        else:
            block = wt1_1h > wt2_1h
    elif gate in ("4h", "4h_d"):
        wt1_4h = _get("wt1_4h")
        wt2_4h = _get("wt2_4h")
        if is_long:
            _4h_against = wt1_4h < wt2_4h
        else:
            _4h_against = wt1_4h > wt2_4h
        block = _4h_against
        if gate == "4h_d":
            wt1_D = _get("wt1_D")
            wt2_D = _get("wt2_D")
            if is_long:
                _D_against = wt1_D < wt2_D
            else:
                _D_against = wt1_D > wt2_D
            block = _4h_against | _D_against
    return block
