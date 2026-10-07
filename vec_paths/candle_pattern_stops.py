"""vec_paths/candle_pattern_stops.py — Phase B 2026-05-19.

New stop-loss family for the no-hedge regime sweep. All stops are
EXIT-ONLY (close-the-position triggers). LONG exits fire on bearish
candle patterns; SHORT exits fire on bullish (mirror).

Patterns implemented (all on a configurable TF in {3m,15m,1h,4h,D}):

  LH  — Lower-High exit:
        LONG  exits if current TF-bar high < prev TF-bar high
        SHORT exits if current TF-bar low  > prev TF-bar low (i.e. higher-low)

  LL  — Lower-Low exit:
        LONG  exits if current TF-bar low  < prev TF-bar low (range breakdown)
        SHORT exits if current TF-bar high > prev TF-bar high (range break-up)

  IB  — Inside-Bar at extreme exit:
        Bar fully inside prior (h<prev_h AND l>prev_l) AND near recent N-bar extreme
        LONG  exits if inside-bar AND prev_h >= max(high[i-N:i])  (compression at top)
        SHORT exits if inside-bar AND prev_l <= min(low [i-N:i])  (compression at bottom)

  PULLBACK — 3 variants, master TF, mode selector:
        mode='red1'  : close < open (single red bar)
        mode='red2'  : close < open AND close < prev close
        mode='red3'  : close < open AND close < prev low
        LONG  exits on red bar; SHORT exits on green-equivalent (close>open ...)

  BB_TAG_FAIL — recent BB tag failure exit:
        Long  : a TF-bar within the last LOOKBACK bars touched bb_upper
                AND the CURRENT TF-bar's high < bb_upper
        Short : symmetric on bb_lower

All builders return float32 boolean arrays indexed on base-TF bars i (0..n-1).
The base-TF granularity matches v8_vec_sweep's hot loop (3m crypto / 5m tradier),
so each builder maps the TF-bar pattern onto the base TF via _step_function helper:
the pattern is True for ALL base-bars within a TF-bar where the pattern holds.
Backtests then close on FIRST such base-bar after the pattern engages.

KNOBS REFERENCED (whole-word — audit_vec_aware_knobs.py reads):
    LH_STOP_ENABLED
    LH_STOP_TF
    LL_STOP_ENABLED
    LL_STOP_TF
    IB_STOP_ENABLED
    IB_STOP_TF
    IB_STOP_LOOKBACK
    PULLBACK_STOP_ENABLED
    PULLBACK_STOP_TF
    PULLBACK_STOP_MODE
    BB_TAG_FAIL_STOP_ENABLED
    BB_TAG_FAIL_STOP_TF
    BB_TAG_FAIL_STOP_LOOKBACK
"""
from __future__ import annotations
import numpy as np
from typing import Tuple

CANDLE_PATTERN_STOP_KNOBS = (
    "LH_STOP_ENABLED",
    "LH_STOP_TF",
    "LL_STOP_ENABLED",
    "LL_STOP_TF",
    "IB_STOP_ENABLED",
    "IB_STOP_TF",
    "IB_STOP_LOOKBACK",
    "PULLBACK_STOP_ENABLED",
    "PULLBACK_STOP_TF",
    "PULLBACK_STOP_MODE",
    "BB_TAG_FAIL_STOP_ENABLED",
    "BB_TAG_FAIL_STOP_TF",
    "BB_TAG_FAIL_STOP_LOOKBACK",
)

PULLBACK_MODES = ("red1", "red2", "red3")


def _get_arr(npz, key: str, n: int) -> np.ndarray:
    """Read npz array with key, fall back to zeros if missing/None."""
    arr = npz.get(key)
    if arr is None:
        return np.zeros(n, dtype=np.float32)
    return np.nan_to_num(arr, nan=0.0).astype(np.float32)


def _shift_prev(arr: np.ndarray) -> np.ndarray:
    """Shift array forward by 1 (i.e. prev value at each index)."""
    out = np.empty_like(arr)
    out[0] = 0.0
    out[1:] = arr[:-1]
    return out


def _rolling_max_prev(arr: np.ndarray, window: int) -> np.ndarray:
    """Max over [i-window:i] (exclusive of i, exclusive past)."""
    n = arr.shape[0]
    out = np.zeros(n, dtype=arr.dtype)
    if window <= 0 or n == 0:
        return out
    cumlen = max(1, window)
    for i in range(n):
        lo = max(0, i - cumlen)
        hi = i  # exclusive
        if hi <= lo:
            out[i] = 0.0
        else:
            out[i] = arr[lo:hi].max()
    return out


