#!/usr/bin/env python3
"""
V7 Precompute — Build NPZ from klines using REAL tradier_indicators.py functions.

Calls the same functions as live (rsi_series, atr_series, stoch_result, wavetrend,
donchian, mfi_value, heikin_ashi, etc.) but extracts the FULL arrays instead of
just the last value. One call per TF per symbol = fast.

Klines from klines_cache/tradier/ (stocks) or klines_cache/ (crypto).
Output: backtest_v7/indicators/SYMBOL.npz

Usage:
    python3 backtest_v7_precompute.py --symbol AAPL --mode tradier
    python3 backtest_v7_precompute.py --all --mode tradier --workers 8
"""
import argparse
import json
import logging
import os
import platform
import sys
import time
from pathlib import Path
from typing import Dict, Optional

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("v7_precompute")

# Imports for new indicator fields (Improvement Framework A3+A4+A5, 2026-04-26).
# Existing scalar functions in tradier_indicators_extra.py are reused where useful;
# numpy-vectorized rolling wrappers below handle the time-series fields.
try:
    from tradier_indicators_extra import (
        compute_minervini_sepa as _scalar_sepa,
        compute_clenow_score as _scalar_clenow,
        detect_episodic_pivot as _scalar_ep,
    )
except Exception as _e:
    _scalar_sepa = None
    _scalar_clenow = None
    _scalar_ep = None

# Module-level mode flag set by main(). Used by compute_tf_arrays() to choose
# annualization factor (252 for tradier, 365 for crypto). Threading via kwarg
# would alter the public signature; module-level keeps existing call sites stable.
MODE = "tradier"


def _ann_factor() -> float:
    """Annualization factor: 252 trading days for stocks, 365 for crypto (24/7)."""
    return 365.0 if MODE == "crypto" else 252.0


def _yz_vol(open_arr: np.ndarray, high_arr: np.ndarray, low_arr: np.ndarray,
            close_arr: np.ndarray, n: int, ann_factor: float) -> np.ndarray:
    """Yang-Zhang (2000) annualized realized volatility, rolling window n. % units."""
    o = np.log(np.maximum(open_arr, 1e-10))
    h = np.log(np.maximum(high_arr, 1e-10))
    l = np.log(np.maximum(low_arr, 1e-10))
    c = np.log(np.maximum(close_arr, 1e-10))
    pc = np.roll(c, 1); pc[0] = c[0]
    o_minus_pc = o - pc
    c_minus_o = c - o
    rs = (h - c) * (h - o) + (l - c) * (l - o)
    def _roll_var(x):
        s1 = pd.Series(x).rolling(n).mean()
        s2 = pd.Series(x * x).rolling(n).mean()
        return (s2 - s1 * s1).clip(lower=0).values
    sig_o2 = _roll_var(o_minus_pc)
    sig_c2 = _roll_var(c_minus_o)
    sig_rs2 = pd.Series(rs).rolling(n).mean().clip(lower=0).values
    k = 0.34 / (1.34 + (n + 1) / max(n - 1, 1))
    yz_var = sig_o2 + k * sig_c2 + (1.0 - k) * sig_rs2
    yz_var = np.nan_to_num(yz_var, nan=0.0, posinf=0.0, neginf=0.0)
    return (np.sqrt(np.maximum(yz_var, 0)) * np.sqrt(ann_factor) * 100.0).astype(np.float32)


def _pk_vol(high_arr: np.ndarray, low_arr: np.ndarray,
            n: int, ann_factor: float) -> np.ndarray:
    """Parkinson high-low annualized volatility. % units."""
    h = np.log(np.maximum(high_arr, 1e-10))
    l = np.log(np.maximum(low_arr, 1e-10))
    hl2 = (h - l) ** 2
    var = pd.Series(hl2).rolling(n).mean().values / (4.0 * np.log(2.0))
    var = np.nan_to_num(var, nan=0.0, posinf=0.0, neginf=0.0)
    return (np.sqrt(np.maximum(var, 0)) * np.sqrt(ann_factor) * 100.0).astype(np.float32)


def _gk_vol(open_arr: np.ndarray, high_arr: np.ndarray, low_arr: np.ndarray,
            close_arr: np.ndarray, n: int, ann_factor: float) -> np.ndarray:
    """Garman-Klass annualized volatility. % units."""
    o = np.log(np.maximum(open_arr, 1e-10))
    h = np.log(np.maximum(high_arr, 1e-10))
    l = np.log(np.maximum(low_arr, 1e-10))
    c = np.log(np.maximum(close_arr, 1e-10))
    term1 = 0.5 * (h - l) ** 2
    term2 = (2.0 * np.log(2.0) - 1.0) * (c - o) ** 2
    var = pd.Series(term1 - term2).rolling(n).mean().clip(lower=0).values
    var = np.nan_to_num(var, nan=0.0, posinf=0.0, neginf=0.0)
    return (np.sqrt(np.maximum(var, 0)) * np.sqrt(ann_factor) * 100.0).astype(np.float32)


def _rolling_sepa(close_arr: np.ndarray, high_arr: np.ndarray, low_arr: np.ndarray,
                  volume_arr: np.ndarray) -> tuple:
    """Rolling Minervini SEPA: per-bar pass/score using bars[:i+1].
    Returns (sepa_pass int8, sepa_score int8). Daily TF only (slow loop)."""
    n = len(close_arr)
    sepa_pass = np.zeros(n, dtype=np.int8)
    sepa_score = np.zeros(n, dtype=np.int8)
    if n < 252 or _scalar_sepa is None:
        return sepa_pass, sepa_score
    cl = close_arr.tolist()
    hl = high_arr.tolist()
    ll = low_arr.tolist()
    vl = volume_arr.tolist()
    for i in range(252, n):
        try:
            res = _scalar_sepa(cl[: i + 1], hl[: i + 1], ll[: i + 1], vl[: i + 1])
            if res:
                sepa_pass[i] = 1 if res.get("sepa_pass") else 0
                sepa_score[i] = int(res.get("sepa_score", 0))
        except Exception:
            pass
    return sepa_pass, sepa_score


def _rolling_clenow(close_arr: np.ndarray, lookback: int = 90) -> tuple:
    """Rolling Clenow: per-bar slope_ann × R². Returns (score, slope, r2) float32 arrays.
    Daily TF only (slow loop)."""
    n = len(close_arr)
    score = np.zeros(n, dtype=np.float32)
    slope = np.zeros(n, dtype=np.float32)
    r2 = np.zeros(n, dtype=np.float32)
    if n < lookback + 5 or _scalar_clenow is None:
        return score, slope, r2
    cl = close_arr.tolist()
    for i in range(lookback + 5, n):
        try:
            res = _scalar_clenow(cl[: i + 1], lookback=lookback)
            if res:
                score[i] = float(res.get("clenow_score", 0.0))
                slope[i] = float(res.get("clenow_slope", 0.0))
                r2[i] = float(res.get("clenow_r2", 0.0))
        except Exception:
            pass
    return score, slope, r2


