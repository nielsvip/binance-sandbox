"""NumPy ports for ordered inline AdvancedSignalRater score families."""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np


def _cfg(cfg, key, default):
    return getattr(cfg, key, default)


def _float_column(values, key, n, default):
    raw = values.get(key)
    if raw is None:
        return np.full(n, default, dtype=float)
    array = np.asarray(raw)
    if array.ndim == 0:
        array = np.full(n, array.item())
    array = array.reshape(-1)
    if array.size != n:
        raise ValueError(f"{key} length {array.size} does not match n={n}")
    if array.dtype.kind in "biuf":
        return array.astype(float)
    result = np.full(n, default, dtype=float)
    for index, value in enumerate(array):
        if value is None:
            continue
        try:
            result[index] = float(value)
        except (TypeError, ValueError):
            pass
    return result


def _text_column(values, key, n, default=""):
    raw = values.get(key)
    if raw is None:
        return np.full(n, default, dtype=object)
    array = np.asarray(raw, dtype=object)
    if array.ndim == 0:
        array = np.full(n, array.item(), dtype=object)
    array = array.reshape(-1)
    if array.size != n:
        raise ValueError(f"{key} length {array.size} does not match n={n}")
    return array


def _size(values, key):
    if key not in values:
        raise ValueError(f"missing required ASR inline-score field {key}")
    return max(1, np.asarray(values[key]).size)


def bc155_score_array(indicators, current_price, is_long, cfg, is_exit=False, score=None):
    """Port native BC_155a-e score changes, preserving source defaults/order."""
    price = np.asarray(current_price, dtype=float).reshape(-1)
    n = price.size
    result = np.zeros(n, dtype=float) if score is None else np.asarray(score, dtype=float).reshape(-1).copy()
    if result.size != n:
        raise ValueError("score and current_price lengths differ")
    masks = {}
    if not is_exit:
        adx = _float_column(indicators, "adx_4h", n, 25.0)
        adx_fire = bool(_cfg(cfg, "TR_ADX4H_GATE_ENABLED", True)) & (adx > float(_cfg(cfg, "TR_ADX4H_MAX", 20.0)))
        result += adx_fire * float(_cfg(cfg, "TR_ADX4H_BOYCOTT_SCORE", -40))
        masks["TR_ADX4H_TREND"] = adx_fire

        upper = _float_column(indicators, "bb_upper_4h", n, 0.0)
        lower = _float_column(indicators, "bb_lower_4h", n, 0.0)
        width = np.zeros(n, dtype=float)
        valid = (price > 0) & (upper > 0) & (lower > 0)
        np.divide((upper - lower) * 100.0, price, out=width, where=valid)
        bb_fire = bool(_cfg(cfg, "TR_BBWIDTH4H_GATE_ENABLED", True)) & (width > float(_cfg(cfg, "TR_BBWIDTH4H_MAX", 10.0)))
        result += bb_fire * float(_cfg(cfg, "TR_BBWIDTH4H_BOYCOTT_SCORE", -35))
        masks["TR_BBW4H_WIDE"] = bb_fire

        chop = _float_column(indicators, "choppiness_4h", n, 50.0)
        chop_on = bool(_cfg(cfg, "TR_CHOP4H_GATE_ENABLED", True))
        chop_bonus = chop_on & (chop >= float(_cfg(cfg, "TR_CHOP4H_MIN", 50.0)))
        chop_penalty = chop_on & ~chop_bonus & (chop < float(_cfg(cfg, "TR_CHOP4H_TREND_MAX", 38.0)))
        result += chop_bonus * float(_cfg(cfg, "TR_CHOP4H_BONUS", 15))
        result += chop_penalty * float(_cfg(cfg, "TR_CHOP4H_PENALTY", -20))
        masks["TR_CHOP4H_OK"] = chop_bonus
        masks["TR_CHOP4H_TREND"] = chop_penalty

        mfi = _float_column(indicators, "mfi_4h", n, 50.0)
        mfi_fire = bool(_cfg(cfg, "TR_MFI4H_LONG_ENABLED", True)) & bool(is_long) & (mfi > 0) & (mfi < float(_cfg(cfg, "TR_MFI4H_LONG_MIN", 40.0)))
        result += mfi_fire * float(_cfg(cfg, "TR_MFI4H_LONG_BOYCOTT_SCORE", -25))
        masks["TR_MFI4H_LOW_LONG"] = np.broadcast_to(np.asarray(mfi_fire, dtype=bool), (n,)).copy()

        high = _float_column(indicators, "dc_high_4h", n, 0.0)
        low = _float_column(indicators, "dc_low_4h", n, 0.0)
        dc_width = np.zeros(n, dtype=float)
        dc_valid = (price > 0) & (high > 0) & (low > 0)
        np.divide((high - low) * 100.0, price, out=dc_width, where=dc_valid)
        dc_fire = bool(_cfg(cfg, "TR_DCWIDTH4H_SHORT_ENABLED", True)) & bool(not is_long) & (dc_width > float(_cfg(cfg, "TR_DCWIDTH4H_SHORT_MAX", 15.0)))
        result += dc_fire * float(_cfg(cfg, "TR_DCWIDTH4H_SHORT_BOYCOTT_SCORE", -25))
        masks["TR_DCW4H_WIDE_SHORT"] = np.broadcast_to(np.asarray(dc_fire, dtype=bool), (n,)).copy()
    return result, masks


