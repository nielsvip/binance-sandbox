"""Exact per-bar score bonus masks extracted from AdvancedSignalRater entry scoring."""
import numpy as np


def _cfg(cfg, key, default):
    if isinstance(cfg, dict):
        return cfg.get(key, default)
    return getattr(cfg, key, default)


def bb_breakout_score_scalar(indicators, current_price, is_long, cfg, score=0.0):
    """Mirror ez_positions_quick.py AdvancedSignalRater.rate BB breakout bonus."""
    if not bool(_cfg(cfg, "BB_BREAKOUT_ENABLED", False)):
        return float(score), False
    tf = str(_cfg(cfg, "BB_BREAKOUT_TF", "1h"))
    ind = indicators or {}
    try:
        pct_b = float(ind.get(f"bb_pct_b_{tf}", 0.5))
    except (TypeError, ValueError):
        pct_b = 0.5
    try:
        sma = float(ind.get(f"sma_200_{tf}", 0))
    except (TypeError, ValueError):
        sma = 0.0
    try:
        adx = float(ind.get(f"adx_{tf}", 0))
    except (TypeError, ValueError):
        adx = 0.0
    px = float(current_price or 0.0)
    fire = adx > 25.0 and sma > 0.0 and ((is_long and pct_b > 1.0 and px > sma) or (not is_long and pct_b < 0.0 and px < sma))
    bonus = float(_cfg(cfg, "BB_BREAKOUT_SCORE", 20)) if fire else 0.0
    return float(score) + bonus, bool(fire)


def bb_breakout_score_vec(npz, n, is_long, cfg, score=None):
    """Return `(score_after_bonus, fire_mask)` for 15m-bar evaluator inputs."""
    if not bool(_cfg(cfg, "BB_BREAKOUT_ENABLED", False)):
        base = np.zeros(n, dtype=float) if score is None else np.asarray(score, dtype=float)
        return base.copy(), np.zeros(n, dtype=bool)
    tf = str(_cfg(cfg, "BB_BREAKOUT_TF", "1h"))
    pct_b = _column(npz, f"bb_pct_b_{tf}", n, 0.5)
    sma = _column(npz, f"sma_200_{tf}", n, 0.0)
    adx = _column(npz, f"adx_{tf}", n, 0.0)
    px = _column(npz, "close", n, 0.0)
    if is_long:
        fire = np.isfinite(adx) & (adx > 25.0) & np.isfinite(sma) & (sma > 0.0) & np.isfinite(pct_b) & (pct_b > 1.0) & np.isfinite(px) & (px > sma)
    else:
        fire = np.isfinite(adx) & (adx > 25.0) & np.isfinite(sma) & (sma > 0.0) & np.isfinite(pct_b) & (pct_b < 0.0) & np.isfinite(px) & (px < sma)
    base = np.zeros(n, dtype=float) if score is None else np.asarray(score, dtype=float)
    if base.shape != (n,):
        raise ValueError("score must have shape (n,)")
    return base + fire.astype(float) * float(_cfg(cfg, "BB_BREAKOUT_SCORE", 20)), fire


def _column(npz, key, n, default):
    try:
        if key not in npz:
            return np.full(n, default, dtype=float)
        values = np.asarray(npz[key], dtype=float).reshape(-1)
        if values.size != n:
            return np.full(n, default, dtype=float)
        return values
    except Exception:
        return np.full(n, default, dtype=float)


