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
        ("dc_high_15m_ant", "dc_high_5m_ant"),
        ("dc_low_15m_ant", "dc_low_5m_ant"),
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


def pullback_setup_score_vec(npz, n, is_long, ltf_enabled):
    """Vectorize native pullback/setup additive score layer."""
    def col(key, default=0.0):
        return _column(npz, key, n, default)
    price = col("close", 0.0)
    dc_low_15 = col("dc_low_15m", 0.0)
    dc_high_15 = col("dc_high_15m", 0.0)
    dc_low_5 = col("dc_low_5m" if ltf_enabled else "dc_low_15m", 0.0)
    dc_high_5 = col("dc_high_5m" if ltf_enabled else "dc_high_15m", 0.0)
    k_d, d_d = col("k_D", 50.0), col("d_D", 50.0)
    k4, d4 = col("k_4h", 50.0), col("d_4h", 50.0)
    k1, d1 = col("k_1h", 50.0), col("d_1h", 50.0)
    k1_prev = col("k_1h_prev", 0.0)
    points = np.zeros(n, dtype=float)
    reasons = [[] for _ in range(n)]
    if is_long:
        bounce15 = (dc_low_15 > 0.0) & (((price - dc_low_15) / np.where(dc_low_15 > 0.0, dc_low_15, 1.0)) < 0.015)
        bounce5 = (~bounce15) & (dc_low_5 > 0.0) & (((price - dc_low_5) / np.where(dc_low_5 > 0.0, dc_low_5, 1.0)) < 0.008)
        points += np.where(bounce15, 5.0, np.where(bounce5, 4.0, 0.0))
        points += np.where(k_d > 95.0, -5.0, np.where((k_d < 50.0) & (k_d > d_d), 5.0, 0.0))
        points += np.where(k4 < 50.0, 5.0, np.where(k4 < 80.0, 1.0, 0.0))
        points += np.where((k4 < 20.0) & (k4 > d4), 4.0, np.where((k4 < 50.0) & (k4 > d4), 2.0, -3.0))
        turn = (k1 < 40.0) & (k1 > k1_prev)
        points += np.where(turn, 3.0, np.where((k1 < 80.0) & (k1 > d1), 2.0, 0.0))
        for index in np.flatnonzero(bounce15 | bounce5 | ((k4 < 50.0) & (k4 < 80.0)) | turn):
            if bounce15[index]: reasons[index].append("Bounce_15m_Low")
            elif bounce5[index]: reasons[index].append("Bounce_5m_Low")
            if k4[index] < 50.0: reasons[index].append("4h_Deep_Value")
            if turn[index]: reasons[index].append("1h_Turn_Up")
    else:
        bounce15 = (dc_high_15 > 0.0) & (((dc_high_15 - price) / np.where(dc_high_15 > 0.0, dc_high_15, 1.0)) < 0.015)
        bounce5 = (~bounce15) & (dc_high_5 > 0.0) & (((dc_high_5 - price) / np.where(dc_high_5 > 0.0, dc_high_5, 1.0)) < 0.008)
        points += np.where(bounce15, 5.0, np.where(bounce5, 4.0, 0.0))
        points += np.where(k_d < 5.0, -5.0, 0.0)
        points += np.where((k_d < 50.0) & (k_d > d_d), 5.0, 0.0)
        points += np.where(k4 > 50.0, 5.0, np.where(k4 > 20.0, 2.0, 0.0))
        points += np.where((k4 > 80.0) & (k4 < d4), 4.0, np.where((k4 > 50.0) & (k4 < d4), 2.0, -3.0))
        turn = (k1 > 60.0) & (k1 < k1_prev)
        points += np.where(turn, 3.0, np.where((k1 > 20.0) & (k1 < d1), 2.0, 0.0))
        for index in np.flatnonzero(bounce15 | bounce5 | (k4 > 50.0) | turn):
            if bounce15[index]: reasons[index].append("Bounce_15m_High")
            elif bounce5[index]: reasons[index].append("Bounce_5m_High")
            if k4[index] > 50.0: reasons[index].append("4h_Premium_Short")
            if turn[index]: reasons[index].append("1h_Turn_Down")
    return points, reasons


def trigger_score_vec(npz, n, is_long, cfg, ltf_enabled):
    """Vectorize native trigger points except the score-dependent falling-knife penalty."""
    def col(key, default=0.0):
        return _column(npz, key, n, default)
    k15, d15 = col("k_15m", 50.0), col("d_15m", 50.0)
    k5 = col("k_5m" if ltf_enabled else "k_15m", 50.0)
    d5 = col("d_5m" if ltf_enabled else "d_15m", 50.0)
    k1, d1, k1_prev = col("k_1m", 50.0), col("d_1m", 50.0), col("k_1m_prev", 50.0)
    wt1_15, wt2_15 = col("wt1_15m", 0.0), col("wt2_15m", 0.0)
    rel_vol = col("rel_vol_15m", 1.0)
    timestamps = col("timestamps", 0.0)
    try:
        raw_ts = np.asarray(npz["timestamp_1m"], dtype=float).reshape(-1)
        if raw_ts.size != n: raise ValueError("unaligned timestamp_1m")
        fresh = (timestamps - raw_ts) < 60.0
    except (KeyError, TypeError, ValueError):
        fresh = np.ones(n, dtype=bool)
    points = np.zeros(n, dtype=float)
    reasons = [[] for _ in range(n)]
    if is_long:
        points += np.where(k15 > d15, 2.0, 0.0)
        points += np.where(k5 > d5, 2.0, 0.0)
        points += np.where(wt1_15 > wt2_15, 2.0, 0.0)
        sniper = (k5 < 30.0) & (k5 > d5)
        points += np.where(sniper, 3.0, 0.0)
        cross1 = fresh & (k1 > d1) & (k1_prev <= d1) & (k1 < 30.0)
        points += np.where(cross1, 4.0, 0.0)
    else:
        points += np.where(k15 < d15, 2.0, 0.0)
        points += np.where(k5 < d5, 2.0, 0.0)
        points += np.where(wt1_15 < wt2_15, 2.0, 0.0)
        sniper = (k5 > 70.0) & (k5 < d5)
        points += np.where(sniper, 3.0, 0.0)
        cross1 = fresh & (k1 < d1) & (k1_prev >= d1) & (k1 > 70.0)
        points += np.where(cross1, 4.0, 0.0)
    stale = ~fresh
    high_vol = rel_vol > 1.2
    points += np.where(high_vol, 2.0, 0.0)
    for index in np.flatnonzero(sniper | cross1 | stale | high_vol):
        if sniper[index]: reasons[index].append("5m_Sniper")
        if cross1[index]: reasons[index].append("1m_Cross_Up" if is_long else "1m_Cross_Down")
        if stale[index]: reasons[index].append("1m_Stale_Ignored")
        if high_vol[index]: reasons[index].append("High_Vol")
    if bool(getattr(cfg, "MOMENTUM_FADE_ENABLED_TRADIER", True)):
        body_min = float(getattr(cfg, "MOMENTUM_FADE_BODY_ATR_MIN_TRADIER", 2.0))
        volume_min = float(getattr(cfg, "MOMENTUM_FADE_VOL_MIN_TRADIER", 2.0))
        k_zone = bool(getattr(cfg, "MOMENTUM_FADE_K_ZONE_TRADIER", True))
        bonus_raw = getattr(cfg, "MOMENTUM_FADE_SCORE_BONUS_TRADIER", 5)
        bonus = float(bonus_raw)
        atr = col("atr_15m", 0.0)
        high = col("high_15m", 0.0)
        low = col("low_15m", 0.0)
        price_range = np.where((high > 0.0) & (low > 0.0), high - low, 0.0)
        body_vs_atr = np.divide(price_range, atr, out=np.zeros(n, dtype=float), where=atr > 0.0)
        k_ok = ((k15 < 40.0) if is_long else (k15 > 60.0)) if k_zone else np.ones(n, dtype=bool)
        fade = (body_vs_atr >= body_min) & (rel_vol >= volume_min) & k_ok
        points += np.where(fade, bonus, 0.0)
        for index in np.flatnonzero(fade):
            reasons[index].append(f"MOM_FADE({'L' if is_long else 'S'},rng={body_vs_atr[index]:.1f}xATR,rv={rel_vol[index]:.1f}x,k={k15[index]:.0f},+{bonus_raw})_bcT55b")
    return points, reasons, {"k1": k1, "d1": d1, "k5": k5, "d5": d5, "fresh": fresh}


