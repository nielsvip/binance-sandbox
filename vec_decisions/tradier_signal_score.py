"""Scalar-array adapter for Tradier's ordered native entry score route."""
import numpy as np
import re


class _Tradeability:
    def __init__(self, symbol, is_long):
        self.symbol = symbol
        self.symbols_long_v12 = [symbol] if is_long else []
        self.symbols_short_v12 = [symbol] if not is_long else []

    def is_symbol_tradeable(self, symbol, account_key, side):
        return symbol == self.symbol and side == ("LONG" if self.symbols_long_v12 else "SHORT")


def _value_at(npz, key, index, n):
    try:
        raw = np.asarray(npz[key])
        if raw.ndim != 1 or raw.size != n:
            return None
        value = raw[index]
        return value.item() if hasattr(value, "item") else value
    except (KeyError, TypeError, ValueError, IndexError):
        return None


def paired_lower_tf_indicators(indicators, ltf_enabled):
    """Pair native 3m/5m score inputs to their 15m twins under LTF-off."""
    result = dict(indicators or {})
    if ltf_enabled:
        return result
    for key in list(result):
        match = re.match(r"^(.*_)(?:3m|5m)(.*)$", str(key))
        if not match:
            continue
        paired = f"{match.group(1)}15m{match.group(2)}"
        if paired in result:
            result[key] = result[paired]
        else:
            result.pop(key, None)
    paired_score_fields = (
        ("k_15m", "k_5m"), ("d_15m", "d_5m"),
        ("k_15m_prev", "k_5m_prev"), ("dc_high_15m", "dc_high_5m"),
        ("dc_low_15m", "dc_low_5m"),
        ("dc_high_crossover_15m", "dc_high_crossover_5m"),
        ("dc_low_crossunder_15m", "dc_low_crossunder_5m"),
        ("relative_volume_15m", "relative_volume_5m"),
        ("rel_vol_15m", "rel_vol_5m"),
    )
    for source, target in paired_score_fields:
        if target not in result and source in result:
            result[target] = result[source]
    return result


def _column(npz, key, n, default):
    try:
        value = np.asarray(npz[key], dtype=float).reshape(-1)
        if value.size == n:
            return value
    except (KeyError, TypeError, ValueError):
        pass
    return np.full(n, default, dtype=float)


