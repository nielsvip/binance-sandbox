"""UAGAIN_GATE — vector twin of the live execute_now UNIVERSAL_AUGMENT_GAIN_GATE choke point (Agent C2, staged b5).

LIVE SOURCE (ez_manage.py:31577-31630, execute_now): for EVERY non-reduce order on an EXISTING position (augment / quick / reentry-on-
existing), before any reason/action routing: gain_since_last_add = side-adjusted (px - last_augmentation_price)/last_augmentation_price
(entry price when never augmented). If gain_since_last_add < AUGMENT_MIN_GAIN_PCT (fallback MIN_GAIN_TO_BUY_AGGRESSIVELY, 3.0) the order is
BLOCKED ("BLOCKED_UAGAIN_...") unless the PULLBACK exception holds: PULLBACK_AUGMENT_ENABLED and max_gain >= min and raw_gain >= 0.5*min and
(max_gain - raw_gain) >= PULLBACK_AUGMENT_REVERSAL_MIN. Gate switch: UNIVERSAL_AUGMENT_GAIN_GATE_ENABLED (default True).

The existing vector twin (gain_ladder_augment.gain_ladder_fire) models the gate only for the ladder-INITIATED augment. The augment sources
augment_sig (bounce/pyramid) and FAST_RISER bypassed it in the vector although live blocks them at the same choke point. This module is the
gate predicate alone (no profit gate, no cooldown) so simulate_one can apply it to those sources.
"""
from __future__ import annotations


def effective_min_gain(config):
    """MIRROR of live_entry_gates.effective_min_gain (Agent D batch5, LG-19): AUGMENT_MIN_GAIN_PCT>0 = explicit override, else MIN_GAIN_TO_BUY_AGGRESSIVELY; floor 2.5."""
    try:
        a = float(getattr(config, "AUGMENT_MIN_GAIN_PCT", 0.0) or 0.0)
    except (TypeError, ValueError):
        a = 0.0
    try:
        m = float(getattr(config, "MIN_GAIN_TO_BUY_AGGRESSIVELY", 3.0) or 3.0)
    except (TypeError, ValueError):
        m = 3.0
    return max(2.5, a if a > 0 else m)



def uagain_gate_pass(config, is_long: bool, px: float, last_aug_px: float, raw_gain_pct: float, max_gain_pct: float, min_gain_override=None) -> bool:
    if not bool(getattr(config, "UNIVERSAL_AUGMENT_GAIN_GATE_ENABLED", True)):
        return True
    min_gain = effective_min_gain(config) if min_gain_override is None else float(min_gain_override)  # [UNWV/002] typed tier (AUGMENT_TYPED_MIN_GAIN_ENABLED) replaces the generic tier for typed augments
    if not (last_aug_px and last_aug_px > 0 and px and px > 0):
        return True  # live fail-open: no reference price -> gate not evaluated
    gain_since = ((px - last_aug_px) / last_aug_px * 100.0) if is_long else ((last_aug_px - px) / last_aug_px * 100.0)
    if gain_since >= min_gain:
        return True
    pb_enabled = bool(getattr(config, "PULLBACK_AUGMENT_ENABLED", True))
    pb_rev = float(getattr(config, "PULLBACK_AUGMENT_REVERSAL_MIN", 1.0) or 1.0)
    return bool(pb_enabled and max_gain_pct >= min_gain and raw_gain_pct >= 0.5 * min_gain and (max_gain_pct - raw_gain_pct) >= pb_rev)
