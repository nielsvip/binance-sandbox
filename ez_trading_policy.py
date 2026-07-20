"""
trading_policy.py — Shared Core Trading Philosophy
Imported by ez_manage, ez_positions_quick, and ez_positions_service to guarantee
identical decision-making across all three scripts.
"""
import logging
import math
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("trading_policy")

def _sf(v, default=50.0):
    try: return float(v) if v is not None and not (isinstance(v, float) and (math.isnan(v) or math.isinf(v))) else default
    except (TypeError, ValueError): return default

# ═══════════════════════════════════════════════════════════════════════════════
# 1. LONG/SHORT RATIO — Double the calculated ratio, gated by 2-of-3 LTF
# ═══════════════════════════════════════════════════════════════════════════════
def compute_applied_ratio(calculated_long_pct: float, calculated_short_pct: float, indicators: Dict[str, Any], is_for_long: bool) -> Tuple[float, float, bool]:
    """Returns (applied_long_pct, applied_short_pct, ratio_active).
    Ratio is ONLY applied when 2 of 3 k_1m/k_3m/k_15m agree with the direction.
    Formula: applied_long = 50 + (calculated_long - 50) * 2, clamped 10-90."""
    k_1m, d_1m = _sf(indicators.get('stoch_k_1m')), _sf(indicators.get('stoch_d_1m'))
    k_3m, d_3m = _sf(indicators.get('stoch_k_3m')), _sf(indicators.get('stoch_d_3m'))
    k_15m, d_15m = _sf(indicators.get('stoch_k_15m')), _sf(indicators.get('stoch_d_15m'))
    if is_for_long:
        ltf_agree = int(k_1m > d_1m) + int(k_3m > d_3m) + int(k_15m > d_15m)
    else:
        ltf_agree = int(k_1m < d_1m) + int(k_3m < d_3m) + int(k_15m < d_15m)
    if ltf_agree < 2:
        return calculated_long_pct, calculated_short_pct, False
    raw_applied = 50.0 + (calculated_long_pct - 50.0) * 2.0
    applied_long = max(10.0, min(90.0, raw_applied))
    applied_short = 100.0 - applied_long
    return applied_long, applied_short, True

# ═══════════════════════════════════════════════════════════════════════════════
# 2. ENTRY ALIGNMENT — 2-of-3 LTF + 2-of-3 HTF required for new entries
# ═══════════════════════════════════════════════════════════════════════════════
def check_entry_alignment(indicators: Dict[str, Any], is_long: bool) -> Tuple[bool, str]:
    """Returns (allowed, reason). Requires 2/3 LTF (k_1m/3m/15m) AND 2/3 HTF (1h/4h/D) aligned."""
    k_1m, d_1m = _sf(indicators.get('stoch_k_1m')), _sf(indicators.get('stoch_d_1m'))
    k_3m, d_3m = _sf(indicators.get('stoch_k_3m')), _sf(indicators.get('stoch_d_3m'))
    k_15m, d_15m = _sf(indicators.get('stoch_k_15m')), _sf(indicators.get('stoch_d_15m'))
    k_1h, d_1h = _sf(indicators.get('stoch_k_1h')), _sf(indicators.get('stoch_d_1h'))
    k_4h, d_4h = _sf(indicators.get('stoch_k_4h')), _sf(indicators.get('stoch_d_4h'))
    k_D, d_D = _sf(indicators.get('stoch_k_D')), _sf(indicators.get('stoch_d_D'))
    ha_D = str(indicators.get('ha_D', 'neutral'))
    if is_long:
        ltf = int(k_1m > d_1m) + int(k_3m > d_3m) + int(k_15m > d_15m)
        htf = int(k_1h > d_1h) + int(k_4h > d_4h) + int(ha_D == 'green' or k_D > d_D)
    else:
        ltf = int(k_1m < d_1m) + int(k_3m < d_3m) + int(k_15m < d_15m)
        htf = int(k_1h < d_1h) + int(k_4h < d_4h) + int(ha_D == 'red' or k_D < d_D)
    if ltf < 3: return False, f"LTF_ALIGN_FAIL({ltf}/3)"
    if htf < 3: return False, f"HTF_ALIGN_FAIL({htf}/3)"
    return True, f"ALIGNED_LTF({ltf}/3)_HTF({htf}/3)"