def bar_pattern_score_vec(npz, n, is_long):
    """Vectorize native multi-timeframe bar-pattern and streak score layer."""
    points = np.zeros(n, dtype=float)
    reasons = [[] for _ in range(n)]
    for tf, weight in (("1h", 1.0), ("4h", 2.0), ("D", 3.0)):
        direction = _column(npz, f"bar_direction_{tf}", n, 0.0)
        strength = _column(npz, f"bar_strength_{tf}", n, 0.0)
        vol_confirm = np.fromiter((bool(_value_at(npz, f"bar_vol_confirm_{tf}", i, n)) for i in range(n)), dtype=bool, count=n)
        pattern = [_value_at(npz, f"bar_pattern_{tf}", i, n) for i in range(n)]
        bull = np.fromiter((bool(_value_at(npz, f"bar_swing_bull_{tf}", i, n)) for i in range(n)), dtype=bool, count=n)
        bear = np.fromiter((bool(_value_at(npz, f"bar_swing_bear_{tf}", i, n)) for i in range(n)), dtype=bool, count=n)
        streak = np.asarray([int(_value_at(npz, f"bar_streak_{tf}", i, n) or 0) for i in range(n)], dtype=int)
        compression = np.fromiter((bool(_value_at(npz, f"bar_compression_{tf}", i, n)) for i in range(n)), dtype=bool, count=n)
        vol_spike = np.fromiter((bool(_value_at(npz, f"bar_vol_spike_{tf}", i, n)) for i in range(n)), dtype=bool, count=n)
        scored_bar = (direction != 0.0) & (strength >= 0.3)
        bar_value = weight * (1.0 + vol_confirm.astype(float)) * np.where(strength >= 0.6, 1.0, 0.5)
        favorable = (direction > 0.0) if is_long else (direction < 0.0)
        points += np.where(scored_bar, np.where(favorable, bar_value, -bar_value), 0.0)
        if is_long:
            swing_value = np.where(bull, weight, np.where(bear, -weight, 0.0))
            streak_value = np.where(streak >= 3, weight, np.where(streak <= -3, -weight, 0.0))
        else:
            swing_value = np.where(bear, weight, np.where(bull, -weight, 0.0))
            streak_value = np.where(streak <= -3, weight, np.where(streak >= 3, -weight, 0.0))
        points += swing_value + streak_value + compression.astype(float)
        points += np.where(vol_spike & (direction != 0.0) & favorable, weight, 0.0)
        for index in np.flatnonzero(scored_bar | bull | bear | compression):
            if scored_bar[index] and favorable[index]:
                reasons[index].append(f"{str(pattern[index] or 'none')}_{tf}")
            if bull[index] and is_long:
                reasons[index].append(f"Swing_Bull_{tf}")
            elif bear[index] and not is_long:
                reasons[index].append(f"Swing_Bear_{tf}")
            elif bull[index] and not is_long:
                pass
            elif bear[index] and is_long:
                pass
            if compression[index]:
                reasons[index].append(f"Squeeze_{tf}")
    return points, reasons


def entry_bonus_layers_vec(npz, n, is_long, cfg):
    """Vectorize DC-position and K-zone additive entry score factors."""
    dc_pos_1h = _column(npz, "dc_position_1h", n, 0.5)
    dc_pos_4h = _column(npz, "dc_position_4h", n, 0.5)
    dc_threshold = float(getattr(cfg, "DC_POSITION_ENTRY_THRESHOLD", 0.25))
    dc_long = (dc_pos_1h < dc_threshold) | (dc_pos_4h < dc_threshold)
    dc_short = (dc_pos_1h > (1.0 - dc_threshold)) | (dc_pos_4h > (1.0 - dc_threshold))
    if bool(getattr(cfg, "TRADIER_DC_DAYTRADE_REQUIRE_1H_EXPANSION", True)) and "dc_width_1h" in npz and "dc_width_1h_prev" in npz:
        width_raw = np.asarray(npz["dc_width_1h"])
        width_prev_raw = np.asarray(npz["dc_width_1h_prev"])
        if width_raw.ndim == 1 and width_raw.size == n and width_prev_raw.ndim == 1 and width_prev_raw.size == n:
            width = _column(npz, "dc_width_1h", n, 0.0)
            width_prev = _column(npz, "dc_width_1h_prev", n, 0.0)
            expansion_ok = np.where(np.isfinite(width), width, 0.0) > np.where(np.isfinite(width_prev), width_prev, 0.0)
            dc_long &= expansion_ok
            dc_short &= expansion_ok
    dc_points = np.where(dc_long | dc_short, 5.0, 0.0)
    dc_reasons = [f"DC_POS_BONUS({'L' if is_long else 'S'}_1h={dc_pos_1h[i]:.2f}_4h={dc_pos_4h[i]:.2f})" if dc_points[i] else "" for i in range(n)]
    kzone_enabled = bool(getattr(cfg, "K_ZONE_ENTRY_ENABLED_TRADIER", True))
    k4, d4 = _column(npz, "k_4h", n, 50.0), _column(npz, "d_4h", n, 50.0)
    kzone_long = kzone_enabled & (k4 < float(getattr(cfg, "K_ZONE_LONG_THRESHOLD_TRADIER", 35.0))) & (k4 > d4)
    kzone_short = kzone_enabled & (k4 > float(getattr(cfg, "K_ZONE_SHORT_THRESHOLD_TRADIER", 65.0))) & (k4 < d4)
    bonus_raw = getattr(cfg, "K_ZONE_ENTRY_BONUS_TRADIER", getattr(cfg, "TRADIER_K_ZONE_ENTRY_BONUS_TRADIER", 25))
    kzone_points = np.where(kzone_long | kzone_short, float(bonus_raw), 0.0)
    kzone_reasons = [f"KZone_4h(+{bonus_raw})" if kzone_points[i] else "" for i in range(n)]
    return dc_points, dc_reasons, kzone_points, kzone_reasons