def _rolling_episodic_pivot(open_arr: np.ndarray, high_arr: np.ndarray,
                             low_arr: np.ndarray, close_arr: np.ndarray,
                             volume_arr: np.ndarray, fwd_days: int = 30) -> tuple:
    """Rolling Episodic Pivot detection (Daily TF). On bar i, run detect on bars[:i+1];
    if detected, mark ep_detected=1 on bars [i, i+fwd_days). Returns (ep_detected int8,
    ep_breakout_level float32, ep_direction int8 +1/-1/0)."""
    n = len(close_arr)
    ep_det = np.zeros(n, dtype=np.int8)
    ep_lvl = np.zeros(n, dtype=np.float32)
    ep_dir = np.zeros(n, dtype=np.int8)
    if n < 30 or _scalar_ep is None:
        return ep_det, ep_lvl, ep_dir
    for i in range(20, n):
        bars = []
        for j in range(max(0, i - 60), i + 1):
            bars.append({
                "open": float(open_arr[j]),
                "high": float(high_arr[j]),
                "low": float(low_arr[j]),
                "close": float(close_arr[j]),
                "volume": float(volume_arr[j]),
            })
        try:
            res = _scalar_ep(bars)
        except Exception:
            res = None
        if res and res.get("ep_detected"):
            lvl = float(res.get("ep_breakout_level", 0.0) or 0.0)
            d = res.get("ep_direction", "")
            d_int = 1 if d == "LONG" else (-1 if d == "SHORT" else 0)
            for k in range(i, min(n, i + fwd_days)):
                # Only fill if not already set by a more recent detection
                if ep_det[k] == 0:
                    ep_det[k] = 1
                    ep_lvl[k] = lvl
                    ep_dir[k] = d_int
    return ep_det, ep_lvl, ep_dir

if platform.system() == "Darwin":
    BASE_PATH = Path("/Users/niels/Documents/binance")
else:
    BASE_PATH = Path("/home/niels/binance-sandbox")

TRADIER_KLINES = BASE_PATH / "klines_cache_backtest" / "tradier" if not (BASE_PATH / "klines_cache" / "tradier").exists() or platform.system() != "Darwin" else BASE_PATH / "klines_cache" / "tradier"
# Use klines_cache_backtest for full 4yr history on server; klines_cache has only ~1200 bars for many symbols
_kcb = BASE_PATH / "klines_cache_backtest"
CRYPTO_KLINES = _kcb if _kcb.exists() and platform.system() != "Darwin" else BASE_PATH / "klines_cache"
OUT_DIR = BASE_PATH / "backtest_v8" / "indicators"

STR_MAP = {"green": 1, "red": -1, "neutral": 0, "BUY": 1, "SELL": -1, "NEUTRAL": 0,
           "bullish": 1, "bearish": -1, "strong_bullish": 2, "strong_bearish": -2,
           "higher": 1, "lower": -1, "none": 0, "bull_cross": 1, "bear_cross": -1}


def load_klines(path: Path) -> Optional[pd.DataFrame]:
    if not path.exists():
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        if not data:
            return None
        df = pd.DataFrame(data)
        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        if "timestamp" in df.columns:
            df["timestamp_dt"] = pd.to_datetime(df["timestamp"], utc=True)
        elif "timestamp_dt" in df.columns:
            df["timestamp_dt"] = pd.to_datetime(df["timestamp_dt"], utc=True)
        else:
            return None
        df = df.set_index("timestamp_dt").sort_index()
        df = df[~df.index.duplicated(keep="last")]
        return df
    except Exception as e:
        logger.warning(f"Load failed {path}: {e}")
        return None