def k_zone_score_array(values, is_long, cfg, is_exit=False, score=None):
    """Port base and Tradier K-zone score bonuses after BC_155."""
    n = _size(values, "close")
    k3 = _float_column(values, "k_3m", n, 50.0)
    k3_prev = _float_column(values, "k_3m_prev", n, 50.0)
    k15 = _text_column(values, "ha_3m", n, "neutral")
    ha3_prev = _text_column(values, "ha_3m_prev", n, "neutral")
    ha15 = _text_column(values, "ha_15m", n, "neutral")
    result = np.zeros(n, dtype=float) if score is None else np.asarray(score, dtype=float).reshape(-1).copy()
    if result.size != n:
        raise ValueError("score and K-zone input lengths differ")
    masks = {}
    if not is_exit:
        base_on = bool(_cfg(cfg, "K_ZONE_ENTRY_ENABLED", True))
        base_long = float(_cfg(cfg, "K_ZONE_LONG_THRESHOLD", 35))
        base_short = float(_cfg(cfg, "K_ZONE_SHORT_THRESHOLD", 65))
        rising, falling = k3 > k3_prev, k3 < k3_prev
        flip_green, flip_red = (k15 == "green") & (ha3_prev != "green"), (k15 == "red") & (ha3_prev != "red")
        bull = flip_green | ((k15 == "green") & (ha15 == "green"))
        bear = flip_red | ((k15 == "red") & (ha15 == "red"))
        base_fire = (k3 < base_long) & rising & bull if is_long else (k3 > base_short) & falling & bear
        base_fire &= base_on
        base_bonus = float(_cfg(cfg, "K_ZONE_ENTRY_BONUS", 25))
        result += base_fire * base_bonus
        masks["K_ZONE_BASE"] = base_fire

        tradier_on = bool(_cfg(cfg, "K_ZONE_ENTRY_ENABLED_TRADIER", False))
        tradier_long = float(int(_cfg(cfg, "K_ZONE_LONG_THRESHOLD_TRADIER", 35)))
        tradier_short = float(int(_cfg(cfg, "K_ZONE_SHORT_THRESHOLD_TRADIER", 65)))
        tradier_bonus = float(int(_cfg(cfg, "K_ZONE_ENTRY_BONUS_TRADIER", 20)))
        tradier_fire = (k3 < tradier_long) & rising & bull if is_long else (k3 > tradier_short) & falling & bear
        tradier_fire &= tradier_on
        result += tradier_fire * tradier_bonus
        masks["K_ZONE_TRADIER"] = tradier_fire
    return result, masks