# ═══════════════════════════════════════════════════════════════════════════════
# 3. DC_POSITION — Multi-TF Donchian position as quantity multiplier
# ═══════════════════════════════════════════════════════════════════════════════
def _dc_pos_single(current_price: float, dc_high: float, dc_low: float) -> float:
    """0.0 = at low, 1.0 = at high, 0.5 = middle."""
    if dc_high <= 0 or dc_low <= 0 or dc_high <= dc_low: return 0.5
    return max(0.0, min(1.0, (current_price - dc_low) / (dc_high - dc_low)))

def compute_dc_position_multiplier(indicators: Dict[str, Any], current_price: float, is_long: bool) -> float:
    """Multi-TF dc_position ratio used as a quantity multiplier (0.2 – 5.0).
    For LONG: low dc_position = buy more (cheap). For SHORT: high dc_position = sell more (expensive)."""
    if current_price <= 0: return 1.0
    tfs = [('3m', 0.15), ('15m', 0.25), ('1h', 0.25), ('4h', 0.20), ('D', 0.15)]
    weighted_pos = 0.0
    total_weight = 0.0
    for tf, weight in tfs:
        dc_high = _sf(indicators.get(f'dc_high_{tf}'), 0.0)
        dc_low = _sf(indicators.get(f'dc_low_{tf}'), 0.0)
        if dc_high > 0 and dc_low > 0 and dc_high > dc_low:
            pos = _dc_pos_single(current_price, dc_high, dc_low)
            weighted_pos += pos * weight
            total_weight += weight
    if total_weight < 0.3: return 1.0
    avg_pos = weighted_pos / total_weight
    if is_long:
        mult = 1.0 + (1.0 - avg_pos) * 2.0
    else:
        mult = 1.0 + avg_pos * 2.0
    return max(0.2, min(7.0, mult))

# ═══════════════════════════════════════════════════════════════════════════════
# 4. HTF TREND — Higher-high/lower-low + Stochastic RSI D
# ═══════════════════════════════════════════════════════════════════════════════
def check_htf_trend(indicators: Dict[str, Any], current_price: float) -> Tuple[str, int]:
    """Returns (direction, score). direction is 'BULL'/'BEAR'/'NEUTRAL'. score is -10..+10."""
    bull = 0; bear = 0
    k_1h, d_1h = _sf(indicators.get('stoch_k_1h')), _sf(indicators.get('stoch_d_1h'))
    k_4h, d_4h = _sf(indicators.get('stoch_k_4h')), _sf(indicators.get('stoch_d_4h'))
    k_D, d_D = _sf(indicators.get('stoch_k_D')), _sf(indicators.get('stoch_d_D'))
    ha_1h, ha_4h, ha_D = str(indicators.get('ha_1h', 'neutral')), str(indicators.get('ha_4h', 'neutral')), str(indicators.get('ha_D', 'neutral'))
    dc_basis_1h, dc_basis_4h = _sf(indicators.get('dc_basis_1h'), 0), _sf(indicators.get('dc_basis_4h'), 0)
    sma_200_1h = _sf(indicators.get('sma_200_1h'), 0)
    high_1h, high_1h_prev = _sf(indicators.get('high_1h'), 0), _sf(indicators.get('high_1h_prev'), 0)
    low_1h, low_1h_prev = _sf(indicators.get('low_1h'), 0), _sf(indicators.get('low_1h_prev'), 0)
    high_4h, high_4h_prev = _sf(indicators.get('high_4h'), 0), _sf(indicators.get('high_4h_prev'), 0)
    low_4h, low_4h_prev = _sf(indicators.get('low_4h'), 0), _sf(indicators.get('low_4h_prev'), 0)
    high_D, low_D = _sf(indicators.get('high_D'), 0), _sf(indicators.get('low_D'), 0)
    if k_1h > d_1h: bull += 1
    else: bear += 1
    if k_4h > d_4h: bull += 1
    else: bear += 1
    if k_D > d_D: bull += 1
    else: bear += 1
    if ha_1h == 'green': bull += 1
    elif ha_1h == 'red': bear += 1
    if ha_4h == 'green': bull += 1
    elif ha_4h == 'red': bear += 1
    if ha_D == 'green': bull += 1
    elif ha_D == 'red': bear += 1
    if dc_basis_1h > 0 and current_price > dc_basis_1h: bull += 1
    elif dc_basis_1h > 0: bear += 1
    if dc_basis_4h > 0 and current_price > dc_basis_4h: bull += 1
    elif dc_basis_4h > 0: bear += 1
    if sma_200_1h > 0 and current_price > sma_200_1h: bull += 1
    elif sma_200_1h > 0: bear += 1
    if high_1h > 0 and high_1h_prev > 0 and low_1h > 0 and low_1h_prev > 0:
        if high_1h > high_1h_prev and low_1h > low_1h_prev: bull += 1
        elif high_1h < high_1h_prev and low_1h < low_1h_prev: bear += 1
    if high_4h > 0 and high_4h_prev > 0 and low_4h > 0 and low_4h_prev > 0:
        if high_4h > high_4h_prev and low_4h > low_4h_prev: bull += 1
        elif high_4h < high_4h_prev and low_4h < low_4h_prev: bear += 1
    score = bull - bear
    if score >= 6: return 'BULL', score
    elif score <= -6: return 'BEAR', score
    return 'NEUTRAL', score