def possible_entry_setup_mask(npz, n, is_long, cfg):
    """Cheap necessary-condition mask for native good_entry; never excludes a native setup."""
    if float(getattr(cfg, "ENTRY_SCORE_THRESHOLD", 12.0) or 0.0) <= 0.0:
        return np.ones(n, dtype=bool)
    ltf = bool(getattr(cfg, "LTF_SUB15M_TRADING_ENABLED", False))
    k5 = _column(npz, "k_5m" if ltf else "k_15m", n, 50.0)
    d5 = _column(npz, "d_5m" if ltf else "d_15m", n, 50.0)
    k5p = _column(npz, "k_5m_prev" if ltf else "k_15m_prev", n, 50.0)
    k1 = _column(npz, "k_1m", n, 50.0)
    d1 = _column(npz, "d_1m", n, 50.0)
    k1p = _column(npz, "k_1m_prev", n, 50.0)
    timestamps = _column(npz, "timestamps", n, 0.0)
    try:
        m1_raw = np.asarray(npz["timestamp_1m"], dtype=float).reshape(-1)
        if m1_raw.size != n:
            raise ValueError("unaligned timestamp_1m")
        m1_lag = timestamps - m1_raw
        fresh = m1_lag < 60.0
    except (KeyError, TypeError, ValueError):
        # The scalar scorer defaults a missing/unparseable 1m timestamp to now_ts.
        fresh = np.ones(n, dtype=bool)
    if is_long:
        zone = float(getattr(cfg, "TRADIER_STOCH_ENTRY_LONG_TRADIER", 30.0))
        extreme = float(getattr(cfg, "TRADIER_STOCH_EXTREME_LONG_TRADIER", 15.0))
        stoch = ((k5 < zone) & (k5 > d5)) | (k5 < extreme)
        stoch |= (k5 > d5) & (k5p <= d5) & (k5 < 35.0) & fresh
        stoch |= (k1 > d1) & (k1p <= d1) & (k1 < 30.0) & fresh
    else:
        zone = float(getattr(cfg, "TRADIER_STOCH_ENTRY_SHORT_TRADIER", 70.0))
        extreme = float(getattr(cfg, "TRADIER_STOCH_EXTREME_SHORT_TRADIER", 85.0))
        stoch = ((k5 > zone) & (k5 < d5)) | (k5 > extreme)
        stoch |= (k5 < d5) & (k5p >= d5) & (k5 > 65.0) & fresh
        stoch |= (k1 < d1) & (k1p >= d1) & (k1 > 70.0) & fresh
    kz_enabled = bool(getattr(cfg, "K_ZONE_ENTRY_ENABLED_TRADIER", True))
    k4 = _column(npz, "k_4h", n, 50.0)
    d4 = _column(npz, "d_4h", n, 50.0)
    if is_long:
        kzone = kz_enabled & (k4 < float(getattr(cfg, "K_ZONE_LONG_THRESHOLD_TRADIER", 35.0))) & (k4 > d4)
        primary = str(getattr(cfg, "ENTRY_PRIMARY_TF", "4h") or "4h")
        flow = _column(npz, f"mfi_{primary}", n, 50.0)
        rsi = flow < float(getattr(cfg, "RSI_ENTRY_LONG_TRADIER", 42.0))
    else:
        kzone = kz_enabled & (k4 > float(getattr(cfg, "K_ZONE_SHORT_THRESHOLD_TRADIER", 65.0))) & (k4 < d4)
        primary = str(getattr(cfg, "ENTRY_PRIMARY_TF", "4h") or "4h")
        flow = _column(npz, f"mfi_{primary}", n, 50.0)
        rsi = flow > float(getattr(cfg, "RSI_ENTRY_SHORT_TRADIER", 58.0))
    bb_tf = str(getattr(cfg, "BB_BREAKOUT_ENTRY_TF", "OFF") or "OFF")
    bb = np.zeros(n, dtype=bool)
    if bb_tf in ("15m", "1h", "4h", "D"):
        pct = _column(npz, f"bb_pct_b_{bb_tf}", n, 0.5)
        band = _column(npz, f"bb_upper_{bb_tf}" if is_long else f"bb_lower_{bb_tf}", n, 0.0)
        bb = ((pct > 0.95) if is_long else (pct < 0.05)) & (band > 0.0)
    return stoch | kzone | rsi | bb