def momentum_fade_score_array(values, is_long, cfg, is_exit=False, score=None):
    """Port the crypto and Tradier momentum-fade bonuses in native order."""
    n = _size(values, "close")
    high3 = _float_column(values, "high_3m", n, 0.0)
    low3 = _float_column(values, "low_3m", n, 0.0)
    atr3 = _float_column(values, "atr_3m", n, 0.0)
    rv3 = _float_column(values, "rel_vol_3m", n, 0.0)
    rv15 = _float_column(values, "rel_vol_15m", n, 0.0)
    k3 = _float_column(values, "k_3m", n, 50.0)
    high15 = _float_column(values, "high_15m", n, 0.0)
    low15 = _float_column(values, "low_15m", n, 0.0)
    atr15 = _float_column(values, "atr_15m", n, 0.0)
    k15 = _float_column(values, "k_15m", n, 50.0)
    result = np.zeros(n, dtype=float) if score is None else np.asarray(score, dtype=float).reshape(-1).copy()
    if result.size != n:
        raise ValueError("score and momentum-fade input lengths differ")
    masks = {}
    if not is_exit:
        range3 = np.where((high3 > 0) & (low3 > 0), high3 - low3, 0.0)
        body3 = np.zeros(n, dtype=float)
        np.divide(range3, atr3, out=body3, where=atr3 > 0)
        base_on = bool(_cfg(cfg, "MOMENTUM_FADE_ENABLED", True))
        base_vol_min = float(_cfg(cfg, "MOMENTUM_FADE_VOL_MIN", 2.0))
        base_body_min = float(_cfg(cfg, "MOMENTUM_FADE_BODY_ATR_MIN", 2.0))
        base_k_zone = bool(_cfg(cfg, "MOMENTUM_FADE_K_ZONE", True))
        base_k_ok = (k3 < 40.0) if is_long else (k3 > 60.0)
        base_fire = base_on & (body3 >= base_body_min) & ((rv3 >= base_vol_min) | (rv15 >= base_vol_min)) & (~base_k_zone | base_k_ok)
        base_bonus = float(_cfg(cfg, "MOMENTUM_FADE_SCORE_BONUS", 35))
        result += base_fire * base_bonus
        masks["MOMENTUM_FADE"] = base_fire

        range15 = np.where((high15 > 0) & (low15 > 0), high15 - low15, 0.0)
        body15 = np.zeros(n, dtype=float)
        np.divide(range15, atr15, out=body15, where=atr15 > 0)
        tradier_on = bool(_cfg(cfg, "MOMENTUM_FADE_ENABLED_TRADIER", False))
        tradier_vol_min = float(_cfg(cfg, "MOMENTUM_FADE_VOL_MIN_TRADIER", 2.0))
        tradier_body_min = float(_cfg(cfg, "MOMENTUM_FADE_BODY_ATR_MIN_TRADIER", 2.0))
        tradier_k_zone = bool(_cfg(cfg, "MOMENTUM_FADE_K_ZONE_TRADIER", True))
        tradier_k_ok = (k15 < 40.0) if is_long else (k15 > 60.0)
        tradier_fire = tradier_on & (body15 >= tradier_body_min) & (rv15 >= tradier_vol_min) & (~tradier_k_zone | tradier_k_ok)
        tradier_bonus = float(int(_cfg(cfg, "MOMENTUM_FADE_SCORE_BONUS_TRADIER", 5)))
        result += tradier_fire * tradier_bonus
        masks["MOMENTUM_FADE_TRADIER"] = tradier_fire
    return result, masks


def mover_score_array(direction, active, is_long, cfg, score=None):
    """Apply the registry-backed mover bonus from explicit causal snapshot state."""
    directions = np.asarray(direction, dtype=object).reshape(-1)
    n = directions.size
    active_rows = np.broadcast_to(np.asarray(active, dtype=bool), (n,))
    result = np.zeros(n, dtype=float) if score is None else np.asarray(score, dtype=float).reshape(-1).copy()
    if result.size != n:
        raise ValueError("score and mover-state lengths differ")
    expected = "LONG" if is_long else "SHORT"
    fire = active_rows & (directions == expected)
    result += fire * float(_cfg(cfg, "MOVER_SCORE_BONUS", 40))
    return result, {"MOVER_DETECT": fire}


def _satoshi_cfg(cfg, name, default):
    tradier_name = f"{name}_TRADIER"
    if hasattr(cfg, tradier_name):
        return getattr(cfg, tradier_name)
    return _cfg(cfg, name, default)


