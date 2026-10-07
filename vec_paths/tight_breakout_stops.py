"""vec_paths/tight_breakout_stops.py — 2026-05-19 USER MANDATE.

"BREAKOUT trades on low tf need to be closed before gain<0 OR below dc_low4_3(5)m
OR when hitting previous dc or bb high from above (vv shorts). We are DETERIORATING
this needs to stop now."

Three tight exit rules:

NEVER_GO_RED — close immediately when gain<0 AFTER max_gain has reached
   NEVER_GO_RED_MIN_PEAK_PCT (default 0.3%). Prevents giving back any profit.
   This is STATE-DEPENDENT (uses position.max_gain), so the engine evaluates
   it inline in the hot loop — no precomputed array.

CHANNEL_REENTRY — close when price was OUTSIDE the channel (above dc_high_<TF>
   or bb_upper_<TF> for LONG; below low for SHORT) at some prior bar of the
   position's life AND has just crossed back INSIDE. STATE-DEPENDENT (per-position
   `_ever_outside_channel` flag), inline check.

Both are LONG/SHORT symmetric.

KNOBS REFERENCED:
    NEVER_GO_RED_STOP_ENABLED
    NEVER_GO_RED_MIN_PEAK_PCT          # don't fire unless max_gain >= this
    NEVER_GO_RED_BUFFER_PCT            # fire when gain < -buffer (default 0.0)
    CHANNEL_REENTRY_STOP_ENABLED
    CHANNEL_REENTRY_STOP_TF            # 15m / 1h / 4h
    CHANNEL_REENTRY_STOP_FIELD         # 'dc_high' or 'bb_upper' (auto-flips for SHORT)
"""
from __future__ import annotations
import numpy as np

TIGHT_BREAKOUT_STOP_KNOBS = (
    "NEVER_GO_RED_STOP_ENABLED",
    "NEVER_GO_RED_MIN_PEAK_PCT",
    "NEVER_GO_RED_BUFFER_PCT",
    "CHANNEL_REENTRY_STOP_ENABLED",
    "CHANNEL_REENTRY_STOP_TF",
    "CHANNEL_REENTRY_STOP_FIELD",
)


def _get_arr(npz, key: str, n: int) -> np.ndarray:
    arr = npz.get(key)
    if arr is None:
        return np.zeros(n, dtype=np.float32)
    return np.nan_to_num(arr, nan=0.0).astype(np.float32)


def build_channel_arrays(npz: dict, n: int, is_long: bool, config) -> tuple:
    """Returns (channel_upper_arr, channel_lower_arr) on base TF.

    For LONG: upper = dc_high or bb_upper (the level price must NOT re-cross from above);
              lower = the inner side; we monitor price relative to upper.
    For SHORT: mirror — track lower band.

    Caller uses upper_arr for LONG, lower_arr for SHORT.
    """
    if not bool(getattr(config, "CHANNEL_REENTRY_STOP_ENABLED", False)):
        return np.zeros(n, dtype=np.float32), np.zeros(n, dtype=np.float32)
    tf = str(getattr(config, "CHANNEL_REENTRY_STOP_TF", "1h"))
    field = str(getattr(config, "CHANNEL_REENTRY_STOP_FIELD", "dc_high"))
    if is_long:
        if field == "bb_upper":
            upper = _get_arr(npz, f"bb_upper_{tf}", n)
        else:
            upper = _get_arr(npz, f"dc_high_{tf}", n)
        return upper, np.zeros(n, dtype=np.float32)
    else:
        if field == "bb_upper":  # symmetric — use bb_lower
            lower = _get_arr(npz, f"bb_lower_{tf}", n)
        else:
            lower = _get_arr(npz, f"dc_low_{tf}", n)
        return np.zeros(n, dtype=np.float32), lower


def check_never_go_red(gain: float, max_gain: float, config) -> tuple[bool, str]:
    """Inline check — returns (fire, reason)."""
    if not bool(getattr(config, "NEVER_GO_RED_STOP_ENABLED", False)):
        return False, ""
    min_peak = float(getattr(config, "NEVER_GO_RED_MIN_PEAK_PCT", 0.3))
    buffer = float(getattr(config, "NEVER_GO_RED_BUFFER_PCT", 0.0))
    if max_gain < min_peak:
        return False, ""
    if gain < -buffer:
        return True, f"NGR_peak{max_gain:.2f}_now{gain:.2f}"
    return False, ""


def check_channel_reentry(
    mark: float, is_long: bool, ever_outside: bool,
    channel_upper: float, channel_lower: float, config,
) -> tuple[bool, str, bool]:
    """Inline check — returns (fire, reason, new_ever_outside_flag).

    LONG: ever_outside = True once mark > upper (price broke out above channel).
          Fire when ever_outside AND mark < upper (came back inside).
    SHORT: ever_outside = True once mark < lower. Fire when ever_outside AND mark > lower.
    """
    if not bool(getattr(config, "CHANNEL_REENTRY_STOP_ENABLED", False)):
        return False, "", ever_outside
    new_ever = ever_outside
    if is_long:
        if channel_upper > 0:
            if mark > channel_upper:
                new_ever = True
            elif new_ever and mark < channel_upper:
                tf = str(getattr(config, "CHANNEL_REENTRY_STOP_TF", "1h"))
                field = str(getattr(config, "CHANNEL_REENTRY_STOP_FIELD", "dc_high"))
                return True, f"CHRE_{field}_{tf}_px{mark:.4f}_band{channel_upper:.4f}", new_ever
    else:
        if channel_lower > 0:
            if mark < channel_lower:
                new_ever = True
            elif new_ever and mark > channel_lower:
                tf = str(getattr(config, "CHANNEL_REENTRY_STOP_TF", "1h"))
                field = str(getattr(config, "CHANNEL_REENTRY_STOP_FIELD", "dc_high"))
                return True, f"CHRE_{field}_{tf}_px{mark:.4f}_band{channel_lower:.4f}", new_ever
    return False, "", new_ever
