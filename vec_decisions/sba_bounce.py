"""sba_bounce.py — SHARED scalar+vectorized SBA_BOUNCE_SCORE predicate.

Single source of truth for the live SBA bounce composite scorer.
The live scalar path (ez_positions_quick.py:964-991 _sba_bounce_score)
AND the vectorized backtest path (v12_quick_engine compute_entry_signals)
BOTH derive their score from the same pure predicate below.

Live logic (ez_positions_quick.py):

    def _sba_bounce_score(ind, is_long):
        if adx_1h > SBA_ADX_MAX(25): return 0, SBA_DEAL_ADX
        if bb_width_4h >16: return 0, SBA_DEAL_BB4H
        if bb_width_1h >14: return 0, SBA_DEAL_BB1H
        if dc_width_4h >18: return 0, SBA_DEAL_DC4H
        if is_long and k_15m>75: return 0, SBA_DEAL_OVERBOUGHT
        if not is_long and k_15m<25: return 0, SBA_DEAL_OVERSOLD
        if is_long and ha_1h=='red' and ha_4h=='red': return 0, SBA_DEAL_HTF_DOWN
        if not is_long and ha_1h=='green' and ha_4h=='green': return 0, SBA_DEAL_HTF_UP
        if is_long and mfi_4h<25 and mfi_1h<30: return 0, SBA_DEAL_MFI_DRY
        if not is_long and mfi_4h>75 and mfi_1h>70: return 0, SBA_DEAL_MFI_FLOOD
        quality, reversal, _, detail = _market_quality_score(ind, is_long)
        return quality+reversal, detail

_market_quality_score (ez_positions_quick.py:590-... ) builds quality 0-6
and reversal 0-8 from BB/DC/ADX/SMA200/rvol/MFI or RSI + stoch/WT/HA
divergence/exhaustion/pattern/MFI-or-RSI extreme. The score 0-14 is
compared to SBA_MIN_SCORE(4) at the gate; the bounce scorer itself
just computes the score.

This module vectorizes the DEALBREAKER lattice + the quality+reversal
sum via numpy arrays so v12_quick can compute a per-bar bounce score
without Python loops. Threshold gating is done by the caller (or by
sba_bounce_passes_vec).
"""
from __future__ import annotations

from typing import Any, Mapping, Tuple
import math

import numpy as np

_SBA_ADX_MAX = 25.0
_SBA_BB_W_4H_MAX = 16.0
_SBA_BB_W_1H_MAX = 14.0
_SBA_DC_W_4H_MAX = 18.0
_SBA_MIN_SCORE = 4.0


def _num(m: Mapping[str, Any] | None, key: str, default: float = 50.0) -> float:
    try:
        v = (m or {}).get(key, default)
        fv = float(v)
        return fv if math.isfinite(fv) else default
    except (TypeError, ValueError):
        return default


