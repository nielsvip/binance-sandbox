"""PROCESS_POSITION_STOCKS · R1 emergency + FROZEN_ACT_STOP — price-breach stops (shared scalar+vec).

LIVE SOURCE: tradier_manage.py process_position.

R1_DC_LOW4_EMERGENCY (lines ~2087-2106):
  stop level = position.r1_stop_price (set at open/augment), or LIVE fallback
  dc_low4_5m (LONG) / dc_high4_5m (SHORT) when r1_stop_price was never set.
  Breach (CLOSE) when:  LONG: price <= r1_stop ;  SHORT: price >= r1_stop   (r1_stop > 0)

FROZEN_ACT_STOP (lines ~2150-2169):
  frozen level = position._frozen_dc_act (= dc_low_4h@entry LONG / dc_high_4h@entry SHORT),
  absolute floor = FROZEN_ABSOLUTE_FLOOR_PCT_TRADIER (default -8.0).
  floor_hit = gain <= floor
  breach    = frozen is not None AND ((LONG and price<frozen) or (SHORT and price>frozen)) AND gain<0
  FIRE (CLOSE) when floor_hit OR breach.

2026-05-30 PARITY: live scalar AND vec twin derive from the SAME pure predicates
(_r1_breach_fires / _frozen_act_fires) — cannot drift.

CLASSIFICATION: stateful_seam. The BREACH math is a pure per-bar price-vs-level
comparison, BUT the level (r1_stop / frozen) and the gain are PER-POSITION SIM STATE the
engine already maintains (set at open/augment, frozen at entry, gain from entry_price).
So the vec mask takes the (already-resolved, possibly broadcast) level/gain arrays from
the engine's state seam — it does NOT recompute them. This is the same seam used by
position_evaluator for stateful exits. For the R1 LIVE-fallback (stop never stored), the
fallback level IS a pure indicator field (dc_low4_5m/dc_high4_5m) and is vectorizable when
the caller supplies it as the level array.

NPZ / indicator fields read by the FALLBACK path: dc_low4_5m, dc_high4_5m. State (caller-
supplied, NOT in this core): r1_stop level, frozen level, position gain%.
"""
from typing import Tuple


# ── R1_DC_LOW4_EMERGENCY ────────────────────────────────────────────────────────
def _r1_breach_fires(current_price: float, r1_stop: float, is_long: bool) -> bool:
    """PURE per-bar breach. Faithful replica of tradier_manage.py lines 2102-2105.
    r1_stop must be > 0 to be active (matches the `if _r1_stop > 0` guard)."""
    if r1_stop <= 0:
        return False
    if is_long:
        return current_price <= r1_stop
    return current_price >= r1_stop


def check_r1_breach(config, indicators: dict, current_price: float, r1_stop_price: float,
                    is_long: bool) -> Tuple[bool, str]:
    """LIVE/scalar path. r1_stop_price is the per-position state (position.r1_stop_price).
    If it is <=0, fall back to the live indicator level dc_low4_5m / dc_high4_5m (line ~2091)."""
    if not bool(getattr(config, "R1_DC_LOW4_3M_EMERGENCY_ENABLED", True)):
        return False, ""
    r1_stop = float(r1_stop_price or 0.0)
    if r1_stop <= 0:
        fallback_key = "dc_low4_5m" if is_long else "dc_high4_5m"
        r1_stop = float(indicators.get(fallback_key, 0.0) or 0.0)
    if not _r1_breach_fires(current_price, r1_stop, is_long):
        return False, ""
    return True, f"R1_DC_LOW4_EMERGENCY_stop{r1_stop:.4f}"


def check_r1_breach_vec(config, current_price_arr, r1_stop_arr, is_long):
    """VECTORIZED per-bar breach mask. r1_stop_arr = per-bar resolved stop level (state seam:
    stored r1_stop_price broadcast, OR the dc_low4_5m/dc_high4_5m fallback array the caller
    supplies). SAME predicate as the live scalar. Returns a bool ndarray."""
    import numpy as np
    p = np.asarray(current_price_arr, dtype=float)
    if not bool(getattr(config, "R1_DC_LOW4_3M_EMERGENCY_ENABLED", True)):
        return np.zeros(len(p), dtype=bool)
    s = np.asarray(r1_stop_arr, dtype=float)
    active = s > 0
    if is_long:
        return active & (p <= s)
    return active & (p >= s)


# ── FROZEN_ACT_STOP ─────────────────────────────────────────────────────────────
def _frozen_act_fires(current_price: float, gain_pct: float, frozen_level, is_long: bool,
                      floor_pct: float) -> Tuple[bool, str]:
    """PURE per-bar. Faithful replica of tradier_manage.py lines 2164-2169.
    frozen_level None means no frozen breach possible (only floor_hit can fire)."""
    floor_hit = gain_pct <= floor_pct
    breach = False
    if frozen_level is not None:
        if is_long:
            breach = current_price < frozen_level and gain_pct < 0
        else:
            breach = current_price > frozen_level and gain_pct < 0
    if floor_hit:
        return True, "FROZEN_ACT_STOP_ABSOLUTE_FLOOR"
    if breach:
        return True, "FROZEN_ACT_STOP_FROZEN_BREACH"
    return False, ""


def _frozen_floor_pct(config) -> float:
    return float(getattr(config, "FROZEN_ABSOLUTE_FLOOR_PCT_TRADIER", -8.0))


def check_frozen_act_stop(config, current_price: float, gain_pct: float, frozen_level,
                          is_long: bool) -> Tuple[bool, str]:
    """LIVE/scalar path. frozen_level + gain_pct are per-position state the engine maintains."""
    if not bool(getattr(config, "FROZEN_ACTIVATION_STOP_ENABLED", True)):
        return False, ""
    return _frozen_act_fires(current_price, gain_pct, frozen_level, is_long, _frozen_floor_pct(config))


def check_frozen_act_stop_vec(config, current_price_arr, gain_arr, frozen_level_arr, is_long):
    """VECTORIZED per-bar fire mask. frozen_level_arr = per-bar frozen level (state seam; use NaN
    for 'no frozen level' = the None case). gain_arr = per-bar gain%. SAME predicate as scalar.
    Returns a bool ndarray (fires)."""
    import numpy as np
    p = np.asarray(current_price_arr, dtype=float)
    if not bool(getattr(config, "FROZEN_ACTIVATION_STOP_ENABLED", True)):
        return np.zeros(len(p), dtype=bool)
    g = np.asarray(gain_arr, dtype=float)
    fl = np.asarray(frozen_level_arr, dtype=float)
    floor_pct = _frozen_floor_pct(config)
    floor_hit = g <= floor_pct
    has_frozen = ~np.isnan(fl)
    if is_long:
        breach = has_frozen & (p < fl) & (g < 0)
    else:
        breach = has_frozen & (p > fl) & (g < 0)
    return floor_hit | breach