def golden_angles_score_vec(npz, n, is_long, safe):
    """Vectorize the native Golden Angles additive scoring section."""
    wt1_tf = "wt1_4h" if is_long else "wt1_15m"
    wt2_tf = "wt2_4h" if is_long else "wt2_15m"
    wt_score = _column(npz, wt1_tf, n, 0.0) - _column(npz, wt2_tf, n, 0.0)
    dc_pos = _column(npz, "dc_position_1h", n, 0.5)
    wt_vel = _column(npz, "wt_velocity_1h", n, 0.0)
    cross = np.asarray([_value_at(npz, "wt_cross_rising_1h", index, n) for index in range(n)], dtype=object)
    result = np.zeros(n, dtype=float)
    if is_long:
        result += np.where(wt_score > 5.0, 6.0, np.where(wt_score > 0.0, 2.0, 0.0))
        result += np.where(dc_pos < 0.25, 5.0, np.where(dc_pos < 0.40, 2.0, 0.0))
        result += np.fromiter((3.0 if value is True else 0.0 for value in cross), dtype=float, count=n)
        result += np.where(wt_vel > 2.0, 2.0, 0.0)
    else:
        result += np.where(wt_score < -5.0, 6.0, np.where(wt_score < 0.0, 2.0, 0.0))
        result += np.where(dc_pos > 0.75, 5.0, np.where(dc_pos > 0.60, 2.0, 0.0))
        result += np.fromiter((3.0 if value is False else 0.0 for value in cross), dtype=float, count=n)
        result += np.where(wt_vel < -2.0, 2.0, 0.0)
    reason_rows = [[] for _ in range(n)]
    for index in np.flatnonzero(result):
        if is_long:
            if wt_score[index] > 5.0:
                reason_rows[index].append(f"GA_WT4H_BULL({wt_score[index]:.0f},+6)")
            elif wt_score[index] > 0.0:
                reason_rows[index].append(f"GA_WT4H_OK({wt_score[index]:.0f},+2)")
            if dc_pos[index] < 0.25:
                reason_rows[index].append(f"GA_DC_BOT({dc_pos[index]:.2f},+5)")
            elif dc_pos[index] < 0.40:
                reason_rows[index].append(f"GA_DC_LOW({dc_pos[index]:.2f},+2)")
            if cross[index] is True:
                reason_rows[index].append("GA_WT_RISING(+3)")
            if wt_vel[index] > 2.0:
                reason_rows[index].append(f"GA_VEL_UP({wt_vel[index]:.1f},+2)")
        else:
            if wt_score[index] < -5.0:
                reason_rows[index].append(f"GA_WT15M_BEAR({wt_score[index]:.0f},+6)")
            elif wt_score[index] < 0.0:
                reason_rows[index].append(f"GA_WT15M_OK({wt_score[index]:.0f},+2)")
            if dc_pos[index] > 0.75:
                reason_rows[index].append(f"GA_DC_TOP({dc_pos[index]:.2f},+5)")
            elif dc_pos[index] > 0.60:
                reason_rows[index].append(f"GA_DC_HIGH({dc_pos[index]:.2f},+2)")
            if cross[index] is False:
                reason_rows[index].append("GA_WT_FALLING(+3)")
            if wt_vel[index] < -2.0:
                reason_rows[index].append(f"GA_VEL_DN({wt_vel[index]:.1f},+2)")
    return result, reason_rows