def satoshit_score_array(values, is_long, cfg, enabled, score=None):
    """Vector port of satoshit_score_bonus; eligibility is explicit external state."""
    n = _size(values, "close")
    result = np.zeros(n, dtype=float) if score is None else np.asarray(score, dtype=float).reshape(-1).copy()
    if result.size != n:
        raise ValueError("score and Satoshit input lengths differ")
    masks = {}
    if not enabled:
        masks["SATOSHIT"] = np.zeros(n, dtype=bool)
        return result, masks

    def clean(key, default):
        column = _float_column(values, key, n, default)
        return np.where(np.isfinite(column), column, default)

    rsi = clean("rsi_15m", 50.0)
    k = clean("stoch_k_15m", 50.0)
    mfi = clean("mfi_15m", 50.0)
    bb = clean("bb_pct_b_1h", 0.5)
    mfi_d = clean("mfi_D", 50.0)
    rvol = clean("relative_volume_1h", 1.0)
    ha = _text_column(values, "ha_15m", n, "neutral")
    raw_ha = np.asarray(values.get("ha_15m", np.full(n, "neutral", object)), dtype=object).reshape(-1)
    if raw_ha.size != n:
        raise ValueError("ha_15m length mismatch")
    green = ha == "green"
    red = ha == "red"
    for index, value in enumerate(raw_ha):
        if isinstance(value, (bool, int, float, np.integer, np.floating)):
            green[index] = float(value) > 0
            red[index] = float(value) < 0
    min_votes = _satoshi_cfg(cfg, "SATOSHIT_MIN_VOTES", 3)
    if is_long:
        ha_streak = np.where(red, -1, np.where(green, 1, 0))
        vote_terms = (rsi < float(_satoshi_cfg(cfg, "SATOSHIT_LONG_RSI_MAX", 50)),
                      bb < float(_satoshi_cfg(cfg, "SATOSHIT_LONG_BB_PCTB_MAX", 0.50)),
                      ha_streak < float(_satoshi_cfg(cfg, "SATOSHIT_LONG_HA_STREAK_MAX", 1)),
                      k < float(_satoshi_cfg(cfg, "SATOSHIT_LONG_STOCH_K_MAX", 60)),
                      mfi < float(_satoshi_cfg(cfg, "SATOSHIT_LONG_MFI_MAX", 60)))
        votes = np.zeros(n, dtype=np.int8)
        for vote in vote_terms:
            votes += np.asarray(vote, dtype=np.int8)
    else:
        ha_streak = np.where(green, 1, np.where(red, -1, 0))
        vote_terms = (rsi > float(_satoshi_cfg(cfg, "SATOSHIT_SHORT_RSI_MIN", 55)),
                      bb > float(_satoshi_cfg(cfg, "SATOSHIT_SHORT_BB_PCTB_MIN", 0.55)),
                      ha_streak > float(_satoshi_cfg(cfg, "SATOSHIT_SHORT_HA_STREAK_MIN", 0)),
                      k > float(_satoshi_cfg(cfg, "SATOSHIT_SHORT_STOCH_K_MIN", 50)),
                      mfi > float(_satoshi_cfg(cfg, "SATOSHIT_SHORT_MFI_MIN", 50)))
        votes = np.zeros(n, dtype=np.int8)
        for vote in vote_terms:
            votes += np.asarray(vote, dtype=np.int8)
    fire = ((votes >= min_votes)
            & (mfi_d >= float(_satoshi_cfg(cfg, "SATOSHIT_HTF_MFI_D_MIN", 30)))
            & (rvol >= float(_satoshi_cfg(cfg, "SATOSHIT_HTF_RVOL_1H_MIN", 0.3))))
    result += fire * float(_cfg(cfg, "SATOSHIT_SCORE_BONUS", 30))
    masks["SATOSHIT"] = fire
    masks["SATOSHIT_VOTES"] = votes
    return result, masks


def stochastic_alignment_score_array(values, long_ok, short_ok, is_long):
    """Port native LTF/HTF alignment bonuses after the Satoshit branch."""
    long_ok = np.asarray(long_ok, dtype=bool).reshape(-1)
    short_ok = np.asarray(short_ok, dtype=bool).reshape(-1)
    n = long_ok.size
    if short_ok.size != n:
        raise ValueError("stochastic flags have different lengths")
    directions = {}
    for tf in ("1m", "3m", "15m", "1h", "4h", "D"):
        key = f"wt_bullish_{tf}"
        if key not in values:
            raise ValueError(f"missing required ASR alignment input {key}")
        directions[tf] = _float_column(values, key, n, 0.0) != 0
    ltf_count = np.zeros(n, dtype=np.int8)
    htf_count = np.zeros(n, dtype=np.int8)
    for tf in ("1m", "3m", "15m"):
        ltf_count += (directions[tf] if is_long else ~directions[tf]).astype(np.int8)
    for tf in ("1h", "4h"):
        htf_count += (directions[tf] if is_long else ~directions[tf]).astype(np.int8)
    ha_d = _text_column(values, "ha_D", n, "neutral")
    daily = ((ha_d == "green") | directions["D"]) if is_long else ((ha_d == "red") | ~directions["D"])
    htf_count += daily.astype(np.int8)
    ltf_aligned = ltf_count >= 2
    htf_aligned = htf_count >= 2
    side_stoch_ok = long_ok if is_long else short_ok
    delta = side_stoch_ok.astype(float) * 5.0 + ltf_aligned.astype(float) * 3.0 + htf_aligned.astype(float) * 5.0
    return delta, {"STOCH_SIDE_OK": side_stoch_ok, "LTF_ALIGNED": ltf_aligned, "HTF_ALIGNED": htf_aligned}


