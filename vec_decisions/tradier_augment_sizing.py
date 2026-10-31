"""Shared scalar and array factors from Tradier's initial augment sizing chain."""
from __future__ import annotations

import datetime as _dt
from datetime import time as _time
from zoneinfo import ZoneInfo

import numpy as np


_ET = ZoneInfo("America/New_York")


def initial_adjustment_components(is_long, ha_5m, k_15m, d_15m, k_1h, k_5m, local_time):
    knife = 1.0
    if is_long and ha_5m == "red":
        knife *= 0.3
    elif not is_long and ha_5m == "green":
        knife *= 0.3
    reduction = 1.0
    long_focus = local_time < _time(11, 0) or local_time > _time(14, 0)
    short_focus = not long_focus
    near_close = local_time > _time(15, 30)
    k_ok = (k_15m >= d_15m) if is_long else (k_15m <= d_15m)
    if not k_ok:
        reduction *= 0.3
    if long_focus and ((is_long and k_1h > 80) or (not is_long and k_1h < 20)):
        reduction *= 0.7
    if short_focus and ((is_long and k_5m > 85) or (not is_long and k_5m < 15)):
        reduction *= 0.6
    if near_close:
        reduction *= 0.5
    return knife, reduction


def initial_adjustment_arrays(is_long, timestamps, ha_5m, k_15m, d_15m, k_1h, k_5m):
    ts = np.asarray(timestamps, dtype=np.float64)
    scale = 1000.0 if ts.size and np.nanmax(ts) > 1e11 else 1.0
    seconds = ts / scale
    times = [_dt.datetime.fromtimestamp(float(s), tz=_dt.timezone.utc).astimezone(_ET).time() for s in seconds]
    n = len(ts)
    ha = np.asarray(ha_5m, dtype=object)
    k15 = np.asarray(k_15m, dtype=np.float64)
    d15 = np.asarray(d_15m, dtype=np.float64)
    k1h = np.asarray(k_1h, dtype=np.float64)
    k5 = np.asarray(k_5m, dtype=np.float64)
    if any(len(a) != n for a in (ha, k15, d15, k1h, k5)):
        raise ValueError("Tradier sizing inputs must align with timestamps")
    knife = np.ones(n, dtype=np.float64)
    if is_long:
        knife[ha == "red"] *= 0.3
    else:
        knife[ha == "green"] *= 0.3
    reduction = np.ones(n, dtype=np.float64)
    long_focus = np.asarray([t < _time(11, 0) or t > _time(14, 0) for t in times], dtype=bool)
    short_focus = ~long_focus
    near_close = np.asarray([t > _time(15, 30) for t in times], dtype=bool)
    k_ok = (k15 >= d15) if is_long else (k15 <= d15)
    reduction[~k_ok] *= 0.3
    if is_long:
        reduction[long_focus & (k1h > 80)] *= 0.7
        reduction[short_focus & (k5 > 85)] *= 0.6
    else:
        reduction[long_focus & (k1h < 20)] *= 0.7
        reduction[short_focus & (k5 < 15)] *= 0.6
    reduction[near_close] *= 0.5
    return knife, reduction


