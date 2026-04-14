# pylint: disable=W,C,R,I
"""
YouTube Consensus Indicators — VWAP, 9/21 EMA, Keltner Channels, TTM Squeeze, ORB, Episodic Pivot
Standalone module to avoid touching locked tradier_indicators.py.
Called from tradier_manage.py to enrich the indicator dict per symbol.
"""
import json
import logging
import math
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import numpy as np

logger = logging.getLogger("tradier_manage")


def compute_vwap_from_bars(bars: List[Dict], reset_daily: bool = True) -> Dict[str, float]:
    """Compute session-anchored VWAP + bands from intraday bars (5m or 15m).
    Returns: {vwap, vwap_upper1, vwap_lower1, vwap_upper2, vwap_lower2, vwap_distance_pct}
    """
    if not bars or len(bars) < 2:
        return {}
    try:
        highs, lows, closes, volumes = [], [], [], []
        for b in bars:
            h = float(b.get('high') or b.get('h') or 0)
            l = float(b.get('low') or b.get('l') or 0)
            c = float(b.get('close') or b.get('c') or 0)
            v = float(b.get('volume') or b.get('v') or 0)
            if c <= 0 or v <= 0:
                continue
            highs.append(h)
            lows.append(l)
            closes.append(c)
            volumes.append(v)
        if len(closes) < 2:
            return {}
        tp = np.array([(h + l + c) / 3.0 for h, l, c in zip(highs, lows, closes)])
        vol = np.array(volumes)
        cum_tp_vol = np.cumsum(tp * vol)
        cum_vol = np.cumsum(vol)
        vwap_arr = cum_tp_vol / np.maximum(cum_vol, 1e-10)
        vwap = float(vwap_arr[-1])
        if vwap <= 0:
            return {}
        # Standard deviation bands
        sq_diff = np.cumsum(((tp - vwap_arr) ** 2) * vol)
        variance = sq_diff / np.maximum(cum_vol, 1e-10)
        std = float(np.sqrt(max(variance[-1], 0)))
        current_price = closes[-1]
        dist_pct = ((current_price - vwap) / vwap) * 100.0 if vwap > 0 else 0
        return {
            "vwap": round(vwap, 4),
            "vwap_upper1": round(vwap + std, 4),
            "vwap_lower1": round(vwap - std, 4),
            "vwap_upper2": round(vwap + 2 * std, 4),
            "vwap_lower2": round(vwap - 2 * std, 4),
            "vwap_distance_pct": round(dist_pct, 4),
        }
    except Exception as e:
        logger.debug(f"[VWAP] Error: {e}")
        return {}


def compute_ema(values: List[float], period: int) -> float:
    """Compute EMA of the last `period` values. Returns latest EMA value."""
    if not values or len(values) < period:
        return 0.0
    mult = 2.0 / (period + 1)
    ema = values[0]
    for v in values[1:]:
        ema = v * mult + ema * (1.0 - mult)
    return ema


def compute_ema_9_21(closes: List[float]) -> Dict[str, float]:
    """Compute EMA 9 and EMA 21 from close prices.
    Returns: {ema_9, ema_21, ema_9_above_21 (bool as 1/0), ema_9_21_dist_pct}
    """
    if not closes or len(closes) < 21:
        return {}
    ema9 = compute_ema(closes, 9)
    ema21 = compute_ema(closes, 21)
    if ema21 <= 0:
        return {}
    dist = ((ema9 - ema21) / ema21) * 100.0
    return {
        "ema_9": round(ema9, 4),
        "ema_21": round(ema21, 4),
        "ema_9_above_21": 1.0 if ema9 > ema21 else 0.0,
        "ema_9_21_dist_pct": round(dist, 4),
    }


def compute_keltner_channels(closes: List[float], highs: List[float], lows: List[float], ema_period: int = 20, atr_period: int = 20, atr_mult: float = 1.5) -> Dict[str, float]:
    """Compute Keltner Channels (EMA ± mult * ATR).
    Returns: {kc_upper, kc_middle, kc_lower}
    """
    if not closes or len(closes) < max(ema_period, atr_period):
        return {}
    try:
        mid = compute_ema(closes, ema_period)
        # ATR calculation
        trs = []
        for j in range(1, len(closes)):
            h = highs[j] if j < len(highs) else closes[j]
            l = lows[j] if j < len(lows) else closes[j]
            pc = closes[j - 1]
            tr = max(h - l, abs(h - pc), abs(l - pc))
            trs.append(tr)
        if len(trs) < atr_period:
            return {}
        atr = compute_ema(trs[-atr_period * 3:], atr_period) if len(trs) >= atr_period else sum(trs[-atr_period:]) / atr_period
        return {
            "kc_upper": round(mid + atr_mult * atr, 4),
            "kc_middle": round(mid, 4),
            "kc_lower": round(mid - atr_mult * atr, 4),
            "kc_atr": round(atr, 4),
        }
    except Exception:
        return {}