# ═══════════════════════════════════════════════════════════════════════════════
# 4b. HTF DIRECTION GATE — Require positive trend (BULL for longs, BEAR for shorts)
# ═══════════════════════════════════════════════════════════════════════════════
def check_htf_trend_aligned(indicators: Dict[str, Any], current_price: float, is_long: bool) -> Tuple[bool, str]:
    """Returns (ok, reason). Requires BULL for longs, BEAR for shorts. Blocks NEUTRAL entries."""
    direction, score = check_htf_trend(indicators, current_price)
    if is_long:
        return direction == 'BULL', f"HTF_{direction}_score={score:.0f}"
    return direction == 'BEAR', f"HTF_{direction}_score={score:.0f}"

# ═══════════════════════════════════════════════════════════════════════════════
# 4c. ENTRY TRIGGER — Require concurrent 1m AND 3m stochastic crossover
# ═══════════════════════════════════════════════════════════════════════════════
def check_entry_trigger(indicators: Dict[str, Any], is_long: bool) -> Tuple[bool, str]:
    """Require concurrent 1m AND 3m stoch crossover (K crosses D).
    Backtest: 150 avg Sharpe vs 77 OR-mode (2x improvement). Falls back if 1m prev missing."""
    k_1m = _sf(indicators.get('stoch_k_1m'))
    d_1m = _sf(indicators.get('stoch_d_1m'))
    d_1m_prev = _sf(indicators.get('d_1m_prev'), d_1m)
    k_3m = _sf(indicators.get('stoch_k_3m'))
    d_3m = _sf(indicators.get('stoch_d_3m'))
    k_3m_prev = _sf(indicators.get('k_3m_prev'), k_3m)
    k_1m_prev_raw = indicators.get('k_1m_prev')
    if k_1m_prev_raw is None:
        return True, "TRIGGER_NO_1M_PREV_ALLOW"
    k_1m_prev = _sf(k_1m_prev_raw, k_1m)
    if is_long:
        co_1m = bool(indicators.get('stoch_crossover_1m', False)) or (k_1m > d_1m and k_1m_prev <= d_1m_prev)
        co_3m = bool(indicators.get('stoch_crossover_3m', False)) or (k_3m > d_3m and k_3m_prev <= d_3m)
        if co_1m and co_3m:
            return True, f"TRIGGER_1m_AND_3m_OK_k1={k_1m:.0f}_k3={k_3m:.0f}"
        return False, f"TRIGGER_BLOCKED_co1m={co_1m}_co3m={co_3m}_k1={k_1m:.0f}_k3={k_3m:.0f}"
    cu_1m = bool(indicators.get('stoch_crossunder_1m', False)) or (k_1m < d_1m and k_1m_prev >= d_1m_prev)
    cu_3m = bool(indicators.get('stoch_crossunder_3m', False)) or (k_3m < d_3m and k_3m_prev >= d_3m)
    if cu_1m and cu_3m:
        return True, f"TRIGGER_1m_AND_3m_OK_k1={k_1m:.0f}_k3={k_3m:.0f}"
    return False, f"TRIGGER_BLOCKED_cu1m={cu_1m}_cu3m={cu_3m}_k1={k_1m:.0f}_k3={k_3m:.0f}"

