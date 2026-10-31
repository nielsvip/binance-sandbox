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
        add("WT_STRONG", ~np.isnan(side_comp) & (side_comp >= strong), 8.0)
        # Mutually exclusive tiers, matching the scalar if/elif order.
        add("WT_GOOD", ~np.isnan(side_comp) & (side_comp < strong) & (side_comp >= good), 5.0)
        add("WT_OK", ~np.isnan(side_comp) & (side_comp < good) & (side_comp >= ok), 2.0)
        if is_long:
            add("WT_MULTI_OS", ~np.isnan(col("wt_oversold_tf_count", 0.0)) & (col("wt_oversold_tf_count", 0.0) >= 2), 5.0)
            add("WT_HL_STRUCT", ~np.isnan(col("wt_hl_count", 0.0)) & (col("wt_hl_count", 0.0) >= 2), 5.0)
        else:
            add("WT_MULTI_OB", ~np.isnan(col("wt_overbought_tf_count", 0.0)) & (col("wt_overbought_tf_count", 0.0) >= 2), 5.0)
            add("WT_LH_STRUCT", ~np.isnan(col("wt_lh_count", 0.0)) & (col("wt_lh_count", 0.0) >= 2), 5.0)
        delta = col("wt_composite_delta", np.nan)
        if bool(_cfg(cfg, "R_S1_WT_COMPOSITE_DELTA_USE_ENABLED", False)):
            threshold = float(_cfg(cfg, "R_S1_WT_COMPOSITE_DELTA_THR", 50.0))
            add("R_S1_CDELTA", ~np.isnan(delta) & ((delta > threshold) if is_long else (delta < -threshold)), 6.0)
        if bool(_cfg(cfg, "R_S2_WT_ADAPTIVE_OS_ENABLED", False)):
            percentile = col("wt_percentile_15m", -1.0)
            threshold = float(_cfg(cfg, "R_S2_WT_PCT_OS_LONG", 10.0) if is_long else _cfg(cfg, "R_S2_WT_PCT_OB_SHORT", 90.0))
            add("R_S2_PCT", ~np.isnan(percentile) & ((percentile >= 0) & ((percentile < threshold) if is_long else (percentile > threshold))), 7.0)
        div_key = "wt_any_bull_div" if is_long else "wt_any_bear_div"
        div = col(div_key, 0.0)
        add("WT_DIVERGENCE", ~np.isnan(div) & (div != 0), 10.0)
        cross = col("wt_bull_cross_count" if is_long else "wt_bear_cross_count", 0.0)
        add("WT_MULTI_CROSS", ~np.isnan(cross) & (cross >= 2), 3.0)

    if bool(_cfg(cfg, "DC_MOMENT_ENABLED", True)):
        moment = col("0dc_moment", 0.0)
        threshold = float(_cfg(cfg, "DC_MOMENT_STRONG_THRESHOLD", 40.0))
        bonus = float(_cfg(cfg, "DC_MOMENT_STRONG_BONUS", 10.0))
        penalty = float(_cfg(cfg, "DC_MOMENT_OPPOSITE_PENALTY", -15.0))
        if is_long:
            add("DC_MOMENT_STRONG", ~np.isnan(moment) & (moment > threshold), bonus)
            add("DC_MOMENT_OPPOSITE", ~np.isnan(moment) & (moment < -threshold), penalty)
        else:
            add("DC_MOMENT_STRONG", ~np.isnan(moment) & (moment < -threshold), bonus)
            add("DC_MOMENT_OPPOSITE", ~np.isnan(moment) & (moment > threshold), penalty)
    if bool(_cfg(cfg, "WT_COMPOSITE_DELTA_SCORE_ENABLED", True)):
        delta = col("wt_composite_delta", np.nan)
        threshold = float(_cfg(cfg, "WT_COMPOSITE_DELTA_SCORE_THRESHOLD", 50.0))
        bonus = float(_cfg(cfg, "WT_COMPOSITE_DELTA_SCORE_BONUS", 3.0))
        fire = ~np.isnan(delta) & ((delta > threshold) if is_long else (delta < -threshold))
        add("WT_COMPOSITE_DELTA", fire, bonus)
    return result, masks


