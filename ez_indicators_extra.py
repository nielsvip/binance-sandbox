# pylint: disable=W,C,R,I
"""
Crypto Consensus Indicators — VWAP, 9/21 EMA, Keltner Channels, TTM Squeeze,
Episodic Pivot, Clenow, Minervini SEPA, SMFI, Funding Z-score, OI Velocity.
Crypto parallel of tradier_indicators_extra.py (stocks). Standalone module to avoid
touching locked ez_indicators.py.
Called from ez_manage.py / ez_positions_quick.py to enrich the indicator dict per
crypto symbol.

KEY DIFFERENCES VS STOCKS MODULE:
- Clenow annualization: 365 (24/7 markets) vs 252 (RTH only).
- VWAP session: 24h vs 6.5h (configurable via session_hours).
- Episodic Pivot: kept for parity but mostly inert on perps (gaps are rare 24/7).
- ORB / lunch dead zone / Connors RSI: NOT included
  (RTH-specific or RSI banned by feedback_rsi_is_bullshit).

NEW FOR CRYPTO (no stocks parallel):
- compute_funding_zscore — perp funding-rate extremes via z-score.
- compute_oi_velocity — open-interest 1h/4h pct change + regime flag.
"""
import json
import logging
import math
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import numpy as np

logger = logging.getLogger("ez_manage")


def compute_vwap_from_bars(bars: List[Dict], reset_daily: bool = True, session_hours: int = 24) -> Dict[str, float]:
    """Compute session-anchored VWAP + bands from intraday bars (1m/3m/5m/15m).
    Crypto session = 24h (continuous). `session_hours` retained for caller flexibility.
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
        sq_diff = np.cumsum(((tp - vwap_arr) ** 2) * vol)
        variance = sq_diff / np.maximum(cum_vol, 1e-10)
        std = float(np.sqrt(max(variance[-1], 0)))
        current_price = closes[-1]
        dist_pct = ((current_price - vwap) / vwap) * 100.0 if vwap > 0 else 0
        return {
            "vwap": round(vwap, 6),
            "vwap_upper1": round(vwap + std, 6),
            "vwap_lower1": round(vwap - std, 6),
            "vwap_upper2": round(vwap + 2 * std, 6),
            "vwap_lower2": round(vwap - 2 * std, 6),
            "vwap_distance_pct": round(dist_pct, 4),
            "vwap_session_hours": session_hours,
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
        "ema_9": round(ema9, 6),
        "ema_21": round(ema21, 6),
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
            "kc_upper": round(mid + atr_mult * atr, 6),
            "kc_middle": round(mid, 6),
            "kc_lower": round(mid - atr_mult * atr, 6),
            "kc_atr": round(atr, 6),
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


def detect_episodic_pivot(daily_bars: List[Dict], min_gap_pct: float = 5.0, min_vol_mult: float = 3.0, max_consolidation_days: int = 8, max_retrace_pct: float = 25.0) -> Dict[str, Any]:
    """Qullamaggie Episodic Pivot: gap up 5%+ on 3x volume, then tight consolidation, then breakout.
    NOTE for crypto: perpetuals trade 24/7 so true gaps are rare; this function will mostly return
    {"ep_detected": False}. Kept for parity with stocks pipeline so cross-asset code can call uniformly.
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
                                "ep_breakout_level": round(breakout_level, 6),
                                "ep_direction": "LONG",
                                "ep_max_retrace_pct": round(max_retrace, 2),
                            }
                else:
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
                                "ep_breakout_level": round(breakout_level, 6),
                                "ep_direction": "SHORT",
                                "ep_max_retrace_pct": round(max_retrace, 2),
                            }
        return {"ep_detected": False}
    except Exception as e:
        logger.debug(f"[EPISODIC_PIVOT] Error: {e}")
        return {"ep_detected": False}


