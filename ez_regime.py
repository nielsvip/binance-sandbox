"""
Market Regime Detection — Adaptive Trading Modes

Detects TRENDING_UP / TRENDING_DOWN / RANGING per symbol using existing indicators.
Returns regime-adapted parameters for entry/exit scoring, sizing, and slot management.

Uses: ADX + Choppiness (40%), DC Width (25%), WT Alignment (25%), SMA200 Slope (10%)
All from 1h/4h/D timeframes only — no LTF noise.

Usage:
    from ez_regime import get_symbol_regime, get_regime_params
    regime = get_symbol_regime(ind, symbol)  # {'mode': 'RANGING', 'score': -5.2, ...}
    params = get_regime_params(regime['mode'], account_key, config)
"""

import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger("ez_regime")

REGIME_TRENDING_UP = "TRENDING_UP"
REGIME_TRENDING_DOWN = "TRENDING_DOWN"
REGIME_RANGING = "RANGING"

_regime_states: Dict[str, Dict[str, Any]] = {}


def _sf(val, default=0.0):
    if val is None:
        return default
    try:
        v = float(val)
        return v if v == v else default  # NaN check
    except (TypeError, ValueError):
        return default


def compute_regime_score(ind: Dict) -> float:
    adx_1h = _sf(ind.get('adx_1h'), 0)
    adx_4h = _sf(ind.get('adx_4h'), 0)
    chop_1h = _sf(ind.get('choppiness_1h'), 50)
    chop_4h = _sf(ind.get('choppiness_4h'), 50)
    dc_width_1h = _sf(ind.get('dc_width_1h'), 0)
    dc_width_4h = _sf(ind.get('dc_width_4h'), 0)
    dc_width_D = _sf(ind.get('dc_width_D'), 0)
    dc_pos_1h = _sf(ind.get('dc_position_1h'), 0.5)
    dc_pos_4h = _sf(ind.get('dc_position_4h'), 0.5)
    dc_pos_D = _sf(ind.get('dc_position_D'), 0.5)
    wt_bull_1h = 1 if _sf(ind.get('wt_bullish_1h'), 0) > 0.5 else 0
    wt_bull_4h = 1 if _sf(ind.get('wt_bullish_4h'), 0) > 0.5 else 0
    wt_bull_D = 1 if _sf(ind.get('wt_bullish_D'), 0) > 0.5 else 0
    wt_vel_1h = _sf(ind.get('wt_velocity_1h'), 0)
    wt_vel_4h = _sf(ind.get('wt_velocity_4h'), 0)
    wt_vel_D = _sf(ind.get('wt_velocity_D'), 0)
    sma200_1h = _sf(ind.get('sma_200_1h'), 0)
    sma200_1h_prev = _sf(ind.get('sma_200_1h_prev'), 0)
    sma200_D = _sf(ind.get('sma_200_D'), 0)
    sma200_D_prev = _sf(ind.get('sma_200_D_prev'), 0)
    # --- ADX + Choppiness component (weight: 40%) ---
    adx_avg = adx_1h * 0.6 + adx_4h * 0.4
    chop_avg = chop_1h * 0.6 + chop_4h * 0.4
    wt_direction = 1.0 if wt_bull_1h else -1.0
    if adx_avg >= 25 and chop_avg <= 45:
        trend_strength = min(100, (adx_avg - 15) * 5)
        adx_component = trend_strength * wt_direction
    elif adx_avg <= 20 and chop_avg >= 55:
        adx_component = 0.0
    else:
        adx_component = (adx_avg - 22.5) * 4 * wt_direction
    # --- DC Width component (weight: 25%) ---
    dc_avg = dc_width_1h * 0.5 + dc_width_4h * 0.3 + dc_width_D * 0.2
    dc_pos_avg = dc_pos_1h * 0.5 + dc_pos_4h * 0.3 + dc_pos_D * 0.2
    if dc_avg > 5.0:
        dc_component = (dc_pos_avg - 0.5) * 200
    elif dc_avg < 2.0:
        dc_component = 0.0
    else:
        dc_component = (dc_pos_avg - 0.5) * 100 * (dc_avg - 2.0) / 3.0
    # --- WT Alignment component (weight: 25%) ---
    wt_bull_count = wt_bull_1h + wt_bull_4h + wt_bull_D
    wt_vel_avg = wt_vel_1h * 0.5 + wt_vel_4h * 0.3 + wt_vel_D * 0.2
    if wt_bull_count == 3:
        wt_component = min(100, 60 + abs(wt_vel_avg) * 5)
    elif wt_bull_count == 0:
        wt_component = max(-100, -60 - abs(wt_vel_avg) * 5)
    else:
        wt_component = (wt_bull_count - 1.5) * 30
    # --- SMA200 Slope component (weight: 10%) ---
    sma_slope_1h = (sma200_1h - sma200_1h_prev) / sma200_1h * 10000 if sma200_1h > 0 else 0
    sma_slope_D = (sma200_D - sma200_D_prev) / sma200_D * 10000 if sma200_D > 0 else 0
    sma_component = max(-100, min(100, (sma_slope_1h * 0.6 + sma_slope_D * 0.4) * 20))
    # --- Weighted sum ---
    score = adx_component * 0.40 + dc_component * 0.25 + wt_component * 0.25 + sma_component * 0.10
    return max(-100, min(100, score))