# ═══════════════════════════════════════════════════════════════════════════════
# 5. WINNER MOMENTUM — Must hold while 1m AND 3m are UP (vv for losers)
# ═══════════════════════════════════════════════════════════════════════════════
def check_winner_momentum(indicators: Dict[str, Any], is_long: bool, true_lag: float = 0.0) -> Tuple[bool, str]:
    """Returns (should_hold, reason). True = momentum still favorable — HOLD the winner.
    Only allows exit when stoch_15m is exhausted (>70 long / <30 short)
    AND 3m structure is declining (high_3m < high_3m_prev for long)."""
    k_15m = _sf(indicators.get('stoch_k_15m'))
    high_3m = _sf(indicators.get('high_3m'), 0)
    high_3m_prev = _sf(indicators.get('high_3m_prev'), 0)
    low_3m = _sf(indicators.get('low_3m'), 0)
    low_3m_prev = _sf(indicators.get('low_3m_prev'), 0)
    if true_lag > 15.0:
        return True, "1M_DATA_LAG_HOLD"
    if is_long:
        exhausted = k_15m > 80.0
        structure_breaking = high_3m > 0 and high_3m_prev > 0 and high_3m < high_3m_prev
    else:
        exhausted = k_15m < 20.0
        structure_breaking = low_3m > 0 and low_3m_prev > 0 and low_3m > low_3m_prev
    if exhausted and structure_breaking:
        return False, f"EXHAUSTED_k15m={k_15m:.0f}_STRUCT_BREAK"
    return True, f"HOLD_k15m={k_15m:.0f}_struct={'BREAKING' if structure_breaking else 'OK'}"

# ═══════════════════════════════════════════════════════════════════════════════
# 6. ENTRY VETTING — DC breakout + higher low/lower high
# ═══════════════════════════════════════════════════════════════════════════════
def check_entry_vetting(indicators: Dict[str, Any], current_price: float, is_long: bool) -> Tuple[bool, str]:
    """NO ENTRY unless price has broken recent DC highs on at least 3m tf
    OR low_15m > low_15m_prev, AND (stoch crossover on 1m/3m OR price > dc_high_3m).
    The ONLY entry should be a higher low on HTF AND higher volume (unless re-entry)."""
    dc_high_3m = _sf(indicators.get('dc_high_3m'), 0)
    dc_high_3m_ant = _sf(indicators.get('dc_high_3m_ant'), 0)
    dc_low_3m = _sf(indicators.get('dc_low_3m'), 0)
    dc_low_3m_ant = _sf(indicators.get('dc_low_3m_ant'), 0)
    low_15m = _sf(indicators.get('low_15m'), 0)
    low_15m_prev = _sf(indicators.get('low_15m_prev'), 0)
    high_15m = _sf(indicators.get('high_15m'), 0)
    high_15m_prev = _sf(indicators.get('high_15m_prev'), 0)
    stoch_co_1m = bool(indicators.get('stoch_crossover_1m', False))
    stoch_co_3m = bool(indicators.get('stoch_crossover_3m', False))
    stoch_cu_1m = bool(indicators.get('stoch_crossunder_1m', False))
    stoch_cu_3m = bool(indicators.get('stoch_crossunder_3m', False))
    k_1m, k_1m_prev = _sf(indicators.get('stoch_k_1m')), _sf(indicators.get('k_1m_prev'))
    k_3m, d_3m = _sf(indicators.get('stoch_k_3m')), _sf(indicators.get('stoch_d_3m'))
    if is_long:
        dc_breakout = dc_high_3m > 0 and dc_high_3m_ant > 0 and dc_high_3m > dc_high_3m_ant
        structure_ok = low_15m > 0 and low_15m_prev > 0 and low_15m > low_15m_prev
        trigger = stoch_co_1m or stoch_co_3m or (current_price > dc_high_3m and dc_high_3m > 0) or (k_1m > k_1m_prev and k_3m > d_3m)
    else:
        dc_breakout = dc_low_3m > 0 and dc_low_3m_ant > 0 and dc_low_3m < dc_low_3m_ant
        structure_ok = high_15m > 0 and high_15m_prev > 0 and high_15m < high_15m_prev
        trigger = stoch_cu_1m or stoch_cu_3m or (current_price < dc_low_3m and dc_low_3m > 0) or (k_1m < k_1m_prev and k_3m < d_3m)
    if not (dc_breakout or structure_ok): return False, f"NO_DC_BREAKOUT_OR_STRUCTURE"
    if not trigger: return False, f"NO_TRIGGER"
    return True, f"VETTED_dc={dc_breakout}_struct={structure_ok}"

