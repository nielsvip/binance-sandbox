"""check_exit_candidates_crypto__breakeven_dc_struct — DC(4)_LOW/HIGH_3M_GAIN_EROSION
structural stop predicate (shared scalar+vectorized).

LIVE SOURCE: ez_positions_quick.py check_exit_candidates_for_account, lines
14157-14181 (BREAKEVEN_DC_LOW4 block):

    _dc_mode = config.BREAKEVEN_DC_FIELD_MODE  ('DC4' default | 'DC')
    if _dc_mode == 'DC':  low=dc_low_3m   high=dc_high_3m    tag='DC'
    else:                 low=dc_low4_3m  high=dc_high4_3m   tag='DC4'
    LONG : low > 0 and price > 0 and price < low   -> (HTF veto? hold : fire DC4_LOW_3M...)
    SHORT: high> 0 and price > 0 and price > high   -> (HTF veto? hold : fire DC4_HIGH_3M...)

Pure per-bar structural break (price below the 3m 4-bar Donchian low for LONG / above
the high for SHORT). Gates applied by caller: not hard_exit_reason, not _in_grace_period,
BREAKEVEN_DC_LOW4_ENABLED. The HTF-veto is a runtime/state seam: when active the live
block HOLDS instead of firing, so the vec wrapper accepts an htf_veto mask and suppresses.

2026-05-30 PARITY (mirrors strategy_enhancements._pyramid_fires pattern): live scalar
(check_breakeven_dc_struct) AND vec (check_breakeven_dc_struct_vec) share the pure core
_breakeven_dc_struct_fires() — CANNOT drift.

NPZ / indicator fields read (mode-dependent):
  DC4 mode: dc_low4_3m / dc_high4_3m   — present in NPZ
  DC  mode: dc_low_3m  / dc_high_3m    — present in NPZ
  current_price / close

Config:
  BREAKEVEN_DC_LOW4_ENABLED   (default True)
  BREAKEVEN_DC_FIELD_MODE     (default 'DC4')
"""
from typing import Tuple
import numpy as np


def _breakeven_dc_struct_fires(current_price: float, dc_low: float, dc_high: float,
                               is_long: bool) -> bool:
    """PURE per-bar structural-break test. Mirrors live 14170 / 14176 EXACTLY."""
    if is_long:
        return dc_low > 0 and current_price > 0 and current_price < dc_low
    return dc_high > 0 and current_price > 0 and current_price > dc_high


def _breakeven_dc_struct_fields(config):
    mode = str(getattr(config, "BREAKEVEN_DC_FIELD_MODE", "DC4")).upper()
    if mode == "DC":
        return "dc_low_3m", "dc_high_3m", "DC"
    return "dc_low4_3m", "dc_high4_3m", "DC4"


def check_breakeven_dc_struct(config, indicators: dict, current_price: float,
                              is_long: bool, htf_veto_active: bool = False) -> Tuple[bool, str]:
    """LIVE/scalar path. Returns (fires, reason). When htf_veto_active the live block
    HOLDS (logs HTF_VETO_<tag>) instead of firing — replicated here."""
    if not getattr(config, "BREAKEVEN_DC_LOW4_ENABLED", True):
        return False, ""
    low_key, high_key, tag = _breakeven_dc_struct_fields(config)
    dc_low = float(indicators.get(low_key, 0) or 0)
    dc_high = float(indicators.get(high_key, 0) or 0)
    if not _breakeven_dc_struct_fires(current_price, dc_low, dc_high, is_long):
        return False, ""
    if htf_veto_active:
        return False, ""
    if is_long:
        return True, f"{tag}_LOW_3M_GAIN_EROSION_STOP_p{current_price:.6f}<{tag.lower()}{dc_low:.6f}"
    return True, f"{tag}_HIGH_3M_GAIN_EROSION_STOP_p{current_price:.6f}>{tag.lower()}{dc_high:.6f}"


def check_breakeven_dc_struct_vec(config, current_price_arr, dc_low_arr, dc_high_arr,
                                  is_long, htf_veto_arr=None) -> np.ndarray:
    """VECTORIZED per-bar fire mask — backtest path. SAME predicate as scalar.
    dc_low_arr / dc_high_arr are the mode-selected NPZ arrays (DC4: dc_low4_3m /
    dc_high4_3m; DC: dc_low_3m / dc_high_3m). htf_veto_arr suppresses fires (default
    all-False)."""
    p = np.asarray(current_price_arr, dtype=float)
    n = len(p)
    if not getattr(config, "BREAKEVEN_DC_LOW4_ENABLED", True):
        return np.zeros(n, dtype=bool)
    if is_long:
        lo = np.asarray(dc_low_arr, dtype=float)
        m = (lo > 0) & (p > 0) & (p < lo)
    else:
        hi = np.asarray(dc_high_arr, dtype=float)
        m = (hi > 0) & (p > 0) & (p > hi)
    if htf_veto_arr is not None:
        m &= ~np.asarray(htf_veto_arr, dtype=bool)
    return m
