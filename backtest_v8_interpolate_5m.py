#!/usr/bin/env python3
"""
Interpolate 5m candles from 15m OHLC data for STOCKS (Tradier) and compute 5m indicators.

Each 15m bar becomes 3 x 5m sub-bars with interpolated OHLC:
- Sub-bar 0: open=O, close=lerp(O,C,0.33)
- Sub-bar 1: open=prev_close, close=lerp(O,C,0.67)
- Sub-bar 2: open=prev_close, close=C

Then compute stoch(14,3,3), WaveTrend, DC channels on the 5m series.
Output: expanded npz with 3x more bars, each having real 5m indicator values.

Usage:
    python3 backtest_v5_interpolate_5m.py --symbol AAPL
    python3 backtest_v5_interpolate_5m.py --all
"""

import argparse
import gc
import logging
import os
import platform
import sys
import time
from pathlib import Path

import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("interpolate_5m")

if platform.system() == "Darwin":
    BASE_PATH = Path("/Users/niels/Documents/binance")
else:
    BASE_PATH = Path("/home/niels/binance-sandbox")

NPZ_DIR = BASE_PATH / "backtest_v4_tradier" / "indicators"
OUT_DIR = BASE_PATH / "backtest_v5" / "indicators_5m_tradier"


def stoch_rsi(close, k_period=14, d_period=3, smooth=3):
    n = len(close)
    k_out = np.full(n, 50.0, dtype=np.float32)
    d_out = np.full(n, 50.0, dtype=np.float32)
    delta = np.diff(close, prepend=close[0])
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)
    avg_gain = np.zeros(n, dtype=np.float64)
    avg_loss = np.zeros(n, dtype=np.float64)
    avg_gain[k_period] = np.mean(gain[1:k_period + 1])
    avg_loss[k_period] = np.mean(loss[1:k_period + 1])
    for i in range(k_period + 1, n):
        avg_gain[i] = (avg_gain[i - 1] * (k_period - 1) + gain[i]) / k_period
        avg_loss[i] = (avg_loss[i - 1] * (k_period - 1) + loss[i]) / k_period
    rs = np.where(avg_loss > 0, avg_gain / avg_loss, 100.0)
    rsi = 100.0 - 100.0 / (1.0 + rs)
    # Stoch RSI
    for i in range(k_period * 2, n):
        window = rsi[i - k_period + 1:i + 1]
        lo, hi = window.min(), window.max()
        k_out[i] = ((rsi[i] - lo) / (hi - lo) * 100) if hi > lo else 50.0
    # Smooth K
    k_smooth = np.convolve(k_out, np.ones(smooth) / smooth, mode='same').astype(np.float32)
    # D = SMA of K
    d_out = np.convolve(k_smooth, np.ones(d_period) / d_period, mode='same').astype(np.float32)
    return k_smooth, d_out


def wavetrend(close, esa_period=6, chan_period=10, sig_period=12):
    n = len(close)
    esa = np.zeros(n, dtype=np.float64)
    d_arr = np.zeros(n, dtype=np.float64)
    ci = np.zeros(n, dtype=np.float64)
    wt1 = np.zeros(n, dtype=np.float32)
    wt2 = np.zeros(n, dtype=np.float32)
    alpha_esa = 2.0 / (esa_period + 1)
    alpha_chan = 2.0 / (chan_period + 1)
    alpha_sig = 2.0 / (sig_period + 1)
    esa[0] = close[0]
    for i in range(1, n):
        esa[i] = alpha_esa * close[i] + (1 - alpha_esa) * esa[i - 1]
    for i in range(1, n):
        d_arr[i] = alpha_chan * abs(close[i] - esa[i]) + (1 - alpha_chan) * d_arr[i - 1]
    for i in range(1, n):
        ci[i] = (close[i] - esa[i]) / (0.015 * d_arr[i]) if d_arr[i] > 0 else 0.0
    wt1[0] = ci[0]
    for i in range(1, n):
        wt1[i] = alpha_sig * ci[i] + (1 - alpha_sig) * wt1[i - 1]
    wt2[0] = wt1[0]
    for i in range(1, n):
        wt2[i] = 0.25 * wt1[i] + 0.75 * wt2[i - 1]
    return wt1, wt2