def research_score_masks_vec(npz, n, is_long, cfg, current_price=None, is_exit=False, score=None):
    """Apply exact AdvancedSignalRater bonus branches available from NPZ columns.

    Returns updated score and a name-to-fire-mask map. Missing columns use the
    source scalar defaults; columns with an invalid length also use that default.
    """
    base = np.zeros(n, dtype=float) if score is None else np.asarray(score, dtype=float).copy()
    if base.shape != (n,):
        raise ValueError("score must have shape (n,)")
    masks = {}
    tf = str(_cfg(cfg, "EMA_PULLBACK_TF", "15m"))
    ema9 = _column(npz, f"ema_9_{tf}", n, 0.0)
    ema14 = _column(npz, f"ema_14_{tf}", n, 0.0)
    ema20 = _column(npz, f"ema_20_{tf}", n, 0.0)
    k = _column(npz, f"k_{tf}", n, 50.0)
    px = _column(npz, "close", n, 0.0) if current_price is None else _as_column(current_price, n, 0.0)
    valid = (ema9 > 0.0) & (ema14 > 0.0) & (ema20 > 0.0)
    if is_long:
        fire = valid & (px > ema20) & (px < ema9) & (px < ema14) & (k < 20.0)
    else:
        fire = valid & (px < ema20) & (px > ema9) & (px > ema14) & (k > 80.0)
    fire &= bool(_cfg(cfg, "EMA_PULLBACK_ENABLED", False))
    base += fire.astype(float) * float(_cfg(cfg, "EMA_PULLBACK_SCORE_BONUS", 35))
    masks["EMA_PULLBACK"] = fire

    rsi2 = _column(npz, "rsi_2_1h", n, 50.0)
    threshold = float(_cfg(cfg, "RSI2_THRESHOLD_LONG", 15.0) if is_long else _cfg(cfg, "RSI2_THRESHOLD_SHORT", 85.0))
    fire = (rsi2 < threshold) if is_long else (rsi2 > threshold)
    fire &= bool(_cfg(cfg, "RSI2_MEAN_REVERSION_ENABLED", False))
    base += fire.astype(float) * float(_cfg(cfg, "RSI2_SCORE_BONUS", 20))
    masks["RSI2_MEAN_REVERSION"] = fire

    if not is_exit:
        tf = str(_cfg(cfg, "TRIPLE_CONF_TF", "1h"))
        rsi = _column(npz, f"rsi_{tf}", n, 50.0)
        k = _column(npz, f"k_{tf}", n, 50.0)
        co = _column(npz, f"macd_crossover_{tf}", n, 0.0, dtype=bool)
        cu = _column(npz, f"macd_crossunder_{tf}", n, 0.0, dtype=bool)
        if is_long:
            fire = (rsi < float(_cfg(cfg, "TRIPLE_CONF_RSI_LONG", 30.0))) & (k < float(_cfg(cfg, "TRIPLE_CONF_STOCH_LONG", 20.0))) & co
        else:
            fire = (rsi > float(_cfg(cfg, "TRIPLE_CONF_RSI_SHORT", 70.0))) & (k > float(_cfg(cfg, "TRIPLE_CONF_STOCH_SHORT", 80.0))) & cu
        fire &= bool(_cfg(cfg, "TRIPLE_CONF_ENABLED", False))
        base += fire.astype(float) * float(_cfg(cfg, "TRIPLE_CONF_SCORE", 30))
        masks["TRIPLE_CONF"] = fire

        tf = str(_cfg(cfg, "EMA200_STOCHRSI_TF", "1h"))
        sma = _column(npz, f"sma_200_{tf}", n, 0.0)
        k = _column(npz, f"k_{tf}", n, 50.0)
        co = _column(npz, f"stoch_crossover_{tf}", n, 0.0, dtype=bool)
        cu = _column(npz, f"stoch_crossunder_{tf}", n, 0.0, dtype=bool)
        body = _column(npz, f"candle_body_ratio_{tf}", n, 1.0)
        valid_sma = sma > 0.0
        if is_long:
            fire = valid_sma & (px > sma) & (k < float(_cfg(cfg, "EMA200_STOCHRSI_K_LONG", 20.0))) & co & (body >= float(_cfg(cfg, "EMA200_STOCHRSI_BODY_MULT", 1.05)))
        else:
            fire = valid_sma & (px < sma) & (k > float(_cfg(cfg, "EMA200_STOCHRSI_K_SHORT", 80.0))) & cu & (body >= float(_cfg(cfg, "EMA200_STOCHRSI_BODY_MULT", 1.05)))
        fire &= bool(_cfg(cfg, "EMA200_STOCHRSI_ENABLED", False))
        base += fire.astype(float) * float(_cfg(cfg, "EMA200_STOCHRSI_SCORE", 25))
        masks["EMA200_STOCHRSI"] = fire

        tf = str(_cfg(cfg, "RSI_MACD_EMA_TF", "1h"))
        rsi = _column(npz, f"rsi_{tf}", n, 50.0)
        ema9 = _column(npz, f"ema_9_{tf}", n, 0.0)
        co = _column(npz, f"macd_crossover_{tf}", n, 0.0, dtype=bool)
        cu = _column(npz, f"macd_crossunder_{tf}", n, 0.0, dtype=bool)
        if is_long:
            fire = (ema9 > 0.0) & (px > ema9) & co & (rsi < float(_cfg(cfg, "RSI_MACD_EMA_RSI_LONG", 35.0)))
        else:
            fire = (ema9 > 0.0) & (px < ema9) & cu & (rsi > float(_cfg(cfg, "RSI_MACD_EMA_RSI_SHORT", 65.0)))
        fire &= bool(_cfg(cfg, "RSI_MACD_EMA_ENABLED", False))
        base += fire.astype(float) * float(_cfg(cfg, "RSI_MACD_EMA_SCORE", 25))
        masks["RSI_MACD_EMA"] = fire
    else:
        masks["TRIPLE_CONF"] = np.zeros(n, dtype=bool)
        masks["EMA200_STOCHRSI"] = np.zeros(n, dtype=bool)
        masks["RSI_MACD_EMA"] = np.zeros(n, dtype=bool)
    if not is_exit:
        mq_score, mq_mask = market_quality_score_bonus_vec(npz, n, is_long, cfg)
        base += mq_score
        masks["MARKET_QUALITY_SCORE"] = mq_mask
    else:
        masks["MARKET_QUALITY_SCORE"] = np.zeros(n, dtype=bool)
    return base, masks


