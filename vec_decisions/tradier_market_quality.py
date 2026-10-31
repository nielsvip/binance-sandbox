"""Shared exact scalar and NumPy scoring for Tradier market quality."""
import numpy as np


def _number(value, default):
    try:
        result = float(value)
        return result if np.isfinite(result) else float(default)
    except (TypeError, ValueError):
        return float(default)


def _config_value(cfg, key, default):
    if isinstance(cfg, dict):
        return cfg.get(key, default)
    return getattr(cfg, key, default)


def market_quality_score_scalar(indicators, is_long, get):
    values = indicators or {}
    bb_1h = _number(values.get("bb_width_1h", 10.0), 10.0)
    bb_4h = _number(values.get("bb_width_4h", 10.0), 10.0)
    adx_1h = _number(values.get("adx_1h", 30.0), 30.0)
    adx_4h = _number(values.get("adx_4h", 30.0), 30.0)
    mfi_1h = _number(values.get("mfi_1h", 50.0), 50.0)
    mfi_4h = _number(values.get("mfi_4h", 50.0), 50.0)
    mfi_15m = _number(values.get("mfi_15m", 50.0), 50.0)
    rsi_1h = _number(values.get("rsi_1h", 50.0), 50.0)
    rsi_4h = _number(values.get("rsi_4h", 50.0), 50.0)
    rsi_15m = _number(values.get("rsi_15m", 50.0), 50.0)
    rvol_1h = _number(values.get("relative_volume_1h", 1.0), 1.0)
    rvol_15m = _number(values.get("relative_volume_15m", 1.0), 1.0)
    quality = 0.0
    reasons = []
    if bb_1h < 6.5 and bb_4h < 10.0:
        quality += 1.5
        reasons.append("BB_TIGHT")
    elif bb_1h < 9.0 and bb_4h < 13.0:
        quality += 0.5
    if adx_1h < 20.0 and adx_4h < 25.0:
        quality += 1.0
        reasons.append(f"ADX_LOW({adx_1h:.0f})")
    elif adx_1h < 25.0:
        quality += 0.5
    if is_long:
        if mfi_1h > 50.0 and mfi_4h > 45.0:
            quality += 1.0
            reasons.append("MFI_STRONG_L")
        elif mfi_1h > 40.0 and mfi_4h > 40.0:
            quality += 0.5
    else:
        rvol_gate = _number(get("TRADIER_RSI_SHORT_RVOL_1H", 1.0), 1.0)
        if rsi_1h > 55.0 and rsi_4h > 50.0 and rvol_1h >= rvol_gate:
            quality += 1.0
            reasons.append(f"RSI_STRONG_S(r1h={rsi_1h:.0f},rv={rvol_1h:.2f})")
        elif rsi_1h > 50.0 and rsi_4h > 45.0 and rvol_1h >= rvol_gate:
            quality += 0.5
    if is_long and rvol_1h < 0.95:
        quality += 0.5
        reasons.append("LOW_VOL_L")
    reversal = 0.0
    if is_long:
        if mfi_15m < 30.0 and mfi_1h < 35.0:
            reversal += 1.0
            reasons.append("MFI_EXTREME_L")
        elif mfi_15m > mfi_1h and mfi_1h < 40.0:
            reversal += 0.5
            reasons.append("MFI_TURN_L")
    else:
        rsi_15_gate = _number(get("TRADIER_RSI_SHORT_15M", 65.0), 65.0)
        rsi_1h_gate = _number(get("TRADIER_RSI_SHORT_1H", 65.0), 65.0)
        rvol_15_gate = _number(get("TRADIER_RSI_SHORT_RVOL_15M", 1.0), 1.0)
        if rsi_15m > rsi_15_gate and rsi_1h > rsi_1h_gate and rvol_15m >= rvol_15_gate:
            reversal += 1.0
            reasons.append(f"RSI_EXTREME_S(r15={rsi_15m:.0f},r1h={rsi_1h:.0f},rv={rvol_15m:.2f})")
        elif rsi_15m < rsi_1h and rsi_1h > rsi_1h_gate - 5.0:
            reversal += 0.5
            reasons.append(f"RSI_TURN_S(r15<r1h={rsi_1h:.0f})")
    total = quality + reversal
    if total >= 4.0:
        return 4.0, f"MQ_STRONG({total:.1f})|{'|'.join(reasons)}"
    if total >= 2.5:
        return 2.0, f"MQ_GOOD({total:.1f})"
    return 0.0, ""