def heikin_ashi(open_arr, high_arr, low_arr, close_arr):
    n = len(close_arr)
    ha_close = (open_arr + high_arr + low_arr + close_arr) / 4
    ha_open = np.zeros(n)
    ha_open[0] = (open_arr[0] + close_arr[0]) / 2
    for i in range(1, n):
        ha_open[i] = (ha_open[i - 1] + ha_close[i - 1]) / 2
    return (ha_close > ha_open).astype(np.int8)


def interpolate_15m_to_5m(open_15m, high_15m, low_15m, close_15m, timestamps_15m):
    """Expand each 15m bar into 3 x 5m sub-bars with interpolated OHLC."""
    n = len(close_15m)
    n5 = n * 3
    open_5m = np.zeros(n5, dtype=np.float32)
    high_5m = np.zeros(n5, dtype=np.float32)
    low_5m = np.zeros(n5, dtype=np.float32)
    close_5m = np.zeros(n5, dtype=np.float32)
    ts_5m = np.zeros(n5, dtype=np.int64)
    for i in range(n):
        o, h, l, c = open_15m[i], high_15m[i], low_15m[i], close_15m[i]
        base_ts = timestamps_15m[i]
        is_up = c >= o
        for j in range(3):
            frac = (j + 1) / 3.0
            idx = i * 3 + j
            ts_5m[idx] = base_ts + j * 300  # 5min intervals
            if j == 0:
                open_5m[idx] = o
            else:
                open_5m[idx] = close_5m[idx - 1]
            close_5m[idx] = o + (c - o) * frac
            if is_up:
                if j == 0:
                    low_5m[idx] = o + (l - o) * min(1.0, frac * 3.0)
                    high_5m[idx] = max(open_5m[idx], close_5m[idx])
                elif j == 1:
                    low_5m[idx] = min(open_5m[idx], close_5m[idx])
                    high_5m[idx] = o + (h - o) * min(1.0, frac * 1.5)
                else:
                    low_5m[idx] = min(open_5m[idx], close_5m[idx])
                    high_5m[idx] = h
            else:
                if j == 0:
                    high_5m[idx] = o + (h - o) * min(1.0, frac * 3.0)
                    low_5m[idx] = min(open_5m[idx], close_5m[idx])
                elif j == 1:
                    high_5m[idx] = max(open_5m[idx], close_5m[idx])
                    low_5m[idx] = o + (l - o) * min(1.0, frac * 1.5)
                else:
                    high_5m[idx] = max(open_5m[idx], close_5m[idx])
                    low_5m[idx] = l
            high_5m[idx] = max(high_5m[idx], open_5m[idx], close_5m[idx])
            low_5m[idx] = min(low_5m[idx], open_5m[idx], close_5m[idx])
    return open_5m, high_5m, low_5m, close_5m, ts_5m