def _rolling_min_prev(arr: np.ndarray, window: int) -> np.ndarray:
    n = arr.shape[0]
    out = np.zeros(n, dtype=arr.dtype)
    if window <= 0 or n == 0:
        return out
    cumlen = max(1, window)
    for i in range(n):
        lo = max(0, i - cumlen)
        hi = i
        if hi <= lo:
            out[i] = 0.0
        else:
            out[i] = arr[lo:hi].min()
    return out


def build_lower_high_low_arrays(
    npz: dict, tf: str, n: int, is_long: bool,
) -> Tuple[np.ndarray, np.ndarray]:
    """Returns (lh_fire_mask, ll_fire_mask) on base TF for the LONG/SHORT side.

    LH (LONG): high_<tf>[i] < high_<tf>[i-1]  →  position should exit
    LH (SHORT): low_<tf>[i] > low_<tf>[i-1]   (higher low → bullish, exit short)
    LL (LONG): low_<tf>[i]  < low_<tf>[i-1]
    LL (SHORT): high_<tf>[i] > high_<tf>[i-1]
    """
    h = _get_arr(npz, f"high_{tf}", n)
    l = _get_arr(npz, f"low_{tf}", n)
    h_prev = _shift_prev(h)
    l_prev = _shift_prev(l)
    if is_long:
        lh = (h > 0) & (h_prev > 0) & (h < h_prev)
        ll = (l > 0) & (l_prev > 0) & (l < l_prev)
    else:
        lh = (l > 0) & (l_prev > 0) & (l > l_prev)
        ll = (h > 0) & (h_prev > 0) & (h > h_prev)
    return lh, ll


def build_inside_bar_extreme_array(
    npz: dict, tf: str, n: int, is_long: bool, lookback: int,
) -> np.ndarray:
    """Inside-bar (h<prev_h AND l>prev_l) AND at recent N-bar extreme."""
    h = _get_arr(npz, f"high_{tf}", n)
    l = _get_arr(npz, f"low_{tf}", n)
    h_prev = _shift_prev(h)
    l_prev = _shift_prev(l)
    inside = (h > 0) & (h_prev > 0) & (l_prev > 0) & (h < h_prev) & (l > l_prev)
    if is_long:
        recent_high = _rolling_max_prev(h, lookback)
        at_extreme = (h_prev > 0) & (recent_high > 0) & (h_prev >= recent_high - 1e-9)
    else:
        recent_low = _rolling_min_prev(l, lookback)
        at_extreme = (l_prev > 0) & (recent_low > 0) & (l_prev <= recent_low + 1e-9)
    return inside & at_extreme


def build_pullback_array(
    npz: dict, tf: str, n: int, is_long: bool, mode: str,
) -> np.ndarray:
    """3-variant pullback fire mask on the base TF.

    mode='red1' : close < open
    mode='red2' : close < open AND close < prev close
    mode='red3' : close < open AND close < prev low
    SHORT mirrors with > instead of <.
    """
    o = _get_arr(npz, f"open_{tf}", n)
    c = _get_arr(npz, f"close_{tf}", n)
    l = _get_arr(npz, f"low_{tf}", n)
    h = _get_arr(npz, f"high_{tf}", n)
    c_prev = _shift_prev(c)
    l_prev = _shift_prev(l)
    h_prev = _shift_prev(h)
    valid = (o > 0) & (c > 0)
    if is_long:
        red = valid & (c < o)
        if mode == "red1":
            return red
        if mode == "red2":
            return red & (c_prev > 0) & (c < c_prev)
        if mode == "red3":
            return red & (l_prev > 0) & (c < l_prev)
    else:
        green = valid & (c > o)
        if mode == "red1":
            return green
        if mode == "red2":
            return green & (c_prev > 0) & (c > c_prev)
        if mode == "red3":
            return green & (h_prev > 0) & (c > h_prev)
    return np.zeros(n, dtype=bool)


