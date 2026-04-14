#!/usr/bin/env python3
"""
PRECOMPUTE INDICATORS — Build .npz indicator cache for ALL symbols
===================================================================
Computes ALL technical indicators once and saves them to disk.
Future backtests load from cache in <0.5s per symbol instead of ~42s.

Supports both crypto (USDT pairs) and tradier (stock symbols).

Usage:
  python3 precompute_indicators.py --workers 16               # All symbols
  python3 precompute_indicators.py --workers 16 --crypto-only  # Just crypto
  python3 precompute_indicators.py --workers 16 --stocks-only  # Just stocks
  python3 precompute_indicators.py --symbol XLMUSDT            # Single symbol
  python3 precompute_indicators.py --workers 16 --force        # Rebuild all
"""
import argparse
import json
import os
import signal
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

# Detect environment
if Path("/home/niels/binance-sandbox").exists():
    KLINES_DIR = Path("/home/niels/binance-sandbox/klines_cache")
    CACHE_DIR = Path("/home/niels/binance-sandbox/indicator_cache")
    SYMBOLS_48 = Path("/home/niels/binance-sandbox/backtest_48_symbols.json")
else:
    KLINES_DIR = SCRIPT_DIR / "klines_cache"
    CACHE_DIR = SCRIPT_DIR / "indicator_cache"
    SYMBOLS_48 = SCRIPT_DIR / "backtest_48_symbols.json" if (SCRIPT_DIR / "backtest_48_symbols.json").exists() else None

CACHE_DIR.mkdir(parents=True, exist_ok=True)

WARMUP_MIN = 50  # Minimum bars for a TF to be usable

# Graceful shutdown
_SHUTDOWN = False
def _handle_signal(sig, frame):
    global _SHUTDOWN
    _SHUTDOWN = True
    print("\n[SHUTDOWN] Finishing current workers...")
signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


# ═══════════════════════════════════════════════════════════════
# KLINE LOADING
# ═══════════════════════════════════════════════════════════════