def _as_column(value, n, default):
    try:
        arr = np.asarray(value, dtype=float).reshape(-1)
        return arr if arr.size == n else np.full(n, default, dtype=float)
    except Exception:
        return np.full(n, default, dtype=float)


def _column(npz, key, n, default, dtype=float):
    try:
        if key not in npz:
            return np.full(n, default, dtype=dtype)
        raw = np.asarray(npz[key]).reshape(-1)
        if raw.size != n:
            return np.full(n, default, dtype=dtype)
        if dtype is bool:
            if raw.dtype.kind in "SUO":
                return np.asarray([str(value).lower() in ("1", "true", "yes") for value in raw], dtype=bool)
            return raw.astype(bool)
        return raw.astype(float)
    except Exception:
        return np.full(n, default, dtype=dtype)


def research_score_masks_scalar(ind, current_price, is_long, cfg, is_exit=False, score=0.0):
    """Scalar adapter for the named AdvancedSignalRater bonus branches."""
    ind = ind or {}
    px = _scalar_float(current_price, 0.0)
    masks = {}
    if bool(_cfg(cfg, "EMA_PULLBACK_ENABLED", False)):
        tf = str(_cfg(cfg, "EMA_PULLBACK_TF", "15m"))
        e9 = _scalar_float(ind.get(f"ema_9_{tf}"), 0.0)
        e14 = _scalar_float(ind.get(f"ema_14_{tf}"), 0.0)
        e20 = _scalar_float(ind.get(f"ema_20_{tf}"), 0.0)
        k = _scalar_float(ind.get(f"k_{tf}"), 50.0)
        fire = e9 > 0 and e14 > 0 and e20 > 0 and ((is_long and px > e20 and px < e9 and px < e14 and k < 20) or (not is_long and px < e20 and px > e9 and px > e14 and k > 80))
    else:
        fire = False
    masks["EMA_PULLBACK"] = bool(fire)
    if fire:
        score += float(_cfg(cfg, "EMA_PULLBACK_SCORE_BONUS", 35))

    threshold = float(_cfg(cfg, "RSI2_THRESHOLD_LONG", 15.0) if is_long else _cfg(cfg, "RSI2_THRESHOLD_SHORT", 85.0))
    rsi2 = _scalar_float(ind.get("rsi_2_1h"), 50.0)
    fire = bool(_cfg(cfg, "RSI2_MEAN_REVERSION_ENABLED", False)) and (rsi2 < threshold if is_long else rsi2 > threshold)
    masks["RSI2_MEAN_REVERSION"] = fire
    if fire:
        score += float(_cfg(cfg, "RSI2_SCORE_BONUS", 20))

    for name in ("TRIPLE_CONF", "EMA200_STOCHRSI", "RSI_MACD_EMA"):
        fire = False
        if not is_exit and bool(_cfg(cfg, f"{name}_ENABLED", False)):
            tf = str(_cfg(cfg, f"{name}_TF", "1h"))
            if name == "TRIPLE_CONF":
                rsi = _scalar_float(ind.get(f"rsi_{tf}"), 50.0)
                k = _scalar_float(ind.get(f"k_{tf}"), 50.0)
                cross = bool(ind.get(f"macd_crossover_{tf}" if is_long else f"macd_crossunder_{tf}", False))
                fire = ((rsi < float(_cfg(cfg, "TRIPLE_CONF_RSI_LONG", 30.0)) and k < float(_cfg(cfg, "TRIPLE_CONF_STOCH_LONG", 20.0)) and cross) if is_long else (rsi > float(_cfg(cfg, "TRIPLE_CONF_RSI_SHORT", 70.0)) and k > float(_cfg(cfg, "TRIPLE_CONF_STOCH_SHORT", 80.0)) and cross))
                bonus_key, default = "TRIPLE_CONF_SCORE", 30
            elif name == "EMA200_STOCHRSI":
                sma = _scalar_float(ind.get(f"sma_200_{tf}"), 0.0)
                k = _scalar_float(ind.get(f"k_{tf}"), 50.0)
                cross = bool(ind.get(f"stoch_crossover_{tf}" if is_long else f"stoch_crossunder_{tf}", False))
                body = _scalar_float(ind.get(f"candle_body_ratio_{tf}"), 1.0)
                fire = sma > 0 and body >= float(_cfg(cfg, "EMA200_STOCHRSI_BODY_MULT", 1.05)) and ((px > sma and k < float(_cfg(cfg, "EMA200_STOCHRSI_K_LONG", 20.0)) and cross) if is_long else (px < sma and k > float(_cfg(cfg, "EMA200_STOCHRSI_K_SHORT", 80.0)) and cross))
                bonus_key, default = "EMA200_STOCHRSI_SCORE", 25
            else:
                ema = _scalar_float(ind.get(f"ema_9_{tf}"), 0.0)
                rsi = _scalar_float(ind.get(f"rsi_{tf}"), 50.0)
                cross = bool(ind.get(f"macd_crossover_{tf}" if is_long else f"macd_crossunder_{tf}", False))
                fire = ema > 0 and ((px > ema and rsi < float(_cfg(cfg, "RSI_MACD_EMA_RSI_LONG", 35.0)) and cross) if is_long else (px < ema and rsi > float(_cfg(cfg, "RSI_MACD_EMA_RSI_SHORT", 65.0)) and cross))
                bonus_key, default = "RSI_MACD_EMA_SCORE", 25
        else:
            bonus_key, default = f"{name}_SCORE", 0
        masks[name] = bool(fire)
        if fire:
            score += float(_cfg(cfg, bonus_key, default))
    mq_fire = False
    if not is_exit and bool(_cfg(cfg, "MARKET_QUALITY_SCORE_ENABLED", False)):
        from vec_decisions.sba_bounce import _market_quality_score_parts
        values = dict(ind)
        values.setdefault("current_price", px)
        quality, reversal = _market_quality_score_parts(values, is_long)
        total = quality + reversal
        if total >= 6.0:
            score += 5.0
            mq_fire = True
        elif total >= 4.0:
            score += 2.0
            mq_fire = True
    masks["MARKET_QUALITY_SCORE"] = mq_fire
    return float(score), masks


