"""Vectorize the exact family tags that add +35 in AdvancedSignalRater.rate."""
from collections.abc import Mapping

import numpy as np

from vec_decisions.check_entry_b12_wt_mom import b12_mask_vec
from vec_decisions.check_entry_b14_ha_stack import b14_mask_vec, ha_to_num
from vec_decisions.check_entry_b15_trend import b15_mask_vec
from vec_decisions.check_entry_mom3 import mom3_mask_vec
from vec_decisions.check_entry_srs_permission import pctb_key_for_tf, srs_blocked_mask_vec


def _cfg(cfg, key, default):
    return cfg.get(key, default) if isinstance(cfg, Mapping) else getattr(cfg, key, default)


def _col(values, key, n, default=0.0, dtype=float):
    raw = values.get(key)
    if raw is None:
        return np.full(n, default, dtype=dtype)
    try:
        arr = np.asarray(raw).reshape(-1)
        if arr.size != n:
            raise ValueError(f"{key} length {arr.size} does not match n={n}")
        if dtype is bool:
            if arr.dtype.kind in "SUO":
                return np.asarray([str(value).strip().lower() in ("1", "true", "yes") for value in arr], dtype=bool)
            return arr.astype(bool)
        arr = arr.astype(float)
        return np.where(np.isfinite(arr), arr, float(default))
    except (TypeError, ValueError) as error:
        if isinstance(error, ValueError) and "length" in str(error):
            raise
        return np.full(n, default, dtype=dtype)


def _ha_column(values, key, n):
    raw = values.get(key)
    if raw is None:
        return np.zeros(n, dtype=float)
    arr = np.asarray(raw, dtype=object).reshape(-1)
    if arr.size != n:
        raise ValueError(f"{key} length {arr.size} does not match n={n}")
    return np.asarray([ha_to_num(value) for value in arr], dtype=float)


def family_lane_masks_array(values, current_price, is_long, cfg):
    """Return per-source tag masks matching the native family helper; no mask is a veto."""
    if not isinstance(values, Mapping):
        raise ValueError("family lane indicators must be a mapping")
    px = np.asarray(current_price, dtype=float).reshape(-1)
    n = px.size
    masks = {}
    if bool(_cfg(cfg, "MOM3_ENTRY_ENABLED", False)):
        close15 = _col(values, "close_15m", n, 0.0)
        if "close_15m" not in values:
            close15 = _col(values, "close", n, 0.0)
        close3 = _col(values, "close_3bar_15m", n, 0.0)
        masks["B_MOM3_FAMILY"] = mom3_mask_vec(
            close15, close3, float(_cfg(cfg, "MOM3_LONG_THRESHOLD", -1.0)),
            float(_cfg(cfg, "MOM3_SHORT_THRESHOLD", 1.0)), is_long,
        )
    else:
        masks["B_MOM3_FAMILY"] = np.zeros(n, dtype=bool)

    if bool(_cfg(cfg, "STRUCTURAL_RANGE_SHIFT_EXIT", True)):
        pct_key = pctb_key_for_tf(_cfg(cfg, "STRUCTURAL_RANGE_SHIFT_TF", "bb_1h"))
        pctb = _col(values, pct_key, n, 0.5)
        masks["B_SRS_ENTRY_FAMILY"] = ~srs_blocked_mask_vec(pctb, is_long)
    else:
        masks["B_SRS_ENTRY_FAMILY"] = np.zeros(n, dtype=bool)

    if bool(_cfg(cfg, "REENTRY_B15_STRONG_TREND_ENABLED", True)):
        close = _col(values, "close", n, 0.0)
        if "close" not in values:
            close = px.copy()
        dc = _col(values, "dc_high_4h" if is_long else "dc_low_4h", n, 0.0)
        velocity = _col(values, "wt_velocity_1h", n, 0.0)
        k1h = _col(values, "stoch_k_1h", n, 50.0)
        masks["B15_FAMILY"] = b15_mask_vec(close, dc, velocity, k1h, is_long)
    else:
        masks["B15_FAMILY"] = np.zeros(n, dtype=bool)

    if bool(_cfg(cfg, "REENTRY_B14_HA_TREND_ENABLED", True)):
        ha15 = _ha_column(values, "ha_15m", n)
        ha1h = _ha_column(values, "ha_1h", n)
        k15 = _col(values, "k_15m", n, 50.0)
        masks["B14_FAMILY"] = b14_mask_vec(ha15, ha15, ha1h, k15, is_long)
    else:
        masks["B14_FAMILY"] = np.zeros(n, dtype=bool)

    if bool(_cfg(cfg, "REENTRY_B12_WT_MOM_ENABLED", True)):
        w1_15 = _col(values, "wt1_15m", n, 0.0)
        w2_15 = _col(values, "wt2_15m", n, 0.0)
        w1_1h = _col(values, "wt1_1h", n, 0.0)
        w2_1h = _col(values, "wt2_1h", n, 0.0)
        velocity = _col(values, "wt_velocity_15m", n, 0.0)
        masks["B12_FAMILY"] = b12_mask_vec(w1_15, w2_15, w1_15, w2_15, w1_1h, w2_1h, velocity, is_long)
    else:
        masks["B12_FAMILY"] = np.zeros(n, dtype=bool)

    if bool(_cfg(cfg, "K_ZONE_ENTRY_ENABLED", False)):
        k15 = _col(values, "k_15m", n, 50.0)
        threshold = float(_cfg(cfg, "K_ZONE_LONG_THRESHOLD" if is_long else "K_ZONE_SHORT_THRESHOLD", 35.0 if is_long else 65.0))
        masks["B_KZONE_FAMILY"] = (k15 < threshold) if is_long else (k15 > threshold)
    else:
        masks["B_KZONE_FAMILY"] = np.zeros(n, dtype=bool)
    return masks


def apply_family_lane_score_array(values, current_price, is_long, cfg, score):
    result = np.asarray(score, dtype=float).reshape(-1).copy()
    masks = family_lane_masks_array(values, current_price, is_long, cfg)
    if result.size != next(iter(masks.values())).size:
        raise ValueError("family lane score and indicator lengths differ")
    count = np.zeros(result.size, dtype=np.int8)
    for mask in masks.values():
        count += np.asarray(mask, dtype=bool).astype(np.int8)
    result += count * 35.0
    masks["FAMILY_LANE_COUNT"] = count
    return result, masks