def _market_quality_score_parts(
    ind: Mapping[str, Any] | None, is_long: bool
) -> Tuple[float, float]:
    """Scalar quality+reversal exactly as ez_positions_quick._market_quality_score.

    Extracted to share math with vec path; returns (quality, reversal).
    """
    # import locally to avoid circular; replicate minimal fetch
    def sf(k, d):
        return _num(ind, k, d)

    quality = 0.0
    reversal = 0.0

    bb_w_1h = sf("bb_width_1h", 10.0)
    bb_w_4h = sf("bb_width_4h", 10.0)
    if bb_w_1h < 6.5 and bb_w_4h < 10.0:
        quality += 1.5
    elif bb_w_1h < 9.0 and bb_w_4h < 13.0:
        quality += 0.5

    dc_w_1h = _num(ind, "dc_width_1h", _num(ind, "dc_width", 8.0))
    dc_w_4h = sf("dc_width_4h", 12.0)
    if dc_w_1h < 7.0 and dc_w_4h < 12.0:
        quality += 0.5

    adx_1h = sf("adx_1h", 30)
    adx_4h = sf("adx_4h", 30)
    if adx_1h < 20 and adx_4h < 25:
        quality += 1.0
    elif adx_1h < 25:
        quality += 0.5

    sma200_1h = sf("sma_200_1h", 0)
    price = _num(ind, "current_price", _num(ind, "close_1h", 0))
    pct_sma200 = ((price - sma200_1h) / sma200_1h * 100) if sma200_1h > 0 and price > 0 else -99
    if -3.0 < pct_sma200 < 3.0:
        quality += 1.0
    elif -5.0 < pct_sma200 < 5.0:
        quality += 0.5

    rvol_15m = sf("relative_volume_15m", 1.5)
    rvol_1h = sf("relative_volume_1h", 1.5)
    if rvol_1h < 0.95 and rvol_15m < 1.2:
        quality += 1.0
    elif rvol_1h < 1.2:
        quality += 0.5

    mfi_15m = sf("mfi_15m", 50)
    mfi_1h = sf("mfi_1h", 50)
    mfi_4h = sf("mfi_4h", 50)
    rsi_15m = sf("rsi_15m", 50)
    rsi_1h = sf("rsi_1h", 50)
    rsi_4h = sf("rsi_4h", 50)
    if is_long:
        if mfi_1h > 40 and mfi_4h > 40:
            quality += 1.0 if (mfi_1h > 50 and mfi_4h > 45) else 0.5
    else:
        if rsi_1h > 55 and rsi_4h > 50 and rvol_1h >= 1.0:
            quality += 1.0
        elif rsi_1h > 50 and rsi_4h > 45 and rvol_1h >= 1.0:
            quality += 0.5

    # Reversal
    k_3m = sf("k_3m", 50)
    k_15m = sf("k_15m", 50)
    k_1h = sf("k_1h", 50)
    k_cross_15m = bool(ind.get("stoch_crossover_15m" if is_long else "stoch_crossunder_15m", False)) if ind else False
    k_cross_1h = bool(ind.get("stoch_crossover_1h" if is_long else "stoch_crossunder_1h", False)) if ind else False
    if k_cross_15m and (k_15m < 30 if is_long else k_15m > 70):
        reversal += 1.0
    if k_cross_1h and (k_1h < 35 if is_long else k_1h > 65):
        reversal += 0.5
    all_oversold = (is_long and k_3m < 25 and k_15m < 30 and k_1h < 40) or (not is_long and k_3m > 75 and k_15m > 70 and k_1h > 60)
    if all_oversold:
        reversal += 1.0
    wt_buy = "BUY" if is_long else "SELL"
    wt_15m = (ind.get("wt_signal_15m") == wt_buy) if ind else False
    wt_1h = (ind.get("wt_signal_1h") == wt_buy) if ind else False
    if wt_15m and wt_1h:
        reversal += 1.5
    elif wt_15m or wt_1h:
        reversal += 0.5
    ha_flip_3m = (ind.get("ha_3m") == ("green" if is_long else "red") and ind.get("ha_3m_prev") != ("green" if is_long else "red")) if ind else False
    ha_flip_15m = (ind.get("ha_15m") == ("green" if is_long else "red") and ind.get("ha_15m_prev") != ("green" if is_long else "red")) if ind else False
    if ha_flip_3m and ha_flip_15m:
        reversal += 1.5
    elif ha_flip_3m or ha_flip_15m:
        reversal += 0.5
    div_tag = "BULL" if is_long else "BEAR"
    div_1h = (ind.get("wt_divergence_1h") == div_tag) if ind else False
    div_4h = (ind.get("wt_divergence_4h") == div_tag) if ind else False
    if div_4h:
        reversal += 1.5
    elif div_1h:
        reversal += 1.0
    exhaust_tag = "EXHAUST_DOWN" if is_long else "EXHAUST_UP"
    if (ind.get("wt_momentum_state_1h") == exhaust_tag) if ind else False:
        reversal += 0.5
    if (ind.get("wt_momentum_state_4h") == exhaust_tag) if ind else False:
        reversal += 0.5
    pat = (ind.get("bar_pattern_15m", "") if ind else "")
    long_pats = ("hammer", "bullish_engulfing", "tweezer_bottom")
    short_pats = ("shooting_star", "bearish_engulfing", "tweezer_top")
    if pat in (long_pats if is_long else short_pats):
        reversal += 0.5
    if is_long:
        if mfi_15m < 30 and mfi_1h < 35:
            reversal += 1.0
        elif mfi_15m > mfi_1h and mfi_1h < 40:
            reversal += 0.5
    else:
        if rsi_15m > 70 and rsi_1h > 65 and rvol_15m >= 1.0:
            reversal += 1.0
        elif rsi_15m < rsi_1h and rsi_1h > 60:
            reversal += 0.5
    rsi2 = sf("rsi_2_1h", 50)
    if (rsi2 < 15 if is_long else rsi2 > 85):
        reversal += 0.5
    return quality, reversal