def dc_retest_score_vec(npz, n, is_long, ltf_enabled):
    """Vectorize native multi-timeframe DC expansion/retest score and detail text."""
    price = _column(npz, "close", n, 0.0)
    long_score = np.zeros(n, dtype=int)
    short_score = np.zeros(n, dtype=int)
    details = [[] for _ in range(n)]
    for tf in ("5m", "15m", "1h", "4h", "D"):
        source_tf = "15m" if tf == "5m" and not ltf_enabled else tf
        high = _column(npz, f"dc_high_{source_tf}", n, 0.0)
        high_prev = _column(npz, f"dc_high_{source_tf}_ant", n, 0.0)
        low = _column(npz, f"dc_low_{source_tf}", n, 0.0)
        low_prev = _column(npz, f"dc_low_{source_tf}_ant", n, 0.0)
        usable = (high > 0.0) & (high_prev > 0.0) & (low > 0.0) & (low_prev > 0.0) & ((high - low) > 0.0)
        high_expansion = usable & (high > high_prev) & (((high - high_prev) / np.where(high_prev > 0.0, high_prev, 1.0) * 100.0) > 0.1)
        high_dist = (price - high_prev) / np.where(high_prev > 0.0, high_prev, 1.0) * 100.0
        long_retest = high_expansion & (high_dist > 0.0) & (high_dist < 1.5)
        short_failed = high_expansion & (high_dist <= 0.0)
        long_score += long_retest.astype(int)
        short_score += short_failed.astype(int)
        low_expansion = usable & (low < low_prev) & (((low_prev - low) / np.where(low_prev > 0.0, low_prev, 1.0) * 100.0) > 0.1)
        low_dist = (low_prev - price) / np.where(low_prev > 0.0, low_prev, 1.0) * 100.0
        short_retest = low_expansion & (low_dist > 0.0) & (low_dist < 1.5)
        long_failed = low_expansion & (low_dist <= 0.0)
        short_score += short_retest.astype(int)
        long_score += long_failed.astype(int)
        for index in np.flatnonzero(long_retest | short_failed | short_retest | long_failed):
            if long_retest[index]: details[index].append(f"{tf}:retest_hi({high_dist[index]:.1f}%)")
            elif short_failed[index]: details[index].append(f"{tf}:failed_hi")
            if short_retest[index]: details[index].append(f"{tf}:retest_lo({low_dist[index]:.1f}%)")
            elif long_failed[index]: details[index].append(f"{tf}:failed_lo")
    detail_text = ["|".join(row) if row else "no_retest" for row in details]
    bonus = np.where((long_score if is_long else short_score) >= 2, 6.0, 0.0)
    reasons = [f"DC_RETEST_{'L' if is_long else 'S'}({int(long_score[i] if is_long else short_score[i])}TF:{detail_text[i]})" if bonus[i] else "" for i in range(n)]
    return bonus, reasons


def wt_composite_score_vec(npz, n, is_long, cfg):
    """Vectorize the native Tradier WT-composite entry scoring section."""
    enabled = bool(getattr(cfg, "WT_COMPOSITE_SCORING_ENABLED_TRADIER", False) or getattr(cfg, "WT_COMPOSITE_SCORING_ENABLED", False))
    if not enabled:
        return np.zeros(n, dtype=float), [ [] for _ in range(n)], [None] * n
    comp = _column(npz, "wt_composite_long" if is_long else "wt_composite_short", n, 0.0)
    align = _column(npz, "wt_bull_alignment" if is_long else "wt_bear_alignment", n, 0.0)
    blocked = [None] * n
    for index in range(n):
        if align[index] < 3.0:
            blocked[index] = f"WT_HTF_ALIGN_T({align[index]:.0f}/5,comp={'L' if is_long else 'S'}={comp[index]:.0f})"
        elif comp[index] < -20.0:
            blocked[index] = f"WT_COMP_BLOCK_T({'L' if is_long else 'S'}={comp[index]:.0f}<-20)"
    points = np.zeros(n, dtype=float)
    points += np.where(comp >= 50.0, 8.0, np.where(comp >= 30.0, 5.0, np.where(comp >= 10.0, 2.0, 0.0)))
    oversold = _column(npz, "wt_oversold_tf_count", n, 0.0)
    overbought = _column(npz, "wt_overbought_tf_count", n, 0.0)
    points += np.where((oversold >= 2.0) if is_long else (overbought >= 2.0), 5.0, 0.0)
    divergence_tfs = ("5m", "15m", "1h", "4h")
    bull_counts = np.zeros(n, dtype=int)
    bear_counts = np.zeros(n, dtype=int)
    for tf in divergence_tfs:
        bull_values = [_value_at(npz, f"wt_divergence_{tf}", i, n) for i in range(n)]
        bear_values = bull_values
        bull_counts += np.fromiter((value in ("BULL", "HIDDEN_BULL") for value in bull_values), dtype=int, count=n)
        bear_values = [_value_at(npz, f"wt_divergence_{tf}", i, n) for i in range(n)]
        bear_counts += np.fromiter((value in ("BEAR", "HIDDEN_BEAR") for value in bear_values), dtype=int, count=n)
    favorable = bull_counts if is_long else bear_counts
    against = bear_counts if is_long else bull_counts
    points += np.where(favorable >= 3, 25.0, np.where(favorable >= 2, 15.0, np.where(favorable == 1, 8.0, 0.0)))
    points -= np.where(against >= 2, 15.0, 0.0)
    reasons = [[] for _ in range(n)]
    strongest_key = "wt_strongest_bull_div_tf" if is_long else "wt_strongest_bear_div_tf"
    for index in range(n):
        if comp[index] >= 50.0: reasons[index].append(f"WT_STRONG({comp[index]:.0f},+8)")
        elif comp[index] >= 30.0: reasons[index].append(f"WT_GOOD({comp[index]:.0f},+5)")
        elif comp[index] >= 10.0: reasons[index].append(f"WT_OK({comp[index]:.0f},+2)")
        if is_long and oversold[index] >= 2.0:
            raw_count = _value_at(npz, "wt_oversold_tf_count", index, n)
            reasons[index].append(f"WT_MULTI_OS({raw_count}TF,+5)")
        elif not is_long and overbought[index] >= 2.0:
            raw_count = _value_at(npz, "wt_overbought_tf_count", index, n)
            reasons[index].append(f"WT_MULTI_OB({raw_count}TF,+5)")
        if favorable[index] >= 3: reasons[index].append(f"DIV_{'BULL' if is_long else 'BEAR'}_3TF({favorable[index]},+25)_bc154div")
        elif favorable[index] >= 2: reasons[index].append(f"DIV_{'BULL' if is_long else 'BEAR'}_2TF({favorable[index]},+15)_bc154div")
        elif favorable[index] == 1:
            strongest = _value_at(npz, strongest_key, index, n)
            reasons[index].append(f"WT_{'BULL' if is_long else 'BEAR'}_DIV({strongest},+8)")
        if against[index] >= 2: reasons[index].append(f"DIV_AGAINST_{'LONG' if is_long else 'SHORT'}({against[index]}TF,-15)")
    return points, reasons, blocked



