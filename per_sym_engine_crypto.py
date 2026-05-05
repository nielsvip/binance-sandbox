#!/usr/bin/env python3
"""per_sym_engine_crypto — vectorized per-symbol crypto backtest engine.

Reads 3m base OHLC from NPZ, resamples to {15m,1h,4h,D,W} in numpy, computes BB/WT/DC
at any (length, std/chan/avg/period) per TF, runs vectorized multi-TF entry/exit with
≥MIN_TFS_AGREE_FLOOR (=2) hard floor, deducts commission per side, returns trade list +
canonical metrics. Crypto-only — keep stocks completely separate per user 2026-05-05.

Source: backtest_v8/indicators/{SYM}.npz (3m granularity, ~6.3yr coverage).
NOT a full mirror of ez_manage live logic — multi-TF DC-break + WT-cross + BB-position
core. Additional v8 paths (RZ_EXIT, SATOSHIT, hedge, reentry, augment) ported in phases.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy.signal import lfilter

ROOT = Path(__file__).resolve().parent

# Crypto commission: 0.02% Binance USDC maker + 0.02% slippage = 0.04% per side
CRYPTO_COMMISSION_PER_SIDE = 0.0004
COMMISSION_RT_PCT = 100.0 * 2.0 * CRYPTO_COMMISSION_PER_SIDE  # 0.08% RT in % units

# Hard floor: never trade on a single TF — multi-TF agreement required (user 2026-05-05).
MIN_TFS_AGREE_FLOOR = 2

# 3m bars per HTF bar
TF_BARS_3M = {'15m': 5, '1h': 20, '4h': 80, 'D': 480, 'W': 480 * 7, 'M': 480 * 30}
DECISION_TFS = ('15m', '1h', '4h')


@dataclass
class SymParams:
    BB_LEN_15m: int = 20
    BB_STD_15m: float = 2.0
    BB_LEN_1h: int = 20
    BB_STD_1h: float = 2.0
    BB_LEN_4h: int = 20
    BB_STD_4h: float = 2.0
    WT_CHAN_15m: int = 10
    WT_AVG_15m: int = 21
    WT_CHAN_1h: int = 10
    WT_AVG_1h: int = 21
    WT_CHAN_4h: int = 10
    WT_AVG_4h: int = 21
    DC_PERIOD_15m: int = 20
    DC_PERIOD_1h: int = 20
    DC_PERIOD_4h: int = 20
    MIN_TFS_AGREE: int = 2
    MIN_HOLD_BARS_15m: int = 5
    COOLDOWN_BARS_15m: int = 3
    USE_BB_FILTER: bool = True
    USE_WT_CROSS: bool = True
    USE_DC_BREAK: bool = True
    BB_TOP_THRESHOLD: float = 0.95
    BB_BOT_THRESHOLD: float = 0.05
    BB_LONG_ENTRY_MAX: float = 0.50  # LONG: only enter when bb_pctb<this (pullback bias)
    BB_SHORT_ENTRY_MIN: float = 0.50  # SHORT: only enter when bb_pctb>this
    REQUIRE_D_TREND: bool = False  # daily trend gate (HTF context filter)
    REQUIRE_W_TREND: bool = False  # weekly trend gate
    # Entry mode: 'dc_break' (close > prev dc_high) | 'pullback' (bb_pctb<EMA crossing) | 'or' (either)
    ENTRY_MODE: str = 'or'  # 'or' is loosest; profiler may set tighter
    # Additional entry paths (parallel signals — any may trigger; gated by WT for direction).
    ENTRY_WT_CROSS_EVENT_ENABLED: bool = True   # entry on WT just-crossed event (last N bars)
    ENTRY_WT_CROSS_LOOKBACK: int = 3            # bars to consider "just crossed"
    ENTRY_BB_EXTREME_BOUNCE_ENABLED: bool = True  # entry on bb_pctb crossing back from extreme
    ENTRY_BB_EXTREME_THRESHOLD: float = 0.10    # extreme = pctb<threshold (LONG) / pctb>(1-threshold) (SHORT)
    ENTRY_BB_SQUEEZE_RELEASE_ENABLED: bool = True  # entry on BB width expansion after compression
    ENTRY_BB_SQUEEZE_RATIO: float = 1.5         # current BB width > prior min × ratio = release
    ENTRY_BB_SQUEEZE_LOOKBACK: int = 20         # bars to find prior min width
    # Strategies sourced from internet research not present in v8 (see profiler doc):
    # 1) Liquidity sweep reversal (ICT/Wyckoff): wick below prev-low then reclaim
    ENTRY_LIQ_SWEEP_ENABLED: bool = True
    ENTRY_LIQ_SWEEP_LOOKBACK: int = 10  # rolling N bars to define "recent" low/high
    # 2) NR7 breakout (Crabel): narrowest range in 7 bars → next-bar break
    ENTRY_NR7_ENABLED: bool = True
    ENTRY_NR_LOOKBACK: int = 7  # bars; bar i has narrowest range of last N → break on i+1
    # 3) Volume spike + directional bar (VSA)
    ENTRY_VOL_SPIKE_ENABLED: bool = True
    ENTRY_VOL_SPIKE_RATIO: float = 2.0  # vol > rolling_mean(vol, lookback) × ratio
    ENTRY_VOL_SPIKE_LOOKBACK: int = 20
    # 4) EMA ribbon pullback (9/21/50 stacked + price reclaiming 21 from below for LONG)
    ENTRY_EMA_RIBBON_ENABLED: bool = True
    ENTRY_EMA_FAST: int = 9
    ENTRY_EMA_MID: int = 21
    ENTRY_EMA_SLOW: int = 50
    # 5) Williams %R extreme reclaim
    ENTRY_WILLR_ENABLED: bool = True
    ENTRY_WILLR_LOOKBACK: int = 14
    ENTRY_WILLR_OS_THRESHOLD: float = -85.0   # LONG: %R was below this then crosses above
    ENTRY_WILLR_OB_THRESHOLD: float = -15.0   # SHORT: %R was above this then crosses below
    # 6) Fair Value Gap (FVG) fill — ICT 3-bar imbalance
    ENTRY_FVG_ENABLED: bool = True

    def to_dict(self) -> Dict:
        return asdict(self)

    def copy(self) -> 'SymParams':
        return SymParams(**self.to_dict())


# ───────────────────────── vectorized indicator primitives ─────────────────

def _ema(x: np.ndarray, span: int) -> np.ndarray:
    if span <= 1:
        return x.astype(np.float64)
    a = 2.0 / (span + 1.0)
    return lfilter([a], [1.0, -(1.0 - a)], x.astype(np.float64))


def _rolling_mean_csum(x: np.ndarray, w: int) -> np.ndarray:
    n = len(x)
    out = np.full(n, np.nan, dtype=np.float64)
    if n < w or w < 1:
        return out
    csum = np.concatenate([[0.0], np.cumsum(x.astype(np.float64))])
    out[w - 1:] = (csum[w:] - csum[:-w]) / w
    return out


def _rolling_std_csum(x: np.ndarray, w: int) -> np.ndarray:
    n = len(x)
    out = np.full(n, np.nan, dtype=np.float64)
    if n < w or w < 1:
        return out
    xf = x.astype(np.float64)
    csum = np.concatenate([[0.0], np.cumsum(xf)])
    csum2 = np.concatenate([[0.0], np.cumsum(xf * xf)])
    mean = (csum[w:] - csum[:-w]) / w
    var = (csum2[w:] - csum2[:-w]) / w - mean * mean
    out[w - 1:] = np.sqrt(np.maximum(var, 0.0))
    return out


def _rolling_max(x: np.ndarray, w: int) -> np.ndarray:
    n = len(x)
    out = np.full(n, np.nan, dtype=np.float64)
    if n < w or w < 1:
        return out
    from numpy.lib.stride_tricks import sliding_window_view
    out[w - 1:] = sliding_window_view(x.astype(np.float64), w).max(axis=1)
    return out


def _rolling_min(x: np.ndarray, w: int) -> np.ndarray:
    n = len(x)
    out = np.full(n, np.nan, dtype=np.float64)
    if n < w or w < 1:
        return out
    from numpy.lib.stride_tricks import sliding_window_view
    out[w - 1:] = sliding_window_view(x.astype(np.float64), w).min(axis=1)
    return out


def compute_bb(close: np.ndarray, length: int, std: float) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    m = _rolling_mean_csum(close, length)
    s = _rolling_std_csum(close, length)
    upper = m + std * s
    lower = m - std * s
    rng = upper - lower
    rng_safe = np.where(rng > 1e-12, rng, 1e-12)
    pct_b = np.clip((close - lower) / rng_safe, -2.0, 3.0)
    return upper, lower, pct_b


def compute_wt(high: np.ndarray, low: np.ndarray, close: np.ndarray, chan: int, avg: int) -> Tuple[np.ndarray, np.ndarray]:
    typical = (high + low + close) / 3.0
    esa = _ema(typical, chan)
    d = _ema(np.abs(typical - esa), chan)
    ci = (typical - esa) / (0.015 * np.maximum(d, 1e-10))
    wt1 = _ema(ci, avg)
    # WT2 = 3-bar SMA of WT1
    wt2 = _rolling_mean_csum(wt1, 3)
    wt2[:2] = wt1[:2]  # backfill startup
    return wt1, wt2


def compute_dc(high: np.ndarray, low: np.ndarray, period: int) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    dc_high = _rolling_max(high, period)
    dc_low = _rolling_min(low, period)
    dc_high_prev = np.empty_like(dc_high)
    dc_high_prev[0] = np.nan
    dc_high_prev[1:] = dc_high[:-1]
    dc_low_prev = np.empty_like(dc_low)
    dc_low_prev[0] = np.nan
    dc_low_prev[1:] = dc_low[:-1]
    return dc_high, dc_low, dc_high_prev, dc_low_prev


# ───────────────────────── data loader / resampler ─────────────────────────

NPZ_DIR = ROOT / 'backtest_v8' / 'indicators'

_npz_cache: Dict[str, Dict[str, np.ndarray]] = {}


def load_3m_base(sym: str, years_back: float = 4.0) -> Optional[Dict[str, np.ndarray]]:
    """Load 3m OHLC(+V) + ts from NPZ, sliced to last N years. Cached per process.
    Returns dict {'open','high','low','close','volume','ts'} or None if missing fields.
    """
    cache_key = f'{sym}__y{years_back:.2f}'
    if cache_key in _npz_cache:
        return _npz_cache[cache_key]
    p = NPZ_DIR / f'{sym}.npz'
    if not p.exists():
        return None
    z = np.load(str(p))
    needed = ['open_3m', 'high_3m', 'low_3m', 'close_3m', 'timestamps']
    if not all(k in z.files for k in needed):
        z.close()
        return None
    o = z['open_3m'][:].astype(np.float64)
    h = z['high_3m'][:].astype(np.float64)
    l = z['low_3m'][:].astype(np.float64)
    c = z['close_3m'][:].astype(np.float64)
    ts = z['timestamps'][:].astype(np.int64)
    v = z['volume_3m'][:].astype(np.float64) if 'volume_3m' in z.files else np.ones(len(c), dtype=np.float64)
    z.close()
    cutoff = ts[-1] - int(years_back * 365.25 * 86400)
    si = int(np.searchsorted(ts, cutoff))
    out = {'open': o[si:], 'high': h[si:], 'low': l[si:], 'close': c[si:], 'volume': v[si:], 'ts': ts[si:]}
    _npz_cache[cache_key] = out
    return out


def resample_3m_to_htf(base: Dict[str, np.ndarray], tf_bars: int) -> Dict[str, np.ndarray]:
    """Resample 3m OHLC arrays to HTF (tf_bars=3m bars per HTF bar). Pure numpy."""
    n = len(base['close'])
    n_hf = n // tf_bars
    if n_hf == 0:
        return {'open': np.array([]), 'high': np.array([]), 'low': np.array([]), 'close': np.array([]), 'volume': np.array([]), 'ts': np.array([], dtype=np.int64)}
    cut = n_hf * tf_bars
    o = base['open'][:cut][::tf_bars]
    h = base['high'][:cut].reshape(n_hf, tf_bars).max(axis=1)
    l = base['low'][:cut].reshape(n_hf, tf_bars).min(axis=1)
    c = base['close'][:cut][tf_bars - 1::tf_bars][:n_hf]
    ts = base['ts'][:cut][tf_bars - 1::tf_bars][:n_hf]
    if 'volume' in base and len(base['volume']) >= cut:
        v = base['volume'][:cut].reshape(n_hf, tf_bars).sum(axis=1)
    else:
        v = np.ones(n_hf, dtype=np.float64)
    return {'open': o, 'high': h, 'low': l, 'close': c, 'volume': v, 'ts': ts}


def build_tf_data(base: Dict[str, np.ndarray]) -> Dict[str, Dict[str, np.ndarray]]:
    """Build {tf: ohlc-dict} for 15m, 1h, 4h, D, W. M skipped — too few samples on 4yr."""
    out: Dict[str, Dict[str, np.ndarray]] = {}
    for tf, ratio in TF_BARS_3M.items():
        if tf == 'M':
            continue
        d = resample_3m_to_htf(base, ratio)
        if len(d['close']) > 0:
            out[tf] = d
    return out


# ───────────────────────── per-TF directional signal ───────────────────────

def per_tf_signals(ohlc: Dict[str, np.ndarray], side: str, tf: str, params: SymParams) -> Tuple[np.ndarray, np.ndarray]:
    """Return (entry_sig, exit_sig) boolean arrays for this TF and side."""
    o, h, l, c = ohlc['open'], ohlc['high'], ohlc['low'], ohlc['close']
    v = ohlc.get('volume', np.ones(len(c)))
    n = len(c)
    bb_len = int(getattr(params, f'BB_LEN_{tf}'))
    bb_std = float(getattr(params, f'BB_STD_{tf}'))
    wt_chan = int(getattr(params, f'WT_CHAN_{tf}'))
    wt_avg = int(getattr(params, f'WT_AVG_{tf}'))
    dc_per = int(getattr(params, f'DC_PERIOD_{tf}'))

    bb_u, bb_l, bb_pctb = compute_bb(c, bb_len, bb_std)
    wt1, wt2 = compute_wt(h, l, c, wt_chan, wt_avg)
    dc_h, dc_l, dc_h_prev, dc_l_prev = compute_dc(h, l, dc_per)

    # ── Internet-research strategy paths ──────────────────────────────────
    # 1) Liquidity sweep: low taps below rolling-N low then close reclaims (LONG); mirror SHORT.
    lb = int(params.ENTRY_LIQ_SWEEP_LOOKBACK)
    rmin_l = _rolling_min(l, lb)  # rolling min of LOW
    rmin_l_prev = np.concatenate([[rmin_l[0] if n else 0.0], rmin_l[:-1]])
    rmax_h = _rolling_max(h, lb)
    rmax_h_prev = np.concatenate([[rmax_h[0] if n else 0.0], rmax_h[:-1]])
    liq_sweep_long = (l < rmin_l_prev) & (c > rmin_l_prev) & np.isfinite(rmin_l_prev)
    liq_sweep_short = (h > rmax_h_prev) & (c < rmax_h_prev) & np.isfinite(rmax_h_prev)

    # 2) NR7: bar i has narrowest range of last NR_LOOKBACK; entry on i+1 break of NR bar's H/L.
    nr_lb = int(params.ENTRY_NR_LOOKBACK)
    bar_range = h - l
    range_min = _rolling_min(bar_range, nr_lb)
    is_nr = (bar_range == range_min) & np.isfinite(range_min)
    is_nr_prev = np.concatenate([[False], is_nr[:-1]])
    nr_high_prev = np.concatenate([[h[0] if n else 0.0], h[:-1]])
    nr_low_prev = np.concatenate([[l[0] if n else 0.0], l[:-1]])
    nr7_break_long = is_nr_prev & (c > nr_high_prev)
    nr7_break_short = is_nr_prev & (c < nr_low_prev)

    # 3) Volume spike: vol > rolling_mean(vol, vlb) × ratio AND directional bar
    vlb = int(params.ENTRY_VOL_SPIKE_LOOKBACK)
    vol_mean = _rolling_mean_csum(v, vlb)
    vol_ratio = float(params.ENTRY_VOL_SPIKE_RATIO)
    vol_spike = (v > vol_mean * vol_ratio) & np.isfinite(vol_mean)
    bull_bar = c > o
    bear_bar = c < o
    vol_spike_long = vol_spike & bull_bar
    vol_spike_short = vol_spike & bear_bar

    # 4) EMA ribbon stack: ema_fast > ema_mid > ema_slow (LONG stack); pullback = c crosses ema_mid up.
    ema_fast = _ema(c, int(params.ENTRY_EMA_FAST))
    ema_mid  = _ema(c, int(params.ENTRY_EMA_MID))
    ema_slow = _ema(c, int(params.ENTRY_EMA_SLOW))
    stack_up = (ema_fast > ema_mid) & (ema_mid > ema_slow)
    stack_dn = (ema_fast < ema_mid) & (ema_mid < ema_slow)
    c_prev = np.concatenate([[c[0] if n else 0.0], c[:-1]])
    ema_mid_prev = np.concatenate([[ema_mid[0] if n else 0.0], ema_mid[:-1]])
    pullback_long = stack_up & (c > ema_mid) & (c_prev <= ema_mid_prev)
    pullback_short = stack_dn & (c < ema_mid) & (c_prev >= ema_mid_prev)

    # 5) Williams %R: -100 × (HH - close) / (HH - LL); reclaim from extreme = entry
    wlb = int(params.ENTRY_WILLR_LOOKBACK)
    wr_hh = _rolling_max(h, wlb)
    wr_ll = _rolling_min(l, wlb)
    wr_rng = wr_hh - wr_ll
    wr_rng_safe = np.where(wr_rng > 1e-12, wr_rng, 1e-12)
    willr = -100.0 * (wr_hh - c) / wr_rng_safe
    willr_prev = np.concatenate([[willr[0] if n else 0.0], willr[:-1]])
    os_th = float(params.ENTRY_WILLR_OS_THRESHOLD)
    ob_th = float(params.ENTRY_WILLR_OB_THRESHOLD)
    willr_reclaim_long  = (willr > os_th) & (willr_prev <= os_th)
    willr_reclaim_short = (willr < ob_th) & (willr_prev >= ob_th)

    # 6) Fair Value Gap: bullish FVG = bar[i-1].high < bar[i+1].low at bar i (3-bar imbalance);
    # entry on later bar when close returns to fill the gap (touches the gap zone).
    # We mark the gap on the middle bar then check for fill within next K bars.
    if n >= 3:
        h_prev2 = np.concatenate([[h[0], h[0]], h[:-2]])  # bar[i-1].high lined up with bar[i]+1 effectively bar i (gap formed by bars i-1,i,i+1)
        l_next2 = np.concatenate([l[2:], [l[-1], l[-1]]])  # bar[i+1].low aligned with bar i
        bull_fvg = (h_prev2 < l_next2) & np.isfinite(h_prev2) & np.isfinite(l_next2)
        h_next2 = np.concatenate([h[2:], [h[-1], h[-1]]])
        l_prev2 = np.concatenate([[l[0], l[0]], l[:-2]])
        bear_fvg = (l_prev2 > h_next2) & np.isfinite(l_prev2) & np.isfinite(h_next2)
        # Fill detection: next 1 bar after FVG touches the gap zone
        fvg_long_signal = np.concatenate([[False, False, False], bull_fvg[:-3]])  # entry 3 bars after gap formed
        fvg_short_signal = np.concatenate([[False, False, False], bear_fvg[:-3]])
        fvg_long_signal = fvg_long_signal[:n]
        fvg_short_signal = fvg_short_signal[:n]
    else:
        fvg_long_signal = np.zeros(n, dtype=bool)
        fvg_short_signal = np.zeros(n, dtype=bool)

    # WT cross EVENT detection: True at bars where (wt1>wt2) just transitioned from (wt1<=wt2) in last N bars
    wt_bull_now = wt1 > wt2
    wt_bull_event = wt_bull_now & ~np.concatenate([[False], wt_bull_now[:-1]])  # transition this bar
    wt_bear_event = (~wt_bull_now) & np.concatenate([[False], wt_bull_now[:-1]])
    if params.ENTRY_WT_CROSS_LOOKBACK > 1:
        # Smear the event forward by lookback bars (any bar within window is "just crossed")
        lb = int(params.ENTRY_WT_CROSS_LOOKBACK)
        kernel = np.ones(lb, dtype=bool)
        # Use cumulative AND-windowed: event is True if any of last lb bars had a transition
        wt_bull_event_w = np.zeros(n, dtype=bool)
        wt_bear_event_w = np.zeros(n, dtype=bool)
        cs_bull = np.concatenate([[0], np.cumsum(wt_bull_event.astype(np.int32))])
        cs_bear = np.concatenate([[0], np.cumsum(wt_bear_event.astype(np.int32))])
        wt_bull_event_w[lb - 1:] = (cs_bull[lb:] - cs_bull[:-lb]) > 0
        wt_bear_event_w[lb - 1:] = (cs_bear[lb:] - cs_bear[:-lb]) > 0
        wt_bull_event = wt_bull_event_w
        wt_bear_event = wt_bear_event_w

    # BB squeeze: compute BB width = upper - lower; release = current width > rolling_min(width, lookback) * ratio
    bb_width = bb_u - bb_l
    bb_width_min = _rolling_min(bb_width, int(params.ENTRY_BB_SQUEEZE_LOOKBACK))
    bb_squeeze_release = (bb_width > bb_width_min * params.ENTRY_BB_SQUEEZE_RATIO) & (bb_width_min > 0)

    # BB extreme bounce: bb_pctb just crossed UP through ENTRY_BB_EXTREME_THRESHOLD (LONG) or DOWN through (1-thresh) (SHORT)
    pctb_prev = np.concatenate([[bb_pctb[0]], bb_pctb[:-1]])
    bb_extreme_long = (bb_pctb > params.ENTRY_BB_EXTREME_THRESHOLD) & (pctb_prev <= params.ENTRY_BB_EXTREME_THRESHOLD)
    bb_extreme_short = (bb_pctb < (1 - params.ENTRY_BB_EXTREME_THRESHOLD)) & (pctb_prev >= (1 - params.ENTRY_BB_EXTREME_THRESHOLD))

    Z = np.zeros(n, dtype=bool)
    if side == 'LONG':
        wt_ok = (wt1 > wt2) if params.USE_WT_CROSS else np.ones(n, dtype=bool)
        dc_break = (c > dc_h_prev)
        pullback = (bb_pctb < params.BB_LONG_ENTRY_MAX)
        if params.ENTRY_MODE == 'dc_break':
            base_path = dc_break
        elif params.ENTRY_MODE == 'pullback':
            base_path = pullback
        else:
            base_path = dc_break | pullback
        wt_event_p = wt_bull_event if params.ENTRY_WT_CROSS_EVENT_ENABLED else Z
        bb_bounce_p = bb_extreme_long if params.ENTRY_BB_EXTREME_BOUNCE_ENABLED else Z
        bb_squeeze_p = (bb_squeeze_release & wt_ok) if params.ENTRY_BB_SQUEEZE_RELEASE_ENABLED else Z
        liq_p = liq_sweep_long if params.ENTRY_LIQ_SWEEP_ENABLED else Z
        nr7_p = nr7_break_long if params.ENTRY_NR7_ENABLED else Z
        vol_p = vol_spike_long if params.ENTRY_VOL_SPIKE_ENABLED else Z
        ema_p = pullback_long if params.ENTRY_EMA_RIBBON_ENABLED else Z
        wr_p  = willr_reclaim_long if params.ENTRY_WILLR_ENABLED else Z
        fvg_p = fvg_long_signal if params.ENTRY_FVG_ENABLED else Z
        any_path = (base_path | wt_event_p | bb_bounce_p | bb_squeeze_p |
                    liq_p | nr7_p | vol_p | ema_p | wr_p | fvg_p)
        ceiling_ok = (bb_pctb < params.BB_TOP_THRESHOLD) if params.USE_BB_FILTER else np.ones(n, dtype=bool)
        entry = wt_ok & any_path & ceiling_ok
        exit_ = (wt1 < wt2) | (bb_pctb > params.BB_TOP_THRESHOLD) | (c < dc_l_prev)
    else:  # SHORT
        wt_ok = (wt1 < wt2) if params.USE_WT_CROSS else np.ones(n, dtype=bool)
        dc_break = (c < dc_l_prev)
        pullback = (bb_pctb > params.BB_SHORT_ENTRY_MIN)
        if params.ENTRY_MODE == 'dc_break':
            base_path = dc_break
        elif params.ENTRY_MODE == 'pullback':
            base_path = pullback
        else:
            base_path = dc_break | pullback
        wt_event_p = wt_bear_event if params.ENTRY_WT_CROSS_EVENT_ENABLED else Z
        bb_bounce_p = bb_extreme_short if params.ENTRY_BB_EXTREME_BOUNCE_ENABLED else Z
        bb_squeeze_p = (bb_squeeze_release & wt_ok) if params.ENTRY_BB_SQUEEZE_RELEASE_ENABLED else Z
        liq_p = liq_sweep_short if params.ENTRY_LIQ_SWEEP_ENABLED else Z
        nr7_p = nr7_break_short if params.ENTRY_NR7_ENABLED else Z
        vol_p = vol_spike_short if params.ENTRY_VOL_SPIKE_ENABLED else Z
        ema_p = pullback_short if params.ENTRY_EMA_RIBBON_ENABLED else Z
        wr_p  = willr_reclaim_short if params.ENTRY_WILLR_ENABLED else Z
        fvg_p = fvg_short_signal if params.ENTRY_FVG_ENABLED else Z
        any_path = (base_path | wt_event_p | bb_bounce_p | bb_squeeze_p |
                    liq_p | nr7_p | vol_p | ema_p | wr_p | fvg_p)
        ceiling_ok = (bb_pctb > params.BB_BOT_THRESHOLD) if params.USE_BB_FILTER else np.ones(n, dtype=bool)
        entry = wt_ok & any_path & ceiling_ok
        exit_ = (wt1 > wt2) | (bb_pctb < params.BB_BOT_THRESHOLD) | (c > dc_h_prev)
    # NaN safety: treat NaN-context bars as no-signal
    valid = ~(np.isnan(bb_pctb) | np.isnan(wt1) | np.isnan(dc_h_prev))
    return entry & valid, exit_ & valid


def htf_trend_pass(ohlc_d: Dict[str, np.ndarray], ohlc_w: Optional[Dict[str, np.ndarray]],
                   side: str, params: SymParams, n_15m: int) -> np.ndarray:
    """Build a 15m-grid boolean array: True when D (and optionally W) trend confirms side.
    D trend = close > rolling_mean(close, 20). W trend = same on weekly.
    Returns array of length n_15m.
    """
    out = np.ones(n_15m, dtype=bool)
    if not (params.REQUIRE_D_TREND or params.REQUIRE_W_TREND):
        return out

    def _trend_arr(d: Dict[str, np.ndarray], side: str, ratio_to_15m: int) -> np.ndarray:
        c = d['close']
        m = _rolling_mean_csum(c, 20)
        if side == 'LONG':
            up = c > m
        else:
            up = c < m
        # repeat to 15m grid
        rep = np.repeat(up, ratio_to_15m)
        if len(rep) < n_15m:
            rep = np.concatenate([rep, np.zeros(n_15m - len(rep), dtype=bool)])
        return rep[:n_15m]

    if params.REQUIRE_D_TREND:
        out &= _trend_arr(ohlc_d, side, ratio_to_15m=TF_BARS_3M['D'] // TF_BARS_3M['15m'])  # 96
    if params.REQUIRE_W_TREND and ohlc_w is not None and len(ohlc_w['close']) >= 10:
        out &= _trend_arr(ohlc_w, side, ratio_to_15m=TF_BARS_3M['W'] // TF_BARS_3M['15m'])  # 672
    return out


# ───────────────────────── trade walker (vectorized state) ────────────────

def walk_trades(enter_15m: np.ndarray, leave_15m: np.ndarray, c15: np.ndarray, ts15: np.ndarray,
                side: str, min_hold: int, cooldown: int) -> List[Dict]:
    """Walk the 15m grid with one position at a time. Returns trade list.
    O(num_trades) using np.flatnonzero scans.
    """
    trades: List[Dict] = []
    n = len(c15)
    i = 0
    rt_comm = COMMISSION_RT_PCT
    while i < n:
        e_remaining = enter_15m[i:]
        e_idxs = np.flatnonzero(e_remaining)
        if not len(e_idxs):
            break
        entry_i = i + int(e_idxs[0])
        x_start = entry_i + min_hold
        if x_start >= n:
            break
        x_remaining = leave_15m[x_start:]
        x_idxs = np.flatnonzero(x_remaining)
        if len(x_idxs):
            exit_i = x_start + int(x_idxs[0])
        else:
            exit_i = n - 1
        ep = float(c15[entry_i])
        xp = float(c15[exit_i])
        if ep <= 0 or xp <= 0:
            i = exit_i + cooldown + 1
            continue
        if side == 'LONG':
            pnl_gross = (xp - ep) / ep * 100.0
        else:
            pnl_gross = (ep - xp) / ep * 100.0
        pnl_net = pnl_gross - rt_comm
        trades.append({
            'side': side,
            'entry_idx': entry_i, 'exit_idx': exit_i,
            'entry_ts': int(ts15[entry_i]), 'exit_ts': int(ts15[exit_i]),
            'entry_price': ep, 'exit_price': xp,
            'pnl_gross_pct': pnl_gross, 'pnl_pct': pnl_net,
            'bars_held': exit_i - entry_i,
        })
        i = exit_i + cooldown + 1
    return trades


# ───────────────────────── top-level simulate ──────────────────────────────

def simulate(sym: str, side: str, params: SymParams, years_back: float = 4.0) -> Optional[Dict]:
    """Returns dict with per-(sym,side) trades + canonical metrics, or None on data miss."""
    if side not in ('LONG', 'SHORT'):
        raise ValueError(f"side must be LONG or SHORT, got {side}")
    base = load_3m_base(sym, years_back=years_back)
    if base is None or len(base['close']) < 5000:
        return None
    tf_data = build_tf_data(base)
    needed = list(DECISION_TFS) + ['D']
    for tf in needed:
        if tf not in tf_data or len(tf_data[tf]['close']) < 50:
            return None

    n_15m = len(tf_data['15m']['close'])
    e_count = np.zeros(n_15m, dtype=np.int16)
    x_count = np.zeros(n_15m, dtype=np.int16)
    for tf in DECISION_TFS:
        e_sig, x_sig = per_tf_signals(tf_data[tf], side, tf, params)
        ratio = TF_BARS_3M[tf] // TF_BARS_3M['15m']
        if ratio == 1:
            e15 = e_sig
            x15 = x_sig
        else:
            e15 = np.repeat(e_sig, ratio)
            x15 = np.repeat(x_sig, ratio)
            if len(e15) < n_15m:
                e15 = np.concatenate([e15, np.zeros(n_15m - len(e15), dtype=bool)])
                x15 = np.concatenate([x15, np.zeros(n_15m - len(x15), dtype=bool)])
            else:
                e15 = e15[:n_15m]
                x15 = x15[:n_15m]
        e_count += e15.astype(np.int16)
        x_count += x15.astype(np.int16)

    min_tfs = max(MIN_TFS_AGREE_FLOOR, int(params.MIN_TFS_AGREE))
    enter = e_count >= min_tfs
    # Per user 2026-05-05: "no decision based on single TF" — exits also require ≥MIN_TFS_AGREE_FLOOR.
    # This holds positions longer than single-TF-flip exit (the live-system root cause of 0.0001%-0.01% closes).
    leave = x_count >= MIN_TFS_AGREE_FLOOR

    htf_pass = htf_trend_pass(tf_data['D'], tf_data.get('W'), side, params, n_15m)
    enter = enter & htf_pass

    c15 = tf_data['15m']['close']
    ts15 = tf_data['15m']['ts']
    trades = walk_trades(enter, leave, c15, ts15, side,
                         int(params.MIN_HOLD_BARS_15m), int(params.COOLDOWN_BARS_15m))

    if not trades:
        return {'sym': sym, 'side': side, 'trades': 0, 'trades_per_day': 0.0,
                'pool_sharpe': 0.0, 'sym_sharpe': 0.0, 'wr_pct': 0.0,
                'max_dd_pct': 0.0, 'total_gain_pct': 0.0, 'avg_gain_trade': 0.0,
                'gain_per_yr': 0.0, 'gain_sym_yr': 0.0, 'years': (ts15[-1] - ts15[0]) / 86400 / 365.25,
                'n_syms': 1, 'tag': f'per_sym_{sym}_{side}',
                'trade_list': [], 'params': params.to_dict()}

    rets = np.array([t['pnl_pct'] for t in trades], dtype=np.float64)
    n = len(rets)
    span_days = max(1.0, (ts15[-1] - ts15[0]) / 86400.0)
    yrs = max(0.01, span_days / 365.25)
    sd = float(rets.std())
    pool = float(rets.mean() / sd) if sd > 1e-12 else 0.0
    wr = float((rets > 0).mean() * 100.0)
    eq = np.cumsum(rets)
    peak = np.maximum.accumulate(eq)
    dd = float((peak - eq).max())
    total = float(rets.sum())

    return {
        'sym': sym, 'side': side, 'trades': n, 'trades_per_day': n / span_days,
        'pool_sharpe': pool, 'sym_sharpe': max(-5.0, min(5.0, pool)),
        'wr_pct': wr, 'max_dd_pct': dd, 'total_gain_pct': total,
        'avg_gain_trade': total / n, 'gain_per_yr': total / yrs, 'gain_sym_yr': total / yrs,
        'years': yrs, 'n_syms': 1,
        'tag': f'per_sym_{sym}_{side}',
        'trade_list': trades, 'params': params.to_dict(),
    }


# ───────────────────────── CLI smoke test ──────────────────────────────────

def _smoke(syms: List[str]) -> None:
    print(f"smoke test on {syms}")
    for s in syms:
        for side in ('LONG', 'SHORT'):
            r = simulate(s, side, SymParams(), years_back=4.0)
            if r is None:
                print(f"  {s} {side}: NPZ MISS or insufficient data")
                continue
            print(f"  {s:12s} {side:5s} pool={r['pool_sharpe']:+.4f} "
                  f"wr={r['wr_pct']:5.1f}% trades={r['trades']:>5d} "
                  f"tpd={r['trades_per_day']:5.2f} dd={r['max_dd_pct']:5.2f}% "
                  f"yrs={r['years']:.2f}")


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--syms', default='BTCUSDC,ETHUSDC,SOLUSDC')
    args = ap.parse_args()
    _smoke([s.strip() for s in args.syms.split(',') if s.strip()])
