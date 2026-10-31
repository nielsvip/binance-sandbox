"""Shared exact score arithmetic for AdvancedSignalRater's ordered entry-score prefix."""
from __future__ import annotations

from typing import Any, Mapping

import numpy as np


def _cfg(cfg: Any, name: str, default: Any) -> Any:
    if isinstance(cfg, Mapping):
        return cfg.get(name, default)
    return getattr(cfg, name, default)


def _float(value: Any, default: Any) -> Any:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def apply_score_prefix_scalar(
    score: float,
    reasons: list[str],
    indicators: Mapping[str, Any],
    metrics: Mapping[str, Any] | None,
    is_long: bool,
    cfg: Any,
    wt_composite_enabled: bool,
    wt_bull_cross_count: float,
    wt_bear_cross_count: float,
) -> tuple[float, list[str]]:
    """Apply the native ordered WT composite, DC moment and WT delta score blocks."""
    result = float(score)
    if wt_composite_enabled:
        side_comp = _float(
            indicators.get("wt_composite_long" if is_long else "wt_composite_short"), 0
        )
        os_count = _float(indicators.get("wt_oversold_tf_count"), 0)
        ob_count = _float(indicators.get("wt_overbought_tf_count"), 0)
        hl_count = _float(indicators.get("wt_hl_count"), 0)
        lh_count = _float(indicators.get("wt_lh_count"), 0)
        strong = _cfg(cfg, "WT_COMPOSITE_ENTRY_STRONG", 50.0)
        good = _cfg(cfg, "WT_COMPOSITE_ENTRY_GOOD", 30.0)
        ok = _cfg(cfg, "WT_COMPOSITE_ENTRY_OK", 10.0)
        if side_comp >= strong:
            result += 8
            reasons.append(f"WT_STRONG({'L' if is_long else 'S'}={side_comp:.0f},+8)")
        elif side_comp >= good:
            result += 5
            reasons.append(f"WT_GOOD({'L' if is_long else 'S'}={side_comp:.0f},+5)")
        elif side_comp >= ok:
            result += 2
            reasons.append(f"WT_OK({'L' if is_long else 'S'}={side_comp:.0f},+2)")
        if is_long and os_count >= 2:
            result += 5
            reasons.append(f"WT_MULTI_OS({os_count:.0f}TF,+5)")
        elif not is_long and ob_count >= 2:
            result += 5
            reasons.append(f"WT_MULTI_OB({ob_count:.0f}TF,+5)")
        if is_long and hl_count >= 2:
            result += 5
            reasons.append(f"WT_HL_STRUCT({hl_count:.0f},+5)")
        elif not is_long and lh_count >= 2:
            result += 5
            reasons.append(f"WT_LH_STRUCT({lh_count:.0f},+5)")
        if bool(_cfg(cfg, "R_S1_WT_COMPOSITE_DELTA_USE_ENABLED", False)):
            delta = _float(indicators.get("wt_composite_delta"), None)
            threshold = float(_cfg(cfg, "R_S1_WT_COMPOSITE_DELTA_THR", 50.0))
            if delta is not None:
                if is_long and delta > threshold:
                    result += 6
                    reasons.append(f"R_S1_CDELTA_BULL({delta:.0f}>{threshold:.0f},+6)")
                elif not is_long and delta < -threshold:
                    result += 6
                    reasons.append(f"R_S1_CDELTA_BEAR({delta:.0f}<-{threshold:.0f},+6)")
        if bool(_cfg(cfg, "R_S2_WT_ADAPTIVE_OS_ENABLED", False)):
            percentile = _float(indicators.get("wt_percentile_15m"), -1.0)
            if percentile >= 0:
                os_threshold = float(_cfg(cfg, "R_S2_WT_PCT_OS_LONG", 10.0))
                ob_threshold = float(_cfg(cfg, "R_S2_WT_PCT_OB_SHORT", 90.0))
                if is_long and percentile < os_threshold:
                    result += 7
                    reasons.append(f"R_S2_PCT_OS_L({percentile:.0f}<{os_threshold:.0f},+7)")
                elif not is_long and percentile > ob_threshold:
                    result += 7
                    reasons.append(f"R_S2_PCT_OB_S({percentile:.0f}>{ob_threshold:.0f},+7)")
        if indicators.get("wt_any_bull_div") and is_long:
            result += 10
            reasons.append(f"WT_BULL_DIV({indicators.get('wt_strongest_bull_div_tf')},+10)")
        elif indicators.get("wt_any_bear_div") and not is_long:
            result += 10
            reasons.append(f"WT_BEAR_DIV({indicators.get('wt_strongest_bear_div_tf')},+10)")
        if is_long and wt_bull_cross_count >= 2:
            result += 3
            reasons.append(f"WT_MULTI_BULL_X({wt_bull_cross_count:.0f},+3)")
        elif not is_long and wt_bear_cross_count >= 2:
            result += 3
            reasons.append(f"WT_MULTI_BEAR_X({wt_bear_cross_count:.0f},+3)")

    if bool(_cfg(cfg, "DC_MOMENT_ENABLED", True)):
        raw_moment = indicators.get(
            "0dc_moment",
            metrics.get("0dc_moment") if isinstance(metrics, dict) else 0,
        )
        moment = _float(raw_moment, 0)
        threshold = float(_cfg(cfg, "DC_MOMENT_STRONG_THRESHOLD", 40.0))
        bonus = float(_cfg(cfg, "DC_MOMENT_STRONG_BONUS", 10.0))
        penalty = float(_cfg(cfg, "DC_MOMENT_OPPOSITE_PENALTY", -15.0))
        if is_long and moment > threshold:
            result += bonus
            reasons.append(f"DC_MOMENT_STRONG_L({moment:.0f},+{bonus:.0f})")
        elif not is_long and moment < -threshold:
            result += bonus
            reasons.append(f"DC_MOMENT_STRONG_S({moment:.0f},+{bonus:.0f})")
        elif is_long and moment < -threshold:
            result += penalty
            reasons.append(f"DC_MOMENT_OPPOSITE_L({moment:.0f},{penalty:.0f})")
        elif not is_long and moment > threshold:
            result += penalty
            reasons.append(f"DC_MOMENT_OPPOSITE_S({moment:.0f},{penalty:.0f})")

    if bool(_cfg(cfg, "WT_COMPOSITE_DELTA_SCORE_ENABLED", True)):
        delta = _float(indicators.get("wt_composite_delta"), None)
        if delta is not None:
            threshold = float(_cfg(cfg, "WT_COMPOSITE_DELTA_SCORE_THRESHOLD", 50.0))
            bonus = float(_cfg(cfg, "WT_COMPOSITE_DELTA_SCORE_BONUS", 3.0))
            if is_long and delta > threshold:
                result += bonus
                reasons.append(f"WT_CD_BULL({delta:.0f},+{bonus:.0f})")
            elif not is_long and delta < -threshold:
                result += bonus
                reasons.append(f"WT_CD_BEAR({delta:.0f},+{bonus:.0f})")
    return result, reasons