def hlr_rally_score_array(values, current_price, is_long, cfg, is_exit=False, score=None):
    """Port HLR_RALLY score, size multiplier, and hard-gate bypass state."""
    price = np.asarray(current_price, dtype=float).reshape(-1)
    n = price.size
    result = np.zeros(n, dtype=float) if score is None else np.asarray(score, dtype=float).reshape(-1).copy()
    if result.size != n:
        raise ValueError("score and current_price lengths differ")
    size_mult = np.ones(n, dtype=float)
    bypass = np.zeros(n, dtype=bool)
    tf_masks = {}
    if bool(_cfg(cfg, "HLR_RALLY_ENABLED", True)) and not is_exit:
        band = float(_cfg(cfg, "HLR_SMA_BAND_PCT", 0.03))
        off_points = float(_cfg(cfg, "HLR_OFF_SMA_PTS_FRAC", 0.5))
        off_size = float(_cfg(cfg, "HLR_OFF_SMA_SZ_FRAC", 0.7))
        bypass_min = float(_cfg(cfg, "HLR_BYPASS_MIN_TF_WEIGHT", 25))
        definitions = (
            ("3m", "low_3m", "low_3m_prev", "high_3m", "high_3m_prev", "sma_200_1m", "HLR_PTS_3M", 15, "HLR_SZ_3M", 1.2),
            ("15m", "low_15m", "low_15m_prev", "high_15m", "high_15m_prev", "sma_200_15m", "HLR_PTS_15M", 25, "HLR_SZ_15M", 1.5),
            ("1h", "low_1h", "low_1h_prev", "high_1h", "high_1h_prev", "sma_200_1h", "HLR_PTS_1H", 40, "HLR_SZ_1H", 2.0),
            ("4h", "low_4h", "low_4h_prev", "high_4h", "high_4h_prev", "sma_200_4h", "HLR_PTS_4H", 60, "HLR_SZ_4H", 3.5),
            ("D", "low_D", "low_D_prev", "high_D", "high_D_prev", "sma_200_D", "HLR_PTS_D", 90, "HLR_SZ_D", 6.0),
            ("W", "low_W", "low_W_prev", "high_W", "high_W_prev", "sma_200_W", "HLR_PTS_W", 130, "HLR_SZ_W", 10.0),
        )
        total_points = np.zeros(n, dtype=float)
        for tf, low_key, low_prev_key, high_key, high_prev_key, sma_key, pts_key, pts_default, size_key, size_default in definitions:
            low = _float_column(values, low_key, n, 0.0)
            low_prev = _float_column(values, low_prev_key, n, 0.0)
            high = _float_column(values, high_key, n, 0.0)
            high_prev = _float_column(values, high_prev_key, n, 0.0)
            sma = _float_column(values, sma_key, n, 0.0)
            points = float(_cfg(cfg, pts_key, pts_default))
            size = float(_cfg(cfg, size_key, size_default))
            structure = (low > 0) & (low_prev > 0) & (low > low_prev) if is_long else (high > 0) & (high_prev > 0) & (high < high_prev)
            near = (sma > 0) & (np.abs(price - sma) / np.where(sma > 0, sma, 1.0) <= band)
            points_add = np.where(near, points, np.trunc(points * off_points))
            size_add = np.where(near, size, size * off_size)
            total_points += np.where(structure, points_add, 0.0)
            size_mult = np.maximum(size_mult, np.where(structure, size_add, 1.0))
            bypass |= structure & (points_add >= bypass_min)
            tf_masks[tf] = structure
        result += np.where(total_points > 0, total_points, 0.0)
        size_mult = np.where(total_points > 0, np.minimum(size_mult, float(_cfg(cfg, "HLR_SZ_MAX", 10.0))), size_mult)
    return result, size_mult, bypass, tf_masks