def load_klines_df(symbol: str, tf: str) -> Optional[pd.DataFrame]:
    p = KLINES_DIR / f"{symbol}_{tf}.json"
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text())
        if not raw or len(raw) < 50:
            return None
        rows = []
        for k in raw:
            if isinstance(k, dict):
                rows.append({"timestamp": k["timestamp"], "open": float(k["open"]), "high": float(k["high"]), "low": float(k["low"]), "close": float(k["close"]), "volume": float(k.get("volume", 0))})
            elif isinstance(k, list):
                rows.append({"timestamp": k[0], "open": float(k[1]), "high": float(k[2]), "low": float(k[3]), "close": float(k[4]), "volume": float(k[5]) if len(k) > 5 else 0})
        df = pd.DataFrame(rows)
        if df["timestamp"].dtype == object:
            df["timestamp_dt"] = pd.to_datetime(df["timestamp"], format="ISO8601", utc=True)
        else:
            df["timestamp_dt"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df = df.sort_values("timestamp_dt").reset_index(drop=True)
        return df
    except Exception:
        return None


def aggregate_htf(df_primary: pd.DataFrame, primary_tf: str, target_tf: str) -> Optional[pd.DataFrame]:
    """Aggregate primary TF bars into HTF bars."""
    tf_map = {
        "15m": {"1h": 4, "4h": 16, "D": 96},
        "5m":  {"15m": 3, "1h": 12, "4h": 48, "D": 288},
        "3m":  {"15m": 5, "1h": 20, "4h": 80, "D": 480},
    }
    multiplier = tf_map.get(primary_tf, {}).get(target_tf)
    if multiplier is None:
        return None
    n = len(df_primary)
    if n < multiplier * 2:
        return None
    rows = []
    for start in range(0, n - multiplier + 1, multiplier):
        chunk = df_primary.iloc[start:start + multiplier]
        rows.append({"timestamp": chunk.iloc[0]["timestamp"], "open": float(chunk.iloc[0]["open"]), "high": float(chunk["high"].max()), "low": float(chunk["low"].min()), "close": float(chunk.iloc[-1]["close"]), "volume": float(chunk["volume"].sum())})
    if not rows:
        return None
    agg = pd.DataFrame(rows)
    agg["timestamp_dt"] = pd.to_datetime(agg["timestamp"], format="ISO8601", utc=True) if agg["timestamp"].dtype == object else pd.to_datetime(agg["timestamp"], unit="ms", utc=True)
    return agg


# ═══════════════════════════════════════════════════════════════
# INDICATOR COMPUTATION PER TF
# ═══════════════════════════════════════════════════════════════

def compute_tf_indicators(df: pd.DataFrame, tf: str, n_primary: int, idx_map=None) -> Dict[str, np.ndarray]:
    """Compute all indicators for one TF. Returns dict of arrays aligned to primary TF."""
    from ez_indicators import rsi_series, atr_series, stoch_rsi, wavetrend, STOCH_LEN, STOCH_K
    try:
        from scipy.signal import lfilter
    except ImportError:
        lfilter = None

    tf_n = len(df)
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    opn = df["open"].astype(float)
    vol = df["volume"].astype(float)
    ind = {}

    def _m(arr):
        if idx_map is None:
            return arr
        return arr[idx_map] if isinstance(arr, np.ndarray) else np.array([arr[j] if j < len(arr) else 0 for j in idx_map])

    # --- Stochastic RSI ---
    try:
        stoch = stoch_rsi(close, length=STOCH_LEN, k=STOCH_K, d=3)
        if stoch is not None and not stoch.empty:
            k_arr = stoch.iloc[:, 0].clip(0, 100).values.astype(np.float32)
            d_arr = stoch.iloc[:, 1].clip(0, 100).values.astype(np.float32)
            ind[f"k_{tf}"] = _m(k_arr)
            ind[f"d_{tf}"] = _m(d_arr)
            k_prev = np.roll(k_arr, 1); k_prev[0] = k_arr[0]
            d_prev = np.roll(d_arr, 1); d_prev[0] = d_arr[0]
            ind[f"k_{tf}_prev"] = _m(k_prev)
            ind[f"d_{tf}_prev"] = _m(d_prev)
            ind[f"k_cross_up_{tf}"] = _m(((k_arr > d_arr) & (k_prev <= d_prev)).astype(np.float32))
            ind[f"k_cross_dn_{tf}"] = _m(((k_arr < d_arr) & (k_prev >= d_prev)).astype(np.float32))
            # K direction
            ind[f"k_rising_{tf}"] = _m((k_arr > k_prev).astype(np.float32))
            ind[f"k_falling_{tf}"] = _m((k_arr < k_prev).astype(np.float32))
    except Exception:
        pass

    # --- RSI ---
    try:
        rsi_s = rsi_series(close, 14)
        if rsi_s is not None:
            ind[f"rsi_{tf}"] = _m(rsi_s.values.astype(np.float32))
        # RSI(2) for mean-reversion
        rsi2 = rsi_series(close, 2)
        if rsi2 is not None:
            ind[f"rsi2_{tf}"] = _m(rsi2.values.astype(np.float32))
    except Exception:
        pass

    # --- ATR ---
    try:
        atr_s = atr_series(df, 14)
        if atr_s is not None:
            atr_v = atr_s.values.astype(np.float32)
            ind[f"atr_{tf}"] = _m(atr_v)
            atr_pct = np.where(close.values > 0, atr_v / close.values * 100, 0).astype(np.float32)
            ind[f"atr_pct_{tf}"] = _m(atr_pct)
    except Exception:
        pass

    # --- WaveTrend FULL ---
    try:
        wt1, wt2 = wavetrend(df, timeframe=tf)
        if wt1 is not None and wt2 is not None and not wt1.empty:
            wt1_arr = wt1.values.astype(np.float32)
            wt2_arr = wt2.values.astype(np.float32)
            ind[f"wt1_{tf}"] = _m(wt1_arr)
            ind[f"wt2_{tf}"] = _m(wt2_arr)
            wt1_prev = np.roll(wt1_arr, 1); wt2_prev = np.roll(wt2_arr, 1)
            wt1_prev[0] = wt1_arr[0]; wt2_prev[0] = wt2_arr[0]
            cross_bull = (wt1_prev <= wt2_prev) & (wt1_arr > wt2_arr)
            cross_bear = (wt1_prev >= wt2_prev) & (wt1_arr < wt2_arr)
            ind[f"wt_cross_bull_{tf}"] = _m(cross_bull.astype(np.float32))
            ind[f"wt_cross_bear_{tf}"] = _m(cross_bear.astype(np.float32))
            ind[f"wt_bullish_{tf}"] = _m((wt1_arr > wt2_arr).astype(np.float32))
            ind[f"wt_score_{tf}"] = _m((wt1_arr - wt2_arr).astype(np.float32))
            # Velocity (3-bar)
            vel = np.zeros(tf_n, dtype=np.float32)
            vel[3:] = wt1_arr[3:] - wt1_arr[:-3]
            ind[f"wt_vel_{tf}"] = _m(vel)
            # Acceleration
            vel_prev = np.zeros(tf_n, dtype=np.float32)
            vel_prev[6:] = wt1_arr[3:tf_n-3] - wt1_arr[:tf_n-6]
            accel = vel - vel_prev
            ind[f"wt_accel_{tf}"] = _m(accel.astype(np.float32))
            # Momentum state
            wt_rising = vel > 0
            mom = np.zeros(tf_n, dtype=np.int8)  # 0=exhaust_down, 1=impulse_up, 2=exhaust_up, 3=impulse_down
            mom[wt_rising & (accel > 0)] = 1
            mom[wt_rising & (accel <= 0)] = 2
            mom[(~wt_rising) & (accel < 0)] = 3
            ind[f"wt_mom_{tf}"] = _m(mom)
            # Cross tracking
            bull_idx = np.where(cross_bull)[0]
            bear_idx = np.where(cross_bear)[0]
            all_cross = np.sort(np.concatenate([bull_idx, bear_idx]))
            bars_since_cross = np.full(tf_n, 999.0, dtype=np.float32)
            if len(all_cross) > 0:
                ci = np.searchsorted(all_cross, np.arange(tf_n), side='right') - 1
                valid = ci >= 0
                bars_since_cross[valid] = (np.arange(tf_n)[valid] - all_cross[ci[valid]]).astype(np.float32)
            ind[f"wt_bars_since_cross_{tf}"] = _m(bars_since_cross)
            # WT percentile (rolling 200)
            wt1_s = pd.Series(wt1_arr)
            pctile = wt1_s.rolling(200, min_periods=50).apply(lambda x: (x.iloc[-1] - x.min()) / (x.max() - x.min()) * 100 if x.max() != x.min() else 50, raw=False).fillna(50).values.astype(np.float32)
            ind[f"wt_pctile_{tf}"] = _m(pctile)
            # Structure: higher highs / lower lows on WT
            if tf_n >= 3:
                peaks = np.zeros(tf_n, dtype=bool)
                troughs = np.zeros(tf_n, dtype=bool)
                peaks[1:-1] = (wt1_arr[1:-1] > wt1_arr[:-2]) & (wt1_arr[1:-1] > wt1_arr[2:])
                troughs[1:-1] = (wt1_arr[1:-1] < wt1_arr[:-2]) & (wt1_arr[1:-1] < wt1_arr[2:])
                ind[f"wt_peak_{tf}"] = _m(peaks.astype(np.float32))
                ind[f"wt_trough_{tf}"] = _m(troughs.astype(np.float32))
    except Exception:
        pass

    # --- Bollinger Bands ---
    try:
        sma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        bb_upper = (sma20 + 2 * std20).values
        bb_lower = (sma20 - 2 * std20).values
        bb_mid = sma20.values
        bb_pctb = np.where(bb_upper > bb_lower, (close.values - bb_lower) / (bb_upper - bb_lower), 0.5).astype(np.float32)
        bb_width = np.where(bb_mid > 0, (bb_upper - bb_lower) / bb_mid * 100, 10).astype(np.float32)
        bb_squeeze = np.zeros(tf_n, dtype=np.float32)
        bw_s = pd.Series(bb_width)
        bw_pctile = bw_s.rolling(100, min_periods=20).rank(pct=True).fillna(0.5).values.astype(np.float32)
        ind[f"bb_pctb_{tf}"] = _m(bb_pctb)
        ind[f"bb_width_{tf}"] = _m(bb_width)
        ind[f"bb_squeeze_{tf}"] = _m(bw_pctile)  # Low = squeeze
    except Exception:
        pass

    # --- Donchian Channels ---
    try:
        dc_h20 = high.rolling(20).max().values.astype(np.float32)
        dc_l20 = low.rolling(20).min().values.astype(np.float32)
        dc_basis = ((dc_h20 + dc_l20) / 2.0).astype(np.float32)
        dc_range = dc_h20 - dc_l20
        dc_pos = np.clip(np.where(dc_range > 0, (close.values - dc_l20) / dc_range, 0.5), 0, 1).astype(np.float32)
        dc_width = np.where(dc_l20 > 0, dc_range / dc_l20 * 100, 10).astype(np.float32)
        ind[f"dc_pos_{tf}"] = _m(dc_pos)
        ind[f"dc_h_{tf}"] = _m(dc_h20)
        ind[f"dc_l_{tf}"] = _m(dc_l20)
        ind[f"dc_basis_{tf}"] = _m(dc_basis)
        ind[f"dc_width_{tf}"] = _m(dc_width)
        # DC breakout flags
        dc_h_prev = np.roll(dc_h20, 1); dc_h_prev[0] = dc_h20[0]
        dc_l_prev = np.roll(dc_l20, 1); dc_l_prev[0] = dc_l20[0]
        close_prev = np.roll(close.values, 1)
        ind[f"dc_breakout_high_{tf}"] = _m(((close.values > dc_h20) & (close_prev <= dc_h_prev)).astype(np.float32))
        ind[f"dc_breakout_low_{tf}"] = _m(((close.values < dc_l20) & (close_prev >= dc_l_prev)).astype(np.float32))
    except Exception:
        pass

    # --- Heikin-Ashi ---
    try:
        if lfilter is not None:
            ha_close = ((opn + high + low + close) / 4.0).values.astype(np.float64)
            init = (float(opn.iloc[0]) + float(close.iloc[0])) / 2.0
            shifted = np.empty(tf_n); shifted[0] = init; shifted[1:] = ha_close[:-1]
            ha_open_arr = lfilter([0.5], [1, -0.5], shifted, zi=[init * 0.5])[0]
            ha_green = (ha_close > ha_open_arr).astype(np.float32)
            ha_prev = np.roll(ha_green, 1); ha_prev[0] = ha_green[0]
            ind[f"ha_green_{tf}"] = _m(ha_green)
            ind[f"ha_green_{tf}_prev"] = _m(ha_prev)
            # HA streaks
            streak = np.zeros(tf_n, dtype=np.float32)
            for j in range(1, tf_n):
                if ha_green[j] == ha_green[j-1]:
                    streak[j] = streak[j-1] + (1 if ha_green[j] > 0.5 else -1)
                else:
                    streak[j] = 1 if ha_green[j] > 0.5 else -1
            ind[f"ha_streak_{tf}"] = _m(streak)
    except Exception:
        pass

    # --- EMA / SMA ---
    try:
        ema9 = close.ewm(span=9, adjust=False).mean().values.astype(np.float32)
        ema20 = close.ewm(span=20, adjust=False).mean().values.astype(np.float32)
        ema50 = close.ewm(span=50, adjust=False).mean().values.astype(np.float32)
        sma200 = close.rolling(200).mean().fillna(0).values.astype(np.float32)
        ind[f"ema9_{tf}"] = _m(ema9)
        ind[f"ema20_{tf}"] = _m(ema20)
        ind[f"ema50_{tf}"] = _m(ema50)
        ind[f"sma200_{tf}"] = _m(sma200)
        # Distances (%)
        ind[f"ema_dist_{tf}"] = _m(np.where(ema20 > 0, (close.values - ema20) / ema20 * 100, 0).astype(np.float32))
        ind[f"sma200_dist_{tf}"] = _m(np.where(sma200 > 0, (close.values - sma200) / sma200 * 100, 0).astype(np.float32))
        # EMA20 slope
        ema20_prev = np.roll(ema20, 1); ema20_prev[0] = ema20[0]
        ema20_slope = np.where(ema20_prev > 0, (ema20 - ema20_prev) / ema20_prev * 100, 0).astype(np.float32)
        ind[f"ema20_slope_{tf}"] = _m(ema20_slope)
    except Exception:
        pass

    # --- Relative Volume ---
    try:
        vol_sma = vol.rolling(20).mean()
        rvol = (vol / vol_sma).fillna(1.0).values.astype(np.float32)
        ind[f"rvol_{tf}"] = _m(rvol)
    except Exception:
        pass

    # --- Momentum ---
    try:
        mom3 = (close.pct_change(3).fillna(0).values * 100).astype(np.float32)
        mom5 = (close.pct_change(5).fillna(0).values * 100).astype(np.float32)
        mom10 = (close.pct_change(10).fillna(0).values * 100).astype(np.float32)
        ind[f"mom3_{tf}"] = _m(mom3)
        ind[f"mom5_{tf}"] = _m(mom5)
        ind[f"mom10_{tf}"] = _m(mom10)
    except Exception:
        pass

    # --- MFI ---
    try:
        tp = (high + low + close) / 3.0
        mf = tp * vol
        tp_diff = tp.diff()
        pos_mf = mf.where(tp_diff > 0, 0.0).rolling(14).sum()
        neg_mf = mf.where(tp_diff < 0, 0.0).rolling(14).sum()
        mfi = (100.0 - 100.0 / (1.0 + pos_mf / neg_mf.replace(0, np.nan))).fillna(50.0).values.astype(np.float32)
        ind[f"mfi_{tf}"] = _m(mfi)
    except Exception:
        pass

    # --- ADX ---
    try:
        _adx_len = 14
        if tf_n > _adx_len * 2 + 1:
            plus_dm = high.diff().clip(lower=0.0)
            minus_dm = (-low.diff()).clip(lower=0.0)
            mask = plus_dm < minus_dm
            plus_dm = plus_dm.where(~mask, 0.0)
            minus_dm = minus_dm.where(mask, 0.0)
            _atr = atr_series(df, _adx_len)
            if _atr is not None:
                alpha = 1.0 / _adx_len
                plus_di = 100.0 * (plus_dm.ewm(alpha=alpha, adjust=False, min_periods=_adx_len).mean() / _atr.replace(0.0, np.nan))
                minus_di = 100.0 * (minus_dm.ewm(alpha=alpha, adjust=False, min_periods=_adx_len).mean() / _atr.replace(0.0, np.nan))
                di_sum = (plus_di + minus_di).replace(0.0, np.nan)
                dx = ((plus_di - minus_di).abs() / di_sum) * 100.0
                adx_s = dx.ewm(alpha=alpha, adjust=False, min_periods=_adx_len).mean()
                ind[f"adx_{tf}"] = _m(adx_s.fillna(25.0).values.astype(np.float32))
                ind[f"plus_di_{tf}"] = _m(plus_di.fillna(0).values.astype(np.float32))
                ind[f"minus_di_{tf}"] = _m(minus_di.fillna(0).values.astype(np.float32))
    except Exception:
        pass

    # --- MACD ---
    try:
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd_line = (ema12 - ema26).astype(np.float32)
        signal_line = macd_line.ewm(span=9, adjust=False).mean().astype(np.float32)
        macd_hist = (macd_line - signal_line).astype(np.float32)
        ind[f"macd_{tf}"] = _m(macd_line.values)
        ind[f"macd_signal_{tf}"] = _m(signal_line.values)
        ind[f"macd_hist_{tf}"] = _m(macd_hist.values)
        # MACD cross
        macd_prev = np.roll(macd_line.values, 1)
        sig_prev = np.roll(signal_line.values, 1)
        ind[f"macd_cross_bull_{tf}"] = _m(((macd_line.values > signal_line.values) & (macd_prev <= sig_prev)).astype(np.float32))
        ind[f"macd_cross_bear_{tf}"] = _m(((macd_line.values < signal_line.values) & (macd_prev >= sig_prev)).astype(np.float32))
    except Exception:
        pass

    # --- Choppiness Index ---
    try:
        _ci_len = 14
        if tf_n > _ci_len + 1:
            _atr_ci = atr_series(df, 1)  # True range
            if _atr_ci is not None:
                atr_sum = _atr_ci.rolling(_ci_len).sum()
                hh = high.rolling(_ci_len).max()
                ll = low.rolling(_ci_len).min()
                ci = 100 * np.log10((atr_sum / (hh - ll).replace(0, np.nan)).fillna(0)) / np.log10(_ci_len)
                ind[f"chop_{tf}"] = _m(ci.fillna(50).values.astype(np.float32))
    except Exception:
        pass

    # --- Linear Regression Slope ---
    try:
        from numpy.polynomial.polynomial import polyfit
        lr_period = 20
        if tf_n > lr_period:
            x = np.arange(lr_period, dtype=np.float64)
            lr_slope = np.zeros(tf_n, dtype=np.float32)
            c_arr = close.values.astype(np.float64)
            for j in range(lr_period, tf_n):
                coeffs = np.polyfit(x, c_arr[j-lr_period:j], 1)
                lr_slope[j] = coeffs[0] / c_arr[j] * 100 if c_arr[j] > 0 else 0
            ind[f"lr_slope_{tf}"] = _m(lr_slope)
    except Exception:
        pass

    # --- OHLCV ---
    ind[f"close_{tf}"] = _m(close.values.astype(np.float32))
    ind[f"open_{tf}"] = _m(opn.values.astype(np.float32))
    ind[f"high_{tf}"] = _m(high.values.astype(np.float32))
    ind[f"low_{tf}"] = _m(low.values.astype(np.float32))
    ind[f"volume_{tf}"] = _m(vol.values.astype(np.float32))
    h_prev = np.roll(high.values, 1).astype(np.float32); h_prev[0] = high.values[0]
    l_prev = np.roll(low.values, 1).astype(np.float32); l_prev[0] = low.values[0]
    ind[f"high_{tf}_prev"] = _m(h_prev)
    ind[f"low_{tf}_prev"] = _m(l_prev)
    # Candle metrics
    body = np.abs(close.values - opn.values).astype(np.float32)
    total_range = (high.values - low.values).astype(np.float32)
    body_ratio = np.where(total_range > 0, body / total_range, 0).astype(np.float32)
    ind[f"body_ratio_{tf}"] = _m(body_ratio)

    return ind


# ═══════════════════════════════════════════════════════════════
# MAIN PRECOMPUTATION
# ═══════════════════════════════════════════════════════════════

def precompute_symbol(symbol: str, primary_tf: str = "15m", force: bool = False) -> Optional[str]:
    """Precompute all indicators for one symbol and save to .npz cache.
    Returns cache path or None on failure."""
    cache_path = CACHE_DIR / f"{symbol}_{primary_tf}.npz"
    kline_path = KLINES_DIR / f"{symbol}_{primary_tf}.json"

    # Skip if cache is newer than klines and not forced
    if not force and cache_path.exists() and kline_path.exists():
        if cache_path.stat().st_mtime > kline_path.stat().st_mtime:
            return str(cache_path)

    # Load primary TF
    primary_df = load_klines_df(symbol, primary_tf)
    if primary_df is None or len(primary_df) < 200:
        return None

    n = len(primary_df)
    primary_ts_unix = primary_df["timestamp_dt"].values.astype("datetime64[s]").astype(np.int64)

    # Determine HTF list based on symbol type
    is_stock = not symbol.endswith("USDT") and not symbol.endswith("USDC") and not symbol.endswith("BUSD")
    all_tfs = [primary_tf, "1h", "4h", "D"]
    if not is_stock and primary_tf == "15m":
        all_tfs = ["15m", "1h", "4h", "D"]

    # Load/aggregate TF data
    tf_data = {primary_tf: primary_df}
    primary_start = primary_df["timestamp_dt"].iloc[0]
    primary_end = primary_df["timestamp_dt"].iloc[-1]
    primary_span = (primary_end - primary_start).total_seconds()
    for tf in all_tfs:
        if tf == primary_tf:
            continue
        df = load_klines_df(symbol, tf)
        use_agg = False
        if df is not None and len(df) >= WARMUP_MIN:
            htf_span = (df["timestamp_dt"].iloc[-1] - df["timestamp_dt"].iloc[0]).total_seconds()
            if htf_span < primary_span * 0.5:
                use_agg = True
            else:
                tf_data[tf] = df
        else:
            use_agg = True
        if use_agg and tf in ("1h", "4h", "D"):
            agg = aggregate_htf(primary_df, primary_tf, tf)
            if agg is not None and len(agg) >= WARMUP_MIN:
                tf_data[tf] = agg

    # Compute indicators for each TF
    all_ind = {}
    for tf, df in tf_data.items():
        if tf != primary_tf:
            htf_ts = df["timestamp_dt"].values.astype("datetime64[s]").astype(np.int64)
            idx_map = np.searchsorted(htf_ts, primary_ts_unix, side="right") - 1
            np.clip(idx_map, 0, len(df) - 1, out=idx_map)
        else:
            idx_map = None
        tf_ind = compute_tf_indicators(df, tf, n, idx_map)
        all_ind.update(tf_ind)

    # Proxy 3m from 15m if 3m data not available
    for key in list(all_ind.keys()):
        if key.endswith("_15m"):
            dst = key.replace("_15m", "_3m")
            if dst not in all_ind:
                all_ind[dst] = all_ind[key]

    # Add convenience aliases
    all_ind["close"] = all_ind.get(f"close_{primary_tf}", np.zeros(n, dtype=np.float32))
    all_ind["high"] = all_ind.get(f"high_{primary_tf}", np.zeros(n, dtype=np.float32))
    all_ind["low"] = all_ind.get(f"low_{primary_tf}", np.zeros(n, dtype=np.float32))
    all_ind["open"] = all_ind.get(f"open_{primary_tf}", np.zeros(n, dtype=np.float32))

    # Save metadata
    all_ind["_n"] = np.array([n], dtype=np.int64)
    all_ind["_primary_tf"] = np.array([primary_tf], dtype="U10")
    all_ind["_symbol"] = np.array([symbol], dtype="U30")
    all_ind["_is_stock"] = np.array([1 if is_stock else 0], dtype=np.int8)
    ts_start = primary_df["timestamp_dt"].iloc[0]
    ts_end = primary_df["timestamp_dt"].iloc[-1]
    all_ind["_ts_start"] = np.array([str(ts_start)], dtype="U30")
    all_ind["_ts_end"] = np.array([str(ts_end)], dtype="U30")
    all_ind["_tfs_available"] = np.array(list(tf_data.keys()), dtype="U10")

    # Filter out any non-numpy arrays and ensure lengths match
    clean = {}
    for k, v in all_ind.items():
        if isinstance(v, np.ndarray):
            if k.startswith("_") or len(v) == n:
                clean[k] = v
            elif len(v) > n:
                clean[k] = v[:n]

    # Save as compressed npz
    np.savez_compressed(str(cache_path), **clean)
    return str(cache_path)


def worker(args):
    sym, primary_tf, force = args
    try:
        t0 = time.time()
        result = precompute_symbol(sym, primary_tf, force)
        elapsed = time.time() - t0
        if result:
            size_mb = os.path.getsize(result) / 1024 / 1024
            return sym, True, f"{elapsed:.1f}s, {size_mb:.1f}MB"
        return sym, False, "No data"
    except Exception as e:
        return sym, False, str(e)[:100]


# ═══════════════════════════════════════════════════════════════
# LOADER — for backtests to use
# ═══════════════════════════════════════════════════════════════

def load_cached(symbol: str, primary_tf: str = "15m") -> Optional[Dict[str, np.ndarray]]:
    """Load precomputed indicators from cache. Returns dict with 'n' key + indicator arrays."""
    cache_path = CACHE_DIR / f"{symbol}_{primary_tf}.npz"
    if not cache_path.exists():
        return None
    try:
        data = dict(np.load(str(cache_path), allow_pickle=True))
        data["n"] = int(data["_n"][0])
        return data
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Precompute indicator cache for all symbols")
    parser.add_argument("--workers", type=int, default=16, help="Parallel workers")
    parser.add_argument("--crypto-only", action="store_true", help="Only crypto symbols")
    parser.add_argument("--stocks-only", action="store_true", help="Only stock symbols")
    parser.add_argument("--symbol", type=str, help="Single symbol")
    parser.add_argument("--force", action="store_true", help="Rebuild all even if cached")
    parser.add_argument("--tf", type=str, default="15m", help="Primary timeframe")
    args = parser.parse_args()

    if args.symbol:
        print(f"Precomputing {args.symbol}...")
        result = precompute_symbol(args.symbol, args.tf, force=True)
        if result:
            size_mb = os.path.getsize(result) / 1024 / 1024
            ind = load_cached(args.symbol, args.tf)
            print(f"  Saved: {result} ({size_mb:.1f}MB)")
            print(f"  Bars: {ind['n']}, Arrays: {len([k for k in ind if not k.startswith('_') and k != 'n'])}")
            print(f"  TFs: {list(ind['_tfs_available'])}")
            print(f"  Range: {ind['_ts_start'][0]} — {ind['_ts_end'][0]}")
        else:
            print("  FAILED — no data")
        return

    # Discover symbols
    all_15m = sorted(KLINES_DIR.glob("*_15m.json"))
    crypto_syms = []
    stock_syms = []
    for p in all_15m:
        sym = p.stem.replace("_15m", "")
        if any(sym.endswith(s) for s in ("USDT", "USDC", "BUSD")):
            crypto_syms.append(sym)
        else:
            stock_syms.append(sym)

    if args.crypto_only:
        symbols = crypto_syms
    elif args.stocks_only:
        symbols = stock_syms
    else:
        symbols = crypto_syms + stock_syms

    print(f"Precomputing indicators for {len(symbols)} symbols ({len(crypto_syms)} crypto + {len(stock_syms)} stocks)")
    print(f"  Klines: {KLINES_DIR}")
    print(f"  Cache:  {CACHE_DIR}")
    print(f"  TF:     {args.tf}")
    print(f"  Workers: {args.workers}")
    if not args.force:
        # Count already cached
        cached = sum(1 for s in symbols if (CACHE_DIR / f"{s}_{args.tf}.npz").exists())
        print(f"  Already cached: {cached}/{len(symbols)} (use --force to rebuild)")

    tasks = [(sym, args.tf, args.force) for sym in symbols]
    start = time.time()
    done = 0
    success = 0
    skipped = 0

    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(worker, t): t[0] for t in tasks}
        for f in as_completed(futures):
            if _SHUTDOWN:
                break
            sym = futures[f]
            try:
                s, ok, msg = f.result()
                if ok:
                    success += 1
            except Exception as e:
                ok = False
                msg = str(e)[:50]
            done += 1
            if done % 20 == 0 or not ok:
                elapsed = time.time() - start
                print(f"  [{done}/{len(symbols)}] {elapsed:.0f}s — {success} cached, last: {sym} {'OK' if ok else 'FAIL'} ({msg})")

    elapsed = time.time() - start
    print(f"\n{'='*70}")
    print(f"  DONE in {elapsed:.0f}s ({elapsed/60:.1f} min)")
    print(f"  Cached: {success}/{len(symbols)} symbols")
    total_size = sum(f.stat().st_size for f in CACHE_DIR.glob(f"*_{args.tf}.npz")) / 1024 / 1024
    print(f"  Total cache size: {total_size:.0f}MB")
    print(f"  Cache dir: {CACHE_DIR}")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