# ═══════════════════════════════════════════════════════════════════════════════
# 7. MARKET REGIME — Crash/Jump detection for ratio suspension
# ═══════════════════════════════════════════════════════════════════════════════
def check_market_regime(market_index: float, indicators: Optional[Dict[str, Any]] = None) -> str:
    """Returns 'CRASH' (suspend longs), 'JUMP' (suspend shorts), or 'NORMAL'."""
    if market_index is None: return 'NORMAL'
    mi = _sf(market_index, 50.0)
    if mi <= 15.0: return 'CRASH'
    if mi >= 85.0: return 'JUMP'
    if indicators:
        sent = _sf(indicators.get('0market_sentiment_score'), 0.0)
        if sent < -60 and mi < 30: return 'CRASH'
        if sent > 60 and mi > 70: return 'JUMP'
    return 'NORMAL'

# ═══════════════════════════════════════════════════════════════════════════════
# 8. NO-LOSS EXIT — Exit BEFORE a loss materializes
# ═══════════════════════════════════════════════════════════════════════════════
def check_no_loss_exit(indicators: Dict[str, Any], current_price: float, entry_price: float, is_long: bool, gain_pct: float, prev_gain_pct: float, true_lag: float = 0.0) -> Tuple[bool, str]:
    """The 'never-losing' rule: exit BEFORE a loss materializes.
    TIGHTENED: only fires when gain is critically close to zero (<0.03%) AND all 3 LTF stochs
    are against AND 15m structure is breaking. Backtesting showed the looser version hurt returns."""
    if gain_pct <= 0.0: return False, "ALREADY_NEGATIVE"
    if gain_pct > 0.5: return False, "HEALTHY_GAIN"
    if gain_pct >= 0.03: return False, "HOLD"
    k_1m, d_1m = _sf(indicators.get('stoch_k_1m')), _sf(indicators.get('stoch_d_1m'))
    k_3m, d_3m = _sf(indicators.get('stoch_k_3m')), _sf(indicators.get('stoch_d_3m'))
    k_15m, d_15m = _sf(indicators.get('stoch_k_15m')), _sf(indicators.get('stoch_d_15m'))
    if is_long:
        all_against = k_1m < d_1m and k_3m < d_3m and k_15m < d_15m
    else:
        all_against = k_1m > d_1m and k_3m > d_3m and k_15m > d_15m
    if not all_against: return False, "HOLD"
    high_15m = _sf(indicators.get('high_15m'), 0)
    high_15m_prev = _sf(indicators.get('high_15m_prev'), 0)
    low_15m = _sf(indicators.get('low_15m'), 0)
    low_15m_prev = _sf(indicators.get('low_15m_prev'), 0)
    if is_long:
        struct_break = high_15m > 0 and high_15m_prev > 0 and high_15m < high_15m_prev
    else:
        struct_break = low_15m > 0 and low_15m_prev > 0 and low_15m > low_15m_prev
    if struct_break:
        return True, f"NO_LOSS_EXIT_CRITICAL_gain={gain_pct:.3f}%_ALL_TF_AGAINST"
    return False, "HOLD"