def mts_score_vec(npz, n, is_long, cfg):
    """Vectorize the MTS outputs consumed by StockStrategy's entry scorer."""
    if not bool(getattr(cfg, "LTF_SUB15M_TRADING_ENABLED", False)):
        npz = paired_lower_tf_indicators(npz, False)
    def col(key, default=0.0): return _column(npz, key, n, default)
    def obj(key, default=None):
        values = [_value_at(npz, key, index, n) for index in range(n)]
        return np.asarray([default if value is None else value for value in values], dtype=object)
    total_bottom = np.zeros(n, dtype=float)
    total_entry = np.zeros(n, dtype=float)
    total_weight = 0.0
    extreme_count = np.zeros(n, dtype=int)
    htf_bull_count = np.zeros(n, dtype=int)
    convergence_data = []
    for tf, default_weight in (("5m", 2.0), ("15m", 4.0), ("1h", 3.0), ("4h", 5.0), ("D", 12.0)):
        weight = float(getattr(cfg, f"MTS_WEIGHT_{tf}", default_weight))
        k = col(f"k_{tf}", 50.0)
        d = col(f"d_{tf}", 50.0)
        k_prev = col(f"k_{tf}_prev", 50.0) if f"k_{tf}_prev" in npz else k
        wt1, wt2 = col(f"wt1_{tf}", 0.0), col(f"wt2_{tf}", 0.0)
        wt_score = wt1 - wt2
        cross = obj(f"wt_cross_{tf}", "")
        cross_value = col(f"wt_cross_value_{tf}", np.nan)
        cross_prev = col(f"wt_cross_prev_value_{tf}", np.nan)
        cross_rising = np.asarray([bool(value) for value in obj(f"wt_cross_rising_{tf}", False)], dtype=bool)
        bars_ago = col(f"wt_cross_bars_ago_{tf}", 999.0)
        divergence = obj(f"wt_divergence_{tf}", "")
        momentum = obj(f"wt_momentum_state_{tf}", "")
        dc_pos = col(f"dc_position_{tf}", 0.5)
        dc_pos = np.where(dc_pos > 1.0, dc_pos / 100.0, dc_pos)
        dc_pos = np.clip(dc_pos, 0.0, 1.0)
        dc_width = col(f"dc_width_{tf}", 0.0)
        wt_bull = wt1 > wt2
        k_extreme = (k < 10.0) if is_long else (k > 90.0)
        k_zone = (k < 30.0) if is_long else (k > 70.0)
        extreme_count += k_extreme.astype(int)
        bottom = np.zeros(n, dtype=float)
        bottom += np.where(k_extreme, 20.0, 0.0) + np.where(k_zone, 10.0, 0.0)
        bottom += np.where((cross == ("BULL" if is_long else "BEAR")) & (bars_ago < 5.0), 15.0, 0.0)
        cross_valid = ~np.isnan(cross_value)
        if is_long:
            bottom += np.where(cross_valid & (cross_value < -40.0), 15.0, np.where(cross_valid & (cross_value < -20.0), 8.0, 0.0))
            bottom += np.where(divergence == "BULL", 20.0, np.where(divergence == "HIDDEN_BULL", 20.0, 0.0))
            bottom += np.where(momentum == "EXHAUST_DOWN", 10.0, 0.0)
            bottom += np.where((col(f"wt_velocity_{tf}", 0.0) > 0.0) & (k > k_prev), 5.0, 0.0)
            bottom += np.where(dc_pos < 0.10, 25.0, np.where(dc_pos < 0.20, 18.0, np.where(dc_pos < 0.30, 12.0, np.where(dc_pos < 0.40, 5.0, np.where(dc_pos > 0.80, -10.0, 0.0)))))
            entry = np.where(cross_valid & ~np.isnan(cross_prev) & (cross_value > cross_prev), 15.0, 0.0)
            entry += np.where((dc_pos < 0.25) & wt_bull, 15.0, 0.0)
        else:
            bottom += np.where(cross_valid & (cross_value > 40.0), 15.0, np.where(cross_valid & (cross_value > 20.0), 8.0, 0.0))
            bottom += np.where(divergence == "BEAR", 20.0, np.where(divergence == "HIDDEN_BEAR", 20.0, 0.0))
            bottom += np.where(momentum == "EXHAUST_UP", 10.0, 0.0)
            bottom += np.where((col(f"wt_velocity_{tf}", 0.0) < 0.0) & ~(k > k_prev), 5.0, 0.0)
            bottom += np.where(dc_pos > 0.90, 25.0, np.where(dc_pos > 0.80, 18.0, np.where(dc_pos > 0.70, 12.0, np.where(dc_pos > 0.60, 5.0, np.where(dc_pos < 0.20, -10.0, 0.0)))))
            entry = np.where(cross_valid & ~np.isnan(cross_prev) & (cross_value < cross_prev), 15.0, 0.0)
            entry += np.where((dc_pos > 0.75) & ~wt_bull, 15.0, 0.0)
        bottom += np.where(cross_rising, 10.0, 0.0)
        entry += np.where(bars_ago < 3.0, 15.0, np.where(bars_ago < 10.0, 5.0, 0.0))
        entry += np.where(cross_rising, 10.0, 0.0)
        total_bottom += bottom * weight
        total_entry += entry * weight
        total_weight += weight
        htf_bull_count += wt_bull.astype(int)
        convergence_data.append((dc_pos, wt_score, k, bars_ago, wt_bull))
    if total_weight > 0.0:
        bottom_score = total_bottom / total_weight
        entry_quality = total_entry / total_weight
    else:
        bottom_score = np.zeros(n, dtype=float)
        entry_quality = np.zeros(n, dtype=float)
    bottom_score *= np.where(extreme_count >= 4, 2.0, np.where(extreme_count >= 3, 1.5, 1.0))
    entry_quality += htf_bull_count * 5.0
    bottom_score = np.clip(bottom_score, -100.0, 100.0)
    entry_quality = np.clip(entry_quality, -100.0, 100.0)
    bottom_conv_count = np.zeros(n, dtype=int)
    top_conv_count = np.zeros(n, dtype=int)
    for dc_pos, wt_score, k, bars_ago, wt_bull in convergence_data:
        bot_sigs = (dc_pos < 0.15).astype(int) + (wt_score < -15.0).astype(int) + (k < 25.0).astype(int) + ((bars_ago < 12.0) & wt_bull).astype(int)
        top_sigs = (dc_pos > 0.85).astype(int) + (wt_score > 15.0).astype(int) + (k > 75.0).astype(int) + ((bars_ago < 12.0) & ~wt_bull).astype(int)
        bottom_conv_count += (bot_sigs >= 2).astype(int)
        top_conv_count += (top_sigs >= 2).astype(int)
    focus_dc = np.full(n, 0.5, dtype=float)
    focus_set = np.zeros(n, dtype=bool)
    for tf in ("5m", "15m", "1h"):
        raw = col(f"dc_position_{tf}", 0.5)
        eligible = (raw != 0.5) & (raw != 0.0) & ~focus_set
        focus_dc = np.where(eligible, raw, focus_dc)
        focus_set |= eligible
    focus_dc = np.where(focus_dc > 1.0, focus_dc / 100.0, focus_dc)
    price_below, price_above = focus_dc < 0.05, focus_dc > 0.95
    price_near_low, price_near_high = focus_dc < 0.15, focus_dc > 0.85
    if is_long:
        bottom_conv = (bottom_conv_count >= 2) & (price_near_low | price_below)
        top_conv = (top_conv_count >= 2) & price_above
        conv_count = np.where(bottom_conv, bottom_conv_count, np.where(top_conv, top_conv_count, 0))
        bottom_score += np.where(bottom_conv, 10.0 * bottom_conv_count, np.where(top_conv, 15.0 * top_conv_count, 0.0))
        entry_quality += np.where(bottom_conv, 8.0 * bottom_conv_count, np.where(top_conv, 12.0 * top_conv_count, 0.0))
    else:
        top_conv = (top_conv_count >= 2) & (price_near_high | price_above)
        bottom_conv = (bottom_conv_count >= 2) & price_below
        conv_count = np.where(top_conv, top_conv_count, np.where(bottom_conv, bottom_conv_count, 0))
        bottom_score += np.where(top_conv, 10.0 * top_conv_count, np.where(bottom_conv, 15.0 * bottom_conv_count, 0.0))
        entry_quality += np.where(top_conv, 8.0 * top_conv_count, np.where(bottom_conv, 12.0 * bottom_conv_count, 0.0))
    bottom_score = np.clip(bottom_score, -100.0, 100.0)
    entry_quality = np.clip(entry_quality, -100.0, 100.0)
    adjustment = np.where(bottom_score > 40.0, 8.0, np.where(bottom_score > 25.0, 4.0, 0.0))
    adjustment += np.where(entry_quality > 40.0, 5.0, np.where(entry_quality > 25.0, 2.0, 0.0))
    reason_rows = [[] for _ in range(n)]
    for index in np.flatnonzero(adjustment):
        if bottom_score[index] > 40.0: reason_rows[index].append(f"MTS_STRONG({bottom_score[index]:.0f},+8)")
        elif bottom_score[index] > 25.0: reason_rows[index].append(f"MTS_BOTTOM({bottom_score[index]:.0f},+4)")
        if entry_quality[index] > 40.0: reason_rows[index].append(f"MTS_EQ({entry_quality[index]:.0f},+5)")
        elif entry_quality[index] > 25.0: reason_rows[index].append(f"MTS_EQ_OK({entry_quality[index]:.0f},+2)")
    gate_enabled = bool(getattr(cfg, "MTS_GATE_ENABLED_TRADIER", False))
    bottom_min = float(getattr(cfg, "MTS_BOTTOM_MIN_TRADIER", 5.0))
    entry_min = float(getattr(cfg, "MTS_ENTRY_QUALITY_MIN_TRADIER", 0.0))
    blocked = [None] * n
    if gate_enabled:
        for index in np.flatnonzero((bottom_score < bottom_min) | (entry_quality < entry_min)):
            blocked[index] = f"MTS_GATE_T(b={bottom_score[index]:.0f}<{bottom_min},eq={entry_quality[index]:.0f}<{entry_min})"
    return adjustment, reason_rows, blocked, bottom_score, entry_quality




