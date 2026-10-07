"""SHARED scalar+vectorized predicate for the LIVE decision DC_BASIS_3M_REDUCE.

LIVE SOURCE OF TRUTH: ez_manage.py process_position() lines ~43348-43359.

REDUCE-to-pos_min_qty when price breaks the 3m Donchian extreme against the
position (despite the "BASIS" in the name, the live threshold is dc_low_3m /
dc_high_3m, NOT dc_basis_3m):

  price_below_dc_low_3m_long  = is_long  AND dc_low_3m  > 0 AND price < dc_low_3m
  price_above_dc_high_3m_short = (not long) AND dc_high_3m > 0 AND price > dc_high_3m
  fires iff (price_below_dc_low_3m_long OR price_above_dc_high_3m_short)
            AND not config.HEDGE_MODE

The downstream gates that the LIVE caller still applies and are NOT part of this
pure predicate (state / control-flow, supplied by the engine seam):
  position.positionAmt > pos_min_qty   (size seam)
  not _recently_reduced                (reduction-cooldown state)
  not _check_loss_protection(...)      (loss-protection gate)
  reduction_cooldown_ok                (wall-clock cooldown)
  reduce_qty > pos_min_qty             (sizing tail)

CLASSIFICATION: manual -> vectorized core. The DECISION predicate (price vs the
3m DC extreme, plus the HEDGE_MODE switch) is pure per-bar over NPZ
dc_low_3m/dc_high_3m + price. The size/cooldown/loss-protection layer is the
action-emission tail and stays with the engine, exactly like the live caller.
"""
from typing import Tuple
import numpy as np


def _dc_basis_3m_reduce_fires(price: float, dc_low_3m: float, dc_high_3m: float,
                              is_long: bool, hedge_mode: bool) -> bool:
    """Pure per-bar fire test (size/cooldown/loss-protection applied by caller)."""
    if hedge_mode:
        return False
    if is_long:
        return dc_low_3m > 0 and price < dc_low_3m
    return dc_high_3m > 0 and price > dc_high_3m


def check_dc_basis_3m_reduce(config, indicators: dict, is_long: bool, price: float) -> Tuple[bool, str]:
    """LIVE/scalar path. HEDGE_MODE from config; sizing/cooldown gates by caller."""
    g = indicators or {}
    dl = float(g.get("dc_low_3m", 0) or 0)
    dh = float(g.get("dc_high_3m", 0) or 0)
    hedge_mode = bool(getattr(config, "HEDGE_MODE", False))
    if not _dc_basis_3m_reduce_fires(price, dl, dh, is_long, hedge_mode):
        return False, ""
    edge = dl if is_long else dh
    return True, f"DC_BASIS_3M_REDUCE_price_{'below' if is_long else 'above'}_dc_basis_3m_px{price:.6f}_edge{edge:.6f}"


def check_dc_basis_3m_reduce_vec(config, price_arr, dc_low_3m_arr, dc_high_3m_arr, is_long):
    """VECTORIZED per-bar fire mask. SAME logic as _dc_basis_3m_reduce_fires."""
    p = np.asarray(price_arr, dtype=float)
    dl = np.asarray(dc_low_3m_arr, dtype=float)
    dh = np.asarray(dc_high_3m_arr, dtype=float)
    if bool(getattr(config, "HEDGE_MODE", False)):
        return np.zeros(len(p), dtype=bool)
    if is_long:
        return (dl > 0) & (p < dl)
    return (dh > 0) & (p > dh)