def market_quality_score_vec(npz, n, is_long, cfg, safe):
    if not bool(_config_value(cfg, "MARKET_QUALITY_SCORE_ENABLED_TRADIER", False)):
        return np.zeros(n, dtype=float), np.zeros(n, dtype=bool)
    defaults = {
        "bb_width_1h": 10.0, "bb_width_4h": 10.0, "adx_1h": 30.0, "adx_4h": 30.0,
        "mfi_1h": 50.0, "mfi_4h": 50.0, "mfi_15m": 50.0, "rsi_1h": 50.0, "rsi_4h": 50.0,
        "rsi_15m": 50.0, "relative_volume_1h": 1.0, "relative_volume_15m": 1.0,
    }
    values = {}
    for key, default in defaults.items():
        raw = np.asarray(safe(npz, key, n, default), dtype=float)
        values[key] = np.where(np.isfinite(raw), raw, default)
    quality = np.zeros(n, dtype=float)
    bb_tight = (values["bb_width_1h"] < 6.5) & (values["bb_width_4h"] < 10.0)
    bb_mid = (values["bb_width_1h"] < 9.0) & (values["bb_width_4h"] < 13.0)
    quality += np.where(bb_tight, 1.5, np.where(bb_mid, 0.5, 0.0))
    adx_low = (values["adx_1h"] < 20.0) & (values["adx_4h"] < 25.0)
    quality += np.where(adx_low, 1.0, np.where(values["adx_1h"] < 25.0, 0.5, 0.0))
    reversal = np.zeros(n, dtype=float)
    if is_long:
        strong_mfi = (values["mfi_1h"] > 50.0) & (values["mfi_4h"] > 45.0)
        mid_mfi = (values["mfi_1h"] > 40.0) & (values["mfi_4h"] > 40.0)
        quality += np.where(strong_mfi, 1.0, np.where(mid_mfi, 0.5, 0.0))
        quality += (values["relative_volume_1h"] < 0.95).astype(float) * 0.5
        extreme = (values["mfi_15m"] < 30.0) & (values["mfi_1h"] < 35.0)
        turn = (values["mfi_15m"] > values["mfi_1h"]) & (values["mfi_1h"] < 40.0)
        reversal += np.where(extreme, 1.0, np.where(turn, 0.5, 0.0))
    else:
        rvol_1h_gate = float(_config_value(cfg, "TRADIER_RSI_SHORT_RVOL_1H", 1.0))
        strong_rsi = (values["rsi_1h"] > 55.0) & (values["rsi_4h"] > 50.0) & (values["relative_volume_1h"] >= rvol_1h_gate)
        mid_rsi = (values["rsi_1h"] > 50.0) & (values["rsi_4h"] > 45.0) & (values["relative_volume_1h"] >= rvol_1h_gate)
        quality += np.where(strong_rsi, 1.0, np.where(mid_rsi, 0.5, 0.0))
        rsi_15_gate = float(_config_value(cfg, "TRADIER_RSI_SHORT_15M", 65.0))
        rsi_1h_gate = float(_config_value(cfg, "TRADIER_RSI_SHORT_1H", 65.0))
        rvol_15_gate = float(_config_value(cfg, "TRADIER_RSI_SHORT_RVOL_15M", 1.0))
        extreme = (values["rsi_15m"] > rsi_15_gate) & (values["rsi_1h"] > rsi_1h_gate) & (values["relative_volume_15m"] >= rvol_15_gate)
        turn = (values["rsi_15m"] < values["rsi_1h"]) & (values["rsi_1h"] > rsi_1h_gate - 5.0)
        reversal += np.where(extreme, 1.0, np.where(turn, 0.5, 0.0))
    total = quality + reversal
    bonuses = np.where(total >= 4.0, 4.0, np.where(total >= 2.5, 2.0, 0.0))
    return bonuses.astype(float), bonuses > 0.0


def market_quality_score_vec_details(npz, n, is_long, cfg, safe):
    """Return vector MQ bonuses with native-format per-row reasons."""
    bonuses, _ = market_quality_score_vec(npz, n, is_long, cfg, safe)
    reasons = [""] * n
    if not bool(_config_value(cfg, "MARKET_QUALITY_SCORE_ENABLED_TRADIER", False)):
        return bonuses, reasons
    defaults = {
        "bb_width_1h": 10.0, "bb_width_4h": 10.0, "adx_1h": 30.0, "adx_4h": 30.0,
        "mfi_1h": 50.0, "mfi_4h": 50.0, "mfi_15m": 50.0, "rsi_1h": 50.0, "rsi_4h": 50.0,
        "rsi_15m": 50.0, "relative_volume_1h": 1.0, "relative_volume_15m": 1.0,
    }
    from functools import partial
    getter = partial(_config_value, cfg)
    for index in np.flatnonzero(bonuses):
        indicators = {}
        for key, default in defaults.items():
            raw = np.asarray(safe(npz, key, n, default), dtype=float).reshape(-1)
            value = raw[index] if raw.size == n else default
            indicators[key] = value
        scalar_bonus, reasons[index] = market_quality_score_scalar(indicators, is_long, getter)
        if scalar_bonus != bonuses[index]:
            raise RuntimeError("Tradier MQ vector bonus differs from scalar reason oracle")
    return bonuses, reasons
