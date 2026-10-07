"""vec_decisions/dc_break.py — SHARED scalar+vectorized DC_BREAKOUT_ENTRY predicate.

Single source of truth for the live DC breakout *entry-scoring* decision
(BACKTEST_CHANGE_133). The live scalar path (check_dc_break) AND the vectorized
backtest path (check_dc_break_vec) BOTH derive their fire decision from the same
pure per-bar core predicate _dc_break_fires(), so the two paths CANNOT drift.

Faithful to ez_positions_quick.py:4296-4305 (DC_BREAKOUT_ENTRY scoring block):

    if getattr(config, 'DC_BREAKOUT_ENTRY_ENABLED', False) and not is_exit:
        _dc_tf = getattr(config, 'DC_BREAKOUT_TF', '1h')
        _dc_hi = safe_fetch_float(ind.get(f'dc_high_{_dc_tf}'), 0)
        _dc_lo = safe_fetch_float(ind.get(f'dc_low_{_dc_tf}'), 0)
        _dc_adx = safe_fetch_float(ind.get(f'adx_{_dc_tf}'), 0)
        if _dc_hi > 0 and _dc_lo > 0 and _dc_adx > 25:
            if is_long and current_price > _dc_hi:        # LONG fires
                score += DC_BREAKOUT_SCORE
            elif not is_long and current_price < _dc_lo:  # SHORT fires
                score += DC_BREAKOUT_SCORE

NOTE: this is a SCORE CONTRIBUTION inside check_entry_signal_for_account scoring,
NOT a standalone open. It returns whether the DC-breakout bonus fires (True), which
the entry scorer adds DC_BREAKOUT_SCORE points for. The vec mask is the per-bar
"this bonus would fire" indicator.

Indicator / NPZ fields read (TF = config.DC_BREAKOUT_TF, default "1h"):
    dc_high_<TF>   (e.g. dc_high_1h)   — present in NPZ (precompute writes dc_high_*)
    dc_low_<TF>    (e.g. dc_low_1h)    — present in NPZ
    adx_<TF>       (e.g. adx_1h)       — present in NPZ (precompute writes adx_<tf>, line 891)
    current_price / close              — close array in vec

Config thresholds:
    DC_BREAKOUT_ENTRY_ENABLED  (default True)
    DC_BREAKOUT_TF             (default "1h")
    DC_BREAKOUT_SCORE          (default 15)  — points added, returned for reference
    DC_BREAKOUT_MIN_ADX        (default 25.0) — live HARDCODED as `_dc_adx > 25`;
                                exposed as a config knob defaulting to the live value
                                so parity is preserved when the knob is absent.
"""
import numpy as np
from typing import Tuple


# ── SHARED per-bar DC_BREAKOUT predicate (single source of truth) ─────────────
# Pure: no config, no state, no I/O. Live scalar + vectorized backtest both call
# this exact logic, so they cannot drift. Mirrors the boolean test at
# ez_positions_quick.py:4301-4305 EXACTLY (strict `>`/`<`, `dc_hi>0 and dc_lo>0
# and adx>min_adx` guard).
def _dc_break_fires(current_price: float, dc_hi: float, dc_lo: float, dc_adx: float,
                    is_long: bool, min_adx: float) -> bool:
    if not (dc_hi > 0 and dc_lo > 0 and dc_adx > min_adx):
        return False
    if is_long:
        return current_price > dc_hi
    return current_price < dc_lo


def _dc_break_thresholds(config):
    return (str(getattr(config, "DC_BREAKOUT_TF", "1h")),
            float(getattr(config, "DC_BREAKOUT_MIN_ADX", 25.0)),
            int(getattr(config, "DC_BREAKOUT_SCORE", 15)))


def check_dc_break(config, indicators: dict, current_price: float,
                   is_long: bool) -> Tuple[bool, int, str]:
    """LIVE/scalar path. Return (fires, score_bonus, reason).
    Fire decision comes from the shared _dc_break_fires() predicate so it cannot
    drift from the vectorized backtest path."""
    if not getattr(config, "DC_BREAKOUT_ENTRY_ENABLED", False):
        return False, 0, ""
    tf, min_adx, score = _dc_break_thresholds(config)
    def _f(key):
        v = indicators.get(key)
        try:
            return float(v) if v is not None else 0.0
        except (TypeError, ValueError):
            return 0.0
    dc_hi = _f(f"dc_high_{tf}")
    dc_lo = _f(f"dc_low_{tf}")
    dc_adx = _f(f"adx_{tf}")
    if not _dc_break_fires(current_price, dc_hi, dc_lo, dc_adx, is_long, min_adx):
        return False, 0, ""
    _side = "L" if is_long else "S"
    _ref = dc_hi if is_long else dc_lo
    _cmp = ">" if is_long else "<"
    return True, score, f"DC_BKOUT_{_side}({_cmp}{_ref:.2f},adx={dc_adx:.0f})_bc133"


def check_dc_break_vec(config, current_price_arr, dc_hi_arr, dc_lo_arr, dc_adx_arr,
                       is_long) -> np.ndarray:
    """VECTORIZED per-bar DC_BREAKOUT fire mask — backtest path. SAME thresholds +
    SAME predicate as the live scalar check_dc_break (vectorized via numpy).
    Returns a bool ndarray. Arrays are per-bar (NPZ in backtest):
        current_price_arr = close
        dc_hi_arr         = npz['dc_high_<TF>']
        dc_lo_arr         = npz['dc_low_<TF>']
        dc_adx_arr        = npz['adx_<TF>']"""
    p = np.asarray(current_price_arr, dtype=float)
    if not getattr(config, "DC_BREAKOUT_ENTRY_ENABLED", False):
        return np.zeros(len(p), dtype=bool)
    _tf, min_adx, _score = _dc_break_thresholds(config)
    hi = np.asarray(dc_hi_arr, dtype=float)
    lo = np.asarray(dc_lo_arr, dtype=float)
    adx = np.asarray(dc_adx_arr, dtype=float)
    guard = (hi > 0) & (lo > 0) & (adx > min_adx)
    if is_long:
        return guard & (p > hi)
    return guard & (p < lo)