# ═══════════════════════════════════════════════════════════════════════════════
# 9. RE-ENTRY ELIGIBILITY — Monitor closed trades for re-entry
# ═══════════════════════════════════════════════════════════════════════════════
def check_reentry_eligible(indicators: Dict[str, Any], current_price: float, last_exit_price: float, is_long: bool, time_since_exit_min: float) -> Tuple[bool, str]:
    """Re-entry: closed trade must be monitored for reentry at better price if rally still in place.
    NOT when 15m is making lower low / lower high. Resort to breakout entries when above exit price."""
    if last_exit_price <= 0 or time_since_exit_min > 1200: return False, "NO_EXIT_DATA_OR_TOO_OLD"
    low_15m = _sf(indicators.get('low_15m'), 0)
    low_15m_prev = _sf(indicators.get('low_15m_prev'), 0)
    high_15m = _sf(indicators.get('high_15m'), 0)
    high_15m_prev = _sf(indicators.get('high_15m_prev'), 0)
    k_1m, d_1m = _sf(indicators.get('stoch_k_1m')), _sf(indicators.get('stoch_d_1m'))
    k_3m, d_3m = _sf(indicators.get('stoch_k_3m')), _sf(indicators.get('stoch_d_3m'))
    k_15m, d_15m = _sf(indicators.get('stoch_k_15m')), _sf(indicators.get('stoch_d_15m'))
    if is_long:
        rally_dead = low_15m > 0 and low_15m_prev > 0 and low_15m < low_15m_prev and high_15m > 0 and high_15m_prev > 0 and high_15m < high_15m_prev
        if rally_dead: return False, "15M_LOWER_LOW_AND_LOWER_HIGH"
        better_price = current_price < last_exit_price
        breakout = current_price > last_exit_price and k_1m > d_1m and k_3m > d_3m
        momentum_ok = k_1m > d_1m or k_3m > d_3m
    else:
        rally_dead = high_15m > 0 and high_15m_prev > 0 and high_15m > high_15m_prev and low_15m > 0 and low_15m_prev > 0 and low_15m > low_15m_prev
        if rally_dead: return False, "15M_HIGHER_HIGH_AND_HIGHER_LOW"
        better_price = current_price > last_exit_price
        breakout = current_price < last_exit_price and k_1m < d_1m and k_3m < d_3m
        momentum_ok = k_1m < d_1m or k_3m < d_3m
    if better_price and momentum_ok: return True, f"REENTRY_BETTER_PRICE"
    if breakout: return True, f"REENTRY_BREAKOUT_ABOVE_EXIT"
    return False, "NO_REENTRY_CONDITION"

# ═══════════════════════════════════════════════════════════════════════════════
# 10. SCALPING — Only with HTF alignment + good dc_position
# ═══════════════════════════════════════════════════════════════════════════════
def check_scalp_eligible(indicators: Dict[str, Any], current_price: float, is_long: bool) -> Tuple[bool, str]:
    """Scalping ONLY with HTF alignment and a good dc_position."""
    aligned, align_reason = check_entry_alignment(indicators, is_long)
    if not aligned: return False, f"SCALP_BLOCKED_{align_reason}"
    dc_high_15m = _sf(indicators.get('dc_high_15m'), 0)
    dc_low_15m = _sf(indicators.get('dc_low_15m'), 0)
    if dc_high_15m <= 0 or dc_low_15m <= 0 or dc_high_15m <= dc_low_15m: return True, "NO_DC_DATA_ALLOW"
    dc_pos = _dc_pos_single(current_price, dc_high_15m, dc_low_15m)
    if is_long and dc_pos > 0.85: return False, f"SCALP_BLOCKED_DC_TOO_HIGH({dc_pos:.2f})"
    if not is_long and dc_pos < 0.15: return False, f"SCALP_BLOCKED_DC_TOO_LOW({dc_pos:.2f})"
    return True, f"SCALP_OK_dc={dc_pos:.2f}"