# =========================================================================
# BACKTEST WINNERS — Clenow, SMFI, Minervini (RSI-based primitives BANNED)
# =========================================================================
def compute_clenow_score(closes: List[float], lookback: int = 90, ann_factor: int = 365) -> Dict[str, float]:
    """Clenow: annualized exponential regression slope * R².
    Crypto: ann_factor=365 (24/7 trading). Stocks would pass 252.
    """
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
        slope_ann = (np.exp(b * ann_factor) - 1) * 100  # Annualized %, 365d for crypto
        ss_res = np.sum((yc - b * xc) ** 2)
        ss_tot = np.sum(yc ** 2)
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0
        score = slope_ann * r2
        return {
            "clenow_slope": round(slope_ann, 2),
            "clenow_r2": round(r2, 4),
            "clenow_score": round(score, 2),
            "clenow_ann_factor": ann_factor,
        }
    except Exception:
        return {}


def compute_minervini_sepa(closes: List[float], highs: List[float], lows: List[float], volumes: List[float]) -> Dict[str, Any]:
    """Minervini SEPA trend template. Same structure as stocks variant.
    Lookback windows preserved (50/150/200/252 calendar days). For crypto 24/7 these
    map directly to calendar days, not trading days.
    """
    n = len(closes)
    if n < 252:
        return {}
    try:
        c = np.array(closes)
        h = np.array(highs)
        v = np.array(volumes)
        sma50 = float(np.mean(c[-50:]))
        sma150 = float(np.mean(c[-150:])) if n >= 150 else float(np.mean(c))
        sma200 = float(np.mean(c[-200:])) if n >= 200 else float(np.mean(c))
        sma200_prev = float(np.mean(c[-222:-22])) if n >= 222 else sma200
        price = float(c[-1])
        high_52w = float(np.max(h[-252:]))
        low_52w = float(np.min(c[-252:]))
        avg_vol = float(np.mean(v[-20:])) if n >= 20 else 1
        cur_vol = float(v[-1])
        cond1 = price > sma150 and price > sma200
        cond2 = sma150 > sma200
        cond3 = sma200 > sma200_prev
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
    """Smart Money Flow Index. Divergence = signal."""
    n = len(closes)
    if n < period + 5:
        return {}
    try:
        o = np.array(opens)
        h = np.array(highs)
        l = np.array(lows)
        c = np.array(closes)
        midrange = (h + l) / 2.0
        smfi = np.zeros(n)
        for i in range(1, n):
            smart = c[i] - midrange[i]
            retail = midrange[i] - o[i]
            smfi[i] = smfi[i - 1] + smart - retail
        smfi_sma = float(np.mean(smfi[-period:])) if n >= period else float(smfi[-1])
        price_sma = float(np.mean(c[-period:])) if n >= period else float(c[-1])
        smfi_val = float(smfi[-1])
        price_val = float(c[-1])
        bull_div = price_val < price_sma and smfi_val > smfi_sma
        bear_div = price_val > price_sma and smfi_val < smfi_sma
        return {
            "smfi": round(smfi_val, 4),
            "smfi_sma": round(smfi_sma, 4),
            "smfi_bull_divergence": bull_div,
            "smfi_bear_divergence": bear_div,
            "smfi_signal": "LONG" if bull_div else ("SHORT" if bear_div else "NEUTRAL"),
        }
    except Exception:
        return {}