def data_blind_gate_array(values, state):
    """Port the rate() 5050 gate; external gain state is needed only on blind rows."""
    k3 = _float_column(values, "k_3m", _size(values, "close"), 50.0)
    d3 = _float_column(values, "d_3m", k3.size, 50.0)
    k1 = _float_column(values, "k_1m", k3.size, 50.0)
    d1 = _float_column(values, "d_1m", k3.size, 50.0)
    blind = ((k3 == 50.0) & (d3 == 50.0)) | ((k1 == 50.0) & (d1 == 50.0))
    if not np.any(blind):
        return np.zeros(k3.size, dtype=bool), {"DATA_BLIND": blind}
    if "gain" not in state or "prev_gain" not in state:
        raise ValueError("5050 indicator rows require explicit gain and prev_gain state")
    gain = _float_column(state, "gain", k3.size, 0.0)
    prev_gain = _float_column(state, "prev_gain", k3.size, 0.0)
    allow = (gain < prev_gain)
    block = blind & ~allow
    return block, {"DATA_BLIND": blind, "DATA_BLIND_LOSING_POSITION_EXEMPTION": blind & allow}


def stoch_kx_score_array(values, state, is_long, is_exit=False, score=None):
    """NumPy port of `vec_paths.stoch_kx_scorer.score_stoch_kx` plus its ASR inputs."""
    n = _size(values, "close")
    price = _float_column(state, "current_price", n, 0.0)
    red_price = _float_column(state, "last_reduction_price", n, 0.0)
    since_red = _float_column(state, "minutes_since_reduction", n, 0.0)
    gain = _float_column(state, "gain", n, 0.0)
    lag = _float_column(state, "true_lag", n, 0.0)
    result = np.zeros(n, dtype=float) if score is None else np.asarray(score, dtype=float).reshape(-1).copy()
    if result.size != n:
        raise ValueError("score and KX inputs have different lengths")
    masks = {}
    delta = np.zeros(n, dtype=float)
    recent_reduction = ((price > red_price) if is_long else (price < red_price)) & (since_red < 35)
    delta += recent_reduction * 8.0
    masks["RECENT_REDUCTION"] = recent_reduction

    dc = {}
    for tf in ("D", "4h"):
        dc[f"basis_{tf}"] = _float_column(values, f"dc_basis_{tf}", n, 0.0)
        dc[f"low_{tf}"] = _float_column(values, f"dc_low_{tf}", n, 0.0)
        dc[f"high_{tf}"] = _float_column(values, f"dc_high_{tf}", n, 0.0)
        low = dc[f"low_{tf}"]
        width = np.zeros(n, dtype=float)
        np.divide(dc[f"high_{tf}"] - low, low, out=width, where=low > 0)
        dc[f"width_{tf}"] = width * 100.0
    if not is_exit:
        if is_long:
            pb_d = (red_price > 0) & (dc["low_D"] > 0) & (dc["width_D"] > 4.0) & (price > dc["low_D"]) & (price < dc["basis_D"])
            pb_4h = ~pb_d & (red_price > 0) & (dc["low_D"] > 0) & (dc["width_4h"] > 3.0) & (dc["low_4h"] > 0) & (price > dc["low_4h"]) & (price < dc["basis_4h"])
        else:
            pb_d = (red_price > 0) & (dc["width_D"] > 4.0) & (price < dc["high_D"]) & (price > dc["basis_D"])
            pb_4h = ~pb_d & (red_price > 0) & (dc["low_D"] > 0) & (dc["width_4h"] > 3.0) & (dc["high_4h"] > 0) & (price < dc["high_4h"]) & (price > dc["basis_4h"])
        delta += pb_d * 20.0 + pb_4h * 15.0
        masks["DC_REENTRY_PB_D"] = pb_d
        masks["DC_REENTRY_PB_4H"] = pb_4h

    def num(key, default=0.0):
        return _float_column(values, key, n, default)
    k1, d1 = num("k_1m", 50), num("d_1m", 50)
    k1p = num("k_1m_prev", 50)
    k3, d3, k3p = num("k_3m", 50), num("d_3m", 50), num("k_3m_prev", 50)
    k15, d15, k15p = num("k_15m", 50), num("d_15m", 50), num("k_15m_prev", 50)
    k1co = (k1 > d1) & (k1p <= d1) & (k1 < 30) & (lag < 10.0)
    k1cu = (k1 < d1) & (k1p >= d1) & (k1 > 70) & (lag < 10.0)
    k3co = (k3 > d3) & (k3p <= d3) & (k3 < 30)
    k3cu = (k3 < d3) & (k3p >= d3) & (k3 > 70)
    k15co = (k15 > d15) & (k15p <= d15) & (k15 < 30)
    k15cu = (k15 < d15) & (k15p >= d15) & (k15 > 70)
    cp3, cpp3 = num("crossover_price_3m"), num("crossover_price_previous_3m")
    cu3, cup3 = num("crossunder_price_3m"), num("crossunder_price_previous_3m")
    cp15, cpp15 = num("crossover_price_15m"), num("crossover_price_previous_15m")
    cu15, cup15 = num("crossunder_price_15m"), num("crossunder_price_previous_15m")
    fire = (is_long & k1co) | ((not is_long) & k1cu)
    favorable = (price > cp3) if is_long else (price < cu3)
    improved = (cp3 > cpp3) if is_long else (cu3 < cup3)
    delta += fire * 1.0 + (fire & favorable) * 2.0 + (fire & favorable & improved) * 3.0 + (fire & (since_red < 25)) * 3.0
    masks["K1_CROSS_FAVORABLE"] = fire
    fire = ((not is_long) & k1co) | (is_long & k1cu)
    favorable = (price < cp3) if is_long else (price > cu3)
    worsened = (cp3 < cpp3) if is_long else (cu3 > cup3)
    delta += fire * -1.0 + (fire & favorable) * -1.0 + (fire & favorable & worsened) * -4.0 + (fire & (gain < 0.1)) * -5.0
    masks["K1_CROSS_AGAINST"] = fire
    fire = (is_long & k3co) | ((not is_long) & k3cu)
    favorable = (price > cp3) if is_long else (price < cu3)
    improved = (cp3 > cpp3) if is_long else (cu3 < cup3)
    delta += fire * 4.0 + (fire & favorable) * 2.0 + (fire & favorable & improved) * 2.0 + (fire & (gain < 0.1)) * -8.0 + (fire & (since_red < 35)) * 3.0
    masks["K3_CROSS_FAVORABLE"] = fire
    fire = ((not is_long) & k3co) | (is_long & k3cu)
    favorable = (price < cp3) if is_long else (price > cu3)
    worsened = (cp3 < cpp3) if is_long else (cu3 > cup3)
    delta += fire * -3.0 + (fire & favorable) * -2.0 + (fire & favorable & worsened) * -3.0 + (fire & (gain < 0.1)) * -7.0
    masks["K3_CROSS_AGAINST"] = fire
    fire = (is_long & k15co) | ((not is_long) & k15cu)
    favorable = (price > cp15) if is_long else (price < cu15)
    improved = (cp15 > cpp15) if is_long else (cu15 < cup15)
    delta += fire * 7.0 + (fire & favorable) * 5.0 + (fire & favorable & improved) * 3.0 + (fire & (gain < 0.1)) * -9.0
    masks["K15_CROSS_FAVORABLE"] = fire
    fire = ((not is_long) & k15co) | (is_long & k15cu)
    favorable = (price < cp15) if is_long else (price > cu15)
    worsened = (cp15 < cpp15) if is_long else (cu15 > cup15)
    delta += fire * -9.0 + (fire & favorable) * -4.0 + (fire & favorable & worsened) * -4.0 + (fire & (gain < 0.1)) * -19.0
    masks["K15_CROSS_AGAINST"] = fire
    result += delta
    return result, masks


