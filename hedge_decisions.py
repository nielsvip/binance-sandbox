"""
hedge_decisions.py — Pure-function hedge decision module.

Owner directive 2026-04-26: sandbox/paper must call the SAME functions live calls,
otherwise sandbox PnL is silent on the real bleeders. Live close/picker logic was
duplicated across 4 sites in ez_manage.py + ez_positions_quick.py — this module
is the single source of truth.

Functions:
- should_close_hedge_wt3m1h(indicators, position_is_long, hedge_gain, config) -> dict | None
    Decision for HEDGE_CLOSE_WT3M1H_PRE_GATE / PP_ABS / ABS / KILL_REVERSING_WT.
    Returns the decision dict on fire, None when holding.
    Honors HEDGE_WT_CLOSE_REQUIRE_NONNEG_GAIN: when True, gain<0 → hold.

- score_hedge_candidate(data, price, config) -> float
    The _quick_hedge_rank score (positive = LONG candidate, negative = SHORT, 0 = reject).
    Honors HEDGE_DC_RESISTANCE_GATE_ENABLED, HEDGE_WT_VEL_GATE_ENABLED, HEDGE_STRICT_WT_ALL_TFS_ENABLED.

Both are pure: no Redis, no async, no logging side effects.
"""
from __future__ import annotations
from typing import Dict, Optional


def _sf(v, default=0.0):
    try:
        return float(v) if v is not None else default
    except Exception:
        return default


_HEDGE_TF_LIST = ('3m', '15m', '1h', '4h', 'D')


def _wt_against_tf(indicators: Dict, tf: str, position_is_long: bool):
    """Returns: True/False if data present, None if missing for this TF."""
    w1 = _sf(indicators.get(f'wt1_{tf}'), 0)
    w2 = _sf(indicators.get(f'wt2_{tf}'), 0)
    if w1 == 0 and w2 == 0:
        return None
    return (position_is_long and w1 < w2) or ((not position_is_long) and w1 > w2)


def should_close_hedge_wt3m1h(indicators: Dict, position_is_long: bool, hedge_gain: float, config) -> Optional[Dict]:
    """Configurable hedge close decision (formerly hardcoded WT 3m+1h).

    Mode controlled by HEDGE_CLOSE_MODE (default 'wt_3m_1h' = legacy behavior):
        'wt_3m'           : WT 3m only (single TF — fastest signal)
        'wt_3m_15m'       : WT 3m AND 15m
        'wt_3m_1h'        : WT 3m AND 1h (LEGACY default — was hardcoded)
        'wt_3m_15m_1h'    : WT 3m AND 15m AND 1h (3-TF strict)
        'wt_3m_15m_htf1'  : WT 3m AND 15m AND 1of(1h, 4h, D)
        'wt_3m_15m_htf2'  : WT 3m AND 15m AND 2of(1h, 4h, D)
        'wt_3m_15m_htf3'  : WT 3m AND 15m AND ALL(1h, 4h, D) — 5-TF strict
        'wt_dc_score'     : wt_dc_exit_scorer.score_exit() >= HEDGE_CLOSE_WT_DC_THRESHOLD

    Returns:
        {'fire': True,  'reason': str, 'wt': {...}}  → close
        {'fire': False, 'reason': str, 'wt': {...}}  → noloss-hold (signal fired but gain<0)
        None                                          → no decision (data missing or criteria not met)
    """
    if not indicators:
        return None
    mode = str(getattr(config, 'HEDGE_CLOSE_MODE', 'wt_3m_1h')).lower().strip()
    against = {tf: _wt_against_tf(indicators, tf, position_is_long) for tf in _HEDGE_TF_LIST}

    fired_reason = None
    if mode == 'wt_3m':
        if against['3m'] is None: return None
        if not against['3m']: return None
        fired_reason = 'WT_3M_AGAINST'
    elif mode == 'wt_3m_15m':
        if against['3m'] is None or against['15m'] is None: return None
        if not (against['3m'] and against['15m']): return None
        fired_reason = 'WT_3M_15M_AGAINST'
    elif mode == 'wt_3m_15m_1h':
        if any(against[t] is None for t in ('3m', '15m', '1h')): return None
        if not (against['3m'] and against['15m'] and against['1h']): return None
        fired_reason = 'WT_3M_15M_1H_AGAINST'
    elif mode in ('wt_3m_15m_htf1', 'wt_3m_15m_htf2', 'wt_3m_15m_htf3'):
        if against['3m'] is None or against['15m'] is None: return None
        if not (against['3m'] and against['15m']): return None
        htf_count = sum(1 for tf in ('1h', '4h', 'D') if against[tf] is True)
        need = {'wt_3m_15m_htf1': 1, 'wt_3m_15m_htf2': 2, 'wt_3m_15m_htf3': 3}[mode]
        if htf_count < need: return None
        fired_reason = f'WT_3M_15M_HTF{need}_AGAINST'
    elif mode == 'wt_dc_score':
        try:
            from wt_dc_exit_scorer import score_exit
            score, score_reason = score_exit(indicators, position_is_long, 0.0, config)
            threshold = float(getattr(config, 'HEDGE_CLOSE_WT_DC_THRESHOLD', 25.0))
            if score < threshold: return None
            fired_reason = f'WT_DC_SCORE_{score:.0f}>={threshold:.0f}'
        except Exception:
            return None
    else:  # 'wt_3m_1h' (LEGACY default) and any unknown
        if against['3m'] is None or against['1h'] is None: return None
        if not (against['3m'] and against['1h']): return None
        fired_reason = 'WT3M1H_AGAINST'

    wt_block = {f'wt1_{tf}': _sf(indicators.get(f'wt1_{tf}'), 0) for tf in _HEDGE_TF_LIST}
    wt_block.update({f'wt2_{tf}': _sf(indicators.get(f'wt2_{tf}'), 0) for tf in _HEDGE_TF_LIST})
    wt_block['mode'] = mode
    if bool(getattr(config, 'HEDGE_WT_CLOSE_REQUIRE_NONNEG_GAIN', True)) and hedge_gain < 0:
        return {'fire': False, 'reason': 'NOLOSS_HOLD', 'wt': wt_block, 'gain': hedge_gain}
    return {'fire': True, 'reason': fired_reason, 'wt': wt_block, 'gain': hedge_gain}