def sentiment_trend_score_vec(npz, n, is_long, safe):
    """Vectorize native sentiment and multi-timeframe trend additive score layers."""
    rank = _column(npz, "0sentiment_rank", n, 999.0)
    sentiment = _column(npz, "0market_sentiment_score", n, 0.0)
    local = _column(npz, "0market_sentiment_local", n, 0.0)
    sent_points = np.zeros(n, dtype=float)
    sent_reasons = [[] for _ in range(n)]
    if is_long:
        sent_points += np.where(rank <= 20.0, 4.0, np.where(rank <= 50.0, 2.0, 0.0))
        sent_points += np.where(local > 20.0, 2.0, 0.0)
        rel = local > sentiment + 10.0
        sent_points += np.where(rel, 2.0, 0.0)
        classes = [_value_at(npz, "0sentiment_classification", index, n) for index in range(n)]
        sent_points += np.fromiter((2.0 if isinstance(value, str) and "BULLISH" in value else 0.0 for value in classes), dtype=float, count=n)
        for index in np.flatnonzero((rank <= 20.0) | rel):
            if rank[index] <= 20.0:
                sent_reasons[index].append("Rank_Top20")
            if rel[index]:
                sent_reasons[index].append("Rel_Strength")
    else:
        sent_points += np.where(rank >= 180.0, 4.0, np.where(rank >= 150.0, 2.0, 0.0))
        sent_points += np.where(local < -20.0, 2.0, 0.0)
        rel = local < sentiment - 10.0
        sent_points += np.where(rel, 2.0, 0.0)
        classes = [_value_at(npz, "0sentiment_classification", index, n) for index in range(n)]
        sent_points += np.fromiter((2.0 if isinstance(value, str) and "BEARISH" in value else 0.0 for value in classes), dtype=float, count=n)
        for index in np.flatnonzero((rank >= 180.0) | rel):
            if rank[index] >= 180.0:
                sent_reasons[index].append("Rank_Bot20")
            if rel[index]:
                sent_reasons[index].append("Rel_Weakness")
    high_1h = _column(npz, "dc_high_1h", n, 0.0)
    low_1h = _column(npz, "dc_low_1h", n, 0.0)
    high_4h = _column(npz, "dc_high_4h", n, 0.0)
    low_4h = _column(npz, "dc_low_4h", n, 0.0)
    high_d = _column(npz, "dc_high_D", n, 0.0)
    low_d = _column(npz, "dc_low_D", n, 0.0)
    high_1h_prev = _column(npz, "dc_high_1h_ant", n, 0.0) if "dc_high_1h_ant" in npz else high_1h
    low_1h_prev = _column(npz, "dc_low_1h_ant", n, 0.0) if "dc_low_1h_ant" in npz else low_1h
    high_4h_prev = _column(npz, "dc_high_4h_ant", n, 0.0) if "dc_high_4h_ant" in npz else high_4h
    low_4h_prev = _column(npz, "dc_low_4h_ant", n, 0.0) if "dc_low_4h_ant" in npz else low_4h
    high_d_prev = _column(npz, "dc_high_D_ant", n, 0.0) if "dc_high_D_ant" in npz else high_d
    low_d_prev = _column(npz, "dc_low_D_ant", n, 0.0) if "dc_low_D_ant" in npz else low_d
    price = _column(npz, "close", n, 0.0)
    ema200 = _column(npz, "ema_200_1h", n, 0.0 if is_long else 999999.0)
    trend_points = np.zeros(n, dtype=float)
    trend_reasons = [[] for _ in range(n)]
    if is_long:
        trend_points += np.where(high_d >= high_d_prev, 2.0, 0.0)
        d_trend = low_d > low_d_prev
        trend_points += np.where(d_trend, 3.0, 0.0)
        expand = high_4h > high_4h_prev
        low_expand = (~expand) & (low_4h > low_4h_prev)
        trend_points += np.where(expand, 3.0, np.where(low_expand, 2.0, 0.0))
        trend_points += np.where(low_1h >= low_1h_prev, 2.0, 0.0)
        trend_points += np.where(price > ema200, 2.0, 0.0)
        for index in np.flatnonzero(d_trend | expand):
            if d_trend[index]:
                trend_reasons[index].append("D_Trend_Up")
            if expand[index]:
                trend_reasons[index].append("4h_Expanding")
    else:
        trend_points += np.where(low_d <= low_d_prev, 2.0, 0.0)
        d_trend = high_d < high_d_prev
        down_expand = low_4h < low_4h_prev
        high_down_expand = (~down_expand) & (high_4h < high_4h_prev)
        trend_points += np.where(d_trend, 3.0, 0.0)
        trend_points += np.where(down_expand, 3.0, np.where(high_down_expand, 2.0, 0.0))
        trend_points += np.where(high_1h <= high_1h_prev, 2.0, 0.0)
        trend_points += np.where(price < ema200, 2.0, 0.0)
        for index in np.flatnonzero(d_trend | down_expand):
            if d_trend[index]:
                trend_reasons[index].append("D_Trend_Down")
            if down_expand[index]:
                trend_reasons[index].append("4h_Expanding_Down")
    reason_rows = [sent_reasons[index] + trend_reasons[index] for index in range(n)]
    return sent_points, trend_points, reason_rows