def market_quality_score_bonus_vec(npz, n, is_long, cfg):
    """Exact no-veto MQ score bonus, without SBA's independent dealbreakers."""
    bonuses = np.zeros(n, dtype=float)
    fires = np.zeros(n, dtype=bool)
    if not bool(_cfg(cfg, "MARKET_QUALITY_SCORE_ENABLED", False)):
        return bonuses, fires
    from vec_decisions.sba_bounce import _market_quality_score_parts
    enum_keys = ("wt_signal_15m", "wt_signal_1h", "ha_3m", "ha_3m_prev", "ha_15m", "ha_15m_prev", "wt_divergence_1h", "wt_divergence_4h", "wt_momentum_state_1h", "wt_momentum_state_4h", "bar_pattern_15m")
    bool_keys = ("stoch_crossover_15m", "stoch_crossunder_15m", "stoch_crossover_1h", "stoch_crossunder_1h")
    numeric_keys = ("bb_width_1h", "bb_width_4h", "dc_width_1h", "dc_width", "dc_width_4h", "adx_1h", "adx_4h", "sma_200_1h", "close_1h", "relative_volume_15m", "relative_volume_1h", "mfi_15m", "mfi_1h", "mfi_4h", "rsi_15m", "rsi_1h", "rsi_4h", "k_3m", "k_15m", "k_1h", "rsi_2_1h")
    enum_maps = {"ha_3m": {1: "green", -1: "red"}, "ha_3m_prev": {1: "green", -1: "red"}, "ha_15m": {1: "green", -1: "red"}, "ha_15m_prev": {1: "green", -1: "red"}, "wt_divergence_1h": {1: "BULL", -1: "BEAR"}, "wt_divergence_4h": {1: "BULL", -1: "BEAR"}, "wt_momentum_state_1h": {-2: "EXHAUST_DOWN", 2: "EXHAUST_UP"}, "wt_momentum_state_4h": {-2: "EXHAUST_DOWN", 2: "EXHAUST_UP"}}
    arrays = {key: _raw_column(npz, key, n) for key in enum_keys + bool_keys + numeric_keys}
    close = _column(npz, "close", n, 0.0)
    for i in range(n):
        ind = {"current_price": float(close[i])}
        for key, arr in arrays.items():
            if arr is None:
                continue
            value = arr[i]
            if key in enum_maps:
                try:
                    value = enum_maps[key].get(int(value), str(value))
                except (TypeError, ValueError):
                    pass
            elif key in bool_keys:
                value = bool(value)
            elif key in numeric_keys:
                try:
                    value = float(value)
                except (TypeError, ValueError):
                    continue
            ind[key] = value
        quality, reversal = _market_quality_score_parts(ind, is_long)
        total = quality + reversal
        if total >= 6.0:
            bonuses[i], fires[i] = 5.0, True
        elif total >= 4.0:
            bonuses[i], fires[i] = 2.0, True
    return bonuses, fires


def _raw_column(npz, key, n):
    try:
        if key not in npz:
            return None
        values = np.asarray(npz[key]).reshape(-1)
        return values if values.size == n else None
    except Exception:
        return None


def _scalar_float(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)
