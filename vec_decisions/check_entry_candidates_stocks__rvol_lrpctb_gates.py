"""RVOL_MOMENTUM + LR_PCTB_D (short) entry gates (stocks) — pure per-bar block predicates.

LIVE SOURCE: tradier_manage.py should_enter_long/short (fallback section).
  RVOL_GATE (~11957/12237, both sides): block when rel_vol_5m < RVOL_MOMENTUM_MIN (1.5) AND rel_vol_5m > 0.
                           (rel_vol_5m default 1.0 — note default passes since 1.0<1.5 → blocked.)
  LR_PCTB_D (SHORT only ~12343): when LR_PCTB_D_SHORT_THRESHOLD > 0, block when lr_pctb_D < threshold (0.1).
                           lr_pctb_D default 0.5.

Returns True == BLOCKED. 2026-05-30 PARITY: shared pure predicates drive scalar+vec.
NPZ fields: rel_vol_5m (alias of relative_volume_5m), lr_pctb_D (alias of bb_pct_b_D) — both present.
"""
from typing import Tuple


def _rvol_gate_blocks(rel_vol_5m: float, rvol_min: float) -> bool:
    """PURE: block when rel_vol_5m < rvol_min AND rel_vol_5m > 0 (faithful to live both-sided gate)."""
    return rel_vol_5m < rvol_min and rel_vol_5m > 0


def _lr_pctb_d_blocks_short(lr_pctb_D: float, threshold: float) -> bool:
    """PURE (SHORT only): block when lr_pctb_D < threshold."""
    return lr_pctb_D < threshold


def _rvol_min(config) -> float:
    return float(getattr(config, "RVOL_MOMENTUM_MIN", 1.5))


def _lr_pctb_short_thr(config) -> float:
    return float(getattr(config, "LR_PCTB_D_SHORT_THRESHOLD", 0.0))


def check_rvol_gate(config, indicators: dict) -> Tuple[bool, str]:
    rv = float(indicators.get("rel_vol_5m", 1.0) or 1.0)
    if _rvol_gate_blocks(rv, _rvol_min(config)):
        return True, "RVOL_GATE_BLOCK"
    return False, ""


def check_lr_pctb_d_short(config, indicators: dict) -> Tuple[bool, str]:
    thr = _lr_pctb_short_thr(config)
    if thr <= 0:
        return False, ""
    lr = float(indicators.get("lr_pctb_D", 0.5) or 0.5)
    if _lr_pctb_d_blocks_short(lr, thr):
        return True, "LR_PCTB_D_BLOCK_SHORT"
    return False, ""


def check_rvol_gate_vec(config, rel_vol_5m_arr):
    import numpy as np
    rv = np.asarray(rel_vol_5m_arr, dtype=float)
    rmin = _rvol_min(config)
    return (rv < rmin) & (rv > 0)


def check_lr_pctb_d_short_vec(config, lr_pctb_D_arr):
    import numpy as np
    lr = np.asarray(lr_pctb_D_arr, dtype=float)
    thr = _lr_pctb_short_thr(config)
    if thr <= 0:
        return np.zeros(lr.shape, dtype=bool)
    return lr < thr