def native_entry_setup_masks(npz, n, is_long, cfg):
    """Vectorize native stoch, K-zone, MFI and BB setup predicates before score assembly."""
    if not bool(getattr(cfg, "LTF_SUB15M_TRADING_ENABLED", False)):
        npz = paired_lower_tf_indicators(npz, False)
    def col(key, default=50.0): return _column(npz, key, n, default)
    k5, d5, k5p = col("k_5m"), col("d_5m"), col("k_5m_prev")
    k15, d15 = col("k_15m"), col("d_15m")
    timestamps = col("timestamps", 0.0)
    try:
        stamp_1m = np.asarray(npz["timestamp_1m"], dtype=float).reshape(-1)
        if stamp_1m.size != n:
            raise ValueError("unaligned timestamp_1m")
        fresh = (timestamps - stamp_1m) < 60.0
    except (KeyError, TypeError, ValueError):
        fresh = np.ones(n, dtype=bool)
    if is_long:
        stoch = ((k5 < float(getattr(cfg, "TRADIER_STOCH_ENTRY_LONG_TRADIER", 30.0))) & (k5 > d5)) | (k5 < float(getattr(cfg, "TRADIER_STOCH_EXTREME_LONG_TRADIER", 15.0)))
        stoch |= (k5 > d5) & (k5p <= d5) & (k5 < 35.0) & fresh
    else:
        stoch = ((k5 > float(getattr(cfg, "TRADIER_STOCH_ENTRY_SHORT_TRADIER", 70.0))) & (k5 < d5)) | (k5 > float(getattr(cfg, "TRADIER_STOCH_EXTREME_SHORT_TRADIER", 85.0)))
        stoch |= (k5 < d5) & (k5p >= d5) & (k5 > 65.0) & fresh
    from vec_decisions.twin_p0_stocks import dd_bounce_fire_vec, fg_fear_fire_vec, fg_greed_fire_vec
    stoch &= dd_bounce_fire_vec(k5, is_long, getattr(cfg, "DD_BOUNCE_ENABLED", False))
    if bool(getattr(cfg, "STOCH_CROSS_ENTRY_TRADIER", False)):
        stoch = ((k5p <= d5) & (k5 > d5)) if is_long else ((k5p >= d5) & (k5 < d5))
    k4, d4 = col("k_4h"), col("d_4h")
    if is_long:
        kzone = bool(getattr(cfg, "K_ZONE_ENTRY_ENABLED_TRADIER", True)) & (k4 < float(getattr(cfg, "K_ZONE_LONG_THRESHOLD_TRADIER", 35.0))) & (k4 > d4)
    else:
        kzone = bool(getattr(cfg, "K_ZONE_ENTRY_ENABLED_TRADIER", True)) & (k4 > float(getattr(cfg, "K_ZONE_SHORT_THRESHOLD_TRADIER", 65.0))) & (k4 < d4)
    entry_tf = str(getattr(cfg, "ENTRY_PRIMARY_TF", "4h") or "4h")
    mfi = col(f"mfi_{entry_tf}")
    flow = (mfi < float(getattr(cfg, "RSI_ENTRY_LONG_TRADIER", 42.0))) if is_long else (mfi > float(getattr(cfg, "RSI_ENTRY_SHORT_TRADIER", 58.0)))
    rsi1h = col("rsi_1h", 50.0)
    flow &= fg_fear_fire_vec(rsi1h, getattr(cfg, "FG_FEAR_THRESHOLD", 25), 25, is_long)
    flow &= fg_greed_fire_vec(rsi1h, getattr(cfg, "FG_GREED_THRESHOLD", 75), 75, is_long)
    bb_tf = str(getattr(cfg, "BB_BREAKOUT_ENTRY_TF", "OFF") or "OFF").strip()
    bb = np.zeros(n, dtype=bool)
    if bb_tf in ("15m", "1h", "4h", "D"):
        pct = col(f"bb_pct_b_{bb_tf}", 0.5)
        band = col(f"bb_upper_{bb_tf}" if is_long else f"bb_lower_{bb_tf}", 0.0)
        bb = ((pct > 0.95) if is_long else (pct < 0.05)) & (band > 0.0)
    return (stoch | kzone | flow | bb), stoch, kzone, flow, bb


def entry_tail_score_vec(npz, n, is_long, cfg):
    """Vectorize entry-only bear bias and time-to-close score adjustments."""
    adjustment = np.zeros(n, dtype=float)
    reasons = [[] for _ in range(n)]
    blocked = [None] * n
    if bool(getattr(cfg, "BEAR_MARKET_MODE_TRADIER", False)):
        points = -20.0 if is_long else 15.0
        adjustment += points
        label = "BEAR_PENALTY" if is_long else "BEAR_BONUS"
        for row in reasons:
            row.append(label)
    if bool(getattr(cfg, "EOD_RATIO_ENFORCE_TRADIER", False)):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        timestamps = _column(npz, "timestamps", n, np.nan)
        et = ZoneInfo("America/New_York")
        for index in range(n):
            if not np.isfinite(timestamps[index]):
                continue
            now_et = datetime.fromtimestamp(float(timestamps[index]), et)
            mins_to_close = (16 * 60) - (now_et.hour * 60 + now_et.minute)
            if 0 < mins_to_close <= 30:
                adjustment[index] -= int(10 * (1 - mins_to_close / 30))
                reasons[index].append(f"EOD_FADE({mins_to_close}min)")
            if mins_to_close <= 5:
                blocked[index] = f"EOD_BLOCK({mins_to_close}min_to_close)"
    return adjustment, reasons, blocked