def apply_structure_score_scalar(
    score: float,
    reasons: list[str],
    indicators: Mapping[str, Any],
    is_long: bool,
    cfg: Any,
) -> tuple[float, list[str]]:
    """Apply native R-S3, R-S7, R-S4 and R-S5 entry score blocks in source order."""
    result = float(score)
    if bool(_cfg(cfg, "R_S3_DIV_STACK_ENABLED", False)):
        hidden_bonus = float(_cfg(cfg, "R_S3_HIDDEN_BONUS", 25.0))
        main_penalty = float(_cfg(cfg, "R_S3_MAIN_PENALTY", -20.0))
        weighted = bool(_cfg(cfg, "R_S3_HTF_WEIGHT_ENABLED", False))
        weights = {
            "3m": float(_cfg(cfg, "R_S3_TF_WEIGHT_3M", 0.25)),
            "15m": float(_cfg(cfg, "R_S3_TF_WEIGHT_15M", 0.5)),
            "1h": float(_cfg(cfg, "R_S3_TF_WEIGHT_1H", 1.0)),
            "4h": float(_cfg(cfg, "R_S3_TF_WEIGHT_4H", 2.5)),
            "D": float(_cfg(cfg, "R_S3_TF_WEIGHT_D", 4.0)),
        }
        hidden_count = 0
        hidden_weight = 0.0
        main_count = 0
        main_weight = 0.0
        main_tfs = []
        for tf in ("3m", "15m", "1h", "4h", "D"):
            divergence = str(indicators.get(f"wt_divergence_{tf}", "") or "").upper()
            weight = weights.get(tf, 1.0)
            if is_long:
                if divergence == "HIDDEN_BULL":
                    hidden_count += 1
                    hidden_weight += weight
                elif divergence == "BEAR" and (weighted or tf in ("15m", "1h")):
                    main_count += 1
                    main_weight += weight
                    main_tfs.append(tf)
            else:
                if divergence == "HIDDEN_BEAR":
                    hidden_count += 1
                    hidden_weight += weight
                elif divergence == "BULL" and (weighted or tf in ("15m", "1h")):
                    main_count += 1
                    main_weight += weight
                    main_tfs.append(tf)
        if hidden_count >= 2:
            bonus = hidden_bonus * (hidden_weight / max(hidden_count, 1) if weighted else 1.0)
            result += bonus
            reasons.append(f"R_S3_HIDDEN_DIV({hidden_count}TF,w={hidden_weight:.1f},+{bonus:.0f})")
        if main_count >= 1:
            penalty = main_penalty * main_weight if weighted else main_penalty
            result += penalty
            reasons.append(f"R_S3_MAIN_DIV_AGAINST({main_tfs},w={main_weight:.1f},{penalty:.0f})")

    if bool(_cfg(cfg, "R_S7_HHLL_STACK_ENABLED", False)):
        tfs = [tf.strip() for tf in str(_cfg(cfg, "R_S7_HHLL_TFS", "15m,1h,4h,D") or "").split(",") if tf.strip()]
        min_indicators = int(_cfg(cfg, "R_S7_HHLL_MIN_INDICATORS", 2))
        bonus_per_tf = float(_cfg(cfg, "R_S7_HHLL_BONUS_PER_TF", 3.0))
        min_tfs = int(_cfg(cfg, "R_S7_HHLL_MIN_TFS_FOR_BONUS", 2))
        confirmed = []
        for tf in tfs:
            high = _float(indicators.get(f"high_{tf}"), 0)
            high_prev = _float(indicators.get(f"high_{tf}_prev"), 0)
            low = _float(indicators.get(f"low_{tf}"), 0)
            low_prev = _float(indicators.get(f"low_{tf}_prev"), 0)
            wt_structure = str(indicators.get(f"wt_structure_{tf}", "") or "").upper()
            k = _float(indicators.get(f"k_{tf}"), None)
            k_prev = _float(indicators.get(f"k_{tf}_prev"), None)
            count = 0
            if is_long:
                if high > 0 and high_prev > 0 and low > 0 and low_prev > 0 and high > high_prev and low > low_prev:
                    count += 1
                if wt_structure == "HH":
                    count += 1
                if k is not None and k_prev is not None and k > k_prev:
                    count += 1
            else:
                if high > 0 and high_prev > 0 and low > 0 and low_prev > 0 and high < high_prev and low < low_prev:
                    count += 1
                if wt_structure == "LL":
                    count += 1
                if k is not None and k_prev is not None and k < k_prev:
                    count += 1
            if count >= min_indicators:
                confirmed.append(tf)
        if len(confirmed) >= min_tfs:
            bonus = bonus_per_tf * len(confirmed)
            result += bonus
            reasons.append(f"R_S7_HHLL({confirmed},+{bonus:.0f})")

    if bool(_cfg(cfg, "R_S4_HA_STREAK_ENABLED", False)):
        tf = str(_cfg(cfg, "R_S4_HA_STREAK_TF", "1h"))
        weight = float(_cfg(cfg, "R_S4_HA_STREAK_WEIGHT", 5.0))
        streak = _float(indicators.get(f"ha_streak_{tf}"), 0.0)
        color = str(indicators.get(f"ha_{tf}", "") or "").lower()
        aligned = (is_long and color == "green") or (not is_long and color == "red")
        if aligned and streak > 0:
            bonus = weight * min(float(streak or 0.0), 5.0)
            result += bonus
            reasons.append(f"R_S4_HA_STREAK({tf}={streak:.0f},+{bonus:.0f})")

    if bool(_cfg(cfg, "R_S5_SENT_VEL_ENABLED", False)):
        raw_velocity = indicators.get("0sentiment_velocity", indicators.get("velocity", 0))
        velocity = _float(raw_velocity, 0.0)
        threshold = float(_cfg(cfg, "R_S5_SENT_VEL_PCT_THR", 75.0))
        bonus = float(_cfg(cfg, "R_S5_SENT_VEL_BONUS", 5.0))
        aligned = (is_long and velocity > 0) or (not is_long and velocity < 0)
        if aligned and abs(float(velocity or 0.0)) >= threshold:
            result += bonus
            reasons.append(f"R_S5_SENT_VEL({velocity:+.0f},+{bonus:.0f})")
    return result, reasons


