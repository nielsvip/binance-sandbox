"""
scalp_v3_live.py — Live-side V3 scalp entry/exit. TREND-FOLLOWING (2026-04-26 rewrite).

Owner directive: "OPEN when rising and CLOSE when falling". No % gates anywhere.
Replaces the prior mean-reversion design (LONG on k<40 oversold, SHORT on k>85
overbought) which bled in trending markets — that's what motivated the rewrite.

Live limitations vs backtest:
- 1m bar OHLC not in metrics dict → use k_1m as K-only signal.
- 3m bar OHLC IS available via high_3m / low_3m / high_3m_prev / low_3m_prev.

Entry LONG (all required):
- 3m bar HH+HL (price rising)
- k_3m in upper half AND rising (k_3m > 50 AND k_3m > k_3m_prev)
- k_15m, k_1h aligned bullish (>= 50)
- WT 3m bullish (wt1_3m > wt2_3m)

Entry SHORT (mirror).

Exit LONG (any one fires close):
- 3m bar LL or LH (price turning down)
- WT 3m flipped bearish (wt1_3m < wt2_3m)
- k_3m crossed down through midline (k_3m < k_3m_prev AND k_3m < 50)

Exit SHORT (mirror).

NO % GATES: no ATR_TP, no PEAK_GIVEBACK, no STALL, no MAX_LOSS. Time-based
SCALP_V3_MAX_HOLD_MIN remains as a non-% scalp safety net.
"""
from __future__ import annotations
import time
from typing import Dict, Optional


def _sf(v, default=0.0):
    try:
        return float(v) if v is not None else default
    except Exception:
        return default


def check_scalp_v3_live_entry(symbol: str, position_key: str, indicators: Dict, price: float,
                              position, account_key: str, config) -> Optional[Dict]:
    """Returns {'side': 'LONG'|'SHORT', 'reason': str} on fire, else None."""
    if not indicators: return None
    if price <= 0: return None
    if position is not None:
        _amt = abs(_sf(getattr(position, 'positionAmt', 0), 0))
        if _amt > 0: return None
    k_3m = _sf(indicators.get('stoch_k_3m', indicators.get('k_3m', 50)), 50)
    k_3m_prev = _sf(indicators.get('k_3m_prev', k_3m), k_3m)
    k_15m = _sf(indicators.get('stoch_k_15m', 50), 50)
    k_1h = _sf(indicators.get('stoch_k_1h', 50), 50)
    high_3m = _sf(indicators.get('high_3m', 0), 0)
    low_3m = _sf(indicators.get('low_3m', 0), 0)
    high_3m_prev = _sf(indicators.get('high_3m_prev', 0), 0)
    low_3m_prev = _sf(indicators.get('low_3m_prev', 0), 0)
    wt1_3m = _sf(indicators.get('wt1_3m', 0), 0)
    wt2_3m = _sf(indicators.get('wt2_3m', 0), 0)
    if high_3m_prev <= 0 or low_3m_prev <= 0: return None
    bar_rising = (high_3m > high_3m_prev) and (low_3m > low_3m_prev)
    bar_falling = (high_3m < high_3m_prev) and (low_3m < low_3m_prev)
    k_rising = (k_3m > k_3m_prev) and (k_3m > 50)
    k_falling = (k_3m < k_3m_prev) and (k_3m < 50)
    wt_bull = wt1_3m > wt2_3m
    wt_bear = wt1_3m < wt2_3m
    htf_bull = (k_15m >= 50) and (k_1h >= 50)
    htf_bear = (k_15m <= 50) and (k_1h <= 50)
    side_mode = str(getattr(config, 'SCALP_V3_SIDE_MODE', 'BOTH')).upper()
    allow_long = side_mode in ('LONG_ONLY', 'BOTH')
    allow_short = side_mode in ('SHORT_ONLY', 'BOTH')
    if allow_long and bar_rising and k_rising and wt_bull and htf_bull:
        return {"side": "LONG", "reason": f"SCALP_V3_OPEN_LONG_TREND_k3m{k_3m:.0f}>{k_3m_prev:.0f}_k15m{k_15m:.0f}_k1h{k_1h:.0f}_wt3m{wt1_3m:.1f}>{wt2_3m:.1f}"}
    if allow_short and bar_falling and k_falling and wt_bear and htf_bear:
        return {"side": "SHORT", "reason": f"SCALP_V3_OPEN_SHORT_TREND_k3m{k_3m:.0f}<{k_3m_prev:.0f}_k15m{k_15m:.0f}_k1h{k_1h:.0f}_wt3m{wt1_3m:.1f}<{wt2_3m:.1f}"}
    return None