def entry_pre_gate_score_vec(npz, n, is_long, cfg, ltf_enabled):
    """Vectorize native entry pre-gates, ordered returns and additive penalties."""
    def col(key, default=0.0):
        return _column(npz, key, n, default)
    def cfgv(key, default):
        return getattr(cfg, key, default)
    price = col("close", 0.0)
    blocked = [None] * n
    adjustment = np.zeros(n, dtype=float)
    reasons = [[] for _ in range(n)]
    edc_raw = str(cfgv("ENTRY_DC_TF", "OFF") or "OFF").strip()
    if edc_raw.upper() not in ("OFF", ""):
        edc_tfs = [part.strip() for part in edc_raw.replace("+", ",").replace("|", ",").replace(" ", ",").split(",") if part.strip() and part.strip().upper() != "OFF"]
        buffer = float(cfgv("ENTRY_DC_BUFFER_PCT", 0.10) or 0.10) / 100.0
        active = np.zeros(n, dtype=bool)
        allowed = np.zeros(n, dtype=bool)
        for tf in edc_tfs:
            source_tf = "15m" if not ltf_enabled and tf in ("3m", "5m") else tf
            low, high = col(f"dc_low_{source_tf}", 0.0), col(f"dc_high_{source_tf}", 0.0)
            active |= (low > 0.0) | (high > 0.0)
            if is_long:
                allowed |= ((low > 0.0) & (price >= low * (1.0 + buffer))) | ((high > 0.0) & (price >= high * (1.0 + buffer)))
            else:
                allowed |= ((high > 0.0) & (price <= high * (1.0 - buffer))) | ((low > 0.0) & (price <= low * (1.0 - buffer)))
        for index in np.flatnonzero(active & ~allowed):
            blocked[index] = f"ENTRY_DC_TF({edc_raw})"
    if not bool(cfgv("BACKTEST_VALIDATED_GATES_TRADIER", True)):
        return adjustment, reasons, blocked
    atr1, atr4 = col("atr_1h", 0.0), col("atr_4h", 0.0)
    mfi4, wt1h, wt4 = col("mfi_4h", 50.0), col("wt1_1h", 0.0), col("wt1_4h", 0.0)
    k4, dcw1, dcw4 = col("k_4h", 50.0), col("dc_width_1h", 10.0), col("dc_width_4h", 10.0)
    dcpos4, wtvel = col("dc_position_4h", 0.5), col("wt_velocity_1h", 0.0)
    atr4_pct = np.divide(atr4, price, out=np.zeros(n), where=price > 0.0) * 100.0
    atr1_pct = np.divide(atr1, price, out=np.zeros(n), where=price > 0.0) * 100.0
    masks = (
        ((atr4 > 0.0) & (price > 0.0) & (atr4_pct > 3.0), lambda i: f"BV_ATR_HIGH_4h({atr4_pct[i]:.2f}%)"),
        ((atr1 > 0.0) & (price > 0.0) & (atr1_pct > 2.5), lambda i: f"BV_ATR_HIGH_1h({atr1_pct[i]:.2f}%)"),
        ((mfi4 > 85.0) if is_long else (mfi4 < 15.0), lambda i: f"BV_MFI_{'OB' if is_long else 'OS'}({mfi4[i]:.0f})"),
        (((wt1h > 60.0) | (wt4 > 60.0)) if is_long else ((wt1h < -60.0) | (wt4 < -60.0)), lambda i: f"BV_WT_{'OB' if is_long else 'OS'}(1h={wt1h[i]:.0f},4h={wt4[i]:.0f})"),
        ((k4 > 90.0) if is_long else (k4 < 10.0), lambda i: f"BV_K4H_{'OB' if is_long else 'OS'}({k4[i]:.0f})"),
        ((dcw4 < 1.5) | (dcw1 < 1.0), lambda i: f"BV_DC_SQUEEZE(1h={dcw1[i]:.1f},4h={dcw4[i]:.1f})"),
        ((dcpos4 > 0.9) if is_long else (dcpos4 < 0.1), lambda i: f"BV_DC_{'TOP' if is_long else 'BOT'}({dcpos4[i]:.2f})"),
        ((wtvel < -5.0) if is_long else (wtvel > 5.0), lambda i: f"BV_WT_VEL_{'DN' if is_long else 'UP'}({wtvel[i]:.1f})"),
    )
    for mask, make_reason in masks:
        for index in np.flatnonzero(mask & np.asarray([item is None for item in blocked])):
            blocked[index] = make_reason(index)
    if bool(cfgv("CT_WT_VELOCITY_GATE_ENABLED", False)):
        velocity_min = float(cfgv("CT_WT_VELOCITY_1H_MIN", 0.0))
        velocity_block = ((wtvel < velocity_min) if is_long else (wtvel > -velocity_min)) & np.asarray([item is None for item in blocked])
        for index in np.flatnonzero(velocity_block):
            blocked[index] = f"CT_VEL_LONG({wtvel[index]:.1f}<{velocity_min})" if is_long else f"CT_VEL_SHORT({wtvel[index]:.1f}>-{velocity_min})"
    entry_atr_min = float(cfgv("ENTRY_ATR_PCT_MIN", 0.0))
    entry_atr_block = (entry_atr_min > 0.0) & (atr1 > 0.0) & (price > 0.0) & (atr1_pct < entry_atr_min) & np.asarray([item is None for item in blocked])
    for index in np.flatnonzero(entry_atr_block):
        blocked[index] = f"ATR_TOO_LOW({atr1_pct[index]:.2f}%<{entry_atr_min:.2f})"
    rvol5_key = "relative_volume_5m" if "relative_volume_5m" in npz else "rel_vol_5m"
    rvol15_key = "relative_volume_15m" if "relative_volume_15m" in npz else "rel_vol_15m"
    rvol5 = col(rvol5_key, 0.0) if ltf_enabled else col(rvol15_key, 0.0)
    rvol15 = col(rvol15_key, 0.0)
    entry_vol_min = float(cfgv("ENTRY_VOL_MIN_RATIO", 0.0))
    vol_max = np.maximum(rvol5, rvol15)
    vol_block = (entry_vol_min > 0.0) & (vol_max < entry_vol_min) & np.asarray([item is None for item in blocked])
    for index in np.flatnonzero(vol_block):
        blocked[index] = f"VOL_TOO_LOW(max={vol_max[index]:.2f}<{entry_vol_min:.2f})"
    def enabled(key, default=True): return bool(cfgv(key, default))
    adx4 = col("adx_4h", 25.0)
    adx_max, adx_penalty = float(cfgv("TR_ADX4H_MAX", 20.0)), float(cfgv("TR_ADX4H_BOYCOTT_SCORE", -40.0))
    adx_hit = enabled("TR_ADX4H_GATE_ENABLED") & (adx4 > adx_max)
    adjustment += np.where(adx_hit, adx_penalty, 0.0)
    bb_up, bb_lo = col("bb_upper_4h", 0.0), col("bb_lower_4h", 0.0)
    bb_width = np.divide(bb_up - bb_lo, price, out=np.zeros(n), where=(price > 0.0) & (bb_up > 0.0) & (bb_lo > 0.0)) * 100.0
    bb_max, bb_penalty = float(cfgv("TR_BBWIDTH4H_MAX", 10.0)), float(cfgv("TR_BBWIDTH4H_BOYCOTT_SCORE", -35.0))
    bb_hit = enabled("TR_BBWIDTH4H_GATE_ENABLED") & (bb_width > bb_max)
    adjustment += np.where(bb_hit, bb_penalty, 0.0)
    chop = col("choppiness_4h", 50.0)
    chop_hit = enabled("TR_CHOP4H_GATE_ENABLED") & (chop >= float(cfgv("TR_CHOP4H_MIN", 50.0)))
    chop_trend = enabled("TR_CHOP4H_GATE_ENABLED") & ~chop_hit & (chop < float(cfgv("TR_CHOP4H_TREND_MAX", 38.0)))
    chop_bonus, chop_penalty = float(cfgv("TR_CHOP4H_BONUS", 15.0)), float(cfgv("TR_CHOP4H_PENALTY", -20.0))
    adjustment += np.where(chop_hit, chop_bonus, np.where(chop_trend, chop_penalty, 0.0))
    mfi_penalty = float(cfgv("TR_MFI4H_LONG_BOYCOTT_SCORE", -25.0))
    mfi_hit = enabled("TR_MFI4H_LONG_ENABLED") & is_long & (mfi4 < float(cfgv("TR_MFI4H_LONG_MIN", 40.0)))
    adjustment += np.where(mfi_hit, mfi_penalty, 0.0)
    dc_hi4, dc_lo4 = col("dc_high_4h", 0.0), col("dc_low_4h", 0.0)
    dc_width_pct = np.divide(dc_hi4 - dc_lo4, price, out=np.zeros(n), where=(price > 0.0) & (dc_hi4 > 0.0) & (dc_lo4 > 0.0)) * 100.0
    dc_max, dc_penalty = float(cfgv("TR_DCWIDTH4H_SHORT_MAX", 15.0)), float(cfgv("TR_DCWIDTH4H_SHORT_BOYCOTT_SCORE", -25.0))
    dc_hit = enabled("TR_DCWIDTH4H_SHORT_ENABLED") & (not is_long) & (dc_width_pct > dc_max)
    adjustment += np.where(dc_hit, dc_penalty, 0.0)
    for index in np.flatnonzero(adx_hit): reasons[index].append(f"TR_ADX4H_TREND({adx4[index]:.0f}>{adx_max:.0f},{adx_penalty:.0f})")
    for index in np.flatnonzero(bb_hit): reasons[index].append(f"TR_BBW4H_WIDE({bb_width[index]:.1f}%>{bb_max:.1f},{bb_penalty:.0f})")
    for index in np.flatnonzero(chop_hit): reasons[index].append(f"TR_CHOP4H_OK({chop[index]:.0f},+{chop_bonus:.0f})")
    for index in np.flatnonzero(chop_trend): reasons[index].append(f"TR_CHOP4H_TREND({chop[index]:.0f},{chop_penalty:.0f})")
    for index in np.flatnonzero(mfi_hit): reasons[index].append(f"TR_MFI4H_LOW_LONG({mfi4[index]:.0f},{mfi_penalty:.0f})")
    for index in np.flatnonzero(dc_hit): reasons[index].append(f"TR_DCW4H_WIDE_SHORT({dc_width_pct[index]:.1f}%,{dc_penalty:.0f})")
    return adjustment, reasons, blocked