def apply_structure_score_vec(
    npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any, score: Any = None
) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """NumPy twin of native ordered R-S3/R-S7/R-S4/R-S5 entry score blocks."""
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
        return arr if arr.size == n else np.full(n, default, dtype=float)

    def text_col(key: str, default: str = "") -> np.ndarray:
        value = npz.get(key)
        if value is None:
            return np.full(n, default, dtype=object)
        arr = np.asarray(value, dtype=object).reshape(-1)
        if arr.size != n:
            return np.full(n, default, dtype=object)
        return np.asarray([str(item or "") for item in arr], dtype=object)

    def add(name: str, fire: np.ndarray, amount: Any) -> None:
        mask = np.asarray(fire, dtype=bool)
        masks[name] = mask
        result[:] += mask.astype(float) * amount

    if bool(_cfg(cfg, "R_S3_DIV_STACK_ENABLED", False)):
        weighted = bool(_cfg(cfg, "R_S3_HTF_WEIGHT_ENABLED", False))
        weights = {
            "3m": float(_cfg(cfg, "R_S3_TF_WEIGHT_3M", 0.25)),
            "15m": float(_cfg(cfg, "R_S3_TF_WEIGHT_15M", 0.5)),
            "1h": float(_cfg(cfg, "R_S3_TF_WEIGHT_1H", 1.0)),
            "4h": float(_cfg(cfg, "R_S3_TF_WEIGHT_4H", 2.5)),
            "D": float(_cfg(cfg, "R_S3_TF_WEIGHT_D", 4.0)),
        }
        hidden_count = np.zeros(n, dtype=int)
        hidden_weight = np.zeros(n, dtype=float)
        main_count = np.zeros(n, dtype=int)
        main_weight = np.zeros(n, dtype=float)
        for tf in ("3m", "15m", "1h", "4h", "D"):
            div = np.char.upper(text_col(f"wt_divergence_{tf}").astype(str))
            hidden_name = "HIDDEN_BULL" if is_long else "HIDDEN_BEAR"
            main_name = "BEAR" if is_long else "BULL"
            hidden = div == hidden_name
            main = (div == main_name) & (weighted or tf in ("15m", "1h"))
            hidden_count += hidden.astype(int)
            hidden_weight += hidden.astype(float) * weights[tf]
            main_count += main.astype(int)
            main_weight += main.astype(float) * weights[tf]
        hidden_fire = hidden_count >= 2
        hidden_bonus = float(_cfg(cfg, "R_S3_HIDDEN_BONUS", 25.0))
        hidden_amount = hidden_bonus * (hidden_weight / np.maximum(hidden_count, 1) if weighted else 1.0)
        add("R_S3_HIDDEN_DIV", hidden_fire, hidden_amount)
        main_fire = main_count >= 1
        main_penalty = float(_cfg(cfg, "R_S3_MAIN_PENALTY", -20.0))
        main_amount = main_penalty * main_weight if weighted else main_penalty
        add("R_S3_MAIN_DIV_AGAINST", main_fire, main_amount)

    if bool(_cfg(cfg, "R_S7_HHLL_STACK_ENABLED", False)):
        tfs = [tf.strip() for tf in str(_cfg(cfg, "R_S7_HHLL_TFS", "15m,1h,4h,D") or "").split(",") if tf.strip()]
        min_indicators = int(_cfg(cfg, "R_S7_HHLL_MIN_INDICATORS", 2))
        bonus_per_tf = float(_cfg(cfg, "R_S7_HHLL_BONUS_PER_TF", 3.0))
        min_tfs = int(_cfg(cfg, "R_S7_HHLL_MIN_TFS_FOR_BONUS", 2))
        confirmed_count = np.zeros(n, dtype=int)
        for tf in tfs:
            high, high_prev = col(f"high_{tf}", 0.0), col(f"high_{tf}_prev", 0.0)
            low, low_prev = col(f"low_{tf}", 0.0), col(f"low_{tf}_prev", 0.0)
            wt = np.char.upper(text_col(f"wt_structure_{tf}").astype(str))
            k, k_prev = col(f"k_{tf}", np.nan), col(f"k_{tf}_prev", np.nan)
            if is_long:
                price_struct = (high > 0) & (high_prev > 0) & (low > 0) & (low_prev > 0) & (high > high_prev) & (low > low_prev)
                wt_struct = wt == "HH"
                k_struct = ~np.isnan(k) & ~np.isnan(k_prev) & (k > k_prev)
            else:
                price_struct = (high > 0) & (high_prev > 0) & (low > 0) & (low_prev > 0) & (high < high_prev) & (low < low_prev)
                wt_struct = wt == "LL"
                k_struct = ~np.isnan(k) & ~np.isnan(k_prev) & (k < k_prev)
            count = price_struct.astype(int) + wt_struct.astype(int) + k_struct.astype(int)
            confirmed_count += (count >= min_indicators).astype(int)
        fire = confirmed_count >= min_tfs
        add("R_S7_HHLL", fire, bonus_per_tf * confirmed_count)

    if bool(_cfg(cfg, "R_S4_HA_STREAK_ENABLED", False)):
        tf = str(_cfg(cfg, "R_S4_HA_STREAK_TF", "1h"))
        weight = float(_cfg(cfg, "R_S4_HA_STREAK_WEIGHT", 5.0))
        streak = col(f"ha_streak_{tf}", 0.0)
        color = np.char.lower(text_col(f"ha_{tf}").astype(str))
        aligned = (color == "green") if is_long else (color == "red")
        fire = aligned & ~np.isnan(streak) & (streak > 0)
        add("R_S4_HA_STREAK", fire, weight * np.minimum(streak, 5.0))

    if bool(_cfg(cfg, "R_S5_SENT_VEL_ENABLED", False)):
        if "0sentiment_velocity" in npz:
            velocity = col("0sentiment_velocity", 0.0)
        else:
            velocity = col("velocity", 0.0)
        threshold = float(_cfg(cfg, "R_S5_SENT_VEL_PCT_THR", 75.0))
        bonus = float(_cfg(cfg, "R_S5_SENT_VEL_BONUS", 5.0))
        fire = ~np.isnan(velocity) & ((velocity > 0) if is_long else (velocity < 0)) & (np.abs(velocity) >= threshold)
        add("R_S5_SENT_VEL", fire, bonus)
    return result, masks