def build_bb_tag_fail_array(
    npz: dict, tf: str, n: int, is_long: bool, lookback: int,
) -> np.ndarray:
    """LONG: recent N TF-bars had high >= bb_upper, current bar high < bb_upper.
    SHORT: recent N TF-bars had low <= bb_lower, current bar low > bb_lower.
    """
    h = _get_arr(npz, f"high_{tf}", n)
    l = _get_arr(npz, f"low_{tf}", n)
    bbu = _get_arr(npz, f"bb_upper_{tf}", n)
    bbl = _get_arr(npz, f"bb_lower_{tf}", n)
    if is_long:
        tag = (h > 0) & (bbu > 0) & (h >= bbu - 1e-9)
        fail_now = (h > 0) & (bbu > 0) & (h < bbu)
    else:
        tag = (l > 0) & (bbl > 0) & (l <= bbl + 1e-9)
        fail_now = (l > 0) & (bbl > 0) & (l > bbl)
    recent_tag = np.zeros(n, dtype=bool)
    if lookback <= 0:
        return np.zeros(n, dtype=bool)
    for i in range(n):
        lo = max(0, i - lookback)
        hi = i
        if hi > lo:
            recent_tag[i] = tag[lo:hi].any()
    return recent_tag & fail_now


def build_all_pattern_stop_arrays(npz: dict, n: int, is_long: bool, config) -> dict:
    """Build all enabled pattern arrays based on config. Returns dict of name → bool array.
    Unenabled patterns return None (caller should skip)."""
    out = {
        "lh_fire": None,
        "ll_fire": None,
        "ib_fire": None,
        "pullback_fire": None,
        "bb_tag_fail_fire": None,
    }
    lh_on = bool(getattr(config, "LH_STOP_ENABLED", False))
    ll_on = bool(getattr(config, "LL_STOP_ENABLED", False))
    if lh_on or ll_on:
        lh_tf = str(getattr(config, "LH_STOP_TF", "15m")) if lh_on else "15m"
        ll_tf = str(getattr(config, "LL_STOP_TF", "15m")) if ll_on else "15m"
        if lh_on:
            lh_fire, _ = build_lower_high_low_arrays(npz, lh_tf, n, is_long)
            out["lh_fire"] = lh_fire
        if ll_on:
            _, ll_fire = build_lower_high_low_arrays(npz, ll_tf, n, is_long)
            out["ll_fire"] = ll_fire
    if bool(getattr(config, "IB_STOP_ENABLED", False)):
        tf = str(getattr(config, "IB_STOP_TF", "1h"))
        lb = int(getattr(config, "IB_STOP_LOOKBACK", 20))
        out["ib_fire"] = build_inside_bar_extreme_array(npz, tf, n, is_long, lb)
    if bool(getattr(config, "PULLBACK_STOP_ENABLED", False)):
        tf = str(getattr(config, "PULLBACK_STOP_TF", "15m"))
        mode = str(getattr(config, "PULLBACK_STOP_MODE", "red2"))
        out["pullback_fire"] = build_pullback_array(npz, tf, n, is_long, mode)
    if bool(getattr(config, "BB_TAG_FAIL_STOP_ENABLED", False)):
        tf = str(getattr(config, "BB_TAG_FAIL_STOP_TF", "1h"))
        lb = int(getattr(config, "BB_TAG_FAIL_STOP_LOOKBACK", 5))
        out["bb_tag_fail_fire"] = build_bb_tag_fail_array(npz, tf, n, is_long, lb)
    return out


def check_pattern_stops_at_bar(arrays: dict, i: int, config) -> Tuple[bool, str]:
    """Hot-loop callable: returns (fire, reason) for bar i."""
    a = arrays.get("lh_fire")
    if a is not None and a[i]:
        return True, f"PSTOP_LH_{getattr(config, 'LH_STOP_TF', '15m')}"
    a = arrays.get("ll_fire")
    if a is not None and a[i]:
        return True, f"PSTOP_LL_{getattr(config, 'LL_STOP_TF', '15m')}"
    a = arrays.get("ib_fire")
    if a is not None and a[i]:
        return True, f"PSTOP_IB_{getattr(config, 'IB_STOP_TF', '1h')}"
    a = arrays.get("pullback_fire")
    if a is not None and a[i]:
        return True, f"PSTOP_PB_{getattr(config, 'PULLBACK_STOP_TF', '15m')}_{getattr(config, 'PULLBACK_STOP_MODE', 'red2')}"
    a = arrays.get("bb_tag_fail_fire")
    if a is not None and a[i]:
        return True, f"PSTOP_BBTAGFAIL_{getattr(config, 'BB_TAG_FAIL_STOP_TF', '1h')}"
    return False, ""