def golden_angle_multiplier(is_long, dc_position, dc_width, wt1_4h, wt2_4h, wt_velocity_1h):
    dc_pos = float(dc_position or 0.5)
    width = float(dc_width or 4)
    wt_score = float(wt1_4h or 0) - float(wt2_4h or 0)
    wt_vel = float(wt_velocity_1h or 0)
    mult = 1.0
    parts = []
    if is_long:
        if dc_pos < 0.10:
            mult *= 2.0
            parts.append(f"DC_DEEP({dc_pos:.2f},2x)")
        elif dc_pos < 0.20:
            mult *= 1.5
            parts.append(f"DC_LOW({dc_pos:.2f},1.5x)")
        elif dc_pos < 0.30:
            mult *= 1.2
            parts.append(f"DC_OK({dc_pos:.2f},1.2x)")
        elif dc_pos > 0.70:
            mult *= 0.5
            parts.append(f"DC_HIGH({dc_pos:.2f},0.5x)")
    else:
        if dc_pos > 0.90:
            mult *= 2.0
            parts.append(f"DC_DEEP({dc_pos:.2f},2x)")
        elif dc_pos > 0.80:
            mult *= 1.5
            parts.append(f"DC_HIGH({dc_pos:.2f},1.5x)")
        elif dc_pos > 0.70:
            mult *= 1.2
            parts.append(f"DC_OK({dc_pos:.2f},1.2x)")
        elif dc_pos < 0.30:
            mult *= 0.5
            parts.append(f"DC_LOW({dc_pos:.2f},0.5x)")
    if (is_long and wt_score > 10) or (not is_long and wt_score < -10):
        mult *= 1.3
        parts.append(f"WT_STRONG({wt_score:.0f},1.3x)")
    elif (is_long and wt_score > 5) or (not is_long and wt_score < -5):
        mult *= 1.1
        parts.append(f"WT_OK({wt_score:.0f},1.1x)")
    elif (is_long and wt_score < -5) or (not is_long and wt_score > 5):
        mult *= 0.5
        parts.append(f"WT_AGAINST({wt_score:.0f},0.5x)")
    if width > 8:
        mult *= 0.7
        parts.append(f"DCW_WIDE({width:.1f},0.7x)")
    elif width < 3:
        mult *= 1.3
        parts.append(f"DCW_TIGHT({width:.1f},1.3x)")
    if (is_long and wt_vel > 3) or (not is_long and wt_vel < -3):
        mult *= 1.2
        parts.append(f"VEL_STRONG({wt_vel:.1f},1.2x)")
    elif (is_long and wt_vel < -2) or (not is_long and wt_vel > 2):
        mult *= 0.6
        parts.append(f"VEL_AGAINST({wt_vel:.1f},0.6x)")
    return max(0.3, min(3.0, mult)), parts


def golden_angle_multiplier_array(is_long, dc_position, dc_width, wt1_4h, wt2_4h, wt_velocity_1h):
    arrays = [np.asarray(value, dtype=np.float64) for value in (dc_position, dc_width, wt1_4h, wt2_4h, wt_velocity_1h)]
    n = len(arrays[0])
    if any(len(value) != n for value in arrays):
        raise ValueError("Golden-angle sizing arrays must align")
    dc_pos, width, wt1, wt2, wt_vel = arrays
    dc_pos = np.where(dc_pos == 0.0, 0.5, dc_pos)
    width = np.where(width == 0.0, 4.0, width)
    wt_score = wt1 - wt2
    mult = np.ones(n, dtype=np.float64)
    if is_long:
        mult[dc_pos < 0.10] *= 2.0
        mult[(dc_pos >= 0.10) & (dc_pos < 0.20)] *= 1.5
        mult[(dc_pos >= 0.20) & (dc_pos < 0.30)] *= 1.2
        mult[dc_pos > 0.70] *= 0.5
        mult[wt_score > 10] *= 1.3
        mult[(wt_score > 5) & (wt_score <= 10)] *= 1.1
        mult[wt_score < -5] *= 0.5
        mult[wt_vel > 3] *= 1.2
        mult[wt_vel < -2] *= 0.6
    else:
        mult[dc_pos > 0.90] *= 2.0
        mult[(dc_pos > 0.80) & (dc_pos <= 0.90)] *= 1.5
        mult[(dc_pos > 0.70) & (dc_pos <= 0.80)] *= 1.2
        mult[dc_pos < 0.30] *= 0.5
        mult[wt_score < -10] *= 1.3
        mult[(wt_score < -5) & (wt_score >= -10)] *= 1.1
        mult[wt_score > 5] *= 0.5
        mult[wt_vel < -3] *= 1.2
        mult[wt_vel > 2] *= 0.6
    mult[width > 8] *= 0.7
    mult[width < 3] *= 1.3
    return np.clip(mult, 0.3, 3.0)