def check_scalp_v3_live_exit(position_key: str, indicators: Dict, price: float,
                             position, config) -> Optional[Dict]:
    """Returns {'reason': str} on fire, else None. Closes on technical reversal only — NO % gates."""
    if not indicators: return None
    if position is None: return None
    _amt = abs(_sf(getattr(position, 'positionAmt', 0), 0))
    if _amt <= 0: return None
    _reason = str(getattr(position, 'augment_reason', '') or '')
    if 'SCALP_V3_OPEN_' not in _reason: return None
    side = 'LONG' if _reason.find('_LONG_') >= 0 else 'SHORT' if _reason.find('_SHORT_') >= 0 else None
    if side is None: return None
    entry_price = _sf(getattr(position, 'entry_price', 0), 0)
    opened_at = _sf(getattr(position, 'opened_at', 0), 0)
    if entry_price <= 0 or price <= 0: return None
    now_ts = time.time()
    age_sec = now_ts - opened_at if opened_at > 0 else 0
    k_3m = _sf(indicators.get('stoch_k_3m', indicators.get('k_3m', 50)), 50)
    k_3m_prev = _sf(indicators.get('k_3m_prev', k_3m), k_3m)
    high_3m = _sf(indicators.get('high_3m', 0), 0)
    low_3m = _sf(indicators.get('low_3m', 0), 0)
    high_3m_prev = _sf(indicators.get('high_3m_prev', 0), 0)
    low_3m_prev = _sf(indicators.get('low_3m_prev', 0), 0)
    wt1_3m = _sf(indicators.get('wt1_3m', 0), 0)
    wt2_3m = _sf(indicators.get('wt2_3m', 0), 0)
    if high_3m_prev <= 0 or low_3m_prev <= 0: return None
    bar_on = bool(getattr(config, 'SCALP_V3_EXIT_BAR_REVERSAL_ENABLED', True))
    wt_on = bool(getattr(config, 'SCALP_V3_EXIT_WT_FLIP_ENABLED', True))
    k_on = bool(getattr(config, 'SCALP_V3_EXIT_K_CROSS_ENABLED', True))
    require_n = max(1, int(getattr(config, 'SCALP_V3_EXIT_REQUIRE_N_SIGNALS', 1)))
    if side == 'LONG':
        bar_falling = (low_3m < low_3m_prev) or (high_3m < high_3m_prev)
        wt_flip_bear = wt1_3m < wt2_3m
        k_cross_down = (k_3m < k_3m_prev) and (k_3m < 50)
        sigs = []
        if bar_on and bar_falling: sigs.append('BAR')
        if wt_on and wt_flip_bear: sigs.append('WT')
        if k_on and k_cross_down: sigs.append('K')
        if len(sigs) >= require_n:
            return {"reason": f"SCALP_V3_CLOSE_{'_'.join(sigs)}_LONG_n{len(sigs)}_k3m{k_3m:.0f}_wt3m{wt1_3m:.1f}/{wt2_3m:.1f}"}
    else:
        bar_rising = (high_3m > high_3m_prev) or (low_3m > low_3m_prev)
        wt_flip_bull = wt1_3m > wt2_3m
        k_cross_up = (k_3m > k_3m_prev) and (k_3m > 50)
        sigs = []
        if bar_on and bar_rising: sigs.append('BAR')
        if wt_on and wt_flip_bull: sigs.append('WT')
        if k_on and k_cross_up: sigs.append('K')
        if len(sigs) >= require_n:
            return {"reason": f"SCALP_V3_CLOSE_{'_'.join(sigs)}_SHORT_n{len(sigs)}_k3m{k_3m:.0f}_wt3m{wt1_3m:.1f}/{wt2_3m:.1f}"}
    max_hold_min = float(getattr(config, 'SCALP_V3_MAX_HOLD_MIN', 0.0) or 0.0)
    if max_hold_min > 0 and age_sec > max_hold_min * 60.0:
        return {"reason": f"SCALP_V3_CLOSE_MAX_HOLD_{side}_age{age_sec/60:.1f}m"}
    return None
