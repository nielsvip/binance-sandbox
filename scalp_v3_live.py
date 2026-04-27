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
    # 2026-04-27 LIVE PROXY for "3m bar HH+HL/LL+LH" — high_3m/low_3m are BACKTEST-ONLY fields,
    # not in live hot_metrics, so the original gate `if high_3m_prev <= 0: return None` always
    # tripped, dead-coding the entire entry logic. Use wt + velocity + k as direction proxies
    # which ARE live in hot_metrics. Keeps the original behavior when bar fields ARE present
    # (e.g. via shared-ind cache later).
    wt1_3m_prev = _sf(indicators.get('wt1_3m_prev', wt1_3m), wt1_3m)
    wt2_3m_prev = _sf(indicators.get('wt2_3m_prev', wt2_3m), wt2_3m)
    wt_velocity_3m_live = _sf(indicators.get('wt_velocity_3m', 0), 0)
    wt_velocity_1m_live = _sf(indicators.get('wt_velocity_1m', 0), 0)
    if high_3m_prev > 0 and low_3m_prev > 0:
        # Backtest path — use real OHLC.
        bar_rising = (high_3m > high_3m_prev) and (low_3m > low_3m_prev)
        bar_falling = (high_3m < high_3m_prev) and (low_3m < low_3m_prev)
    else:
        # 2026-04-27 LIVE PROXY (relaxed): bar "rising" iff wt1_3m rose AND 3m velocity positive.
        # Removed the additional `wt_velocity_1m > 0` requirement — one bad 1m tick during a real
        # rising 3m sequence was blocking entries on clean breakouts (WIFUSDC pump). 3m velocity
        # alone is the appropriate gate for a 3m-bar scalper.
        bar_rising = (wt1_3m > wt1_3m_prev) and (wt_velocity_3m_live > 0)
        bar_falling = (wt1_3m < wt1_3m_prev) and (wt_velocity_3m_live < 0)
    # 2026-04-27 K-FRESHNESS GATE — was `k_3m > 50` (LONG) / `k_3m < 50` (SHORT) with no
    # upper/lower bound. Live audit (115 SHORT_TREND fires, 18 LONG_TREND): mean k_3m at
    # SHORT entry = 24.6 (26% at k<10 = catching falling knives); mean at LONG entry =
    # 86.7 (61% at k>90 = buying the top). For a 3m scalp we want FRESH momentum
    # (just-crossed 50) with room to run, NOT exhausted extremes. Tighten to a mid-range
    # window: LONG fires only when k_3m crossed up THROUGH 50 and is still in 50-75;
    # SHORT fires only when k_3m crossed down THROUGH 50 and is still in 25-50.
    _k_lo = float(getattr(config, 'SCALP_V3_K_FRESH_LO', 25.0))
    _k_mid_lo = float(getattr(config, 'SCALP_V3_K_FRESH_MID_LO', 50.0))
    _k_mid_hi = float(getattr(config, 'SCALP_V3_K_FRESH_MID_HI', 50.0))
    _k_hi = float(getattr(config, 'SCALP_V3_K_FRESH_HI', 75.0))
    k_rising = (k_3m > k_3m_prev) and (_k_mid_lo <= k_3m <= _k_hi)
    k_falling = (k_3m < k_3m_prev) and (_k_lo <= k_3m <= _k_mid_hi)
    wt_bull = wt1_3m > wt2_3m
    wt_bear = wt1_3m < wt2_3m
    # 2026-04-27 SCALP_V3 IS A 3M SCALPER — do not double-gate on HTF stoch.
    # The global HTF_DIRECTION_GATE in ez_positions_quick (D+4h+1h+SMA200) already
    # blocks suicidal entries against the macro trend. Requiring k_15m≥50 AND
    # k_1h≥50 inside TREND killed obvious scalp setups (e.g. WIFUSDC pump where
    # k_1h hadn't crossed 50 yet at the breakout moment — the move was over by
    # the time it would have). Drop the in-strategy htf_bull/htf_bear gate.
    htf_bull = True
    htf_bear = True
    side_mode = str(getattr(config, 'SCALP_V3_SIDE_MODE', 'BOTH')).upper()
    allow_long = side_mode in ('LONG_ONLY', 'BOTH')
    allow_short = side_mode in ('SHORT_ONLY', 'BOTH')
    # 2026-04-26 entry-quality filters (default OFF; opt-in via config; tested in shadow A/B)
    # HTF SMA200 alignment
    if bool(getattr(config, 'SCALP_V3_HTF_SMA200_ENABLED', False)):
        sma_d = _sf(indicators.get('sma_200_D'), 0)
        sma_4h = _sf(indicators.get('sma_200_4h'), 0)
        if allow_long and sma_d > 0 and sma_4h > 0:
            if not (price > sma_d and price > sma_4h):
                allow_long = False
        if allow_short and sma_d > 0 and sma_4h > 0:
            if not (price < sma_d and price < sma_4h):
                allow_short = False
    # ATR percentile gate (block dead-vol chop)
    if bool(getattr(config, 'SCALP_V3_ATR_PCTL_GATE_ENABLED', False)):
        atr_pctl_min = float(getattr(config, 'SCALP_V3_ATR_PCTL_MIN', 40.0))
        atr_pctl = _sf(indicators.get('atr_3m_pctl_100'), -1)
        if atr_pctl >= 0 and atr_pctl < atr_pctl_min:
            return None  # too quiet — skip entry entirely
    # UTC session block (e.g., [3,4,5] = block 03-06 UTC alts)
    block_hours = getattr(config, 'SCALP_V3_SESSION_BLOCK_HOURS', None) or []
    if block_hours:
        utc_h = time.gmtime().tm_hour
        if utc_h in block_hours:
            return None
    if not (allow_long or allow_short):
        return None
    # 2026-04-26 anchored VWAP filter (default OFF; opt-in via SCALP_V3_VWAP_FILTER_ENABLED).
    # DC_BREAK: LONG requires price > vwap_dc_long; SHORT requires price < vwap_dc_short.
    # US_RTH:   LONG requires price > vwap_us_rth;  SHORT requires price < vwap_us_rth.
    # BOTH:     both checks must pass.
    vwap_on = bool(getattr(config, 'SCALP_V3_VWAP_FILTER_ENABLED', False))
    vwap_type = str(getattr(config, 'SCALP_V3_VWAP_TYPE', 'BOTH')).upper()
    if vwap_on:
        v_dl = _sf(indicators.get('vwap_dc_long'), 0)
        v_ds = _sf(indicators.get('vwap_dc_short'), 0)
        v_us = _sf(indicators.get('vwap_us_rth'), 0)
        # LONG path
        if allow_long and bar_rising and k_rising and wt_bull and htf_bull:
            checks_ok = True
            tag_parts = []
            if vwap_type in ('DC_BREAK', 'BOTH') and v_dl > 0:
                if not (price > v_dl): checks_ok = False
                tag_parts.append(f"DCL{v_dl:.4g}")
            if vwap_type in ('US_RTH', 'BOTH') and v_us > 0:
                if not (price > v_us): checks_ok = False
                tag_parts.append(f"USR{v_us:.4g}")
            if checks_ok and tag_parts:
                return {"side": "LONG", "reason": f"SCALP_V3_OPEN_LONG_TREND_VWAP{vwap_type}_{'+'.join(tag_parts)}_k3m{k_3m:.0f}>{k_3m_prev:.0f}_k15m{k_15m:.0f}_k1h{k_1h:.0f}_wt3m{wt1_3m:.1f}>{wt2_3m:.1f}"}
            elif not tag_parts and (vwap_type in ('DC_BREAK', 'BOTH', 'US_RTH')):
                # VWAP fields missing — fall through to non-VWAP path below
                pass
            else:
                return None  # vwap check failed
        # SHORT path
        if allow_short and bar_falling and k_falling and wt_bear and htf_bear:
            checks_ok = True
            tag_parts = []
            if vwap_type in ('DC_BREAK', 'BOTH') and v_ds > 0:
                if not (price < v_ds): checks_ok = False
                tag_parts.append(f"DCS{v_ds:.4g}")
            if vwap_type in ('US_RTH', 'BOTH') and v_us > 0:
                if not (price < v_us): checks_ok = False
                tag_parts.append(f"USR{v_us:.4g}")
            if checks_ok and tag_parts:
                return {"side": "SHORT", "reason": f"SCALP_V3_OPEN_SHORT_TREND_VWAP{vwap_type}_{'+'.join(tag_parts)}_k3m{k_3m:.0f}<{k_3m_prev:.0f}_k15m{k_15m:.0f}_k1h{k_1h:.0f}_wt3m{wt1_3m:.1f}<{wt2_3m:.1f}"}
            elif not tag_parts and (vwap_type in ('DC_BREAK', 'BOTH', 'US_RTH')):
                pass
            else:
                return None
    # ===== ENTRY PATH 1: TREND (current strict logic) — controlled by SCALP_V3_ENTRY_TREND_ENABLED =====
    if bool(getattr(config, 'SCALP_V3_ENTRY_TREND_ENABLED', True)):
        if allow_long and bar_rising and k_rising and wt_bull and htf_bull:
            return {"side": "LONG", "reason": f"SCALP_V3_OPEN_LONG_TREND_k3m{k_3m:.0f}>{k_3m_prev:.0f}_k15m{k_15m:.0f}_k1h{k_1h:.0f}_wt3m{wt1_3m:.1f}>{wt2_3m:.1f}"}
        if allow_short and bar_falling and k_falling and wt_bear and htf_bear:
            return {"side": "SHORT", "reason": f"SCALP_V3_OPEN_SHORT_TREND_k3m{k_3m:.0f}<{k_3m_prev:.0f}_k15m{k_15m:.0f}_k1h{k_1h:.0f}_wt3m{wt1_3m:.1f}<{wt2_3m:.1f}"}
    # Pull HTF context for the looser paths (no early-return on miss).
    wt1_1h = _sf(indicators.get('wt1_1h', 0), 0); wt2_1h = _sf(indicators.get('wt2_1h', 0), 0)
    htf_bullish_loose = (k_1h >= 50) or (wt1_1h > wt2_1h)
    htf_bearish_loose = (k_1h <= 50) or (wt1_1h < wt2_1h)
    bar_pulling_up = (high_3m > high_3m_prev) or (low_3m > low_3m_prev)    # any rising touch (looser than HH+HL)
    bar_pulling_down = (high_3m < high_3m_prev) or (low_3m < low_3m_prev)
    # ===== ENTRY PATH 2: PULLBACK continuation (research consensus — fixes 0/345 reentry gap) =====
    # LONG: HTF bullish, current 3m showed pullback (LL or LH last bar) AND k_3m oversold-bouncing AND WT 3m turning up.
    if bool(getattr(config, 'SCALP_V3_ENTRY_PULLBACK_ENABLED', False)):
        if allow_long and htf_bullish_loose and bar_pulling_down and (k_3m <= 35) and (k_3m > k_3m_prev) and wt_bull:
            return {"side": "LONG", "reason": f"SCALP_V3_OPEN_LONG_PULLBACK_k3m{k_3m:.0f}>{k_3m_prev:.0f}_k1h{k_1h:.0f}_wt3m{wt1_3m:.1f}>{wt2_3m:.1f}"}
        if allow_short and htf_bearish_loose and bar_pulling_up and (k_3m >= 65) and (k_3m < k_3m_prev) and wt_bear:
            return {"side": "SHORT", "reason": f"SCALP_V3_OPEN_SHORT_PULLBACK_k3m{k_3m:.0f}<{k_3m_prev:.0f}_k1h{k_1h:.0f}_wt3m{wt1_3m:.1f}<{wt2_3m:.1f}"}
    # ===== ENTRY PATH 3: DC channel break (15m channel) =====
    dc_high_15m = _sf(indicators.get('dc_high_15m', 0), 0)
    dc_low_15m = _sf(indicators.get('dc_low_15m', 0), 0)
    if bool(getattr(config, 'SCALP_V3_ENTRY_DC_BREAK_ENABLED', False)):
        if allow_long and dc_high_15m > 0 and price > dc_high_15m and (k_1h > 30) and wt_bull:
            return {"side": "LONG", "reason": f"SCALP_V3_OPEN_LONG_DCBREAK_p{price:.4g}>dc15m{dc_high_15m:.4g}_k1h{k_1h:.0f}_wt3m{wt1_3m:.1f}>{wt2_3m:.1f}"}
        if allow_short and dc_low_15m > 0 and price < dc_low_15m and (k_1h < 70) and wt_bear:
            return {"side": "SHORT", "reason": f"SCALP_V3_OPEN_SHORT_DCBREAK_p{price:.4g}<dc15m{dc_low_15m:.4g}_k1h{k_1h:.0f}_wt3m{wt1_3m:.1f}<{wt2_3m:.1f}"}
    # ===== ENTRY PATH 4: WT 3m line crossover (looser than full TREND stack) =====
    wt_velocity_3m = _sf(indicators.get('wt_velocity_3m', 0), 0)
    if bool(getattr(config, 'SCALP_V3_ENTRY_WT_CROSS_ENABLED', False)):
        if allow_long and wt_bull and wt_velocity_3m > 0 and (k_3m >= 40) and htf_bullish_loose:
            return {"side": "LONG", "reason": f"SCALP_V3_OPEN_LONG_WTCROSS_v3m{wt_velocity_3m:.2f}_k3m{k_3m:.0f}_k1h{k_1h:.0f}_wt3m{wt1_3m:.1f}>{wt2_3m:.1f}"}
        if allow_short and wt_bear and wt_velocity_3m < 0 and (k_3m <= 60) and htf_bearish_loose:
            return {"side": "SHORT", "reason": f"SCALP_V3_OPEN_SHORT_WTCROSS_v3m{wt_velocity_3m:.2f}_k3m{k_3m:.0f}_k1h{k_1h:.0f}_wt3m{wt1_3m:.1f}<{wt2_3m:.1f}"}
    # ===== ENTRY PATH 5: Stoch K bounce off oversold/overbought =====
    d_3m = _sf(indicators.get('stoch_d_3m', 50), 50)
    if bool(getattr(config, 'SCALP_V3_ENTRY_STOCH_BOUNCE_ENABLED', False)):
        if allow_long and (k_3m > 25) and (k_3m_prev <= 25) and (k_3m > d_3m) and (k_1h > 25):
            return {"side": "LONG", "reason": f"SCALP_V3_OPEN_LONG_STOCHBOUNCE_k3m{k_3m_prev:.0f}->{k_3m:.0f}_d3m{d_3m:.0f}_k1h{k_1h:.0f}"}
        if allow_short and (k_3m < 75) and (k_3m_prev >= 75) and (k_3m < d_3m) and (k_1h < 75):
            return {"side": "SHORT", "reason": f"SCALP_V3_OPEN_SHORT_STOCHBOUNCE_k3m{k_3m_prev:.0f}->{k_3m:.0f}_d3m{d_3m:.0f}_k1h{k_1h:.0f}"}
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
    # 2026-04-27 LIVE FALLBACK — high_3m/low_3m_prev are BACKTEST-ONLY fields not in
    # live hot_metrics (same constraint as entry path). Prior `return None` here
    # dead-coded ALL technical exits in live, so V3 only ever closed via MAX_HOLD —
    # i.e. "doesn't scalp". Match entry: when bar fields missing, derive bar
    # rising/falling from wt1_3m direction + 3m velocity (both live-available).
    _live_no_bar = (high_3m_prev <= 0 or low_3m_prev <= 0)
    wt1_3m_prev = _sf(indicators.get('wt1_3m_prev', wt1_3m), wt1_3m)
    wt_velocity_3m_live = _sf(indicators.get('wt_velocity_3m', 0), 0)
    # SBL ("sell before loss"): when True, technical exits ONLY fire while in profit.
    # Goal: lock profit on technical reversal; never close at a loss (rely on hedge/recovery instead).
    # Defaults False so live behavior is unchanged; A/B variants override to True.
    profit_only = bool(getattr(config, 'SCALP_V3_EXIT_PROFIT_ONLY', False))
    if side == 'LONG':
        _gain_now = ((price - entry_price) / entry_price * 100.0)
    else:
        _gain_now = ((entry_price - price) / entry_price * 100.0)
    if profit_only and _gain_now <= 0:
        # Don't fire any technical exit; let MAX_HOLD or hedge logic handle losers.
        max_hold_min = float(getattr(config, 'SCALP_V3_MAX_HOLD_MIN', 0.0) or 0.0)
        if max_hold_min > 0 and age_sec > max_hold_min * 60.0:
            return {"reason": f"SCALP_V3_CLOSE_MAX_HOLD_{side}_age{age_sec/60:.1f}m_g{_gain_now:+.2f}%"}
        return None
    bar_on = bool(getattr(config, 'SCALP_V3_EXIT_BAR_REVERSAL_ENABLED', True))
    wt_on = bool(getattr(config, 'SCALP_V3_EXIT_WT_FLIP_ENABLED', True))
    k_on = bool(getattr(config, 'SCALP_V3_EXIT_K_CROSS_ENABLED', True))
    require_n = max(1, int(getattr(config, 'SCALP_V3_EXIT_REQUIRE_N_SIGNALS', 1)))
    if side == 'LONG':
        if _live_no_bar:
            bar_falling = (wt1_3m < wt1_3m_prev) and (wt_velocity_3m_live < 0)
        else:
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
        if _live_no_bar:
            bar_rising = (wt1_3m > wt1_3m_prev) and (wt_velocity_3m_live > 0)
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
