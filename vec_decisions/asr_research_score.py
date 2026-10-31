"""Ordered AdvancedSignalRater research-bonus block shared by live and arrays."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np


def _cfg(cfg: Any, name: str, default: Any) -> Any:
    return cfg.get(name, default) if isinstance(cfg, Mapping) else getattr(cfg, name, default)


def _safe_float(value: Any, default: float) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def apply_research_score_scalar(score, reasons, indicators, current_price, is_long, cfg, is_exit=False):
    """Mirror the ordered native BACKTEST_CHANGE_126–133 entry score block."""
    if is_exit:
        return float(score), reasons
    ind = indicators or {}
    px = float(current_price)
    result = float(score)
    if bool(_cfg(cfg, "EMA_PULLBACK_ENABLED", False)):
        tf = _cfg(cfg, "EMA_PULLBACK_TF", "15m")
        e9, e14, e20 = (_safe_float(ind.get(f"ema_{period}_{tf}"), 0) for period in (9, 14, 20))
        k = _safe_float(ind.get(f"k_{tf}"), 50)
        if e9 > 0 and e14 > 0 and e20 > 0:
            if is_long and px > e20 and px < e9 and px < e14 and k < 20:
                result += _cfg(cfg, "EMA_PULLBACK_SCORE_BONUS", 35)
                reasons.append(f"EMA_PB_LONG(k={k:.0f})_bc128")
            elif not is_long and px < e20 and px > e9 and px > e14 and k > 80:
                result += _cfg(cfg, "EMA_PULLBACK_SCORE_BONUS", 35)
                reasons.append(f"EMA_PB_SHORT(k={k:.0f})_bc128")
    if bool(_cfg(cfg, "BB_RSI_STOCH_SCALP_ENABLED", False)):
        bb, rsi, k = (_safe_float(ind.get(key), default) for key, default in (("bb_pct_b_1h", .5), ("rsi_1h", 50), ("k_1h", 50)))
        if is_long and bb < .05 and rsi < 30 and k < 20:
            result += _cfg(cfg, "BB_RSI_STOCH_SCALP_SCORE", 25)
            reasons.append(f"BB_RSI_K_SCALP_L(bb={bb:.2f},rsi={rsi:.0f},k={k:.0f})_bc134")
        elif not is_long and bb > .95 and rsi > 70 and k > 80:
            result += _cfg(cfg, "BB_RSI_STOCH_SCALP_SCORE", 25)
            reasons.append(f"BB_RSI_K_SCALP_S(bb={bb:.2f},rsi={rsi:.0f},k={k:.0f})_bc134")
    if bool(_cfg(cfg, "MACD_ZERO_CROSS_ENABLED", False)):
        tf = _cfg(cfg, "MACD_ZERO_CROSS_TF", "1h")
        macd = _safe_float(ind.get(f"macd_{tf}"), 0)
        co, cu = ind.get(f"macd_crossover_{tf}", False), ind.get(f"macd_crossunder_{tf}", False)
        sma = _safe_float(ind.get(f"sma_200_{tf}"), 0)
        if is_long and co and macd < 0 and sma > 0 and px > sma:
            result += _cfg(cfg, "MACD_ZERO_CROSS_SCORE", 15)
            reasons.append(f"MACD_ZERO_L(m={macd:.4f})_bc131")
        elif not is_long and cu and macd > 0 and sma > 0 and px < sma:
            result += _cfg(cfg, "MACD_ZERO_CROSS_SCORE", 15)
            reasons.append(f"MACD_ZERO_S(m={macd:.4f})_bc131")
    if bool(_cfg(cfg, "RSI2_MEAN_REVERSION_ENABLED", False)):
        rsi2 = _safe_float(ind.get("rsi_2_1h"), 50)
        if is_long and rsi2 < _cfg(cfg, "RSI2_THRESHOLD_LONG", 15.0):
            result += _cfg(cfg, "RSI2_SCORE_BONUS", 20)
            reasons.append(f"RSI2_L({rsi2:.1f})_bc126")
        elif not is_long and rsi2 > _cfg(cfg, "RSI2_THRESHOLD_SHORT", 85.0):
            result += _cfg(cfg, "RSI2_SCORE_BONUS", 20)
            reasons.append(f"RSI2_S({rsi2:.1f})_bc126")
    if bool(_cfg(cfg, "HA_WICK_QUALITY_ENABLED", False)):
        tf = _cfg(cfg, "HA_WICK_QUALITY_TF", "1h")
        streak = _safe_float(ind.get(f"ha_streak_{tf}"), 0)
        if is_long and streak >= 3:
            result += _cfg(cfg, "HA_WICK_QUALITY_SCORE", 15)
            reasons.append(f"HA_STREAK_L({streak:.0f})_bc144")
        elif not is_long and streak <= -3:
            result += _cfg(cfg, "HA_WICK_QUALITY_SCORE", 15)
            reasons.append(f"HA_STREAK_S({streak:.0f})_bc144")
    if bool(_cfg(cfg, "BB_BREAKOUT_ENABLED", False)):
        tf = _cfg(cfg, "BB_BREAKOUT_TF", "1h")
        pct = _safe_float(ind.get(f"bb_pct_b_{tf}"), .5)
        sma = _safe_float(ind.get(f"sma_200_{tf}"), 0)
        adx = _safe_float(ind.get(f"adx_{tf}"), 0)
        if adx > 25 and sma > 0:
            if is_long and pct > 1 and px > sma:
                result += _cfg(cfg, "BB_BREAKOUT_SCORE", 20)
                reasons.append(f"BB_BKOUT_L(bb={pct:.2f},adx={adx:.0f})_bc132")
            elif not is_long and pct < 0 and px < sma:
                result += _cfg(cfg, "BB_BREAKOUT_SCORE", 20)
                reasons.append(f"BB_BKOUT_S(bb={pct:.2f},adx={adx:.0f})_bc132")
    if bool(_cfg(cfg, "TRIPLE_CONF_ENABLED", False)) and not is_exit:
        tf = _cfg(cfg, "TRIPLE_CONF_TF", "1h")
        rsi, k = _safe_float(ind.get(f"rsi_{tf}"), 50), _safe_float(ind.get(f"k_{tf}"), 50)
        co, cu = ind.get(f"macd_crossover_{tf}", False), ind.get(f"macd_crossunder_{tf}", False)
        if is_long and rsi < _cfg(cfg, "TRIPLE_CONF_RSI_LONG", 30.0) and k < _cfg(cfg, "TRIPLE_CONF_STOCH_LONG", 20.0) and co:
            result += _cfg(cfg, "TRIPLE_CONF_SCORE", 30)
            reasons.append(f"TRIPLE_CONF_L(rsi={rsi:.0f},k={k:.0f})_bc125")
        elif not is_long and rsi > _cfg(cfg, "TRIPLE_CONF_RSI_SHORT", 70.0) and k > _cfg(cfg, "TRIPLE_CONF_STOCH_SHORT", 80.0) and cu:
            result += _cfg(cfg, "TRIPLE_CONF_SCORE", 30)
            reasons.append(f"TRIPLE_CONF_S(rsi={rsi:.0f},k={k:.0f})_bc125")
    if bool(_cfg(cfg, "EMA200_STOCHRSI_ENABLED", False)) and not is_exit:
        tf = _cfg(cfg, "EMA200_STOCHRSI_TF", "1h")
        sma, k = _safe_float(ind.get(f"sma_200_{tf}"), 0), _safe_float(ind.get(f"k_{tf}"), 50)
        co, cu = ind.get(f"stoch_crossover_{tf}", False), ind.get(f"stoch_crossunder_{tf}", False)
        body = _safe_float(ind.get(f"candle_body_ratio_{tf}"), 1)
        if sma > 0:
            if is_long and px > sma and k < _cfg(cfg, "EMA200_STOCHRSI_K_LONG", 20.0) and co and body >= _cfg(cfg, "EMA200_STOCHRSI_BODY_MULT", 1.05):
                result += _cfg(cfg, "EMA200_STOCHRSI_SCORE", 25)
                reasons.append(f"EMA200_SRSI_L(k={k:.0f},br={body:.2f})_bc127")
            elif not is_long and px < sma and k > _cfg(cfg, "EMA200_STOCHRSI_K_SHORT", 80.0) and cu and body >= _cfg(cfg, "EMA200_STOCHRSI_BODY_MULT", 1.05):
                result += _cfg(cfg, "EMA200_STOCHRSI_SCORE", 25)
                reasons.append(f"EMA200_SRSI_S(k={k:.0f},br={body:.2f})_bc127")
    if bool(_cfg(cfg, "RSI_MACD_EMA_ENABLED", False)) and not is_exit:
        tf = _cfg(cfg, "RSI_MACD_EMA_TF", "1h")
        rsi, ema = _safe_float(ind.get(f"rsi_{tf}"), 50), _safe_float(ind.get(f"ema_9_{tf}"), 0)
        co, cu = ind.get(f"macd_crossover_{tf}", False), ind.get(f"macd_crossunder_{tf}", False)
        if ema > 0:
            if is_long and px > ema and co and rsi < _cfg(cfg, "RSI_MACD_EMA_RSI_LONG", 35.0):
                result += _cfg(cfg, "RSI_MACD_EMA_SCORE", 25)
                reasons.append(f"RSI_MACD_EMA_L(rsi={rsi:.0f})_bc129")
            elif not is_long and px < ema and cu and rsi > _cfg(cfg, "RSI_MACD_EMA_RSI_SHORT", 65.0):
                result += _cfg(cfg, "RSI_MACD_EMA_SCORE", 25)
                reasons.append(f"RSI_MACD_EMA_S(rsi={rsi:.0f})_bc129")
    if bool(_cfg(cfg, "DC_BREAKOUT_ENTRY_ENABLED", False)) and not is_exit:
        tf = str(_cfg(cfg, "DC_BREAKOUT_TF_EXPANDED", _cfg(cfg, "DC_BREAKOUT_TF", "1h")))
        hi, lo = _safe_float(ind.get(f"dc_high_{tf}"), 0), _safe_float(ind.get(f"dc_low_{tf}"), 0)
        adx = _safe_float(ind.get(f"adx_{tf}"), 0)
        if hi > 0 and lo > 0 and adx > 25:
            if is_long and px > hi:
                result += _cfg(cfg, "DC_BREAKOUT_SCORE", 15)
                reasons.append(f"DC_BKOUT_L(>{hi:.2f},adx={adx:.0f})_bc133")
            elif not is_long and px < lo:
                result += _cfg(cfg, "DC_BREAKOUT_SCORE", 15)
                reasons.append(f"DC_BKOUT_S(<{lo:.2f},adx={adx:.0f})_bc133")
    return result, reasons


def apply_research_score_vec(npz, n, is_long, cfg, score=None, current_price=None, is_exit=False):
    """Array counterpart; absent columns use the same scalar defaults."""
    result = np.zeros(n, dtype=float) if score is None else np.asarray(score, dtype=float).copy()
    if result.shape != (n,):
        raise ValueError("score must have shape (n,)")
    if is_exit:
        return result, {}
    px = _column(npz, "close", n, 0.0) if current_price is None else _as_column(current_price, n, 0.0)
    masks = {}

    def add(name, fire, amount):
        masks[name] = fire
        result[:] += fire.astype(float) * float(amount)

    tf = _cfg(cfg, "EMA_PULLBACK_TF", "15m")
    e9, e14, e20 = (_column(npz, f"ema_{period}_{tf}", n, 0.0) for period in (9, 14, 20))
    k = _column(npz, f"k_{tf}", n, 50.0)
    fire = (e9 > 0) & (e14 > 0) & (e20 > 0) & ((px > e20) & (px < e9) & (px < e14) & (k < 20) if is_long else (px < e20) & (px > e9) & (px > e14) & (k > 80))
    fire &= bool(_cfg(cfg, "EMA_PULLBACK_ENABLED", False))
    add("EMA_PULLBACK", fire, _cfg(cfg, "EMA_PULLBACK_SCORE_BONUS", 35))
    bb, rsi, k = (_column(npz, key, n, default) for key, default in (("bb_pct_b_1h", .5), ("rsi_1h", 50), ("k_1h", 50)))
    fire = (bb < .05) & (rsi < 30) & (k < 20) if is_long else (bb > .95) & (rsi > 70) & (k > 80)
    fire &= bool(_cfg(cfg, "BB_RSI_STOCH_SCALP_ENABLED", False))
    add("BB_RSI_STOCH_SCALP", fire, _cfg(cfg, "BB_RSI_STOCH_SCALP_SCORE", 25))
    tf = _cfg(cfg, "MACD_ZERO_CROSS_TF", "1h")
    macd, co, cu, sma = (_column(npz, key, n, default, dtype) for key, default, dtype in ((f"macd_{tf}", 0, float), (f"macd_crossover_{tf}", False, bool), (f"macd_crossunder_{tf}", False, bool), (f"sma_200_{tf}", 0, float)))
    fire = co & (macd < 0) & (sma > 0) & (px > sma) if is_long else cu & (macd > 0) & (sma > 0) & (px < sma)
    fire &= bool(_cfg(cfg, "MACD_ZERO_CROSS_ENABLED", False))
    add("MACD_ZERO_CROSS", fire, _cfg(cfg, "MACD_ZERO_CROSS_SCORE", 15))
    rsi2 = _column(npz, "rsi_2_1h", n, 50)
    threshold = _cfg(cfg, "RSI2_THRESHOLD_LONG", 15.0) if is_long else _cfg(cfg, "RSI2_THRESHOLD_SHORT", 85.0)
    fire = rsi2 < threshold if is_long else rsi2 > threshold
    fire &= bool(_cfg(cfg, "RSI2_MEAN_REVERSION_ENABLED", False))
    add("RSI2_MEAN_REVERSION", fire, _cfg(cfg, "RSI2_SCORE_BONUS", 20))
    tf = _cfg(cfg, "HA_WICK_QUALITY_TF", "1h")
    streak = _column(npz, f"ha_streak_{tf}", n, 0)
    fire = streak >= 3 if is_long else streak <= -3
    fire &= bool(_cfg(cfg, "HA_WICK_QUALITY_ENABLED", False))
    add("HA_WICK_QUALITY", fire, _cfg(cfg, "HA_WICK_QUALITY_SCORE", 15))
    tf = _cfg(cfg, "BB_BREAKOUT_TF", "1h")
    pct, sma, adx = (_column(npz, key, n, default) for key, default in ((f"bb_pct_b_{tf}", .5), (f"sma_200_{tf}", 0), (f"adx_{tf}", 0)))
    fire = (adx > 25) & (sma > 0) & ((pct > 1) & (px > sma) if is_long else (pct < 0) & (px < sma))
    fire &= bool(_cfg(cfg, "BB_BREAKOUT_ENABLED", False))
    add("BB_BREAKOUT", fire, _cfg(cfg, "BB_BREAKOUT_SCORE", 20))
    if not is_exit:
        tf = _cfg(cfg, "TRIPLE_CONF_TF", "1h")
        rsi, k = (_column(npz, f"{key}_{tf}", n, 50) for key in ("rsi", "k"))
        co, cu = (_column(npz, f"macd_{kind}_{tf}", n, False, bool) for kind in ("crossover", "crossunder"))
        fire = (rsi < _cfg(cfg, "TRIPLE_CONF_RSI_LONG", 30.0)) & (k < _cfg(cfg, "TRIPLE_CONF_STOCH_LONG", 20.0)) & co if is_long else (rsi > _cfg(cfg, "TRIPLE_CONF_RSI_SHORT", 70.0)) & (k > _cfg(cfg, "TRIPLE_CONF_STOCH_SHORT", 80.0)) & cu
        fire &= bool(_cfg(cfg, "TRIPLE_CONF_ENABLED", False))
        add("TRIPLE_CONF", fire, _cfg(cfg, "TRIPLE_CONF_SCORE", 30))
        tf = _cfg(cfg, "EMA200_STOCHRSI_TF", "1h")
        sma, k = _column(npz, f"sma_200_{tf}", n, 0), _column(npz, f"k_{tf}", n, 50)
        co, cu = (_column(npz, f"stoch_{kind}_{tf}", n, False, bool) for kind in ("crossover", "crossunder"))
        body = _column(npz, f"candle_body_ratio_{tf}", n, 1)
        fire = (sma > 0) & (px > sma) & (k < _cfg(cfg, "EMA200_STOCHRSI_K_LONG", 20.0)) & co & (body >= _cfg(cfg, "EMA200_STOCHRSI_BODY_MULT", 1.05)) if is_long else (sma > 0) & (px < sma) & (k > _cfg(cfg, "EMA200_STOCHRSI_K_SHORT", 80.0)) & cu & (body >= _cfg(cfg, "EMA200_STOCHRSI_BODY_MULT", 1.05))
        fire &= bool(_cfg(cfg, "EMA200_STOCHRSI_ENABLED", False))
        add("EMA200_STOCHRSI", fire, _cfg(cfg, "EMA200_STOCHRSI_SCORE", 25))
        tf = _cfg(cfg, "RSI_MACD_EMA_TF", "1h")
        rsi, ema = _column(npz, f"rsi_{tf}", n, 50), _column(npz, f"ema_9_{tf}", n, 0)
        co, cu = (_column(npz, f"macd_{kind}_{tf}", n, False, bool) for kind in ("crossover", "crossunder"))
        fire = (ema > 0) & (px > ema) & co & (rsi < _cfg(cfg, "RSI_MACD_EMA_RSI_LONG", 35.0)) if is_long else (ema > 0) & (px < ema) & cu & (rsi > _cfg(cfg, "RSI_MACD_EMA_RSI_SHORT", 65.0))
        fire &= bool(_cfg(cfg, "RSI_MACD_EMA_ENABLED", False))
        add("RSI_MACD_EMA", fire, _cfg(cfg, "RSI_MACD_EMA_SCORE", 25))
    else:
        for name in ("TRIPLE_CONF", "EMA200_STOCHRSI", "RSI_MACD_EMA"):
            masks[name] = np.zeros(n, dtype=bool)
    tf = str(_cfg(cfg, "DC_BREAKOUT_TF_EXPANDED", _cfg(cfg, "DC_BREAKOUT_TF", "1h")))
    hi, lo, adx = (_column(npz, key, n, 0) for key in (f"dc_high_{tf}", f"dc_low_{tf}", f"adx_{tf}"))
    fire = (hi > 0) & (lo > 0) & (adx > 25) & (px > hi if is_long else px < lo)
    fire &= bool(_cfg(cfg, "DC_BREAKOUT_ENTRY_ENABLED", False)) and not is_exit
    add("DC_BREAKOUT_ENTRY", fire, _cfg(cfg, "DC_BREAKOUT_SCORE", 15))
    return result, masks


def _column(npz, key, n, default, dtype=float):
    if key not in npz:
        return np.full(n, default, dtype=dtype)
    try:
        raw = np.asarray(npz[key]).reshape(-1)
        if raw.size != n:
            return np.full(n, default, dtype=dtype)
        if dtype is bool:
            if raw.dtype.kind in "SUO":
                return np.asarray([bool(value) for value in raw], dtype=bool)
            return raw.astype(bool)
        return raw.astype(float)
    except (TypeError, ValueError):
        return np.full(n, default, dtype=dtype)


def _as_column(value, n, default):
    try:
        raw = np.asarray(value, dtype=float).reshape(-1)
        return raw if raw.size == n else np.full(n, default, dtype=float)
    except (TypeError, ValueError):
        return np.full(n, default, dtype=float)