def process_symbol(symbol: str):
    """Process one symbol: interpolate 5m candles and compute indicators."""
    npz_path = NPZ_DIR / f"{symbol}.npz"
    if not npz_path.exists():
        logger.warning(f"{symbol}: no npz found in {NPZ_DIR}")
        return False
    out_path = OUT_DIR / f"{symbol}.npz"
    if out_path.exists():
        logger.info(f"{symbol}: already done, skipping")
        return True
    t0 = time.time()
    data = np.load(str(npz_path), allow_pickle=True)
    arrays = {k: data[k] for k in data.files}
    timestamps = arrays["timestamps"].astype(np.int64)
    n = len(timestamps)
    open_15m = arrays.get("open_15m", arrays.get("close", np.zeros(n))).astype(np.float32)
    high_15m = arrays.get("high_15m", arrays.get("close", np.zeros(n))).astype(np.float32)
    low_15m = arrays.get("low_15m", arrays.get("close", np.zeros(n))).astype(np.float32)
    close_15m = arrays["close"].astype(np.float32)
    open_5m, high_5m, low_5m, close_5m, ts_5m = interpolate_15m_to_5m(open_15m, high_15m, low_15m, close_15m, timestamps)
    n5 = len(close_5m)
    logger.info(f"{symbol}: {n} 15m bars → {n5} 5m bars")
    k5, d5 = stoch_rsi(close_5m, k_period=14, d_period=3, smooth=3)
    wt1_5m, wt2_5m = wavetrend(close_5m, esa_period=6, chan_period=10, sig_period=12)
    ha_5m = heikin_ashi(open_5m, high_5m, low_5m, close_5m)
    k5_prev = np.roll(k5, 1); k5_prev[0] = 50.0
    d5_prev = np.roll(d5, 1); d5_prev[0] = 50.0
    crossover_5m = ((k5 > d5) & (k5_prev <= d5_prev)).astype(np.int8)
    crossunder_5m = ((k5 < d5) & (k5_prev >= d5_prev)).astype(np.int8)
    wt_bullish_5m = (wt1_5m > wt2_5m).astype(np.int8)
    wt_velocity_5m = np.zeros(n5, dtype=np.float32)
    for i in range(3, n5):
        wt_velocity_5m[i] = wt1_5m[i] - wt1_5m[i - 3]
    wt_cross_bull = ((wt1_5m > wt2_5m) & (np.roll(wt1_5m, 1) <= np.roll(wt2_5m, 1))).astype(np.int8)
    wt_cross_bear = ((wt1_5m < wt2_5m) & (np.roll(wt1_5m, 1) >= np.roll(wt2_5m, 1))).astype(np.int8)
    dc_period = 20
    dc_high_5m = np.zeros(n5, dtype=np.float32)
    dc_low_5m = np.zeros(n5, dtype=np.float32)
    for i in range(dc_period, n5):
        dc_high_5m[i] = np.max(high_5m[i - dc_period + 1:i + 1])
        dc_low_5m[i] = np.min(low_5m[i - dc_period + 1:i + 1])
    dc_basis_5m = (dc_high_5m + dc_low_5m) / 2
    dc_width_5m = np.where(dc_basis_5m > 0, (dc_high_5m - dc_low_5m) / dc_basis_5m, 0)
    dc_position_5m = np.where((dc_high_5m - dc_low_5m) > 0, (close_5m - dc_low_5m) / (dc_high_5m - dc_low_5m), 0.5)
    # Map HTF indicators (15m, 1h, 4h, D) to 5m index via forward-fill
    htf_arrays_5m = {}
    for key in arrays:
        if key == "timestamps" or key.endswith("_5m") or key == "close":
            continue
        arr = arrays[key]
        if len(arr) == n:
            expanded = np.repeat(arr, 3)
            htf_arrays_5m[key] = expanded[:n5]
    out = {}
    out["timestamps"] = ts_5m
    out["close"] = close_5m
    out["open_5m"] = open_5m
    out["high_5m"] = high_5m
    out["low_5m"] = low_5m
    out["close_5m"] = close_5m
    out["stoch_k_5m"] = k5.astype(np.float32)
    out["stoch_d_5m"] = d5.astype(np.float32)
    out["k_5m_prev"] = k5_prev.astype(np.float32)
    out["d_5m_prev"] = d5_prev.astype(np.float32)
    out["stoch_crossover_5m"] = crossover_5m
    out["stoch_crossunder_5m"] = crossunder_5m
    out["wt1_5m"] = wt1_5m.astype(np.float32)
    out["wt2_5m"] = wt2_5m.astype(np.float32)
    out["wt_bullish_5m"] = wt_bullish_5m
    out["wt_velocity_5m"] = wt_velocity_5m
    out["wt_cross_bull_5m"] = wt_cross_bull
    out["wt_cross_bear_5m"] = wt_cross_bear
    out["ha_5m"] = ha_5m
    out["dc_high_5m"] = dc_high_5m
    out["dc_low_5m"] = dc_low_5m
    out["dc_basis_5m"] = dc_basis_5m
    out["dc_width_5m"] = dc_width_5m
    out["dc_position_5m"] = dc_position_5m
    out["high_5m_prev"] = np.roll(high_5m, 1).astype(np.float32)
    out["low_5m_prev"] = np.roll(low_5m, 1).astype(np.float32)
    out["close_5m_prev"] = np.roll(close_5m, 1).astype(np.float32)
    # RSI 5m — computed on real 5m close
    delta = np.diff(close_5m, prepend=close_5m[0])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_g = np.zeros(n5, dtype=np.float64)
    avg_l = np.zeros(n5, dtype=np.float64)
    _rp = 14
    if n5 > _rp + 1:
        avg_g[_rp] = np.mean(gain[1:_rp + 1])
        avg_l[_rp] = np.mean(loss[1:_rp + 1])
        for i in range(_rp + 1, n5):
            avg_g[i] = (avg_g[i - 1] * (_rp - 1) + gain[i]) / _rp
            avg_l[i] = (avg_l[i - 1] * (_rp - 1) + loss[i]) / _rp
    rs = np.where(avg_l > 0, avg_g / avg_l, 100.0)
    rsi_5m = (100.0 - 100.0 / (1.0 + rs)).astype(np.float32)
    out["rsi_5m"] = rsi_5m
    # ATR 5m
    tr = np.maximum(high_5m - low_5m, np.maximum(np.abs(high_5m - np.roll(close_5m, 1)), np.abs(low_5m - np.roll(close_5m, 1))))
    tr[0] = high_5m[0] - low_5m[0]
    atr_5m = np.zeros(n5, dtype=np.float32)
    if n5 > _rp:
        atr_5m[_rp - 1] = np.mean(tr[:_rp])
        for i in range(_rp, n5):
            atr_5m[i] = (atr_5m[i - 1] * (_rp - 1) + tr[i]) / _rp
    out["atr_5m"] = atr_5m
    out["atr_5m_prev"] = np.roll(atr_5m, 1).astype(np.float32)
    # MFI 5m
    tp = (high_5m + low_5m + close_5m) / 3.0
    vol_5m = np.repeat(arrays.get("volume_5m", np.ones(n, dtype=np.float32)), 3)[:n5] if "volume_5m" in arrays else np.ones(n5, dtype=np.float32)
    raw_mf = tp * vol_5m
    pos_mf = np.where(np.diff(tp, prepend=tp[0]) > 0, raw_mf, 0)
    neg_mf = np.where(np.diff(tp, prepend=tp[0]) < 0, raw_mf, 0)
    _mp = 14
    mfi_5m = np.full(n5, 50.0, dtype=np.float32)
    for i in range(_mp, n5):
        pm = np.sum(pos_mf[i - _mp + 1:i + 1])
        nm = np.sum(neg_mf[i - _mp + 1:i + 1])
        mfi_5m[i] = 100.0 - (100.0 / (1.0 + pm / nm)) if nm > 0 else 100.0
    out["mfi_5m"] = mfi_5m
    # Relative volume 5m
    if "volume_5m" in arrays:
        vol_arr = np.repeat(arrays["volume_5m"], 3)[:n5].astype(np.float64)
    else:
        vol_arr = np.ones(n5, dtype=np.float64)
    rv_5m = np.ones(n5, dtype=np.float32)
    _rvl = 20
    for i in range(_rvl, n5):
        avg_vol = np.mean(vol_arr[i - _rvl:i])
        rv_5m[i] = vol_arr[i] / avg_vol if avg_vol > 0 else 1.0
    out["relative_volume_5m"] = rv_5m
    for key, arr in htf_arrays_5m.items():
        out[key]  = arr
    # Also forward-fill close_15m_prev if not already present
    if "close_15m_prev" not in out and "close_15m" in out:
        out["close_15m_prev"] = np.roll(out["close_15m"], 1).astype(np.float32)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(str(out_path), **out)
    elapsed = time.time() - t0
    file_size = os.path.getsize(str(out_path))
    logger.info(f"{symbol}: saved {len(out)} arrays ({file_size/1024/1024:.1f}MB) in {elapsed:.1f}s")
    data.close()
    del out, htf_arrays_5m, open_5m, high_5m, low_5m, close_5m, ts_5m
    del k5, d5, wt1_5m, wt2_5m, ha_5m, dc_high_5m, dc_low_5m
    gc.collect()
    return True


def main():
    parser = argparse.ArgumentParser(description="Interpolate 5m candles from 15m OHLC (stocks/tradier)")
    parser.add_argument("--symbol", type=str, default="")
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()
    if args.symbol:
        symbols = [args.symbol]
    else:
        symbols = sorted([p.stem for p in NPZ_DIR.glob("*.npz")])
    logger.info(f"Processing {len(symbols)} symbols from {NPZ_DIR}")
    logger.info(f"Output: {OUT_DIR}")
    done = 0
    failed = 0
    for i, sym in enumerate(symbols):
        logger.info(f"[{i+1}/{len(symbols)}] {sym}")
        ok = process_symbol(sym)
        if ok:
            done += 1
        else:
            failed += 1
    logger.info(f"DONE: {done} succeeded, {failed} failed out of {len(symbols)}")


if __name__ == "__main__":
    main()