def apply_15m_momentum_scalar(
    score: float,
    reasons: list[str],
    indicators: Mapping[str, Any],
    is_long: bool,
    cfg: Any,
    wt1_15m: float,
    wt2_15m: float,
    wt_cross_bull_3m: Any,
    wt_cross_bear_3m: Any,
) -> tuple[float, list[str]]:
    """Apply the native 15m WT-against penalty and exact 3m cross-price bypass."""
    previous = _float(indicators.get("wt1_15m_prev", wt2_15m), wt2_15m)
    penalty = float(_cfg(cfg, "WT15M_AGAINST_PENALTY", -5.0))
    cross_value = _float(indicators.get("wt_crossover_value_3m"), 0.0)
    cross_value_prev = _float(indicators.get("wt_crossover_value_3m_prev"), 0.0)
    hh_bypass = is_long and wt_cross_bull_3m and cross_value > 0 and cross_value_prev > 0 and cross_value > cross_value_prev
    ll_bypass = (not is_long) and wt_cross_bear_3m and cross_value > 0 and cross_value_prev > 0 and cross_value < cross_value_prev
    result = float(score)
    if is_long and wt1_15m < previous and not hh_bypass:
        result += penalty
        reasons.append(f"15M_AGAINST_L(wt15={wt1_15m:.1f}<prev={previous:.1f},{penalty:.0f})")
    elif not is_long and wt1_15m > previous and not ll_bypass:
        result += penalty
        reasons.append(f"15M_AGAINST_S(wt15={wt1_15m:.1f}>prev={previous:.1f},{penalty:.0f})")
    return result, reasons