def classify_regime(score: float, current_mode: str, bars_in_current: int, config=None) -> str:
    enter_thresh = getattr(config, 'REGIME_ENTER_TRENDING_THRESHOLD', 30.0) if config else 30.0
    exit_thresh = getattr(config, 'REGIME_EXIT_TRENDING_THRESHOLD', 15.0) if config else 15.0
    min_dwell = int(getattr(config, 'REGIME_MIN_DWELL_BARS', 16) if config else 16)
    if bars_in_current < min_dwell:
        return current_mode
    if current_mode == REGIME_RANGING:
        if score > enter_thresh:
            return REGIME_TRENDING_UP
        if score < -enter_thresh:
            return REGIME_TRENDING_DOWN
        return REGIME_RANGING
    elif current_mode == REGIME_TRENDING_UP:
        if score < exit_thresh:
            return REGIME_RANGING
        return REGIME_TRENDING_UP
    elif current_mode == REGIME_TRENDING_DOWN:
        if score > -exit_thresh:
            return REGIME_RANGING
        return REGIME_TRENDING_DOWN
    return REGIME_RANGING


def get_symbol_regime(ind: Dict, symbol: str, config=None) -> Dict[str, Any]:
    score = compute_regime_score(ind)
    state = _regime_states.get(symbol)
    if state is None:
        state = {'mode': REGIME_RANGING, 'score': score, 'bars_in_current': 0, 'last_update': time.time()}
        _regime_states[symbol] = state
    state['bars_in_current'] += 1
    new_mode = classify_regime(score, state['mode'], state['bars_in_current'], config)
    if new_mode != state['mode']:
        logger.info(f"[REGIME] {symbol}: {state['mode']} → {new_mode} (score={score:.1f}, bars={state['bars_in_current']})")
        state['mode'] = new_mode
        state['bars_in_current'] = 0
    state['score'] = score
    state['last_update'] = time.time()
    return {'mode': state['mode'], 'score': score, 'bars_in_current': state['bars_in_current']}


def get_market_regime(ind_btc: Optional[Dict] = None, all_regime_scores: Optional[list] = None) -> str:
    if ind_btc:
        btc_score = compute_regime_score(ind_btc)
    else:
        btc_score = 0
    if all_regime_scores:
        import statistics
        median_score = statistics.median(all_regime_scores)
    else:
        median_score = 0
    market_score = btc_score * 0.5 + median_score * 0.5
    if market_score > 30:
        return REGIME_TRENDING_UP
    elif market_score < -30:
        return REGIME_TRENDING_DOWN
    return REGIME_RANGING


def get_regime_params(regime_mode: str, account_key: str, config) -> Dict[str, Any]:
    if regime_mode == REGIME_RANGING:
        return {
            'noloss_min': getattr(config, 'REGIME_RANGING_NOLOSS_MIN', 0.05),
            'exit_gain_min': getattr(config, 'REGIME_RANGING_EXIT_GAIN_MIN', 0.15),
            'min_hold_bars': getattr(config, 'REGIME_RANGING_MIN_HOLD_BARS', 8),
            'wt_reduce_frac_low': getattr(config, 'REGIME_RANGING_WT_REDUCE_FRAC_LOW', 0.40),
            'wt_reduce_frac_med': getattr(config, 'REGIME_RANGING_WT_REDUCE_FRAC_MED', 0.60),
            'wt_exit_vel': getattr(config, 'REGIME_RANGING_WT_EXIT_VEL', -3.0),
            'k_zone_bonus': getattr(config, 'REGIME_RANGING_K_ZONE_BONUS', 40),
            'dc_breakout_score': getattr(config, 'REGIME_RANGING_DC_BREAKOUT_SCORE', 0),
            'position_size_mult': getattr(config, 'REGIME_RANGING_POSITION_SIZE_MULT', 0.5),
            'slot_reserve_pct': getattr(config, 'REGIME_RANGING_SLOT_RESERVE_PCT', 0.60),
            'reentry_size_mult': getattr(config, 'REGIME_RANGING_REENTRY_SIZE_MULT', 1.0),
        }
    else:  # TRENDING_UP or TRENDING_DOWN
        return {
            'noloss_min': getattr(config, 'REGIME_TRENDING_NOLOSS_MIN', 0.50),
            'exit_gain_min': getattr(config, 'REGIME_TRENDING_EXIT_GAIN_MIN', 2.0),
            'min_hold_bars': getattr(config, 'REGIME_TRENDING_MIN_HOLD_BARS', 48),
            'wt_reduce_frac_low': getattr(config, 'REGIME_TRENDING_WT_REDUCE_FRAC_LOW', 0.10),
            'wt_reduce_frac_med': getattr(config, 'REGIME_TRENDING_WT_REDUCE_FRAC_MED', 0.15),
            'wt_exit_vel': getattr(config, 'REGIME_TRENDING_WT_EXIT_VEL', -12.0),
            'k_zone_bonus': getattr(config, 'REGIME_TRENDING_K_ZONE_BONUS', 15),
            'dc_breakout_score': getattr(config, 'REGIME_TRENDING_DC_BREAKOUT_SCORE', 30),
            'position_size_mult': getattr(config, 'REGIME_TRENDING_POSITION_SIZE_MULT', 1.5),
            'slot_reserve_pct': getattr(config, 'REGIME_TRENDING_SLOT_RESERVE_PCT', 0.40),
            'reentry_size_mult': getattr(config, 'REGIME_TRENDING_REENTRY_SIZE_MULT', 2.0),
        }


def reset_states():
    _regime_states.clear()