def _entry_score_arrays_scalar(npz, n, symbol, is_long, cfg, candidate_mask=None):
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
    ltf_enabled = bool(getattr(cfg, "LTF_SUB15M_TRADING_ENABLED", False))
    setup_bonus, setup_reasons = pullback_setup_score_vec(npz, n, is_long, ltf_enabled)
    trigger_bonus, trigger_reasons, trigger_inputs = trigger_score_vec(npz, n, is_long, cfg, ltf_enabled)
    bar_bonus, bar_reasons = bar_pattern_score_vec(npz, n, is_long)
    dc_bonus, dc_reasons, kzone_bonus, kzone_reasons = entry_bonus_layers_vec(npz, n, is_long, cfg)
    dc_retest_bonus, dc_retest_reasons = dc_retest_score_vec(npz, n, is_long, ltf_enabled)
    wt_comp_bonus, wt_comp_reasons, wt_comp_blocked = wt_composite_score_vec(npz, n, is_long, cfg)
    wt_comp_enabled = bool(getattr(cfg, "WT_COMPOSITE_SCORING_ENABLED_TRADIER", False) or getattr(cfg, "WT_COMPOSITE_SCORING_ENABLED", False))
    mts_adjustments, mts_reasons, mts_blocked, mts_bottom, mts_entry_quality = mts_score_vec(npz, n, is_long, cfg)
    tail_adjustments, tail_reasons, tail_blocked = entry_tail_score_vec(npz, n, is_long, cfg)
    pre_adjustments, pre_reasons, pre_blocked = entry_pre_gate_score_vec(npz, n, is_long, cfg, ltf_enabled)
    score_values = np.zeros(n, dtype=float)
    fires = np.zeros(n, dtype=bool)
    reasons = [""] * n
    candidates = possible_entry_setup_mask(npz, n, is_long, cfg) if candidate_mask is None else np.asarray(candidate_mask, dtype=bool)
    if candidates.shape != (n,):
        raise ValueError("candidate_mask must have shape (n,)")
    native_setup = native_entry_setup_masks(npz, n, is_long, cfg)[0]
    candidates &= native_setup
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
            setup_token = tm._TR_SCORE_SETUP_CTX.set((float(setup_bonus[index]), setup_reasons[index]))
            trigger_token = tm._TR_SCORE_TRIGGER_CTX.set((
                float(trigger_bonus[index]),
                trigger_reasons[index],
                {key: value[index].item() if hasattr(value[index], "item") else value[index] for key, value in trigger_inputs.items()},
            ))
            bar_token = tm._TR_SCORE_BAR_CTX.set((float(bar_bonus[index]), bar_reasons[index]))
            dc_bonus_token = tm._TR_SCORE_DC_BONUS_CTX.set((float(dc_bonus[index]), dc_reasons[index]))
            kzone_token = tm._TR_SCORE_KZONE_CTX.set((float(kzone_bonus[index]), kzone_reasons[index]))
            dcrt_token = tm._TR_SCORE_DCRT_CTX.set((float(dc_retest_bonus[index]), dc_retest_reasons[index]))
            wtc_token = tm._TR_SCORE_WTC_CTX.set((float(wt_comp_bonus[index]), wt_comp_reasons[index], wt_comp_blocked[index]) if wt_comp_enabled else None)
            pre_token = tm._TR_SCORE_PRE_CTX.set((float(pre_adjustments[index]), pre_reasons[index], pre_blocked[index]))
            mts_token = tm._TR_SCORE_MTS_CTX.set((float(mts_adjustments[index]), mts_reasons[index], mts_blocked[index], float(mts_bottom[index]), float(mts_entry_quality[index])))
            tail_token = tm._TR_SCORE_TAIL_CTX.set((float(tail_adjustments[index]), tail_reasons[index], tail_blocked[index]))
            try:
                score, recommendation, reason = strategy.calculate_signal_score(
                    "v12", symbol, is_long, float(indicators.get("close", 0.0) or 0.0), indicators, None, is_exit=False
                )
                threshold = float(tm._cfg("ENTRY_SCORE_THRESHOLD", 12.0, "v12", symbol, side) or 0.0)
            finally:
                tm._TR_SCORE_TAIL_CTX.reset(tail_token)
                tm._TR_SCORE_MTS_CTX.reset(mts_token)
                tm._TR_SCORE_PRE_CTX.reset(pre_token)
                tm._TR_SCORE_WTC_CTX.reset(wtc_token)
                tm._TR_SCORE_DCRT_CTX.reset(dcrt_token)
                tm._TR_SCORE_KZONE_CTX.reset(kzone_token)
                tm._TR_SCORE_DC_BONUS_CTX.reset(dc_bonus_token)
                tm._TR_SCORE_BAR_CTX.reset(bar_token)
                tm._TR_SCORE_TRIGGER_CTX.reset(trigger_token)
                tm._TR_SCORE_SETUP_CTX.reset(setup_token)
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