def apply_15m_momentum_vec(
    npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any, score: Any = None
) -> tuple[np.ndarray, np.ndarray]:
    """NumPy twin of the native WT15M_AGAINST score penalty."""
    result = np.zeros(n, dtype=float) if score is None else np.asarray(score, dtype=float).copy()
    if result.shape != (n,):
        raise ValueError("score must have shape (n,)")

    def col(key: str, default: float) -> np.ndarray:
        value = npz.get(key)
        if value is None:
            return np.full(n, default, dtype=float)
        try:
            arr = np.asarray(value, dtype=float).reshape(-1)
        except (TypeError, ValueError):
            return np.full(n, default, dtype=float)
        return arr if arr.size == n else np.full(n, default, dtype=float)

    current = col("wt1_15m", 0.0)
    wt2 = col("wt2_15m", 0.0)
    if "wt1_15m_prev" in npz:
        previous = col("wt1_15m_prev", 0.0)
    else:
        previous = wt2
    cross_bull = col("wt_cross_bull_3m", 0.0) != 0
    cross_bear = col("wt_cross_bear_3m", 0.0) != 0
    cross_value = col("wt_crossover_value_3m", 0.0)
    cross_value_prev = col("wt_crossover_value_3m_prev", 0.0)
    if is_long:
        bypass = cross_bull & (cross_value > 0) & (cross_value_prev > 0) & (cross_value > cross_value_prev)
        fire = (current < previous) & ~bypass
    else:
        bypass = cross_bear & (cross_value > 0) & (cross_value_prev > 0) & (cross_value < cross_value_prev)
        fire = (current > previous) & ~bypass
    result += fire.astype(float) * float(_cfg(cfg, "WT15M_AGAINST_PENALTY", -5.0))
    return result, fire


def apply_shared_entry_score_components_vec(
    npz: Mapping[str, Any],
    n: int,
    is_long: bool,
    cfg: Any,
    score: Any = None,
    metrics: Mapping[str, Any] | None = None,
) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """Apply extracted ASR entry components in their native source order.

    This is a partial shared aggregate: WT/DC prefix, 15m momentum penalty,
    then structural/sentiment components. Other rate() branches remain outside.
    """
    result, masks = apply_score_prefix_vec(npz, n, is_long, cfg, score, metrics)
    result, against = apply_15m_momentum_vec(npz, n, is_long, cfg, result)
    masks["WT15M_AGAINST"] = against
    result, structure = apply_structure_score_vec(npz, n, is_long, cfg, result)
    masks.update(structure)
    return result, masks


def apply_gain_quality_bonus_scalar(score: float, gain: float) -> tuple[float, tuple[bool, bool]]:
    """Apply the source-ordered gain bonuses between the K gate and DC score."""
    result = float(score)
    first = result > 10 and gain > 3
    if first:
        result += 12
    second = result > 8 and gain > 1.5
    if second:
        result += 8
    return result, (first, second)


def apply_gain_quality_bonus_vec(score: Any, gain: Any) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """Vector twin; preserves the second comparison after the first bonus."""
    result, gains = np.broadcast_arrays(np.asarray(score, dtype=float), np.asarray(gain, dtype=float))
    result = result.copy()
    first = (result > 10) & (gains > 3)
    result[first] += 12
    second = (result > 8) & (gains > 1.5)
    result[second] += 8
    return result, {"GAIN_GT3_SCORE_GT10": first, "GAIN_GT1_5_SCORE_GT8_AFTER_FIRST": second}


def apply_ma_distance_score_scalar(
    score: float,
    reasons: list[str],
    indicators: Mapping[str, Any],
    current_price: float,
    is_long: bool,
) -> tuple[float, list[str]]:
    """Apply the native SMA500 1h and EMA20 4h distance score branches."""
    result = float(score)
    sma500 = _float(indicators.get("sma_500_1h"), 0.0)
    ema20 = _float(indicators.get("ema_20_4h"), 0.0)
    if sma500 > 0:
        distance = (current_price - sma500) / sma500
        if is_long and distance < -0.03:
            result += 15
            reasons.append(f"SMA500_DEEP({distance:.1%},+15)")
        elif is_long and distance > 0.08:
            result -= 10
            reasons.append(f"SMA500_OVER({distance:.1%},-10)")
        elif not is_long and distance > 0.03:
            result += 15
            reasons.append(f"SMA500_ABOVE({distance:.1%},+15)")
        elif not is_long and distance < -0.08:
            result -= 10
            reasons.append(f"SMA500_UNDER({distance:.1%},-10)")
    if ema20 > 0:
        distance = (current_price - ema20) / ema20
        if is_long and distance < -0.02:
            result += 10
            reasons.append(f"EMA20_4H_DEEP({distance:.1%},+10)")
        elif not is_long and distance > 0.02:
            result += 10
            reasons.append(f"EMA20_4H_ABOVE({distance:.1%},+10)")
    return result, reasons