def apply_score_prefix_vec(
    npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any, score: Any = None,
    metrics: Mapping[str, Any] | None = None,
) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """Vector twin of the native ordered entry-score prefix (4702–4824)."""
    result = np.zeros(n, dtype=float) if score is None else np.asarray(score, dtype=float).copy()
    if result.shape != (n,):
        raise ValueError("score must have shape (n,)")
    masks: dict[str, np.ndarray] = {}

    def col(key: str, default: float) -> np.ndarray:
        value = npz.get(key)
        if value is None:
            return np.full(n, default, dtype=float)
        try:
            arr = np.asarray(value, dtype=float).reshape(-1)
        except (TypeError, ValueError):
            return np.full(n, default, dtype=float)
        if arr.size != n:
            return np.full(n, default, dtype=float)
        return arr

    def add(name: str, mask: np.ndarray, amount: float) -> None:
        masks[name] = mask
        result[:] += mask.astype(float) * amount

    if metrics is not None and "0dc_moment" not in npz and "0dc_moment" in metrics:
        npz = dict(npz)
        npz["0dc_moment"] = metrics["0dc_moment"]

    enabled = bool(_cfg(cfg, "WT_COMPOSITE_SCORING_ENABLED", False)) or bool(
        _cfg(cfg, "WT_COMPOSITE_SCORING_ENABLED_TRADIER", False)
    )
    if enabled:
        side_comp = col("wt_composite_long" if is_long else "wt_composite_short", 0.0)
        strong = float(_cfg(cfg, "WT_COMPOSITE_ENTRY_STRONG", 50.0))
        good = float(_cfg(cfg, "WT_COMPOSITE_ENTRY_GOOD", 30.0))
        ok = float(_cfg(cfg, "WT_COMPOSITE_ENTRY_OK", 10.0))
        add("WT_STRONG", np.isfinite(side_comp) & (side_comp >= strong), 8.0)
        # Mutually exclusive tiers, matching the scalar if/elif order.
        add("WT_GOOD", np.isfinite(side_comp) & (side_comp < strong) & (side_comp >= good), 5.0)
        add("WT_OK", np.isfinite(side_comp) & (side_comp < good) & (side_comp >= ok), 2.0)
        if is_long:
            add("WT_MULTI_OS", np.isfinite(col("wt_oversold_tf_count", 0.0)) & (col("wt_oversold_tf_count", 0.0) >= 2), 5.0)
            add("WT_HL_STRUCT", np.isfinite(col("wt_hl_count", 0.0)) & (col("wt_hl_count", 0.0) >= 2), 5.0)
        else:
            add("WT_MULTI_OB", np.isfinite(col("wt_overbought_tf_count", 0.0)) & (col("wt_overbought_tf_count", 0.0) >= 2), 5.0)
            add("WT_LH_STRUCT", np.isfinite(col("wt_lh_count", 0.0)) & (col("wt_lh_count", 0.0) >= 2), 5.0)
        delta = col("wt_composite_delta", np.nan)
        if bool(_cfg(cfg, "R_S1_WT_COMPOSITE_DELTA_USE_ENABLED", False)):
            threshold = float(_cfg(cfg, "R_S1_WT_COMPOSITE_DELTA_THR", 50.0))
            add("R_S1_CDELTA", np.isfinite(delta) & ((delta > threshold) if is_long else (delta < -threshold)), 6.0)
        if bool(_cfg(cfg, "R_S2_WT_ADAPTIVE_OS_ENABLED", False)):
            percentile = col("wt_percentile_15m", -1.0)
            threshold = float(_cfg(cfg, "R_S2_WT_PCT_OS_LONG", 10.0) if is_long else _cfg(cfg, "R_S2_WT_PCT_OB_SHORT", 90.0))
            add("R_S2_PCT", np.isfinite(percentile) & ((percentile >= 0) & ((percentile < threshold) if is_long else (percentile > threshold))), 7.0)
        div_key = "wt_any_bull_div" if is_long else "wt_any_bear_div"
        div = col(div_key, 0.0)
        add("WT_DIVERGENCE", np.isfinite(div) & (div != 0), 10.0)
        cross = col("wt_bull_cross_count" if is_long else "wt_bear_cross_count", 0.0)
        add("WT_MULTI_CROSS", np.isfinite(cross) & (cross >= 2), 3.0)

    if bool(_cfg(cfg, "DC_MOMENT_ENABLED", True)):
        moment = col("0dc_moment", 0.0)
        threshold = float(_cfg(cfg, "DC_MOMENT_STRONG_THRESHOLD", 40.0))
        bonus = float(_cfg(cfg, "DC_MOMENT_STRONG_BONUS", 10.0))
        penalty = float(_cfg(cfg, "DC_MOMENT_OPPOSITE_PENALTY", -15.0))
        if is_long:
            add("DC_MOMENT_STRONG", np.isfinite(moment) & (moment > threshold), bonus)
            add("DC_MOMENT_OPPOSITE", np.isfinite(moment) & (moment < -threshold), penalty)
        else:
            add("DC_MOMENT_STRONG", np.isfinite(moment) & (moment < -threshold), bonus)
            add("DC_MOMENT_OPPOSITE", np.isfinite(moment) & (moment > threshold), penalty)
    if bool(_cfg(cfg, "WT_COMPOSITE_DELTA_SCORE_ENABLED", True)):
        delta = col("wt_composite_delta", np.nan)
        threshold = float(_cfg(cfg, "WT_COMPOSITE_DELTA_SCORE_THRESHOLD", 50.0))
        bonus = float(_cfg(cfg, "WT_COMPOSITE_DELTA_SCORE_BONUS", 3.0))
        fire = np.isfinite(delta) & ((delta > threshold) if is_long else (delta < -threshold))
        add("WT_COMPOSITE_DELTA", fire, bonus)
    return result, masks