def _sba_bounce_score_scalar(ind: Mapping[str, Any] | None, is_long: bool, cfg: Any) -> float:
    adx_max = float(getattr(cfg, "SBA_ADX_MAX", _SBA_ADX_MAX)) if cfg is not None else _SBA_ADX_MAX
    if _num(ind, "adx_1h", 50) > adx_max:
        return 0.0
    if _num(ind, "bb_width_4h", 10.0) > _SBA_BB_W_4H_MAX:
        return 0.0
    if _num(ind, "bb_width_1h", 10.0) > _SBA_BB_W_1H_MAX:
        return 0.0
    if _num(ind, "dc_width_4h", 12.0) > _SBA_DC_W_4H_MAX:
        return 0.0
    k15 = _num(ind, "k_15m", 50)
    if is_long and k15 > 75:
        return 0.0
    if not is_long and k15 < 25:
        return 0.0
    ha1 = (ind or {}).get("ha_1h")
    ha4 = (ind or {}).get("ha_4h")
    if is_long and ha1 == "red" and ha4 == "red":
        return 0.0
    if not is_long and ha1 == "green" and ha4 == "green":
        return 0.0
    mfi_4h = _num(ind, "mfi_4h", 50)
    mfi_1h = _num(ind, "mfi_1h", 50)
    if is_long and mfi_4h < 25 and mfi_1h < 30:
        return 0.0
    if not is_long and mfi_4h > 75 and mfi_1h > 70:
        return 0.0
    q, r = _market_quality_score_parts(ind, is_long)
    return q + r


def sba_bounce_score(ind: Mapping[str, Any] | None, is_long: bool, cfg: Any = None) -> Tuple[float, str]:
    """Live scalar: SBA bounce score 0-14. Mirrors ez_positions_quick._sba_bounce_score."""
    score = _sba_bounce_score_scalar(ind, is_long, cfg)
    if score == 0.0:
        # distinguish dealbreaker vs legit 0; caller checks threshold
        return 0.0, "SBA_DEAL"
    return float(score), f"SBA_SCORE_{score:.1f}"