# =========================================================================
# CRYPTO-NATIVE PRIMITIVES — funding & open interest (no stocks parallel)
# =========================================================================
def compute_funding_zscore(funding_rates: List[float], lookback: int = 21) -> Dict[str, Any]:
    """Funding-rate z-score for perpetual futures.
    z = (current_funding - mean(last N)) / std(last N).
    `funding_extreme` flips True when |z| > 2 (crowded longs/shorts paying through the nose).
    Returns: {funding_zscore, funding_extreme, funding_current, funding_mean, funding_std}
    """
    if not funding_rates or len(funding_rates) < lookback + 1:
        return {}
    try:
        arr = np.array(funding_rates[-(lookback + 1):], dtype=np.float64)
        history = arr[:-1]
        current = float(arr[-1])
        mean = float(np.mean(history))
        std = float(np.std(history))
        if std <= 0 or not np.isfinite(std):
            return {
                "funding_zscore": 0.0,
                "funding_extreme": False,
                "funding_current": round(current, 8),
                "funding_mean": round(mean, 8),
                "funding_std": 0.0,
            }
        z = (current - mean) / std
        return {
            "funding_zscore": round(float(z), 4),
            "funding_extreme": bool(abs(z) > 2.0),
            "funding_current": round(current, 8),
            "funding_mean": round(mean, 8),
            "funding_std": round(std, 8),
        }
    except Exception as e:
        logger.debug(f"[FUNDING_Z] Error: {e}")
        return {}


def compute_oi_velocity(oi_series: List[float]) -> Dict[str, Any]:
    """Open-interest velocity. Expects ascending time-ordered OI samples spaced ~hourly.
    1h pct change uses the last two samples; 4h pct change uses last vs four-back.
    Regime: rising / falling / flat (|1h| < 0.5% AND |4h| < 1.5%).
    Returns: {oi_change_1h_pct, oi_change_4h_pct, oi_regime, oi_current}
    """
    if not oi_series or len(oi_series) < 2:
        return {}
    try:
        arr = [float(x) for x in oi_series if float(x) > 0]
        if len(arr) < 2:
            return {}
        current = arr[-1]
        prev_1h = arr[-2]
        prev_4h = arr[-5] if len(arr) >= 5 else arr[0]
        ch1 = ((current - prev_1h) / prev_1h) * 100.0 if prev_1h > 0 else 0.0
        ch4 = ((current - prev_4h) / prev_4h) * 100.0 if prev_4h > 0 else 0.0
        if abs(ch1) < 0.5 and abs(ch4) < 1.5:
            regime = "flat"
        elif ch4 > 0 and ch1 > 0:
            regime = "rising"
        elif ch4 < 0 and ch1 < 0:
            regime = "falling"
        else:
            regime = "flat"
        return {
            "oi_change_1h_pct": round(float(ch1), 4),
            "oi_change_4h_pct": round(float(ch4), 4),
            "oi_regime": regime,
            "oi_current": round(float(current), 4),
        }
    except Exception as e:
        logger.debug(f"[OI_VELOCITY] Error: {e}")
        return {}