def detect_squeeze(bb_upper: float, bb_lower: float, kc_upper: float, kc_lower: float) -> Dict[str, Any]:
    """TTM Squeeze detection: BB inside KC = squeeze on.
    Returns: {squeeze_on (bool), squeeze_fired (need momentum direction from caller)}
    """
    if not all([bb_upper, bb_lower, kc_upper, kc_lower]):
        return {"squeeze_on": False}
    squeeze_on = bb_upper < kc_upper and bb_lower > kc_lower
    return {"squeeze_on": squeeze_on}


def detect_opening_range(bars_5m: List[Dict], market_open_minutes: int = 15) -> Dict[str, float]:
    """Detect Opening Range from first N minutes of 5m bars.
    Expects bars with 'timestamp' or 'time' or 'open_time' field.
    Returns: {orb_high, orb_low, orb_range, orb_midpoint}
    """
    if not bars_5m or len(bars_5m) < 2:
        return {}
    try:
        orb_bars_needed = max(1, market_open_minutes // 5)
        # Take last session's first bars — assume bars are sorted by time
        # We look for bars from today's session
        today_bars = []
        for b in bars_5m:
            ts = b.get('timestamp') or b.get('time') or b.get('open_time') or b.get('t') or 0
            if isinstance(ts, str):
                try:
                    from dateutil.parser import isoparse
                    ts = isoparse(ts).timestamp()
                except Exception:
                    ts = 0
            elif isinstance(ts, (int, float)) and ts > 1e12:
                ts = ts / 1000.0
            today_bars.append({**b, '_ts': float(ts)})
        if not today_bars:
            return {}
        today_bars.sort(key=lambda x: x['_ts'])
        # Take first N bars of the session (the ORB window)
        orb_subset = today_bars[:orb_bars_needed]
        if len(orb_subset) < orb_bars_needed:
            return {}
        orb_high = max(float(b.get('high') or b.get('h') or 0) for b in orb_subset)
        orb_low = min(float(b.get('low') or b.get('l') or b.get('high') or 999999) for b in orb_subset)
        if orb_high <= 0 or orb_low <= 0 or orb_high <= orb_low:
            return {}
        return {
            "orb_high": round(orb_high, 4),
            "orb_low": round(orb_low, 4),
            "orb_range": round(orb_high - orb_low, 4),
            "orb_midpoint": round((orb_high + orb_low) / 2.0, 4),
        }
    except Exception as e:
        logger.debug(f"[ORB] Error: {e}")
        return {}


def detect_episodic_pivot(daily_bars: List[Dict], min_gap_pct: float = 5.0, min_vol_mult: float = 3.0, max_consolidation_days: int = 8, max_retrace_pct: float = 25.0) -> Dict[str, Any]:
    """Qullamaggie Episodic Pivot: gap up 5%+ on 3x volume, then tight consolidation, then breakout.
    Returns: {ep_detected, ep_gap_pct, ep_gap_day_idx, ep_consolidation_days, ep_breakout_level, ep_direction}
    """
    if not daily_bars or len(daily_bars) < 10:
        return {"ep_detected": False}
    try:
        closes = [float(b.get('close') or b.get('c') or 0) for b in daily_bars]
        volumes = [float(b.get('volume') or b.get('v') or 0) for b in daily_bars]
        opens = [float(b.get('open') or b.get('o') or 0) for b in daily_bars]
        highs = [float(b.get('high') or b.get('h') or 0) for b in daily_bars]
        lows = [float(b.get('low') or b.get('l') or 0) for b in daily_bars]
        if any(c <= 0 for c in closes[-10:]):
            return {"ep_detected": False}
        # Look for gap day in last 15 bars
        avg_vol_20 = np.mean(volumes[-25:-5]) if len(volumes) >= 25 else np.mean(volumes[:-5]) if len(volumes) > 5 else 0
        if avg_vol_20 <= 0:
            return {"ep_detected": False}
        for gap_idx in range(-15, -2):
            if abs(gap_idx) >= len(closes):
                continue
            prev_close = closes[gap_idx - 1]
            gap_open = opens[gap_idx]
            gap_close = closes[gap_idx]
            gap_vol = volumes[gap_idx]
            if prev_close <= 0:
                continue
            gap_pct = ((gap_open - prev_close) / prev_close) * 100.0
            vol_mult = gap_vol / avg_vol_20 if avg_vol_20 > 0 else 0
            if abs(gap_pct) >= min_gap_pct and vol_mult >= min_vol_mult:
                is_bullish = gap_pct > 0
                gap_high = highs[gap_idx]
                # Check consolidation after gap
                post_bars = list(range(gap_idx + 1, 0)) if gap_idx < -1 else []
                if not post_bars or len(post_bars) < 2:
                    continue
                if len(post_bars) > max_consolidation_days:
                    post_bars = post_bars[:max_consolidation_days]
                post_highs = [highs[j] for j in post_bars]
                post_lows = [lows[j] for j in post_bars]
                post_closes = [closes[j] for j in post_bars]
                if is_bullish:
                    max_retrace = ((gap_high - min(post_lows)) / (gap_high - prev_close)) * 100.0 if (gap_high - prev_close) > 0 else 100
                    if max_retrace <= max_retrace_pct:
                        breakout_level = max(post_highs)
                        current_price = closes[-1]
                        if current_price >= breakout_level * 0.99:
                            return {
                                "ep_detected": True,
                                "ep_gap_pct": round(gap_pct, 2),
                                "ep_vol_mult": round(vol_mult, 2),
                                "ep_consolidation_days": len(post_bars),
                                "ep_breakout_level": round(breakout_level, 4),
                                "ep_direction": "LONG",
                                "ep_max_retrace_pct": round(max_retrace, 2),
                            }
                else:
                    # Bearish episodic pivot
                    gap_low = lows[gap_idx]
                    max_retrace = ((max(post_highs) - gap_low) / (prev_close - gap_low)) * 100.0 if (prev_close - gap_low) > 0 else 100
                    if max_retrace <= max_retrace_pct:
                        breakout_level = min(post_lows)
                        current_price = closes[-1]
                        if current_price <= breakout_level * 1.01:
                            return {
                                "ep_detected": True,
                                "ep_gap_pct": round(gap_pct, 2),
                                "ep_vol_mult": round(vol_mult, 2),
                                "ep_consolidation_days": len(post_bars),
                                "ep_breakout_level": round(breakout_level, 4),
                                "ep_direction": "SHORT",
                                "ep_max_retrace_pct": round(max_retrace, 2),
                            }
        return {"ep_detected": False}
    except Exception as e:
        logger.debug(f"[EPISODIC_PIVOT] Error: {e}")
        return {"ep_detected": False}


# =========================================================================
# BACKTEST WINNERS — Clenow (5.17), SMFI (4.20), Minervini (3.68), Connors (2.06)
# =========================================================================
def compute_clenow_score(closes: List[float], lookback: int = 90) -> Dict[str, float]:
    """Clenow: annualized exponential regression slope * R². Sharpe 5.17 on 2yr backtest."""
    if len(closes) < lookback + 5:
        return {}
    try:
        arr = np.array(closes[-lookback:], dtype=np.float64)
        if np.any(arr <= 0):
            return {}
        y = np.log(arr)
        x = np.arange(lookback, dtype=np.float64)
        mx, my = np.mean(x), np.mean(y)
        xc = x - mx
        yc = y - my
        ssxx = np.dot(xc, xc)
        if ssxx == 0:
            return {}
        b = np.dot(xc, yc) / ssxx
        slope_ann = (np.exp(b * 252) - 1) * 100  # Annualized %
        ss_res = np.sum((yc - b * xc) ** 2)
        ss_tot = np.sum(yc ** 2)
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0
        score = slope_ann * r2
        return {
            "clenow_slope": round(slope_ann, 2),
            "clenow_r2": round(r2, 4),
            "clenow_score": round(score, 2),
        }
    except Exception:
        return {}


def compute_minervini_sepa(closes: List[float], highs: List[float], lows: List[float], volumes: List[float]) -> Dict[str, Any]:
    """Minervini SEPA trend template. Sharpe 3.68 on 2yr backtest. Returns pass/fail + score."""
    n = len(closes)
    if n < 252:
        return {}
    try:
        c = np.array(closes)
        h = np.array(highs)
        v = np.array(volumes)
        # SMAs
        sma50 = float(np.mean(c[-50:]))
        sma150 = float(np.mean(c[-150:])) if n >= 150 else float(np.mean(c))
        sma200 = float(np.mean(c[-200:])) if n >= 200 else float(np.mean(c))
        sma200_prev = float(np.mean(c[-222:-22])) if n >= 222 else sma200
        price = float(c[-1])
        high_52w = float(np.max(h[-252:]))
        low_52w = float(np.min(c[-252:]))
        avg_vol = float(np.mean(v[-20:])) if n >= 20 else 1
        cur_vol = float(v[-1])
        # SEPA conditions
        cond1 = price > sma150 and price > sma200
        cond2 = sma150 > sma200
        cond3 = sma200 > sma200_prev  # Trending up
        cond4 = price >= low_52w * 1.25 if low_52w > 0 else False
        cond5 = price >= high_52w * 0.75 if high_52w > 0 else False
        cond6 = price > sma50
        vol_surge = cur_vol > avg_vol * 1.5 if avg_vol > 0 else False
        score = sum([cond1, cond2, cond3, cond4, cond5, cond6])
        sepa_pass = score >= 5 and vol_surge
        return {
            "sepa_pass": sepa_pass,
            "sepa_score": score,
            "sepa_vol_surge": vol_surge,
            "sepa_price_vs_sma150": round((price / sma150 - 1) * 100, 2) if sma150 > 0 else 0,
            "sepa_pct_from_52w_high": round((price / high_52w - 1) * 100, 2) if high_52w > 0 else 0,
        }
    except Exception:
        return {}


def compute_smfi(opens: List[float], highs: List[float], lows: List[float], closes: List[float], period: int = 20) -> Dict[str, float]:
    """Smart Money Flow Index. Sharpe 4.20 on 2yr backtest. Divergence = signal."""
    n = len(closes)
    if n < period + 5:
        return {}
    try:
        o = np.array(opens)
        h = np.array(highs)
        l = np.array(lows)
        c = np.array(closes)
        midrange = (h + l) / 2.0
        # SMFI accumulation: last-hour (smart) - first-30min (retail)
        smfi = np.zeros(n)
        for i in range(1, n):
            smart = c[i] - midrange[i]
            retail = midrange[i] - o[i]
            smfi[i] = smfi[i - 1] + smart - retail
        # SMA of SMFI and price
        smfi_sma = float(np.mean(smfi[-period:])) if n >= period else float(smfi[-1])
        price_sma = float(np.mean(c[-period:])) if n >= period else float(c[-1])
        smfi_val = float(smfi[-1])
        price_val = float(c[-1])
        # Divergence detection
        bull_div = price_val < price_sma and smfi_val > smfi_sma  # Price weak, smart money accumulating
        bear_div = price_val > price_sma and smfi_val < smfi_sma  # Price strong, smart money distributing
        return {
            "smfi": round(smfi_val, 4),
            "smfi_sma": round(smfi_sma, 4),
            "smfi_bull_divergence": bull_div,
            "smfi_bear_divergence": bear_div,
            "smfi_signal": "LONG" if bull_div else ("SHORT" if bear_div else "NEUTRAL"),
        }
    except Exception:
        return {}


def compute_connors_rsi(closes: List[float]) -> Dict[str, float]:
    """Connors RSI Composite: RSI(3) + Streak RSI(2) + ROC Percentile(100). Sharpe 2.06 on 2yr backtest."""
    n = len(closes)
    if n < 105:
        return {}
    try:
        c = np.array(closes, dtype=np.float64)
        # RSI(3)
        d = np.diff(c)
        g, lo = np.maximum(d, 0), np.maximum(-d, 0)
        ag, al = np.zeros(len(d)), np.zeros(len(d))
        ag[2] = np.mean(g[:3])
        al[2] = np.mean(lo[:3])
        for i in range(3, len(d)):
            ag[i] = (ag[i-1] * 2 + g[i]) / 3
            al[i] = (al[i-1] * 2 + lo[i]) / 3
        rs3 = np.where(al > 0, ag / al, 100.0)
        rsi3_arr = 100.0 - (100.0 / (1.0 + rs3))
        rsi3 = float(rsi3_arr[-1])
        # Streak: consecutive up/down days
        streaks = np.zeros(n)
        for i in range(1, n):
            if c[i] > c[i-1]:
                streaks[i] = max(1, streaks[i-1] + 1) if streaks[i-1] >= 0 else 1
            elif c[i] < c[i-1]:
                streaks[i] = min(-1, streaks[i-1] - 1) if streaks[i-1] <= 0 else -1
        # RSI(2) of streak (offset by 100 to keep positive)
        s_shifted = streaks + 100
        sd = np.diff(s_shifted)
        sg, sl = np.maximum(sd, 0), np.maximum(-sd, 0)
        asg = np.mean(sg[-2:]) if len(sg) >= 2 else 0
        asl = np.mean(sl[-2:]) if len(sl) >= 2 else 0.001
        streak_rsi = 100.0 - (100.0 / (1.0 + asg / asl)) if asl > 0 else 50.0
        # ROC percentile rank (100-day)
        roc = np.zeros(n)
        roc[1:] = (c[1:] - c[:-1]) / c[:-1] * 100
        lookback = min(100, n - 1)
        window = roc[-lookback:]
        pct_rank = float(np.sum(window < roc[-1]) / lookback * 100) if lookback > 0 else 50.0
        # Composite
        composite = (rsi3 + streak_rsi + pct_rank) / 3.0
        return {
            "connors_rsi": round(composite, 2),
            "connors_rsi3": round(rsi3, 2),
            "connors_streak_rsi": round(streak_rsi, 2),
            "connors_pct_rank": round(pct_rank, 2),
            "connors_signal": "LONG" if composite < 15 else ("SHORT" if composite > 85 else "NEUTRAL"),
        }
    except Exception:
        return {}


def compute_extra_indicators(symbol: str, klines_cache_dir: Path) -> Dict[str, Any]:
    """Compute ALL extra indicators for a symbol.
    Reads klines from disk cache. Returns enriched dict to merge into indicators.
    """
    result = {}
    # --- Load 5m bars for VWAP + ORB + 9/21 EMA ---
    bars_5m = _load_klines(klines_cache_dir, symbol, "5m")
    if bars_5m and len(bars_5m) >= 21:
        closes_5m = [float(b.get('close') or b.get('c') or 0) for b in bars_5m if float(b.get('close') or b.get('c') or 0) > 0]
        highs_5m = [float(b.get('high') or b.get('h') or 0) for b in bars_5m if float(b.get('close') or b.get('c') or 0) > 0]
        lows_5m = [float(b.get('low') or b.get('l') or 0) for b in bars_5m if float(b.get('close') or b.get('c') or 0) > 0]
        # VWAP (use today's bars only — last ~78 bars for full session at 5m)
        today_bars = bars_5m[-78:] if len(bars_5m) >= 78 else bars_5m
        vwap_data = compute_vwap_from_bars(today_bars)
        if vwap_data:
            result.update(vwap_data)
        # 9/21 EMA on 5m
        ema_data_5m = compute_ema_9_21(closes_5m)
        if ema_data_5m:
            result.update({f"{k}_5m": v for k, v in ema_data_5m.items()})
        # Keltner Channels on 5m (for squeeze detection with 1h BB)
        kc_5m = compute_keltner_channels(closes_5m, highs_5m, lows_5m)
        if kc_5m:
            result.update({f"{k}_5m": v for k, v in kc_5m.items()})
        # ORB
        orb_data = detect_opening_range(bars_5m)
        if orb_data:
            result.update(orb_data)
    # --- Load 15m bars for 9/21 EMA ---
    bars_15m = _load_klines(klines_cache_dir, symbol, "15m")
    if bars_15m and len(bars_15m) >= 21:
        closes_15m = [float(b.get('close') or b.get('c') or 0) for b in bars_15m if float(b.get('close') or b.get('c') or 0) > 0]
        ema_data_15m = compute_ema_9_21(closes_15m)
        if ema_data_15m:
            result.update({f"{k}_15m": v for k, v in ema_data_15m.items()})
    # --- Load 1h bars for Keltner + Squeeze ---
    bars_1h = _load_klines(klines_cache_dir, symbol, "1h")
    if bars_1h and len(bars_1h) >= 20:
        closes_1h = [float(b.get('close') or b.get('c') or 0) for b in bars_1h if float(b.get('close') or b.get('c') or 0) > 0]
        highs_1h = [float(b.get('high') or b.get('h') or 0) for b in bars_1h if float(b.get('close') or b.get('c') or 0) > 0]
        lows_1h = [float(b.get('low') or b.get('l') or 0) for b in bars_1h if float(b.get('close') or b.get('c') or 0) > 0]
        kc_1h = compute_keltner_channels(closes_1h, highs_1h, lows_1h)
        if kc_1h:
            result.update({f"{k}_1h": v for k, v in kc_1h.items()})
        # 9/21 EMA on 1h
        ema_data_1h = compute_ema_9_21(closes_1h)
        if ema_data_1h:
            result.update({f"{k}_1h": v for k, v in ema_data_1h.items()})
    # --- Load Daily bars for Episodic Pivot + Clenow + Minervini + SMFI + Connors ---
    bars_d = _load_klines(klines_cache_dir, symbol, "D")
    if bars_d and len(bars_d) >= 15:
        ep_data = detect_episodic_pivot(bars_d)
        if ep_data:
            result.update(ep_data)
    if bars_d and len(bars_d) >= 100:
        closes_d = [float(b.get('close') or b.get('c') or 0) for b in bars_d[-300:] if float(b.get('close') or b.get('c') or 0) > 0]
        opens_d = [float(b.get('open') or b.get('o') or 0) for b in bars_d[-300:] if float(b.get('close') or b.get('c') or 0) > 0]
        highs_d = [float(b.get('high') or b.get('h') or 0) for b in bars_d[-300:] if float(b.get('close') or b.get('c') or 0) > 0]
        lows_d = [float(b.get('low') or b.get('l') or 0) for b in bars_d[-300:] if float(b.get('close') or b.get('c') or 0) > 0]
        vols_d = [float(b.get('volume') or b.get('v') or 0) for b in bars_d[-300:] if float(b.get('close') or b.get('c') or 0) > 0]
        # Clenow Exp Regression Score
        clenow = compute_clenow_score(closes_d, 90)
        if clenow:
            result.update(clenow)
        # Minervini SEPA Template
        sepa = compute_minervini_sepa(closes_d, highs_d, lows_d, vols_d)
        if sepa:
            result.update(sepa)
        # Smart Money Flow Index
        smfi = compute_smfi(opens_d, highs_d, lows_d, closes_d, 20)
        if smfi:
            result.update(smfi)
        # Connors RSI Composite
        crsi = compute_connors_rsi(closes_d)
        if crsi:
            result.update(crsi)
    return result


def _load_klines(klines_cache_dir: Path, symbol: str, tf: str) -> List[Dict]:
    """Load klines from disk cache."""
    paths = [
        klines_cache_dir / f"{symbol.upper()}_{tf}.json",
        klines_cache_dir / f"{symbol.upper()}_{tf}.json.bak",
    ]
    for p in paths:
        if p.exists():
            try:
                raw = json.loads(p.read_text())
                bars = raw if isinstance(raw, list) else raw.get('bars', raw.get('candles', []))
                if isinstance(bars, list) and len(bars) > 0:
                    return bars
            except Exception:
                continue
    return []


# =========================================================================
# LUNCH DEAD ZONE HELPER
# =========================================================================
def is_lunch_dead_zone() -> bool:
    """Returns True during 11:30-14:00 ET (15:30-18:00 UTC) — low volume chop zone."""
    try:
        now_utc = datetime.now(timezone.utc)
        # Convert to ET (UTC-4 during EDT, UTC-5 during EST)
        # Use -4 for EDT (March-November)
        et_offset = timezone(timedelta(hours=-4))
        now_et = now_utc.astimezone(et_offset)
        t = now_et.hour * 60 + now_et.minute
        return 690 <= t < 840  # 11:30 (690) to 14:00 (840)
    except Exception:
        return False


def get_rvol_gate_for_strategy(strategy: str) -> float:
    """Returns minimum RVOL required for each strategy type."""
    gates = {
        "MOMENTUM": 1.5,
        "ORB": 1.5,
        "DC_BREAKOUT": 1.5,
        "SCALP": 1.0,
        "HODL": 0.5,
        "SATOSHIT": 0.3,
        "RSI2": 0.3,
        "GAP_FILL": 0.3,
        "ROTATION": 0.3,
        "EPISODIC_PIVOT": 2.0,
    }
    return gates.get(strategy, 0.5)