def apply_ma_distance_score_vec(
    npz: Mapping[str, Any], n: int, is_long: bool, score: Any = None
) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """NumPy twin of native SMA500/EMA20 distance score branches."""
    result = np.zeros(n, dtype=float) if score is None else np.asarray(score, dtype=float).copy()
    if result.shape != (n,):
        raise ValueError("score must have shape (n,)")

    def col(key: str, default: float) -> np.ndarray:
        value = npz.get(key)
        if value is None:
            return np.full(n, default, dtype=float)
        try:
            arr = np.asarray(value, dtype=float).reshape(-1)
        except (TypeError, ValueError):
            return np.full(n, default, dtype=float)
        return arr if arr.size == n else np.full(n, default, dtype=float)

    price = col("close", 0.0)
    sma500 = col("sma_500_1h", 0.0)
    ema20 = col("ema_20_4h", 0.0)
    sma_distance = np.full(n, np.nan, dtype=float)
    ema_distance = np.full(n, np.nan, dtype=float)
    np.divide(price - sma500, sma500, out=sma_distance, where=(sma500 > 0) & np.isfinite(sma500))
    np.divide(price - ema20, ema20, out=ema_distance, where=(ema20 > 0) & np.isfinite(ema20))
    masks = {}
    if is_long:
        masks["SMA500_DEEP"] = (sma500 > 0) & (sma_distance < -0.03)
        masks["SMA500_OVER"] = (sma500 > 0) & (sma_distance > 0.08)
        masks["EMA20_4H_DEEP"] = (ema20 > 0) & (ema_distance < -0.02)
    else:
        masks["SMA500_ABOVE"] = (sma500 > 0) & (sma_distance > 0.03)
        masks["SMA500_UNDER"] = (sma500 > 0) & (sma_distance < -0.08)
        masks["EMA20_4H_ABOVE"] = (ema20 > 0) & (ema_distance > 0.02)
    amounts = {"SMA500_DEEP": 15, "SMA500_OVER": -10, "SMA500_ABOVE": 15, "SMA500_UNDER": -10, "EMA20_4H_DEEP": 10, "EMA20_4H_ABOVE": 10}
    for name, mask in masks.items():
        result += mask.astype(float) * amounts[name]
    return result, masks


def apply_rank_conviction_scalar(score, reasons, indicators, metrics, is_long, cfg):
    """Apply native RANK_CONVICTION score branch with its metric fallback."""
    if not bool(_cfg(cfg, "RANK_CONVICTION_ENABLED", True)):
        return float(score), reasons
    raw = indicators.get("0ranking_points_global", indicators.get("0ranking_points"))
    if raw is None and isinstance(metrics, dict):
        raw = metrics.get("0ranking_points_global", metrics.get("0ranking_points", 0))
    rank = _float(raw, 0)
    strong = float(_cfg(cfg, "RP_STRONG_THRESHOLD", 70.0))
    weak = float(_cfg(cfg, "RP_WEAK_THRESHOLD", 30.0))
    bonus = float(_cfg(cfg, "RP_STRONG_BONUS", 15.0))
    weak_penalty = float(_cfg(cfg, "RP_WEAK_PENALTY", -10.0))
    opposite_penalty = float(_cfg(cfg, "RP_OPPOSITE_PENALTY", -20.0))
    magnitude = abs(float(rank or 0.0))
    match = (is_long and rank > 0) or ((not is_long) and rank < 0)
    opposite = (is_long and rank < 0) or ((not is_long) and rank > 0)
    result = float(score)
    if match and magnitude >= strong:
        result += bonus
        reasons.append(f"RANK_STRONG({rank:.0f},+{bonus:.0f})")
    elif magnitude < weak:
        result += weak_penalty
        reasons.append(f"RANK_WEAK({rank:.0f},{weak_penalty:.0f})")
    elif opposite and magnitude >= strong:
        result += opposite_penalty
        reasons.append(f"RANK_OPPOSITE({rank:.0f},{opposite_penalty:.0f})")
    return result, reasons


