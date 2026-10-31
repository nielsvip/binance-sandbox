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
    return result


def _column(npz, key, n, default):
    try:
        value = np.asarray(npz[key], dtype=float).reshape(-1)
        if value.size == n:
            return np.where(np.isfinite(value), value, default)
    except (KeyError, TypeError, ValueError):
        pass
    return np.full(n, default, dtype=float)


def possible_entry_setup_mask(npz, n, is_long, cfg):
    """Cheap necessary-condition mask for native good_entry; never excludes a native setup."""
    ltf = bool(getattr(cfg, "LTF_SUB15M_TRADING_ENABLED", False))
    k5 = _column(npz, "k_5m" if ltf else "k_15m", n, 50.0)
    d5 = _column(npz, "d_5m" if ltf else "d_15m", n, 50.0)
    k5p = _column(npz, "k_5m_prev" if ltf else "k_15m_prev", n, 50.0)
    k1 = _column(npz, "k_1m", n, 50.0)
    d1 = _column(npz, "d_1m", n, 50.0)
    k1p = _column(npz, "k_1m_prev", n, 50.0)
    timestamps = _column(npz, "timestamps", n, 0.0)
    m1_ts = _column(npz, "timestamp_1m", n, 0.0)
    fresh = (m1_ts > 0.0) & ((timestamps - m1_ts) < 60.0) & ((timestamps - m1_ts) > 0.0)
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
    from vec_decisions.tradier_market_quality import market_quality_score_vec

    mq_enabled = bool(getattr(cfg, "MARKET_QUALITY_SCORE_ENABLED_TRADIER", False))
    mq_bonus, _ = market_quality_score_vec(npz, n, is_long, cfg, _column)
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
            try:
                score, recommendation, reason = strategy.calculate_signal_score(
                    "v12", symbol, is_long, float(indicators.get("close", 0.0) or 0.0), indicators, None, is_exit=False
                )
                threshold = float(tm._cfg("ENTRY_SCORE_THRESHOLD", 12.0, "v12", symbol, side) or 0.0)
            finally:
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