def score_hedge_candidate(data: Dict, price: float, config) -> float:
    """Pure-function _quick_hedge_rank score.

    Positive return = LONG hedge candidate, negative = SHORT, 0 = reject.
    Mirrors the existing inline logic in RatingRegistry._quick_hedge_rank exactly,
    so live and sandbox produce identical scores from identical data.
    """
    if not data or price <= 0:
        return 0
    sma200_d = _sf(data.get('sma_200_D', 0), 0)
    dc_basis_d = _sf(data.get('dc_basis_D', 0), 0)
    dc_basis_d_ant = _sf(data.get('dc_basis_D_ant', 0), 0)
    ha_4h = str(data.get('ha_4h', '')).lower()
    k_4h = _sf(data.get('stoch_k_4h', 50), 50)
    daily_bear = (sma200_d > 0 and price < sma200_d * 0.99) or (dc_basis_d > 0 and dc_basis_d_ant > 0 and dc_basis_d < dc_basis_d_ant and ha_4h == 'red' and k_4h < 30)
    daily_bull = (sma200_d > 0 and price > sma200_d * 1.01) or (dc_basis_d > 0 and dc_basis_d_ant > 0 and dc_basis_d > dc_basis_d_ant and ha_4h == 'green' and k_4h > 70)
    k3 = _sf(data.get('stoch_k_3m', 50), 50)
    d3 = _sf(data.get('stoch_d_3m', 50), 50)
    if k3 == 50.0 and d3 == 50.0:
        return 0
    wt1_3m = _sf(data.get('wt1_3m', 0), 0); wt2_3m = _sf(data.get('wt2_3m', 0), 0)
    wt1_15m = _sf(data.get('wt1_15m', 0), 0); wt2_15m = _sf(data.get('wt2_15m', 0), 0)
    wt1_1h = _sf(data.get('wt1_1h', 0), 0); wt2_1h = _sf(data.get('wt2_1h', 0), 0)
    wt1_4h = _sf(data.get('wt1_4h', 0), 0); wt2_4h = _sf(data.get('wt2_4h', 0), 0)
    wt1_D = _sf(data.get('wt1_D', 0), 0); wt2_D = _sf(data.get('wt2_D', 0), 0)
    wt_score_3m = _sf(data.get('wt_score_3m', 0), 0)
    wt_score_15m = _sf(data.get('wt_score_15m', 0), 0)
    wt_score_1h = _sf(data.get('wt_score_1h', 0), 0)
    diff_3m = wt1_3m - wt2_3m; diff_15m = wt1_15m - wt2_15m; diff_1h = wt1_1h - wt2_1h; diff_4h = wt1_4h - wt2_4h; diff_D = wt1_D - wt2_D
    wt_raw = diff_3m * 0.5 + diff_15m * 1.0 + diff_1h * 2.0 + diff_4h * 3.0 + diff_D * 2.0
    wt_raw += wt_score_3m * 0.3 + wt_score_15m * 0.5 + wt_score_1h * 0.8
    k15 = _sf(data.get('stoch_k_15m', 50), 50); d15 = _sf(data.get('stoch_d_15m', 50), 50)
    k1h = _sf(data.get('stoch_k_1h', 50), 50); d1h = _sf(data.get('stoch_d_1h', 50), 50)
    d4h = _sf(data.get('stoch_d_4h', 50), 50)
    stoch_raw = (k3 - d3) * 0.5 + (k15 - d15) * 1.0 + (k1h - d1h) * 2.0 + (k_4h - d4h) * 3.0
    composite = wt_raw * 3.0 + stoch_raw * 1.0
    score = composite / 30.0
    if abs(score) < 10:
        return 0
    dc_gate_on = bool(getattr(config, 'HEDGE_DC_RESISTANCE_GATE_ENABLED', True))
    wt_gate_on = bool(getattr(config, 'HEDGE_WT_VEL_GATE_ENABLED', True))
    dc_long_th = float(getattr(config, 'HEDGE_DC_LONG_REJECT_DCP', 0.85))
    dc_short_th = float(getattr(config, 'HEDGE_DC_SHORT_REJECT_DCP', 0.15))
    dcp_15m = _sf(data.get('dc_position_15m', 0.5), 0.5)
    dcp_1h = _sf(data.get('dc_position_1h', 0.5), 0.5)
    dcp_4h = _sf(data.get('dc_position_4h', 0.5), 0.5)
    dch_1h = _sf(data.get('dc_high_1h', 0), 0); dcl_1h = _sf(data.get('dc_low_1h', 0), 0)
    dch_4h = _sf(data.get('dc_high_4h', 0), 0); dcl_4h = _sf(data.get('dc_low_4h', 0), 0)
    wtv_3m = _sf(data.get('wt_velocity_3m', 0), 0)
    wtv_1h = _sf(data.get('wt_velocity_1h', 0), 0)
    wtv_4h = _sf(data.get('wt_velocity_4h', 0), 0)
    strict_on = bool(getattr(config, 'HEDGE_STRICT_WT_ALL_TFS_ENABLED', True))
    strict_min = int(getattr(config, 'HEDGE_STRICT_WT_MIN_TFS_AGAINST', 4))
    wt_pairs = ((wt1_3m, wt2_3m), (wt1_15m, wt2_15m), (wt1_1h, wt2_1h), (wt1_4h, wt2_4h), (wt1_D, wt2_D))
    if score > 0:
        if daily_bear:
            return 0
        if dc_gate_on:
            if dcp_1h >= dc_long_th or dcp_4h >= dc_long_th or dcp_15m >= max(dc_long_th + 0.07, 0.92):
                return 0
            if dch_1h > 0 and price >= dch_1h * 0.995:
                return 0
            if dch_4h > 0 and price >= dch_4h * 0.995:
                return 0
        if wt_gate_on:
            if wtv_1h <= 0 and wtv_4h <= 0:
                return 0
            if wtv_3m < -1.0:
                return 0
        if strict_on:
            aligned_long = sum(1 for w1, w2 in wt_pairs if (w1 != 0 or w2 != 0) and w1 > w2)
            if aligned_long < strict_min:
                return 0
        return min(round(score, 1), 30)
    else:
        if daily_bull:
            return 0
        if dc_gate_on:
            if dcp_1h <= dc_short_th or dcp_4h <= dc_short_th or dcp_15m <= min(dc_short_th - 0.07, 0.08):
                return 0
            if dcl_1h > 0 and price <= dcl_1h * 1.005:
                return 0
            if dcl_4h > 0 and price <= dcl_4h * 1.005:
                return 0
        if wt_gate_on:
            if wtv_1h >= 0 and wtv_4h >= 0:
                return 0
            if wtv_3m > 1.0:
                return 0
        if strict_on:
            aligned_short = sum(1 for w1, w2 in wt_pairs if (w1 != 0 or w2 != 0) and w1 < w2)
            if aligned_short < strict_min:
                return 0
        return max(round(score, 1), -30)