def apply_rank_conviction_vec(npz, n, is_long, cfg, score=None, metrics=None):
    """NumPy twin of RANK_CONVICTION; returns score plus ordered branch masks."""
    result = np.zeros(n, dtype=float) if score is None else np.asarray(score, dtype=float).copy()
    if result.shape != (n,):
        raise ValueError("score must have shape (n,)")
    masks = {}
    if not bool(_cfg(cfg, "RANK_CONVICTION_ENABLED", True)):
        return result, masks
    key = "0ranking_points_global" if "0ranking_points_global" in npz else "0ranking_points"
    raw = npz.get(key)
    if raw is None and isinstance(metrics, Mapping):
        raw = metrics.get("0ranking_points_global", metrics.get("0ranking_points"))
    if raw is None:
        rank = np.zeros(n, dtype=float)
    else:
        try:
            rank = np.asarray(raw, dtype=float).reshape(-1)
        except (TypeError, ValueError):
            rank = np.zeros(n, dtype=float)
        if rank.size != n:
            rank = np.zeros(n, dtype=float)
    strong = float(_cfg(cfg, "RP_STRONG_THRESHOLD", 70.0))
    weak = float(_cfg(cfg, "RP_WEAK_THRESHOLD", 30.0))
    bonus = float(_cfg(cfg, "RP_STRONG_BONUS", 15.0))
    weak_penalty = float(_cfg(cfg, "RP_WEAK_PENALTY", -10.0))
    opposite_penalty = float(_cfg(cfg, "RP_OPPOSITE_PENALTY", -20.0))
    magnitude = np.abs(rank)
    match = rank > 0 if is_long else rank < 0
    opposite = rank < 0 if is_long else rank > 0
    strong_match = match & (magnitude >= strong)
    weak_mask = ~strong_match & (magnitude < weak)
    opposite_mask = ~strong_match & ~weak_mask & opposite & (magnitude >= strong)
    for name, mask, amount in (
        ("RANK_STRONG", strong_match, bonus),
        ("RANK_WEAK", weak_mask, weak_penalty),
        ("RANK_OPPOSITE", opposite_mask, opposite_penalty),
    ):
        masks[name] = mask
        result += mask.astype(float) * amount
    return result, masks


def apply_channel_k_cross_score_scalar(score, reasons, indicators, current_price, is_long):
    """Apply the first directional rate() score slice: channel recovery, K zones and DC-basis crosses."""
    def value(key, default=0.0):
        return _float(indicators.get(key), default)
    result = float(score)
    widths = {}
    for tf, threshold in (("1h", 5.0), ("D", 6.0), ("4h", 4.0)):
        high, low = value(f"dc_high_{tf}"), value(f"dc_low_{tf}")
        widths[tf] = ((high - low) / low) * 100 if low > 0 else 0.0
    if is_long:
        for tf, width_thr, price_frac, amount, tag in (
            ("1h", 5.0, .97, 25, "JUMP_PULLBACK"),
            ("D", 6.0, .95, 35, "JUMP_PB_D"),
            ("4h", 4.0, .96, 30, "JUMP_PB_4H"),
        ):
            if widths[tf] > width_thr and current_price < value(f"dc_high_{tf}") * price_frac and current_price > value(f"dc_basis_{tf}"):
                result += amount; reasons.append(f"{tag}(+{amount})")
        for tf, threshold, amount, tag in (("15m",20,10,"LOW_K15"),("1h",20,15,"LOW_K1H"),("4h",25,20,"LOW_K4H"),("D",30,25,"LOW_KD")):
            if value(f"k_{tf}",50.0) < threshold:
                result += amount; reasons.append(f"{tag}(+{amount})")
        for tf, amount in (("3m",30),("15m",25),("1h",20),("4h",15),("D",10)):
            if indicators.get(f"dc_basis_crossover_{tf}", False):
                result += amount; reasons.append(f"DCB_CO_{tf.upper()}(+{amount})")
        for tf, amount in (("3m",30),("15m",25),("1h",20)):
            if indicators.get(f"dc_basis_crossunder_{tf}", False):
                result -= amount; reasons.append(f"DCB_CU_{tf.upper()}_BAD(-{amount})")
    else:
        for tf, width_thr, price_frac, amount, tag in (
            ("1h", 5.0, 1.03, 25, "DROP_RECOVERY"),
            ("D", 6.0, 1.05, 35, "DROP_REC_D"),
            ("4h", 4.0, 1.04, 30, "DROP_REC_4H"),
        ):
            if widths[tf] > width_thr and current_price > value(f"dc_low_{tf}") * price_frac and current_price < value(f"dc_basis_{tf}"):
                result += amount; reasons.append(f"{tag}(+{amount})")
        for tf, threshold, amount, tag in (("15m",80,10,"HIGH_K15"),("1h",80,15,"HIGH_K1H"),("4h",75,20,"HIGH_K4H"),("D",70,25,"HIGH_KD")):
            if value(f"k_{tf}",50.0) > threshold:
                result += amount; reasons.append(f"{tag}(+{amount})")
        for tf, amount in (("3m",30),("15m",25),("1h",20),("4h",15),("D",10)):
            if indicators.get(f"dc_basis_crossunder_{tf}", False):
                result += amount; reasons.append(f"DCB_CU_{tf.upper()}(+{amount})")
        for tf, amount in (("3m",30),("15m",25),("1h",20)):
            if indicators.get(f"dc_basis_crossover_{tf}", False):
                result -= amount; reasons.append(f"DCB_CO_{tf.upper()}_BAD(-{amount})")
    return result, reasons