# =========================================================================
# WRAPPER — read crypto klines from disk and enrich indicator dict
# =========================================================================
def compute_extra_indicators_crypto(symbol: str, klines_cache_dir: Path, funding_rates: Optional[List[float]] = None, oi_series: Optional[List[float]] = None) -> Dict[str, Any]:
    """Compute ALL extra crypto indicators for a symbol.
    Reads klines from disk cache. Returns enriched dict to merge into indicators.
    Crypto klines convention on this repo = 15m base; 1h/4h/D resampled by callers.
    """
    result = {}
    bars_3m = _load_klines(klines_cache_dir, symbol, "3m")
    if bars_3m and len(bars_3m) >= 21:
        closes_3m = [float(b.get('close') or b.get('c') or 0) for b in bars_3m if float(b.get('close') or b.get('c') or 0) > 0]
        ema_3m = compute_ema_9_21(closes_3m)
        if ema_3m:
            result.update({f"{k}_3m": v for k, v in ema_3m.items()})
        today_bars = bars_3m[-480:] if len(bars_3m) >= 480 else bars_3m
        vwap_data = compute_vwap_from_bars(today_bars, session_hours=24)
        if vwap_data:
            result.update(vwap_data)
    bars_15m = _load_klines(klines_cache_dir, symbol, "15m")
    if bars_15m and len(bars_15m) >= 21:
        closes_15m = [float(b.get('close') or b.get('c') or 0) for b in bars_15m if float(b.get('close') or b.get('c') or 0) > 0]
        highs_15m = [float(b.get('high') or b.get('h') or 0) for b in bars_15m if float(b.get('close') or b.get('c') or 0) > 0]
        lows_15m = [float(b.get('low') or b.get('l') or 0) for b in bars_15m if float(b.get('close') or b.get('c') or 0) > 0]
        ema_15m = compute_ema_9_21(closes_15m)
        if ema_15m:
            result.update({f"{k}_15m": v for k, v in ema_15m.items()})
        kc_15m = compute_keltner_channels(closes_15m, highs_15m, lows_15m)
        if kc_15m:
            result.update({f"{k}_15m": v for k, v in kc_15m.items()})
    bars_1h = _load_klines(klines_cache_dir, symbol, "1h")
    if bars_1h and len(bars_1h) >= 20:
        closes_1h = [float(b.get('close') or b.get('c') or 0) for b in bars_1h if float(b.get('close') or b.get('c') or 0) > 0]
        highs_1h = [float(b.get('high') or b.get('h') or 0) for b in bars_1h if float(b.get('close') or b.get('c') or 0) > 0]
        lows_1h = [float(b.get('low') or b.get('l') or 0) for b in bars_1h if float(b.get('close') or b.get('c') or 0) > 0]
        kc_1h = compute_keltner_channels(closes_1h, highs_1h, lows_1h)
        if kc_1h:
            result.update({f"{k}_1h": v for k, v in kc_1h.items()})
        ema_1h = compute_ema_9_21(closes_1h)
        if ema_1h:
            result.update({f"{k}_1h": v for k, v in ema_1h.items()})
    bars_d = _load_klines(klines_cache_dir, symbol, "D")
    if bars_d and len(bars_d) >= 100:
        closes_d = [float(b.get('close') or b.get('c') or 0) for b in bars_d[-300:] if float(b.get('close') or b.get('c') or 0) > 0]
        opens_d = [float(b.get('open') or b.get('o') or 0) for b in bars_d[-300:] if float(b.get('close') or b.get('c') or 0) > 0]
        highs_d = [float(b.get('high') or b.get('h') or 0) for b in bars_d[-300:] if float(b.get('close') or b.get('c') or 0) > 0]
        lows_d = [float(b.get('low') or b.get('l') or 0) for b in bars_d[-300:] if float(b.get('close') or b.get('c') or 0) > 0]
        vols_d = [float(b.get('volume') or b.get('v') or 0) for b in bars_d[-300:] if float(b.get('close') or b.get('c') or 0) > 0]
        clenow = compute_clenow_score(closes_d, 90, ann_factor=365)
        if clenow:
            result.update(clenow)
        sepa = compute_minervini_sepa(closes_d, highs_d, lows_d, vols_d)
        if sepa:
            result.update(sepa)
        smfi = compute_smfi(opens_d, highs_d, lows_d, closes_d, 20)
        if smfi:
            result.update(smfi)
    if funding_rates:
        fz = compute_funding_zscore(funding_rates, lookback=21)
        if fz:
            result.update(fz)
    if oi_series:
        oiv = compute_oi_velocity(oi_series)
        if oiv:
            result.update(oiv)
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


def get_rvol_gate_for_strategy(strategy: str) -> float:
    """Returns minimum RVOL required for each strategy type. Crypto-aligned."""
    gates = {
        "MOMENTUM": 1.5,
        "DC_BREAKOUT": 1.5,
        "SCALP": 1.0,
        "HODL": 0.5,
        "SATOSHIT": 0.3,
        "ROTATION": 0.3,
        "EPISODIC_PIVOT": 2.0,
        "FUNDING_FADE": 0.5,
        "OI_BREAKOUT": 1.5,
    }
    return gates.get(strategy, 0.5)
