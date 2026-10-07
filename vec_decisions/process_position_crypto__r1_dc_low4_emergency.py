"""SHARED scalar+vectorized predicate for the LIVE decision R1_DC_LOW4_3M_EMERGENCY.

LIVE SOURCE OF TRUTH: ez_manage.py process_position() lines ~38576-38626.

R1 is the DC4 emergency close (USER 2026-05-09 / restricted 2026-05-23). Once a
position has a valid r1_stop (set at open or lazy-set from live dc_low4_3m /
dc_high4_3m), R1 fires CLOSE on ANY of three breach triggers (OR'd):

  (a) DC_LOW4_3M : price breaks the fixed dc4 stop level
        LONG  : current_price <= r1_stop
        SHORT : current_price >= r1_stop
  (b) 3xATR_3M   : price breaks a 3*ATR_3m fixed stop from entry
        LONG  : current_price <= entry_px - mult*atr_3m
        SHORT : current_price >= entry_px + mult*atr_3m
  (c) LH_LL_3M   : lower-high + lower-low 3m bar structural breakdown
        LONG  : high_3m < high_3m_prev AND low_3m < low_3m_prev
        SHORT : high_3m > high_3m_prev AND low_3m > low_3m_prev

The (b)/(c) sub-checks only run when (a) has NOT already breached (live short-
circuits with `if not _r1_breached`), but because all three are OR'd into the
final fire decision, evaluating all three unconditionally produces the IDENTICAL
fire result — that is what the vec path does. Guards mirror live exactly: (b)
requires atr_3m>0 and entry_px>0; (c) requires all four 3m hi/lo values >0.

CLASSIFICATION: stateful_seam. The fire predicate is a pure per-bar function of
NPZ-available fields (price, atr_3m, high/low_3m + _prev, dc_low4/high4_3m) PLUS
two per-position seam values the engine already maintains: r1_stop (the fixed
stop locked at open) and entry_price. The 2026-05-23 overbought-breakout RESTRICT
gate (entry-signal string match) and the position-age gate are NOT part of this
predicate — they are entry-context / state gates the caller applies before
calling this core (same as live, which sets _r1_stop=0 to skip when restricted).
"""
from typing import Tuple
import numpy as np


# ── SHARED per-bar R1 breach predicate (single source of truth) ──────────────
def _r1_breaches(price: float, r1_stop: float, is_long: bool,
                 atr_3m: float, entry_px: float, atr_mult: float,
                 high_3m: float, high_3m_prev: float,
                 low_3m: float, low_3m_prev: float) -> bool:
    """Pure: True iff any of DC_LOW4_3M / 3xATR_3M / LH_LL_3M breach. r1_stop<=0
    means R1 is skipped for this position (live forces this when not overbought
    -breakout or stop unset) → never fires."""
    if r1_stop <= 0:
        return False
    # (a) DC_LOW4_3M fixed-stop breach
    if is_long:
        if price <= r1_stop:
            return True
    else:
        if price >= r1_stop:
            return True
    # (b) 3xATR_3M fixed stop from entry
    if atr_3m > 0 and entry_px > 0:
        if is_long:
            atr_stop = entry_px - atr_mult * atr_3m
            if price <= atr_stop:
                return True
        else:
            atr_stop = entry_px + atr_mult * atr_3m
            if price >= atr_stop:
                return True
    # (c) LH_LL_3M structural breakdown
    if high_3m > 0 and high_3m_prev > 0 and low_3m > 0 and low_3m_prev > 0:
        if is_long:
            if high_3m < high_3m_prev and low_3m < low_3m_prev:
                return True
        else:
            if high_3m > high_3m_prev and low_3m > low_3m_prev:
                return True
    return False


def _r1_atr_mult(config) -> float:
    return float(getattr(config, "R1_ATR_3M_MULT", 3.0))


def check_r1_dc_low4_emergency(config, indicators: dict, is_long: bool,
                               r1_stop: float, entry_px: float,
                               price: float) -> Tuple[bool, str]:
    """LIVE/scalar path. Returns (fires, reason). Caller supplies r1_stop and
    entry_px from per-position state (the seam); indicators supply the per-bar
    NPZ fields. age + overbought-restrict gates are applied by the caller."""
    atr_mult = _r1_atr_mult(config)
    atr_3m = float((indicators or {}).get("atr_3m", 0) or 0)
    h = float((indicators or {}).get("high_3m", 0) or 0)
    hp = float((indicators or {}).get("high_3m_prev", 0) or 0)
    lo = float((indicators or {}).get("low_3m", 0) or 0)
    lp = float((indicators or {}).get("low_3m_prev", 0) or 0)
    if not _r1_breaches(price, r1_stop, is_long, atr_3m, entry_px, atr_mult, h, hp, lo, lp):
        return False, ""
    return True, f"R1_DC_LOW4_3M_EMERGENCY price={price:.6f}_stop={r1_stop:.6f}"


def check_r1_dc_low4_emergency_vec(config, price_arr, r1_stop_arr, entry_px_arr,
                                   atr_3m_arr, high_3m_arr, high_3m_prev_arr,
                                   low_3m_arr, low_3m_prev_arr, is_long):
    """VECTORIZED per-bar R1 breach mask. SAME OR-of-three logic as the scalar
    _r1_breaches. r1_stop_arr / entry_px_arr come from simulated position state."""
    p = np.asarray(price_arr, dtype=float)
    rs = np.asarray(r1_stop_arr, dtype=float)
    ep = np.asarray(entry_px_arr, dtype=float)
    atr = np.asarray(atr_3m_arr, dtype=float)
    h = np.asarray(high_3m_arr, dtype=float)
    hp = np.asarray(high_3m_prev_arr, dtype=float)
    lo = np.asarray(low_3m_arr, dtype=float)
    lp = np.asarray(low_3m_prev_arr, dtype=float)
    mult = _r1_atr_mult(config)
    active = rs > 0
    if is_long:
        a = p <= rs
        atr_ok = (atr > 0) & (ep > 0)
        b = atr_ok & (p <= (ep - mult * atr))
        lhll_ok = (h > 0) & (hp > 0) & (lo > 0) & (lp > 0)
        c = lhll_ok & (h < hp) & (lo < lp)
    else:
        a = p >= rs
        atr_ok = (atr > 0) & (ep > 0)
        b = atr_ok & (p >= (ep + mult * atr))
        lhll_ok = (h > 0) & (hp > 0) & (lo > 0) & (lp > 0)
        c = lhll_ok & (h > hp) & (lo > lp)
    return active & (a | b | c)