def all_tf_wt_entry_array(values, is_long, score=None):
    """Port native all-timeframe WaveTrend entry votes, vetoes, and penalties."""
    n = _size(values, "close")
    result = np.zeros(n, dtype=float) if score is None else np.asarray(score, dtype=float).reshape(-1).copy()
    if result.size != n:
        raise ValueError("score and WT entry inputs have different lengths")
    tfs = ("3m", "15m", "1h", "4h", "D")
    struct = {}
    for tf in tfs:
        pk = _float_column(values, f"wt_peak_{tf}", n, 0.0)
        pkp = _float_column(values, f"wt_peak_prev_{tf}", n, 0.0)
        tr = _float_column(values, f"wt_trough_{tf}", n, 0.0)
        trp = _float_column(values, f"wt_trough_prev_{tf}", n, 0.0)
        cv = _float_column(values, f"wt_cross_value_{tf}", n, 0.0)
        cvp = _float_column(values, f"wt_cross_prev_value_{tf}", n, 0.0)
        struct[tf] = {
            "ht": (tr > trp) & (tr != 0) & (trp != 0),
            "lp": (pk < pkp) & (pk != 0) & (pkp != 0),
            "cv_rising": (cv > cvp) & (cv != 0) & (cvp != 0),
            "cv_falling": (cv <= cvp) & (cv != 0) & (cvp != 0),
            "score": _float_column(values, f"wt_score_{tf}", n, 0.0),
            "vel": _float_column(values, f"wt_velocity_{tf}", n, 0.0),
            "div": _text_column(values, f"wt_divergence_{tf}", n, None),
            "sig": _text_column(values, f"wt_signal_{tf}", n, "NEUTRAL"),
        }
    if is_long:
        struct_votes = sum((struct[tf]["ht"] for tf in tfs), np.zeros(n, dtype=int))
        dir_votes = sum((struct[tf]["score"] > 0 for tf in tfs), np.zeros(n, dtype=int))
        cv_votes = sum((struct[tf]["cv_rising"] for tf in tfs), np.zeros(n, dtype=int))
        vel_ok = (struct["15m"]["vel"] > 0) | (struct["1h"]["vel"] > 0)
        div_bad = np.zeros(n, dtype=bool)
        for tf in ("15m", "1h", "4h"):
            div_bad |= struct[tf]["div"] == "bearish"
        sig_bad = np.zeros(n, dtype=bool)
        for tf in ("1h", "4h", "D"):
            sig_bad |= struct[tf]["sig"] == "SELL"
        anti = sum((struct[tf]["lp"] for tf in tfs), np.zeros(n, dtype=int))
    else:
        struct_votes = sum((struct[tf]["lp"] for tf in tfs), np.zeros(n, dtype=int))
        dir_votes = sum((struct[tf]["score"] < 0 for tf in tfs), np.zeros(n, dtype=int))
        cv_votes = sum((struct[tf]["cv_falling"] for tf in tfs), np.zeros(n, dtype=int))
        vel_ok = (struct["15m"]["vel"] < 0) | (struct["1h"]["vel"] < 0)
        div_bad = np.zeros(n, dtype=bool)
        for tf in ("15m", "1h", "4h"):
            div_bad |= struct[tf]["div"] == "bullish"
        sig_bad = np.zeros(n, dtype=bool)
        for tf in ("1h", "4h", "D"):
            sig_bad |= struct[tf]["sig"] == "BUY"
        anti = sum((struct[tf]["ht"] for tf in tfs), np.zeros(n, dtype=int))
    ltf = ("3m", "15m", "1h")
    htf = ("4h", "D")
    if is_long:
        dir_ltf = sum((struct[tf]["score"] > 0 for tf in ltf), np.zeros(n, dtype=int))
        dir_htf = sum((struct[tf]["score"] > 0 for tf in htf), np.zeros(n, dtype=int))
    else:
        dir_ltf = sum((struct[tf]["score"] < 0 for tf in ltf), np.zeros(n, dtype=int))
        dir_htf = sum((struct[tf]["score"] < 0 for tf in htf), np.zeros(n, dtype=int))
    veto_ltf = dir_ltf < 2
    veto_struct = ~veto_ltf & (struct_votes < 2)
    veto_div = ~veto_ltf & ~veto_struct & div_bad
    veto_vel = ~veto_ltf & ~veto_struct & ~veto_div & ~vel_ok
    penalty = np.where(dir_htf == 0, 10, np.where(dir_htf == 1, 4, 0))
    penalty = penalty + (anti >= 3) * 5 + sig_bad * 3
    passed = ~(veto_ltf | veto_struct | veto_div | veto_vel)
    result += passed * (struct_votes * 2 + cv_votes - penalty)
    return result, {
        "WT_DIR_LTF": veto_ltf,
        "WT_STRUCT": veto_struct,
        "WT_DIV": veto_div,
        "WT_VEL": veto_vel,
        "WT_PASSED": passed,
        "WT_STRUCT_VOTES": struct_votes,
        "WT_DIR_LTF_VOTES": dir_ltf,
        "WT_DIR_HTF_VOTES": dir_htf,
        "WT_CV_VOTES": cv_votes,
        "WT_ANTI_VOTES": anti,
        "WT_SIG_BAD": sig_bad,
    }