def compute_tf_arrays(df: pd.DataFrame, tf: str) -> Dict[str, np.ndarray]:
    """Compute ALL indicators for one TF, returning FULL arrays (not scalars).
    Uses the SAME functions from tradier_indicators.py."""
    from tradier_indicators import (rsi_series, atr_series, mfi_value, stoch_result,
                                     donchian, heikin_ashi, wavetrend,
                                     relative_volume, wavetrend_intelligence,
                                     crossover_flags)
    n = len(df)
    if n < 30:
        return {}
    out = {}
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    open_ = df["open"].astype(float)
    volume = df["volume"].astype(float) if "volume" in df.columns else pd.Series(np.ones(n), index=df.index)
    # OHLCV arrays
    out[f"open_{tf}"] = open_.values.astype(np.float32)
    out[f"high_{tf}"] = high.values.astype(np.float32)
    out[f"low_{tf}"] = low.values.astype(np.float32)
    out[f"close_{tf}"] = close.values.astype(np.float32)
    out[f"volume_{tf}"] = volume.values.astype(np.float32)
    # Prev arrays
    for name, series in [("close", close), ("high", high), ("low", low)]:
        prev = series.shift(1).fillna(series.iloc[0])
        out[f"{name}_{tf}_prev"] = prev.values.astype(np.float32)
    # RSI 14
    rsi = rsi_series(close, 14)
    if rsi is not None and not rsi.empty:
        out[f"rsi_{tf}"] = rsi.values.astype(np.float32)
    # ATR 14
    atr = atr_series(df, 14)
    if atr is not None and not atr.empty:
        out[f"atr_{tf}"] = atr.values.astype(np.float32)
        out[f"atr_{tf}_prev"] = atr.shift(1).fillna(atr.iloc[0]).values.astype(np.float32)
    # Stochastic RSI (full series)
    from tradier_indicators import stoch_rsi
    stoch_df = stoch_rsi(close, 14, 7, 7)
    k_series = stoch_df["k"] if stoch_df is not None and "k" in stoch_df.columns else (stoch_df["%K"] if stoch_df is not None and "%K" in stoch_df.columns else None)
    d_series = stoch_df["d"] if stoch_df is not None and "d" in stoch_df.columns else (stoch_df["%D"] if stoch_df is not None and "%D" in stoch_df.columns else None)
    if k_series is not None and len(k_series) == n:
        out[f"stoch_k_{tf}"] = k_series.values.astype(np.float32)
        out[f"stoch_d_{tf}"] = d_series.values.astype(np.float32)
        out[f"stoch_k_{tf}_prev"] = k_series.shift(1).fillna(50).values.astype(np.float32)
        out[f"stoch_d_{tf}_prev"] = d_series.shift(1).fillna(50).values.astype(np.float32)
        # Crossovers
        co = ((k_series > d_series) & (k_series.shift(1) <= d_series.shift(1))).fillna(False)
        cu = ((k_series < d_series) & (k_series.shift(1) >= d_series.shift(1))).fillna(False)
        out[f"stoch_crossover_{tf}"] = co.values.astype(np.int8)
        out[f"stoch_crossunder_{tf}"] = cu.values.astype(np.int8)
    # MFI (full series via rolling)
    tp = (high + low + close) / 3.0
    raw_mf = tp * volume
    pos_mf = raw_mf.where(tp.diff() > 0, 0)
    neg_mf = raw_mf.where(tp.diff() < 0, 0)
    pos_sum = pos_mf.rolling(14, min_periods=1).sum()
    neg_sum = neg_mf.rolling(14, min_periods=1).sum()
    mfi = 100.0 - (100.0 / (1.0 + pos_sum / neg_sum.replace(0, 1e-10)))
    out[f"mfi_{tf}"] = mfi.values.astype(np.float32)
    # Donchian channels (20)
    dc_high = high.rolling(20, min_periods=1).max()
    dc_low = low.rolling(20, min_periods=1).min()
    dc_basis = (dc_high + dc_low) / 2.0
    out[f"dc_high_{tf}"] = dc_high.values.astype(np.float32)
    out[f"dc_low_{tf}"] = dc_low.values.astype(np.float32)
    out[f"dc_basis_{tf}"] = dc_basis.values.astype(np.float32)
    out[f"dc_high_{tf}_prev"] = dc_high.shift(1).fillna(dc_high.iloc[0]).values.astype(np.float32)
    out[f"dc_low_{tf}_prev"] = dc_low.shift(1).fillna(dc_low.iloc[0]).values.astype(np.float32)
    out[f"dc_basis_{tf}_prev"] = dc_basis.shift(1).fillna(dc_basis.iloc[0]).values.astype(np.float32)
    # DC ancient (30 bars back)
    out[f"dc_high_{tf}_ant"] = dc_high.shift(30).fillna(dc_high.iloc[0]).values.astype(np.float32)
    out[f"dc_low_{tf}_ant"] = dc_low.shift(30).fillna(dc_low.iloc[0]).values.astype(np.float32)
    # DC width + position
    dc_range = dc_high - dc_low
    out[f"dc_width_{tf}"] = (dc_range / dc_basis.replace(0, 1e-10) * 100).values.astype(np.float32)
    out[f"dc_position_{tf}"] = ((close - dc_low) / dc_range.replace(0, 1e-10)).clip(0, 1).values.astype(np.float32)
    # DC4 (4-bar)
    dc_high4 = high.rolling(4, min_periods=1).max()
    dc_low4 = low.rolling(4, min_periods=1).min()
    out[f"dc_high4_{tf}"] = dc_high4.values.astype(np.float32)
    out[f"dc_low4_{tf}"] = dc_low4.values.astype(np.float32)
    # DC crossovers
    close_prev = close.shift(1).fillna(close.iloc[0])
    for name, level, level_prev in [("dc_basis", dc_basis, dc_basis.shift(1).fillna(dc_basis.iloc[0])),
                                      ("dc_high", dc_high, dc_high.shift(1).fillna(dc_high.iloc[0])),
                                      ("dc_low", dc_low, dc_low.shift(1).fillna(dc_low.iloc[0]))]:
        co = ((close > level) & (close_prev <= level_prev)).fillna(False)
        cu = ((close < level) & (close_prev >= level_prev)).fillna(False)
        out[f"{name}_crossover_{tf}"] = co.values.astype(np.int8)
        out[f"{name}_crossunder_{tf}"] = cu.values.astype(np.int8)
    # WaveTrend (full series)
    try:
        wt1_s, wt2_s = wavetrend(df, timeframe=tf)
        if wt1_s is not None and not wt1_s.empty:
            # Pad/truncate to match n
            def _fit(arr, n):
                a = np.asarray(arr, dtype=np.float64)
                if len(a) == n: return a
                if len(a) > n: return a[:n]
                return np.pad(a, (0, n - len(a)), mode='edge')
            wt1_s_fit = _fit(wt1_s.values, n)
            wt2_s_fit = _fit(wt2_s.values, n)
            out[f"wt1_{tf}"] = wt1_s_fit.astype(np.float32)
            out[f"wt2_{tf}"] = wt2_s_fit.astype(np.float32)
            w1 = wt1_s_fit; w2 = wt2_s_fit
            out[f"wt_score_{tf}"] = (w1 - w2).astype(np.float32)
            out[f"wt_bullish_{tf}"] = (w1 > w2).astype(np.int8)
            vel = np.diff(w1, prepend=w1[0])
            out[f"wt_velocity_{tf}"] = vel.astype(np.float32)
            out[f"wt_acceleration_{tf}"] = np.diff(w1 - w2, prepend=0).astype(np.float32)
            cb = np.zeros(n, dtype=bool); cr = np.zeros(n, dtype=bool)
            cb[1:] = (w1[1:] > w2[1:]) & (w1[:-1] <= w2[:-1])
            cr[1:] = (w1[1:] < w2[1:]) & (w1[:-1] >= w2[:-1])
            out[f"wt_cross_bull_{tf}"] = cb.astype(np.int8)
            out[f"wt_cross_bear_{tf}"] = cr.astype(np.int8)
            out[f"wt_cross_{tf}"] = np.where(cb, 1, np.where(cr, -1, 0)).astype(np.int8)
            out[f"wt_cross_value_{tf}"] = w1.astype(np.float32)
            out[f"wt_cross_prev_value_{tf}"] = np.roll(w1, 1).astype(np.float32)
            out[f"wt_cross_rising_{tf}"] = (w1 > w2).astype(np.int8)
            bars_ago = np.full(n, 999, dtype=np.float32)
            lc = -999
            for i in range(n):
                if cb[i] or cr[i]: lc = i
                bars_ago[i] = i - lc if lc >= 0 else 999
            out[f"wt_cross_bars_ago_{tf}"] = bars_ago
            sig = np.zeros(n, dtype=np.int8)
            sig[(cb) & (w1 < -50)] = 1; sig[(cr) & (w1 > 50)] = -1
            out[f"wt_signal_{tf}"] = sig
            out[f"wt_extreme_{tf}"] = ((w1 > 60) | (w1 < -60)).astype(np.int8)
            pct = np.full(n, 50.0, dtype=np.float32)
            zs = np.zeros(n, dtype=np.float32)
            for i in range(100, n):
                win = w1[i-100:i]; std = np.std(win)
                pct[i] = np.sum(win < w1[i]) / 100.0 * 100
                zs[i] = (w1[i] - np.mean(win)) / std if std > 0.01 else 0
            out[f"wt_percentile_{tf}"] = pct
            out[f"wt_zscore_{tf}"] = zs
            score = w1 - w2
            mom = np.zeros(n, dtype=np.int8)
            mom[(score > 0) & (vel > 0)] = 2; mom[(score > 0) & (vel <= 0)] = 1
            mom[(score < 0) & (vel < 0)] = -2; mom[(score < 0) & (vel >= 0)] = -1
            out[f"wt_momentum_state_{tf}"] = mom
            peaks = np.zeros(n, dtype=np.float32); troughs = np.zeros(n, dtype=np.float32)
            pp = pt = lp = lt = 0.0
            for i in range(2, n):
                if w1[i-1] > w1[i-2] and w1[i-1] > w1[i]: pp = lp; lp = w1[i-1]
                if w1[i-1] < w1[i-2] and w1[i-1] < w1[i]: pt = lt; lt = w1[i-1]
                peaks[i] = lp; troughs[i] = lt
            out[f"wt_peak_{tf}"] = peaks
            out[f"wt_trough_{tf}"] = troughs
            peaks_prev = np.zeros(n, dtype=np.float32); troughs_prev = np.zeros(n, dtype=np.float32)
            _pp2 = _pt2 = 0.0
            for i in range(2, n):
                if w1[i-1] > w1[i-2] and w1[i-1] > w1[i]: peaks_prev[i:] = _pp2; _pp2 = w1[i-1]
                if w1[i-1] < w1[i-2] and w1[i-1] < w1[i]: troughs_prev[i:] = _pt2; _pt2 = w1[i-1]
            out[f"wt_peak_prev_{tf}"] = peaks_prev
            out[f"wt_trough_prev_{tf}"] = troughs_prev
            pk_s = np.zeros(n, dtype=np.int8); tr_s = np.zeros(n, dtype=np.int8)
            for i in range(1, n):
                if peaks[i] > peaks_prev[i] and peaks_prev[i] != 0: pk_s[i] = 1
                elif peaks[i] < peaks_prev[i] and peaks_prev[i] != 0: pk_s[i] = -1
                if troughs[i] > troughs_prev[i] and troughs_prev[i] != 0: tr_s[i] = 1
                elif troughs[i] < troughs_prev[i] and troughs_prev[i] != 0: tr_s[i] = -1
            out[f"wt_peak_structure_{tf}"] = pk_s
            out[f"wt_trough_structure_{tf}"] = tr_s
            out[f"wt_structure_{tf}"] = pk_s
            div = np.zeros(n, dtype=np.int8)
            cl = close.values.astype(np.float64) if len(close) == n else _fit(close.values, n)
            for i in range(20, n):
                if pk_s[i] == -1 and cl[i] > cl[i-20]: div[i] = -1
                elif tr_s[i] == 1 and cl[i] < cl[i-20]: div[i] = 1
            out[f"wt_divergence_{tf}"] = div
            out[f"wt_divergence_strength_{tf}"] = np.abs(div).astype(np.float32)
            sa = np.abs(w1 - w2); sp = np.roll(sa, 1)
            out[f"wt_wave_phase_{tf}"] = np.where(sa > sp, 1, -1).astype(np.int8)
            # === PROPER PIVOT-BASED DIVERGENCE (Improvement Framework A4, 2026-04-25) ===
            # Distinct from above structure-based wt_divergence proxy.
            # 4 flags per indicator: regular bull/bear (reversal), hidden bull/bear (continuation).
            # Pivot lookback=5, signal decay=10 bars after confirmation. No repaint.
            from ez_indicators import detect_divergence as _detect_div
            try:
                rb, br, hb, hbr = _detect_div(cl, w1.astype(np.float64), lookback=5, decay=10)
                out[f"div_reg_bull_wt_{tf}"] = rb
                out[f"div_reg_bear_wt_{tf}"] = br
                out[f"div_hid_bull_wt_{tf}"] = hb
                out[f"div_hid_bear_wt_{tf}"] = hbr
            except Exception:
                pass
    except Exception:
        pass
    # MFI divergence (uses mfi computed earlier in this function — out[f"mfi_{tf}"] guaranteed populated)
    try:
        from ez_indicators import detect_divergence as _detect_div_mfi
        mfi_arr = out.get(f"mfi_{tf}")
        if mfi_arr is not None and len(mfi_arr) == n:
            cl_arr = close.values.astype(np.float64) if len(close) == n else None
            if cl_arr is not None:
                rb, br, hb, hbr = _detect_div_mfi(cl_arr, mfi_arr.astype(np.float64), lookback=5, decay=10)
                out[f"div_reg_bull_mfi_{tf}"] = rb
                out[f"div_reg_bear_mfi_{tf}"] = br
                out[f"div_hid_bull_mfi_{tf}"] = hb
                out[f"div_hid_bear_mfi_{tf}"] = hbr
    except Exception:
        pass
    # Heikin Ashi
    try:
        ha_color, ha_prev = heikin_ashi(df)
        if ha_color is not None:
            # HA is per-bar: need full series. heikin_ashi returns last 2 values.
            # Compute full HA series manually
            ha_close = (open_ + high + low + close) / 4.0
            ha_open = pd.Series(np.zeros(n), index=df.index)
            ha_open.iloc[0] = (open_.iloc[0] + close.iloc[0]) / 2.0
            for i in range(1, n):
                ha_open.iloc[i] = (ha_open.iloc[i-1] + ha_close.iloc[i-1]) / 2.0
            ha_colors = np.where(ha_close > ha_open, 1, np.where(ha_close < ha_open, -1, 0)).astype(np.int8)
            out[f"ha_{tf}"] = ha_colors
    except Exception:
        pass
    # Relative volume
    rv = volume / volume.rolling(20, min_periods=1).mean().replace(0, 1)
    out[f"relative_volume_{tf}"] = rv.values.astype(np.float32)
    # EMA 20, 50, 200
    for ema_len in [20, 50, 200]:
        ema = close.ewm(span=ema_len, adjust=False).mean()
        out[f"ema_{ema_len}_{tf}"] = ema.values.astype(np.float32)
        out[f"ema_{ema_len}_{tf}_prev"] = ema.shift(1).fillna(ema.iloc[0]).values.astype(np.float32)
    # SMA 200
    sma = close.rolling(200, min_periods=1).mean()
    out[f"sma_200_{tf}"] = sma.values.astype(np.float32)
    out[f"sma_200_{tf}_prev"] = sma.shift(1).fillna(sma.iloc[0]).values.astype(np.float32)
    sma_co = ((close > sma) & (close_prev <= sma.shift(1).fillna(sma.iloc[0]))).fillna(False)
    sma_cu = ((close < sma) & (close_prev >= sma.shift(1).fillna(sma.iloc[0]))).fillna(False)
    out[f"sma_crossover_{tf}"] = sma_co.values.astype(np.int8)
    out[f"sma_crossunder_{tf}"] = sma_cu.values.astype(np.int8)
    # Bollinger Band %B (SMA20, 2σ) — used by STDEV_BREAKOUT strategy
    # Bands computed from TF close; %B computed from TF close (will be corrected in post-process for HTF)
    bb_sma20 = close.rolling(20, min_periods=1).mean()
    bb_std20 = close.rolling(20, min_periods=1).std(ddof=0).fillna(0)
    bb_upper = bb_sma20 + 2.0 * bb_std20
    bb_lower = bb_sma20 - 2.0 * bb_std20
    bb_width = bb_upper - bb_lower
    out[f"bb_upper_{tf}"] = bb_upper.values.astype(np.float32)
    out[f"bb_lower_{tf}"] = bb_lower.values.astype(np.float32)
    out[f"bb_pct_b_{tf}"] = np.where(bb_width > 0, (close - bb_lower) / bb_width, 0.5).astype(np.float32)
    # Keltner Channel + Squeeze (LazyBear/TTM): EMA(20) ± 1.5 × ATR(20). Squeeze ON when BB is inside KC.
    # Squeeze release (fire) direction: close vs KC midline on release bar. +1 bull, -1 bear, 0 none.
    # Added 2026-04-25 for Improvement Framework A3. NEEDS Tier 2 sweep before live.
    kc_mid = close.ewm(span=20, adjust=False).mean()
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1).fillna(0)
    atr20 = tr.ewm(span=20, adjust=False, min_periods=20).mean()
    kc_upper = kc_mid + 1.5 * atr20
    kc_lower = kc_mid - 1.5 * atr20
    out[f"kc_upper_{tf}"] = kc_upper.values.astype(np.float32)
    out[f"kc_mid_{tf}"] = kc_mid.values.astype(np.float32)
    out[f"kc_lower_{tf}"] = kc_lower.values.astype(np.float32)
    is_squeezed = ((bb_upper <= kc_upper) & (bb_lower >= kc_lower)).fillna(False)
    out[f"squeeze_{tf}"] = is_squeezed.values.astype(np.int8)
    was_squeezed = is_squeezed.shift(1).fillna(False)
    released = was_squeezed & (~is_squeezed)
    fire = np.where(released & (close > kc_mid), 1, np.where(released & (close < kc_mid), -1, 0))
    out[f"squeeze_fire_{tf}"] = fire.astype(np.int8)
    # MACD (1h, 4h, D only)
    if tf in ("1h", "4h", "D"):
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        signal = macd.ewm(span=9, adjust=False).mean()
        hist = macd - signal
        out[f"macd_{tf}"] = macd.values.astype(np.float32)
        out[f"macd_signal_{tf}"] = signal.values.astype(np.float32)
        out[f"macd_hist_{tf}"] = hist.values.astype(np.float32)
    # ADX (1h, 4h, D)
    if tf in ("1h", "4h", "D"):
        try:
            from tradier_indicators import adx_value
            # Compute ADX series manually (adx_value returns scalar)
            # Use ta-lib style ADX if available, otherwise skip
            pass
        except Exception:
            pass
    # === CONTRACT ALIASES (Improvement Framework A3, 2026-04-26) ===
    # Engine reads kc_middle_{tf} and squeeze_on_{tf}; existing fields use kc_mid + squeeze.
    # Alias both names to the same array to keep backward-compat AND fulfill the contract.
    if f"kc_mid_{tf}" in out:
        out[f"kc_middle_{tf}"] = out[f"kc_mid_{tf}"]
    if f"squeeze_{tf}" in out:
        out[f"squeeze_on_{tf}"] = out[f"squeeze_{tf}"]
    # === VOLATILITY ESTIMATORS (Improvement Framework A5, 2026-04-26) ===
    # Yang-Zhang / Parkinson / Garman-Klass annualized realized vol, % units.
    # Daily TF: 60-bar window. 4h TF: 20-bar window. Other TFs: skipped (unused).
    ann = _ann_factor()
    o_arr = open_.values.astype(np.float64)
    h_arr = high.values.astype(np.float64)
    l_arr = low.values.astype(np.float64)
    c_arr = close.values.astype(np.float64)
    if tf == "D" and n >= 60:
        out["yz_vol_60_d"] = _yz_vol(o_arr, h_arr, l_arr, c_arr, n=60, ann_factor=ann)
        out["pk_vol_60_d"] = _pk_vol(h_arr, l_arr, n=60, ann_factor=ann)
        out["gk_vol_60_d"] = _gk_vol(o_arr, h_arr, l_arr, c_arr, n=60, ann_factor=ann)
    if tf == "4h" and n >= 20:
        out["yz_vol_20_4h"] = _yz_vol(o_arr, h_arr, l_arr, c_arr, n=20, ann_factor=ann)
        out["pk_vol_20_4h"] = _pk_vol(h_arr, l_arr, n=20, ann_factor=ann)
        out["gk_vol_20_4h"] = _gk_vol(o_arr, h_arr, l_arr, c_arr, n=20, ann_factor=ann)
    # === DAILY-TF SCALAR INDICATORS (computed once on Daily, broadcast by HTF resampler) ===
    # 52-week extremes, Minervini SEPA, Clenow score, Episodic Pivot.
    # Window-size differs by mode: 252 trading days (stocks) vs 365 calendar days (crypto).
    if tf == "D":
        n52 = 365 if MODE == "crypto" else 252
        min_p = max(20, n52 // 12)
        high_52w = pd.Series(h_arr).rolling(n52, min_periods=min_p).max().bfill().fillna(h_arr[0]).values
        low_52w = pd.Series(l_arr).rolling(n52, min_periods=min_p).min().bfill().fillna(l_arr[0]).values
        out["pct_from_52w_high"] = ((c_arr / np.maximum(high_52w, 1e-10) - 1.0) * 100.0).astype(np.float32)
        out["pct_from_52w_low"] = ((c_arr / np.maximum(low_52w, 1e-10) - 1.0) * 100.0).astype(np.float32)
        # Minervini SEPA (rolling per-bar via existing scalar function)
        v_arr = volume.values.astype(np.float64)
        sepa_pass, sepa_score = _rolling_sepa(c_arr, h_arr, l_arr, v_arr)
        out["sepa_pass"] = sepa_pass
        out["sepa_score"] = sepa_score
        # Clenow score
        cl_score, cl_slope, cl_r2 = _rolling_clenow(c_arr, lookback=90)
        out["clenow_score"] = cl_score
        out["clenow_slope"] = cl_slope
        out["clenow_r2"] = cl_r2
        # Episodic Pivot — fires forward 30 days from detection
        ep_det, ep_lvl, ep_dir = _rolling_episodic_pivot(o_arr, h_arr, l_arr, c_arr, v_arr, fwd_days=30)
        out["ep_detected"] = ep_det
        out["ep_breakout_level"] = ep_lvl
        out["ep_direction"] = ep_dir
    # Filter: only return arrays matching expected length n
    return {k: v for k, v in out.items() if isinstance(v, np.ndarray) and len(v) == n}


def resample_tf(df_base: pd.DataFrame, target_tf: str) -> Optional[pd.DataFrame]:
    """Resample a lower TF DataFrame to a higher TF."""
    rule = {"15m": "15min", "1h": "1h", "4h": "4h", "D": "1D"}.get(target_tf)
    if not rule:
        return None
    try:
        resampled = df_base.resample(rule).agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
        return resampled
    except Exception:
        return None


def fabricate_3m(df_15m):
    """Create 3m bars from 15m via linear interpolation. 5 sub-bars per 15m bar.
    LOCKED — DO NOT REMOVE OR MODIFY. Required for full-history 3m indicators."""
    rows = []
    closes = df_15m["close"].values
    opens = df_15m["open"].values
    highs = df_15m["high"].values
    lows = df_15m["low"].values
    vols = df_15m["volume"].values
    timestamps = df_15m.index
    for i in range(len(df_15m)):
        ts = timestamps[i]
        o, h, l, c, v = opens[i], highs[i], lows[i], closes[i], vols[i]
        prev_c = closes[i - 1] if i > 0 else o
        for j in range(5):
            frac = (j + 1) / 5.0
            prev_frac = j / 5.0
            sub_c = prev_c + (c - prev_c) * frac
            sub_o = prev_c + (c - prev_c) * prev_frac
            spread = h - l
            if j == 2:
                sub_h = max(sub_o, sub_c) + spread * 0.3
                sub_l = min(sub_o, sub_c) - spread * 0.3
            else:
                sub_h = max(sub_o, sub_c) + spread * 0.08
                sub_l = min(sub_o, sub_c) - spread * 0.08
            sub_ts = ts - pd.Timedelta(minutes=12) + pd.Timedelta(minutes=3 * j)
            rows.append({"timestamp_dt": sub_ts, "open": sub_o, "high": sub_h, "low": sub_l, "close": sub_c, "volume": v / 5.0})
    df = pd.DataFrame(rows).set_index("timestamp_dt").sort_index()
    return df


def _inject_funding_oi(merged: dict, symbol: str, ts_epoch_sec: np.ndarray, base_tf: str) -> None:
    # Improvement Framework A1+A2 (2026-04-25): inject Binance Futures funding rate + open interest as NPZ fields.
    # Cache populated by binance_funding_fetcher.py and binance_oi_fetcher.py.
    # Forward-fill aligned to base_tf bar grid. Missing cache → zeros (caller can detect via key presence).
    n = len(ts_epoch_sec)
    funding_arr = np.zeros(n, dtype=np.float32)
    funding_path = BASE_PATH / "data" / "funding_cache" / f"{symbol}.json"
    if funding_path.exists():
        try:
            recs = json.loads(funding_path.read_text())
            if recs:
                ts_fr = np.array([int(r["fundingTime"]) // 1000 for r in recs], dtype=np.int64)
                rates = np.array([float(r["fundingRate"]) for r in recs], dtype=np.float32)
                idx = np.searchsorted(ts_fr, ts_epoch_sec, side="right") - 1
                idx = np.clip(idx, 0, len(ts_fr) - 1)
                funding_arr = rates[idx]
                funding_arr[ts_epoch_sec < ts_fr[0]] = 0.0
        except Exception:
            pass
    merged[f"funding_rate_{base_tf}"] = funding_arr
    oi_arr = np.zeros(n, dtype=np.float32)
    oi_value_arr = np.zeros(n, dtype=np.float32)
    oi_change_15m_arr = np.zeros(n, dtype=np.float32)
    oi_change_1h_arr = np.zeros(n, dtype=np.float32)
    oi_path = BASE_PATH / "data" / "oi_cache" / f"{symbol}.json"
    if oi_path.exists():
        try:
            recs = json.loads(oi_path.read_text())
            if recs:
                ts_oi = np.array([int(r["timestamp"]) // 1000 for r in recs], dtype=np.int64)
                oi = np.array([float(r["sumOpenInterest"]) for r in recs], dtype=np.float32)
                oiv = np.array([float(r["sumOpenInterestValue"]) for r in recs], dtype=np.float32)
                idx = np.searchsorted(ts_oi, ts_epoch_sec, side="right") - 1
                idx = np.clip(idx, 0, len(ts_oi) - 1)
                oi_arr = oi[idx]
                oi_value_arr = oiv[idx]
                pre = ts_epoch_sec < ts_oi[0]
                oi_arr[pre] = 0.0; oi_value_arr[pre] = 0.0
                with np.errstate(divide="ignore", invalid="ignore"):
                    oi_prev = np.roll(oi_arr, 1); oi_prev[0] = oi_arr[0]
                    oi_change_15m_arr = np.where(oi_prev > 0, (oi_arr - oi_prev) / oi_prev * 100.0, 0.0).astype(np.float32)
                    lag_1h = {"3m": 20, "5m": 12, "15m": 4}.get(base_tf, 4)
                    oi_lag = np.roll(oi_arr, lag_1h); oi_lag[:lag_1h] = oi_arr[:lag_1h]
                    oi_change_1h_arr = np.where(oi_lag > 0, (oi_arr - oi_lag) / oi_lag * 100.0, 0.0).astype(np.float32)
        except Exception:
            pass
    merged[f"oi_{base_tf}"] = oi_arr
    merged[f"oi_value_{base_tf}"] = oi_value_arr
    merged[f"oi_change_15m_{base_tf}"] = oi_change_15m_arr
    merged[f"oi_change_1h_{base_tf}"] = oi_change_1h_arr


def compute_symbol(symbol: str, mode: str) -> bool:
    t0 = time.time()
    # Re-assert module-level MODE so worker processes (Pool) and direct callers
    # both end up with the right annualization factor in compute_tf_arrays().
    global MODE
    MODE = mode
    if mode == "tradier":
        base_tf = "15m"  # 15m has 2yr history, 5m only 3mo — use 15m, fabricate 5m
        tfs = ["5m", "15m", "1h", "4h", "D"]
        klines_dir = TRADIER_KLINES
    else:
        base_tf = "15m"  # 15m is the source of truth — full history back to 2020
        tfs = ["3m", "15m", "1h", "4h", "D"]
        klines_dir = CRYPTO_KLINES
    # Load klines per TF — try primary dir, fall back to alternate for longest history
    dfs = {}
    alt_dirs = [BASE_PATH / "klines_cache_backtest", BASE_PATH / "klines_cache"]
    if mode == "tradier":
        alt_dirs = [BASE_PATH / "klines_cache_backtest" / "tradier", BASE_PATH / "klines_cache" / "tradier"]
    for tf in tfs:
        best_df = None
        for d in [klines_dir] + [a for a in alt_dirs if a != klines_dir]:
            path = d / f"{symbol}_{tf}.json"
            df = load_klines(path)
            if df is not None and len(df) >= 30:
                if best_df is None or len(df) > len(best_df):
                    best_df = df
        if best_df is not None:
            dfs[tf] = best_df
    if base_tf not in dfs:
        logger.warning(f"[SKIP] {symbol}: no {base_tf} klines")
        return False
    base_df = dfs[base_tf]
    # Fabricate 3m from 15m if 3m file is missing or shorter than 15m history
    if mode == "crypto" and ("3m" not in dfs or len(dfs.get("3m", [])) < len(base_df) * 3):
        logger.info(f"  {symbol}: fabricating 3m from 15m ({len(base_df)} × 5 = {len(base_df)*5} bars)")
        dfs["3m"] = fabricate_3m(base_df)
    # Fabricate 5m from 15m for tradier if missing
    if mode == "tradier" and ("5m" not in dfs or len(dfs.get("5m", [])) < len(base_df) * 2):
        logger.info(f"  {symbol}: fabricating 5m from 15m ({len(base_df)} × 3 = {len(base_df)*3} bars)")
        # 3 sub-bars per 15m = 5m
        rows = []
        for i in range(len(base_df)):
            ts = base_df.index[i]
            o, h, l, c, v = base_df.iloc[i][["open","high","low","close","volume"]]
            prev_c = base_df.iloc[i-1]["close"] if i > 0 else o
            for j in range(3):
                frac = (j+1)/3.0; prev_frac = j/3.0
                sub_c = prev_c + (c - prev_c) * frac
                sub_o = prev_c + (c - prev_c) * prev_frac
                spread = h - l
                sub_h = max(sub_o, sub_c) + spread * (0.2 if j==1 else 0.05)
                sub_l = min(sub_o, sub_c) - spread * (0.2 if j==1 else 0.05)
                sub_ts = ts - pd.Timedelta(minutes=10) + pd.Timedelta(minutes=5*j)
                rows.append({"timestamp_dt": sub_ts, "open": sub_o, "high": sub_h, "low": sub_l, "close": sub_c, "volume": v/3.0})
        dfs["5m"] = pd.DataFrame(rows).set_index("timestamp_dt").sort_index()
    # Use highest resolution as base for NPZ output — every 3m/5m bar gets its own row
    if mode == "crypto" and "3m" in dfs and len(dfs["3m"]) > len(base_df):
        base_df = dfs["3m"]
        base_tf = "3m"
    if mode == "tradier" and "5m" in dfs and len(dfs["5m"]) > len(base_df):
        base_df = dfs["5m"]
        base_tf = "5m"
    n = len(base_df)
    _dt_unit = np.datetime_data(base_df.index.values.dtype)[0]
    _divisor = {"ns": 10**9, "us": 10**6, "ms": 10**3, "s": 1}.get(_dt_unit, 10**9)
    ts_epoch = (base_df.index.values.astype("int64") // _divisor).astype(np.int64)
    # ALWAYS resample HTFs from 15m — 15m has full history, standalone kline files may be short
    for tf in tfs:
        if tf == base_tf:
            continue
        resampled = resample_tf(base_df, tf)
        if resampled is not None and len(resampled) >= 20:
            existing = dfs.get(tf)
            # Use resampled if it has more data than the standalone file
            if existing is None or len(resampled) > len(existing) * 1.5:
                dfs[tf] = resampled
    merged = {"timestamps": ts_epoch, "close": base_df["close"].values.astype(np.float32)}
    # Compute indicators per TF — ONE call, returns FULL arrays
    for tf in tfs:
        if tf not in dfs:
            continue
        df = dfs[tf]
        tf_arrays = compute_tf_arrays(df, tf)
        _tf_unit = np.datetime_data(df.index.values.dtype)[0]
        _tf_div = {"ns": 10**9, "us": 10**6, "ms": 10**3, "s": 1}.get(_tf_unit, 10**9)
        tf_ts = (df.index.values.astype("int64") // _tf_div).astype(np.int64)
        for key, arr in tf_arrays.items():
            if len(arr) != len(tf_ts):
                continue
            if tf == base_tf:
                merged[key] = arr
            else:
                # Forward-fill HTF to base TF timestamps
                indices = np.searchsorted(tf_ts, ts_epoch, side="right") - 1
                indices = np.clip(indices, 0, len(tf_ts) - 1)
                merged[key] = arr[indices]
    # FIX: Fabricated 3m WT can disagree with parent 15m direction due to interpolation artifacts.
    # When 3m is fabricated from 15m, force 3m WT direction to match 15m at each bar.
    # This prevents WT_LTF_GATE from blocking entries due to artificial 3m/15m disagreement.
    if mode == "crypto" and "wt1_3m" in merged and "wt1_15m" in merged:
        _w1_3m = merged["wt1_3m"].astype(np.float32)
        _w2_3m = merged["wt2_3m"].astype(np.float32)
        _w1_15m = merged["wt1_15m"].astype(np.float32)
        _w2_15m = merged["wt2_15m"].astype(np.float32)
        _bull_15m = _w1_15m > _w2_15m
        _bull_3m = _w1_3m > _w2_3m
        _conflict = _bull_15m != _bull_3m
        _conflict_count = _conflict.sum()
        if _conflict_count > 0:
            _score = _w1_3m - _w2_3m
            _abs_score = np.abs(_score)
            # Where 15m is bullish but 3m is bearish: flip 3m to slightly bullish
            _fix_bull = _conflict & _bull_15m
            _w1_3m[_fix_bull] = _w2_3m[_fix_bull] + _abs_score[_fix_bull] * 0.5
            # Where 15m is bearish but 3m is bullish: flip 3m to slightly bearish
            _fix_bear = _conflict & ~_bull_15m
            _w1_3m[_fix_bear] = _w2_3m[_fix_bear] - _abs_score[_fix_bear] * 0.5
            merged["wt1_3m"] = _w1_3m
            # Also fix wt_bullish_3m and wt_score_3m
            merged["wt_bullish_3m"] = (_w1_3m > _w2_3m).astype(np.int8)
            merged["wt_score_3m"] = (_w1_3m - _w2_3m).astype(np.float32)
            logger.info(f"  {symbol}: fixed {_conflict_count} 3m/15m WT direction conflicts ({_conflict_count*100/len(_w1_3m):.1f}%)")
    # WT composite (cross-TF global fields — all as full arrays)
    bull_count = np.zeros(n, dtype=np.int8)
    bear_count = np.zeros(n, dtype=np.int8)
    bull_cross_count = np.zeros(n, dtype=np.int8)
    bear_cross_count = np.zeros(n, dtype=np.int8)
    oversold_count = np.zeros(n, dtype=np.int8)
    overbought_count = np.zeros(n, dtype=np.int8)
    vel_up_count = np.zeros(n, dtype=np.int8)
    vel_down_count = np.zeros(n, dtype=np.int8)
    rising_cross_count = np.zeros(n, dtype=np.int8)
    falling_cross_count = np.zeros(n, dtype=np.int8)
    hh_count = np.zeros(n, dtype=np.int8)
    hl_count = np.zeros(n, dtype=np.int8)
    ll_count = np.zeros(n, dtype=np.int8)
    any_bull_div = np.zeros(n, dtype=np.int8)
    any_bear_div = np.zeros(n, dtype=np.int8)
    comp_long = np.zeros(n, dtype=np.float32)
    comp_short = np.zeros(n, dtype=np.float32)
    for tf in tfs:
        b = merged.get(f"wt_bullish_{tf}")
        if b is not None:
            bull_count += (np.asarray(b) > 0).astype(np.int8)
            bear_count += (np.asarray(b) <= 0).astype(np.int8)
        cb = merged.get(f"wt_cross_bull_{tf}")
        if cb is not None:
            bull_cross_count += np.asarray(cb).astype(np.int8)
        cr = merged.get(f"wt_cross_bear_{tf}")
        if cr is not None:
            bear_cross_count += np.asarray(cr).astype(np.int8)
        wt1_arr = merged.get(f"wt1_{tf}")
        if wt1_arr is not None:
            wt1_v = np.asarray(wt1_arr, dtype=np.float64)
            oversold_count += (wt1_v < -53).astype(np.int8)
            overbought_count += (wt1_v > 53).astype(np.int8)
        vel = merged.get(f"wt_velocity_{tf}")
        if vel is not None:
            vel_v = np.asarray(vel, dtype=np.float64)
            vel_up_count += (vel_v > 0).astype(np.int8)
            vel_down_count += (vel_v < 0).astype(np.int8)
        cr_rising = merged.get(f"wt_cross_rising_{tf}")
        if cr_rising is not None:
            rising_cross_count += np.asarray(cr_rising).astype(np.int8)
            falling_cross_count += (1 - np.asarray(cr_rising)).astype(np.int8)
        ps = merged.get(f"wt_peak_structure_{tf}")
        if ps is not None:
            ps_v = np.asarray(ps)
            hh_count += (ps_v == 1).astype(np.int8)
        ts_s = merged.get(f"wt_trough_structure_{tf}")
        if ts_s is not None:
            ts_v = np.asarray(ts_s)
            hl_count += (ts_v == 1).astype(np.int8)
            ll_count += (ts_v == -1).astype(np.int8)
        div = merged.get(f"wt_divergence_{tf}")
        if div is not None:
            div_v = np.asarray(div)
            any_bull_div = np.maximum(any_bull_div, (div_v > 0).astype(np.int8))
            any_bear_div = np.maximum(any_bear_div, (div_v < 0).astype(np.int8))
        # Composite long/short scoring: weighted by TF
        w = {"3m": 1, "5m": 1, "15m": 2, "1h": 3, "4h": 4, "D": 5}.get(tf, 1)
        score = merged.get(f"wt_score_{tf}")
        if score is not None:
            s = np.asarray(score, dtype=np.float64)
            comp_long += np.maximum(0, s) * w
            comp_short += np.maximum(0, -s) * w
    merged["wt_bull_alignment"] = bull_count
    merged["wt_bear_alignment"] = bear_count
    merged["wt_bull_cross_count"] = bull_cross_count
    merged["wt_bear_cross_count"] = bear_cross_count
    merged["wt_oversold_tf_count"] = oversold_count
    merged["wt_overbought_tf_count"] = overbought_count
    merged["wt_velocity_up_count"] = vel_up_count
    merged["wt_velocity_down_count"] = vel_down_count
    merged["wt_rising_cross_count"] = rising_cross_count
    merged["wt_falling_cross_count"] = falling_cross_count
    merged["wt_hh_count"] = hh_count
    merged["wt_hl_count"] = hl_count
    merged["wt_ll_count"] = ll_count
    merged["wt_any_bull_div"] = any_bull_div
    merged["wt_any_bear_div"] = any_bear_div
    merged["wt_composite_long"] = comp_long.astype(np.float32)
    merged["wt_composite_short"] = comp_short.astype(np.float32)
    merged["wt_composite_delta"] = (comp_long - comp_short).astype(np.float32)
    merged["wt_composite_bias"] = np.where(comp_long > comp_short, 1, np.where(comp_short > comp_long, -1, 0)).astype(np.int8)
    # Inject funding rate + open interest from cache (Improvement Framework A1+A2, 2026-04-25)
    if mode == "crypto":
        try:
            _inject_funding_oi(merged, symbol, ts_epoch, base_tf)
        except Exception as _e:
            logger.warning(f"[FUNDING_OI_INJECT] {symbol}: {_e}")
    # Save
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"{symbol}.npz"
    np.savez_compressed(str(out_path), **merged)
    elapsed = time.time() - t0
    fsize = os.path.getsize(str(out_path))
    logger.info(f"  {symbol}: {len(merged)} keys, {n} bars, {fsize/1024:.0f}KB, {elapsed:.1f}s")
    return True


def _inject_market_sentiment(out_dir, symbols):
    """Post-pass: compute cross-symbol WT breadth per bar and inject market_sentiment_score
    into every NPZ. score = 50 + (bull_count - bear_count) / total * 50, range [0, 100].
    Requires wt_composite_bias and timestamps in each NPZ (written by compute_symbol)."""
    from collections import defaultdict
    sym_data = {}
    for sym in symbols:
        p = out_dir / f"{sym}.npz"
        if not p.exists():
            continue
        try:
            z = dict(np.load(str(p), allow_pickle=True))
            ts = z.get('timestamps')
            bias = z.get('wt_composite_bias')
            if ts is None or bias is None or len(ts) != len(bias):
                continue
            sym_data[sym] = (ts.astype(np.int64), bias.astype(np.int8), z)
        except Exception as e:
            logger.warning(f"[SENTIMENT_INJECT] load failed {sym}: {e}")
    if not sym_data:
        logger.warning("[SENTIMENT_INJECT] No symbols loaded — skipping")
        return
    ts_bull = defaultdict(int)
    ts_bear = defaultdict(int)
    ts_total = defaultdict(int)
    for sym, (ts, bias, _) in sym_data.items():
        for t, b in zip(ts.tolist(), bias.tolist()):
            ts_total[t] += 1
            if b == 1:
                ts_bull[t] += 1
            elif b == -1:
                ts_bear[t] += 1
    ts_score = {t: float(50.0 + (ts_bull[t] - ts_bear[t]) / ts_total[t] * 50.0) for t in ts_total}
    updated = 0
    for sym, (ts, _, z) in sym_data.items():
        p = out_dir / f"{sym}.npz"
        mss = np.array([ts_score.get(int(t), 50.0) for t in ts], dtype=np.float32)
        z['market_sentiment_score'] = mss
        np.savez_compressed(str(p), **z)
        updated += 1
    logger.info(f"[SENTIMENT_INJECT] {updated} NPZs updated, {len(ts_score)} unique timestamps")


def main():
    parser = argparse.ArgumentParser(description="V7 Precompute")
    parser.add_argument("--symbol", type=str, default="")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--mode", choices=["tradier", "crypto"], required=True)
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()
    # Set module-level MODE so compute_tf_arrays uses the correct annualization
    # factor (252 trading days for tradier, 365 calendar days for crypto).
    global MODE
    MODE = args.mode
    klines_dir = TRADIER_KLINES if args.mode == "tradier" else CRYPTO_KLINES
    if args.symbol:
        symbols = [args.symbol.upper()]
    else:
        # ONLY the 48 crypto / 121 tradier symbols — not the entire klines_cache
        sym_file = BASE_PATH / ("backtest_48_symbols.json" if args.mode == "crypto" else "backtest_tradier_symbols.json")
        if sym_file.exists():
            symbols = json.load(open(sym_file))
        else:
            # Fallback: 15m klines that exist (not 3m which is mostly recent junk)
            symbols = sorted(set(p.stem.rsplit("_", 1)[0] for p in klines_dir.glob("*_15m.json")))
    logger.info(f"V7 Precompute [{args.mode}]: {len(symbols)} symbols")
    if args.workers > 1:
        from multiprocessing import Pool
        with Pool(args.workers) as pool:
            results = pool.starmap(compute_symbol, [(s, args.mode) for s in symbols])
        done = sum(1 for r in results if r)
    else:
        done = 0
        for i, sym in enumerate(symbols):
            logger.info(f"[{i+1}/{len(symbols)}] {sym}")
            if compute_symbol(sym, args.mode):
                done += 1
    logger.info(f"DONE: {done}/{len(symbols)}")
    if done > 1 and not args.symbol:
        _inject_market_sentiment(OUT_DIR, symbols)


if __name__ == "__main__":
    main()