def entry_score_arrays(npz, n, symbol, is_long, cfg, candidate_mask=None):
    """Evaluate native StockStrategy score and final threshold in bar order.

    This calls the production scalar scorer for each prepared bar so every
    ordered pre-gate, contribution, recommendation and Sniper exception stays
    identical while the vector engine assembles its action mask.
    """
    if str(getattr(cfg, "MODE", "crypto")) != "tradier":
        return np.zeros(n, dtype=float), np.zeros(n, dtype=bool), [""] * n
    timestamps = np.asarray(npz.get("timestamps", []), dtype=float).reshape(-1)
    if timestamps.size != n:
        raise ValueError("Tradier signal score requires aligned NPZ timestamps")
    import tradier_manage as tm

    strategy = tm.StockStrategy.__new__(tm.StockStrategy)
    strategy.config = cfg
    strategy.trade_manager = _Tradeability(symbol, is_long)
    side = "LONG" if is_long else "SHORT"
    from vec_decisions.tradier_market_quality import market_quality_score_vec_details

    mq_enabled = bool(getattr(cfg, "MARKET_QUALITY_SCORE_ENABLED_TRADIER", False))
    mq_bonus, mq_reasons = market_quality_score_vec_details(npz, n, is_long, cfg, _column)
    ga_bonus, ga_reasons = golden_angles_score_vec(npz, n, is_long, _column)
    sentiment_bonus, trend_bonus, st_reasons = sentiment_trend_score_vec(npz, n, is_long, _column)
    score_values = np.zeros(n, dtype=float)
    fires = np.zeros(n, dtype=bool)
    reasons = [""] * n
    candidates = possible_entry_setup_mask(npz, n, is_long, cfg) if candidate_mask is None else np.asarray(candidate_mask, dtype=bool)
    if candidates.shape != (n,):
        raise ValueError("candidate_mask must have shape (n,)")
    cfg_token = tm._TR_SCORE_CFG_CTX.set(cfg)
    try:
        for index in range(n):
            if not candidates[index]:
                continue
            indicators = {}
            for key in npz:
                value = _value_at(npz, key, index, n)
                if value is not None:
                    indicators[key] = value
            ts_token = tm._TR_SCORE_TS_CTX.set(float(timestamps[index]))
            mq_token = tm._TR_SCORE_MQ_CTX.set((float(mq_bonus[index]), mq_reasons[index]) if mq_enabled else None)
            ga_token = tm._TR_SCORE_GA_CTX.set((float(ga_bonus[index]), ga_reasons[index]))
            st_token = tm._TR_SCORE_ST_CTX.set((float(sentiment_bonus[index]), float(trend_bonus[index]), st_reasons[index]))
            try:
                score, recommendation, reason = strategy.calculate_signal_score(
                    "v12", symbol, is_long, float(indicators.get("close", 0.0) or 0.0), indicators, None, is_exit=False
                )
                threshold = float(tm._cfg("ENTRY_SCORE_THRESHOLD", 12.0, "v12", symbol, side) or 0.0)
            finally:
                tm._TR_SCORE_ST_CTX.reset(st_token)
                tm._TR_SCORE_GA_CTX.reset(ga_token)
                tm._TR_SCORE_MQ_CTX.reset(mq_token)
                tm._TR_SCORE_TS_CTX.reset(ts_token)
            score_values[index] = float(score)
            native_reason = str(reason or "")
            if mq_enabled and mq_bonus[index] > 0.0 and score_values[index] > 0.0 and "MQ_" not in native_reason:
                raise RuntimeError("Tradier native entry scorer omitted an enabled MQ bonus")
            fires[index] = score_values[index] >= threshold or (score_values[index] >= 6.0 and "Sniper" in native_reason)
            if fires[index]:
                if score_values[index] < threshold and "Sniper" in native_reason:
                    reasons[index] = f"SNIPER_{side}_{recommendation}"
                else:
                    reasons[index] = f"{side}_{recommendation}: {native_reason}"
    finally:
        tm._TR_SCORE_CFG_CTX.reset(cfg_token)
    return score_values, fires, reasons