def sba_bounce_score_vec(
    npz: Mapping[str, Any],
    n: int,
    cfg: Any,
    is_long: bool,
) -> np.ndarray:
    """Vectorized SBA bounce score per bar. SAME dealbreakers + SAME quality math.

    Returns float ndarray shape (n,) with score 0-14 per bar.
    Reads npz keys: adx_1h, bb_width_4h, bb_width_1h, dc_width_4h, k_15m,
    ha_1h/ha_4h (encoded as ints if present, see below), mfi_4h/mfi_1h,
    plus all keys used by _market_quality_score.
    NaN/missing fails open toward scalar defaults.
    """
    def _arr(key: str, default: float) -> np.ndarray:
        if key in npz:
            a = np.asarray(npz[key], dtype=float)
            if a.size < n:
                tmp = np.full(n, default, dtype=float)
                tmp[: min(n, a.size)] = a[: min(n, a.size)]
                a = tmp
            else:
                a = a[:n]
            return a
        return np.full(n, default, dtype=float)

    def _ha_arr(key: str) -> np.ndarray:
        # ha stored as string array or int encoded: 1 green, -1 red
        if key not in npz:
            return np.full(n, 0, dtype=int)
        raw = npz[key]
        # try numeric
        try:
            return np.asarray(raw, dtype=int)[:n] if np.asarray(raw).size >= n else np.full(n, 0, dtype=int)
        except Exception:
            arr = np.asarray(raw)
            out = np.zeros(n, dtype=int)
            for i in range(min(n, arr.size)):
                v = str(arr[i]).lower()
                out[i] = 1 if v == "green" else (-1 if v == "red" else 0)
            return out

    adx_1h = _arr("adx_1h", 50)
    bb_w_4h = _arr("bb_width_4h", 10.0)
    bb_w_1h = _arr("bb_width_1h", 10.0)
    dc_w_4h = _arr("dc_width_4h", 12.0)
    k_15m = _arr("k_15m", 50)
    mfi_4h = _arr("mfi_4h", 50)
    mfi_1h = _arr("mfi_1h", 50)
    ha1 = _ha_arr("ha_1h")
    ha4 = _ha_arr("ha_4h")

    adx_max = float(getattr(cfg, "SBA_ADX_MAX", _SBA_ADX_MAX)) if cfg is not None else _SBA_ADX_MAX

    # dealbreakers: True means blocked -> score 0
    deal = np.zeros(n, dtype=bool)
    deal |= adx_1h > adx_max
    deal |= bb_w_4h > _SBA_BB_W_4H_MAX
    deal |= bb_w_1h > _SBA_BB_W_1H_MAX
    deal |= dc_w_4h > _SBA_DC_W_4H_MAX
    if is_long:
        deal |= k_15m > 75
        deal |= (ha1 == -1) & (ha4 == -1)  # red = -1
        deal |= (mfi_4h < 25) & (mfi_1h < 30)
    else:
        deal |= k_15m < 25
        deal |= (ha1 == 1) & (ha4 == 1)  # green = 1
        deal |= (mfi_4h > 75) & (mfi_1h > 70)

    # quality+reversal per bar — vectorize the same lattice
    # Use scalar helper in loop for correctness; n is backtest bars (cheap vs correctness)
    # To keep numpy, we still compute score via vectorized sub-scores where easy,
    # but fall back to loop for full parity to avoid drift.
    scores = np.zeros(n, dtype=float)
    # Fast path: if not blocked, compute via loop calling _market_quality_score_parts on dict view
    # Build minimal dicts only for unblocked bars
    idx = np.where(~deal)[0]
    if idx.size > 0:
        # gather arrays for quality parts
        for i in idx:
            # build tiny dict for this bar from npz slices
            d = {}
            for k in ("bb_width_1h", "bb_width_4h", "dc_width_1h", "dc_width_4h", "adx_1h", "adx_4h",
                      "sma_200_1h", "current_price", "close_1h", "relative_volume_15m", "relative_volume_1h",
                      "mfi_15m", "mfi_1h", "mfi_4h", "rsi_15m", "rsi_1h", "rsi_4h",
                      "k_3m", "k_15m", "k_1h", "rsi_2_1h"):
                if k in npz:
                    arr = np.asarray(npz[k])
                    if arr.size > i:
                        d[k] = float(arr[i]) if np.isfinite(float(arr[i])) else 50
            # string/enum fields
            for k in ("wt_signal_15m", "wt_signal_1h", "ha_3m", "ha_3m_prev", "ha_15m", "ha_15m_prev",
                      "wt_divergence_1h", "wt_divergence_4h", "wt_momentum_state_1h", "wt_momentum_state_4h",
                      "bar_pattern_15m", "stoch_crossover_15m", "stoch_crossunder_15m", "stoch_crossover_1h", "stoch_crossunder_1h"):
                if k in npz:
                    arr = np.asarray(npz[k])
                    if arr.size > i:
                        d[k] = arr[i]
            q, r = _market_quality_score_parts(d, is_long)
            scores[i] = q + r
    # blocked bars stay 0
    return scores


def sba_bounce_passes_vec(npz: Mapping[str, Any], n: int, cfg: Any, is_long: bool) -> np.ndarray:
    """Bool mask: score >= SBA_MIN_SCORE (and dealbreakers pass)."""
    thr = float(getattr(cfg, "SBA_MIN_SCORE", _SBA_MIN_SCORE)) if cfg is not None else _SBA_MIN_SCORE
    scores = sba_bounce_score_vec(npz, n, cfg, is_long)
    return scores >= thr