def entry_score_arrays_numpy(npz, n, symbol, is_long, cfg, candidate_mask=None):
    """Assemble the native Tradier entry score from ordered NumPy score layers."""
    if str(getattr(cfg, "MODE", "crypto")) != "tradier":
        return np.zeros(n, dtype=float), np.zeros(n, dtype=bool), [""] * n
    timestamps = np.asarray(npz.get("timestamps", []), dtype=float).reshape(-1)
    if timestamps.size != n:
        raise ValueError("Tradier signal score requires aligned NPZ timestamps")
    import tradier_manage as tm
    ltf = bool(getattr(cfg, "LTF_SUB15M_TRADING_ENABLED", False))
    native_setup, stoch, kzone_mask, flow, bb = native_entry_setup_masks(npz, n, is_long, cfg)
    candidates = possible_entry_setup_mask(npz, n, is_long, cfg) if candidate_mask is None else np.asarray(candidate_mask, dtype=bool)
    if candidates.shape != (n,):
        raise ValueError("candidate_mask must have shape (n,)")
    candidates &= native_setup
    pre_adjustment, pre_reasons, pre_blocked = entry_pre_gate_score_vec(npz, n, is_long, cfg, ltf)
    mts_adj, mts_reasons, mts_blocked, _, _ = mts_score_vec(npz, n, is_long, cfg)
    tail_adj, tail_reasons, tail_blocked = entry_tail_score_vec(npz, n, is_long, cfg)
    mq_enabled = bool(getattr(cfg, "MARKET_QUALITY_SCORE_ENABLED_TRADIER", False))
    from vec_decisions.tradier_market_quality import market_quality_score_vec_details
    mq_points, mq_reasons = market_quality_score_vec_details(npz, n, is_long, cfg, _column)
    ga_points, ga_reasons = golden_angles_score_vec(npz, n, is_long, _column)
    sentiment_points, trend_points, st_reasons = sentiment_trend_score_vec(npz, n, is_long, _column)
    setup_points, setup_reasons = pullback_setup_score_vec(npz, n, is_long, ltf)
    trigger_points, trigger_reasons, trigger_inputs = trigger_score_vec(npz, n, is_long, cfg, ltf)
    bar_points, bar_reasons = bar_pattern_score_vec(npz, n, is_long)
    dc_points, dc_reasons, kz_points, kz_reasons = entry_bonus_layers_vec(npz, n, is_long, cfg)
    dcrt_points, dcrt_reasons = dc_retest_score_vec(npz, n, is_long, ltf)
    wt_points, wt_reasons, wt_blocked = wt_composite_score_vec(npz, n, is_long, cfg)
    wt_enabled = bool(getattr(cfg, "WT_COMPOSITE_SCORING_ENABLED_TRADIER", False) or getattr(cfg, "WT_COMPOSITE_SCORING_ENABLED", False))
    if not wt_enabled:
        wt_points = np.zeros(n, dtype=float)
        wt_reasons = [[] for _ in range(n)]
        wt_blocked = [None] * n
    score = pre_adjustment + mts_adj + dc_points + dcrt_points + kz_points + wt_points + ga_points + sentiment_points + trend_points + setup_points + bar_points + tail_adj
    entry_tf = str(getattr(cfg, "ENTRY_PRIMARY_TF", "4h") or "4h")
    mfi_values = _column(npz, f"mfi_{entry_tf}", n, 50.0)
    if mq_enabled:
        score += mq_points
    before_trigger = score - bar_points - tail_adj - (mq_points if mq_enabled else 0.0)
    inputs = {key: np.asarray(value) for key, value in trigger_inputs.items()}
    knife = inputs["fresh"] & (inputs["k1"] < inputs["d1"] if is_long else inputs["k1"] > inputs["d1"]) & (inputs["k5"] < inputs["d5"] if is_long else inputs["k5"] > inputs["d5"]) & (before_trigger < 15.0)
    trigger_points = trigger_points - np.where(knife, 3.0, 0.0)
    score = score + trigger_points
    reasons = [[] for _ in range(n)]
    for index in range(n):
        if not candidates[index]:
            continue
        reasons[index].extend(pre_reasons[index])
        reasons[index].extend(mts_reasons[index])
        if dc_points[index] != 0.0: reasons[index].append(dc_reasons[index]) if isinstance(dc_reasons[index], str) else reasons[index].extend(dc_reasons[index])
        if dcrt_points[index] != 0.0: reasons[index].append(dcrt_reasons[index]) if isinstance(dcrt_reasons[index], str) else reasons[index].extend(dcrt_reasons[index])
        if kz_points[index] != 0.0: reasons[index].append(kz_reasons[index]) if isinstance(kz_reasons[index], str) else reasons[index].extend(kz_reasons[index])
        if flow[index]: reasons[index].append(f"MFI_{mfi_values[index]:.0f}")
        reasons[index].extend(wt_reasons[index])
        reasons[index].extend(ga_reasons[index])
        reasons[index].extend(st_reasons[index])
        reasons[index].extend(setup_reasons[index])
        reasons[index].extend(trigger_reasons[index])
        reasons[index].extend(bar_reasons[index])
        reasons[index].extend([item for item in tail_reasons[index] if not item.startswith("EOD_")])
        if mq_enabled and mq_reasons[index]: reasons[index].append(mq_reasons[index])
        reasons[index].extend([item for item in tail_reasons[index] if item.startswith("EOD_")])
    active = candidates & np.asarray([item is None for item in pre_blocked], dtype=bool) & np.asarray([item is None for item in mts_blocked], dtype=bool) & np.asarray([item is None for item in wt_blocked], dtype=bool) & np.asarray([item is None for item in tail_blocked], dtype=bool)
    raw_score = np.trunc(score)
    final_score = np.where(active, np.clip(raw_score, 0.0, 30.0), 0.0).astype(float)
    side = "LONG" if is_long else "SHORT"
    cfg_token = tm._TR_SCORE_CFG_CTX.set(cfg)
    try:
        threshold = float(tm._cfg("ENTRY_SCORE_THRESHOLD", 12.0, "v12", symbol, side) or 0.0)
    finally:
        tm._TR_SCORE_CFG_CTX.reset(cfg_token)
    sniper = np.asarray([any("Sniper" in reason for reason in row) for row in reasons], dtype=bool)
    fires = active & ((final_score >= threshold) | ((final_score >= 6.0) & sniper))
    reason_out = [""] * n
    for index in np.flatnonzero(fires):
        value = final_score[index]
        if value >= 28.0: recommendation = "STRONG_BUY" if is_long else "STRONG_SELL"
        elif value >= 20.0: recommendation = "GOOD_BUY" if is_long else "GOOD_SELL"
        elif value >= 15.0: recommendation = "WEAK_BUY" if is_long else "WEAK_SELL"
        else: recommendation = "WAIT"
        if value < threshold and sniper[index]:
            reason_out[index] = f"SNIPER_{side}_{recommendation}"
        else:
            reason_out[index] = f"{side}_{recommendation}: {','.join(reasons[index])}"
    # Statefully consumed HTF-only BB setups keep the live gate oracle until the vector retry book is supplied.
    bb_tf = str(getattr(cfg, "BB_BREAKOUT_ENTRY_TF", "OFF") or "OFF").strip()
    htf_only = active & bb & ~stoch & ~kzone_mask & ~flow & (bb_tf in ("1h", "4h", "D")) & bool(getattr(cfg, "HTF_SIGNAL_CONSUME_ENABLED", True))
    if np.any(htf_only):
        scalar_scores, scalar_fires, scalar_reasons = _entry_score_arrays_scalar(npz, n, symbol, is_long, cfg, htf_only)
        final_score[htf_only] = scalar_scores[htf_only]
        fires[htf_only] = scalar_fires[htf_only]
        for index in np.flatnonzero(htf_only): reason_out[index] = scalar_reasons[index]
    return final_score, fires, reason_out


def entry_score_arrays(npz, n, symbol, is_long, cfg, candidate_mask=None, scalar_reference=False):
    """Native NumPy stock score; scalar_reference exposes production oracle for differential tests."""
    if scalar_reference:
        return _entry_score_arrays_scalar(npz, n, symbol, is_long, cfg, candidate_mask)
    return entry_score_arrays_numpy(npz, n, symbol, is_long, cfg, candidate_mask)