def apply_channel_k_cross_score_vec(npz, n, is_long, score=None):
    """NumPy twin of native channel recovery, K-zone and DC-basis cross score slice."""
    result = np.zeros(n, dtype=float) if score is None else np.asarray(score, dtype=float).copy()
    if result.shape != (n,):
        raise ValueError("score must have shape (n,)")
    def col(key, default=0.0):
        value = npz.get(key)
        if value is None:
            return np.full(n, default, dtype=float)
        try:
            arr = np.asarray(value, dtype=float).reshape(-1)
        except (TypeError, ValueError):
            return np.full(n, default, dtype=float)
        return arr if arr.size == n else np.full(n, default, dtype=float)
    masks = {}
    def add(name, mask, amount):
        masks[name] = np.asarray(mask, dtype=bool)
        result[:] += masks[name].astype(float) * amount
    price = col("close")
    widths = {}
    for tf in ("1h", "D", "4h"):
        high, low = col(f"dc_high_{tf}"), col(f"dc_low_{tf}")
        width = np.zeros(n, dtype=float)
        np.divide(high - low, low, out=width, where=low > 0)
        widths[tf] = width * 100
    if is_long:
        for tf, threshold, frac, amount, tag in (("1h",5,.97,25,"JUMP_PULLBACK"),("D",6,.95,35,"JUMP_PB_D"),("4h",4,.96,30,"JUMP_PB_4H")):
            add(tag,(widths[tf]>threshold)&(price<col(f"dc_high_{tf}")*frac)&(price>col(f"dc_basis_{tf}")),amount)
        for tf, threshold, amount, tag in (("15m",20,10,"LOW_K15"),("1h",20,15,"LOW_K1H"),("4h",25,20,"LOW_K4H"),("D",30,25,"LOW_KD")):
            add(tag,col(f"k_{tf}",50)<threshold,amount)
        for tf, amount in (("3m",30),("15m",25),("1h",20),("4h",15),("D",10)):
            add(f"DCB_CO_{tf.upper()}",col(f"dc_basis_crossover_{tf}")!=0,amount)
        for tf, amount in (("3m",30),("15m",25),("1h",20)):
            add(f"DCB_CU_{tf.upper()}_BAD",col(f"dc_basis_crossunder_{tf}")!=0,-amount)
    else:
        for tf, threshold, frac, amount, tag in (("1h",5,1.03,25,"DROP_RECOVERY"),("D",6,1.05,35,"DROP_REC_D"),("4h",4,1.04,30,"DROP_REC_4H")):
            add(tag,(widths[tf]>threshold)&(price>col(f"dc_low_{tf}")*frac)&(price<col(f"dc_basis_{tf}")),amount)
        for tf, threshold, amount, tag in (("15m",80,10,"HIGH_K15"),("1h",80,15,"HIGH_K1H"),("4h",75,20,"HIGH_K4H"),("D",70,25,"HIGH_KD")):
            add(tag,col(f"k_{tf}",50)>threshold,amount)
        for tf, amount in (("3m",30),("15m",25),("1h",20),("4h",15),("D",10)):
            add(f"DCB_CU_{tf.upper()}",col(f"dc_basis_crossunder_{tf}")!=0,amount)
        for tf, amount in (("3m",30),("15m",25),("1h",20)):
            add(f"DCB_CO_{tf.upper()}_BAD",col(f"dc_basis_crossover_{tf}")!=0,-amount)
    return result, masks
