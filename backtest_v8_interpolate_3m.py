#!/usr/bin/env python3
"""
Interpolate 3m candles from 15m OHLC data and compute real 3m indicators.

Each 15m bar becomes 5 x 3m sub-bars with interpolated OHLC:
- Sub-bar 0: open=O, high=lerp(O,H,0.2), low=lerp(O,L,0.2), close=lerp(O,C,0.2)
- Sub-bar 1: open=prev_close, ..., close=lerp(O,C,0.4)
- ...
- Sub-bar 4: open=prev_close, high=lerp(O,H,1.0), low=lerp(O,L,1.0), close=C

Then compute stoch(14,3,3), WaveTrend, and other indicators on the 3m series.
Output: expanded npz with 5x more bars, each having real 3m indicator values.

Usage:
    python3 backtest_v5_interpolate_3m.py --symbol ENJUSDT
    python3 backtest_v5_interpolate_3m.py --all
"""

import argparse
import logging
import os
import platform
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("interpolate_3m")

if platform.system() == "Darwin":
    BASE_PATH = Path("/Users/niels/Documents/binance")
else:
    BASE_PATH = Path("/home/niels/binance-sandbox")

NPZ_DIR = BASE_PATH / "backtest_v4" / "indicators"
OUT_DIR = BASE_PATH / "backtest_v5" / "indicators_3m"


def stoch_rsi(close, k_period=14, d_period=3, smooth=3):
    """Stochastic oscillator on close prices."""
    n = len(close)
    k = np.full(n, 50.0)
    d = np.full(n, 50.0)
    for i in range(k_period, n):
        window = close[i - k_period + 1:i + 1]
        low = np.min(window)
        high = np.max(window)
        rng = high - low
        if rng > 0:
            k[i] = (close[i] - low) / rng * 100
        else:
            k[i] = 50.0
    # Smooth K with SMA
    if smooth > 1:
        k_smooth = np.full(n, 50.0)
        for i in range(smooth - 1, n):
            k_smooth[i] = np.mean(k[i - smooth + 1:i + 1])
        k = k_smooth
    # D = SMA of K
    for i in range(d_period - 1, n):
        d[i] = np.mean(k[i - d_period + 1:i + 1])
    return k, d


def wavetrend(close, esa_period=6, chan_period=10, sig_period=12):
    """WaveTrend oscillator."""
    n = len(close)
    hlc3 = close  # Simplified — use close as proxy for HLC/3
    # ESA = EMA of hlc3
    esa = np.full(n, close[0] if n > 0 else 0.0)
    alpha_esa = 2.0 / (esa_period + 1)
    for i in range(1, n):
        esa[i] = alpha_esa * hlc3[i] + (1 - alpha_esa) * esa[i - 1]
    # D = EMA of abs(hlc3 - esa)
    d_arr = np.full(n, 0.0)
    alpha_chan = 2.0 / (chan_period + 1)
    for i in range(1, n):
        d_arr[i] = alpha_chan * abs(hlc3[i] - esa[i]) + (1 - alpha_chan) * d_arr[i - 1]
    # CI = (hlc3 - esa) / (0.015 * d) if d > 0
    ci = np.zeros(n)
    for i in range(n):
        if d_arr[i] > 0:
            ci[i] = (hlc3[i] - esa[i]) / (0.015 * d_arr[i])
    # WT1 = EMA of CI
    wt1 = np.full(n, 0.0)
    alpha_sig = 2.0 / (sig_period + 1)
    for i in range(1, n):
        wt1[i] = alpha_sig * ci[i] + (1 - alpha_sig) * wt1[i - 1]
    # WT2 = SMA(WT1, 4)
    wt2 = np.full(n, 0.0)
    for i in range(3, n):
        wt2[i] = np.mean(wt1[i - 3:i + 1])
    return wt1, wt2


def heikin_ashi(open_arr, high_arr, low_arr, close_arr):
    """Compute Heikin-Ashi candle colors: 1=green, 0=red."""
    n = len(close_arr)
    ha_close = (open_arr + high_arr + low_arr + close_arr) / 4
    ha_open = np.zeros(n)
    ha_open[0] = (open_arr[0] + close_arr[0]) / 2
    for i in range(1, n):
        ha_open[i] = (ha_open[i - 1] + ha_close[i - 1]) / 2
    return (ha_close > ha_open).astype(np.int8)


def interpolate_15m_to_3m(open_15m, high_15m, low_15m, close_15m, timestamps_15m):
    """Expand each 15m bar into 5 x 3m sub-bars with interpolated OHLC."""
    n = len(close_15m)
    n3 = n * 5
    open_3m = np.zeros(n3, dtype=np.float32)
    high_3m = np.zeros(n3, dtype=np.float32)
    low_3m = np.zeros(n3, dtype=np.float32)
    close_3m = np.zeros(n3, dtype=np.float32)
    ts_3m = np.zeros(n3, dtype=np.int64)
    for i in range(n):
        o, h, l, c = open_15m[i], high_15m[i], low_15m[i], close_15m[i]
        base_ts = timestamps_15m[i]
        # Determine if this is an up or down bar
        is_up = c >= o
        for j in range(5):
            frac = (j + 1) / 5.0
            idx = i * 5 + j
            ts_3m[idx] = base_ts + j * 180  # 3min intervals
            if j == 0:
                open_3m[idx] = o
            else:
                open_3m[idx] = close_3m[idx - 1]
            # Interpolate close progressively toward bar close
            close_3m[idx] = o + (c - o) * frac
            if is_up:
                # Up bar: low comes first, then price rises to high, then settles
                if j < 2:
                    # Early sub-bars: price dips to low then starts recovering
                    low_3m[idx] = o + (l - o) * min(1.0, frac * 2.5)
                    high_3m[idx] = max(open_3m[idx], close_3m[idx])
                elif j < 4:
                    # Mid sub-bars: price pushes toward high
                    low_3m[idx] = min(open_3m[idx], close_3m[idx])
                    high_3m[idx] = o + (h - o) * min(1.0, frac * 1.5)
                else:
                    # Final sub-bar: reaches the actual high, settles at close
                    low_3m[idx] = min(open_3m[idx], close_3m[idx])
                    high_3m[idx] = h
            else:
                # Down bar: price rises to high first, then falls
                if j < 2:
                    high_3m[idx] = o + (h - o) * min(1.0, frac * 2.5)
                    low_3m[idx] = min(open_3m[idx], close_3m[idx])
                elif j < 4:
                    high_3m[idx] = max(open_3m[idx], close_3m[idx])
                    low_3m[idx] = o + (l - o) * min(1.0, frac * 1.5)
                else:
                    high_3m[idx] = max(open_3m[idx], close_3m[idx])
                    low_3m[idx] = l
            # Ensure OHLC consistency
            high_3m[idx] = max(high_3m[idx], open_3m[idx], close_3m[idx])
            low_3m[idx] = min(low_3m[idx], open_3m[idx], close_3m[idx])
    return open_3m, high_3m, low_3m, close_3m, ts_3m


def process_symbol(symbol: str):
    """Process one symbol: interpolate 3m candles and compute indicators."""
    npz_path = NPZ_DIR / f"{symbol}.npz"
    if not npz_path.exists():
        logger.warning(f"{symbol}: no npz found")
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
    # Get 15m OHLC
    open_15m = arrays.get("open_15m", arrays.get("close", np.zeros(n))).astype(np.float32)
    high_15m = arrays.get("high_15m", arrays.get("close", np.zeros(n))).astype(np.float32)
    low_15m = arrays.get("low_15m", arrays.get("close", np.zeros(n))).astype(np.float32)
    close_15m = arrays["close"].astype(np.float32)
    # Interpolate to 3m
    open_3m, high_3m, low_3m, close_3m, ts_3m = interpolate_15m_to_3m(open_15m, high_15m, low_15m, close_15m, timestamps)
    n3 = len(close_3m)
    logger.info(f"{symbol}: {n} 15m bars → {n3} 3m bars")
    # Compute 3m indicators
    k3, d3 = stoch_rsi(close_3m, k_period=14, d_period=3, smooth=3)
    wt1_3m, wt2_3m = wavetrend(close_3m, esa_period=6, chan_period=10, sig_period=12)
    ha_3m = heikin_ashi(open_3m, high_3m, low_3m, close_3m)
    # Previous values
    k3_prev = np.roll(k3, 1)
    k3_prev[0] = 50.0
    d3_prev = np.roll(d3, 1)
    d3_prev[0] = 50.0
    # Crossovers
    crossover_3m = ((k3 > d3) & (k3_prev <= d3_prev)).astype(np.int8)
    crossunder_3m = ((k3 < d3) & (k3_prev >= d3_prev)).astype(np.int8)
    # WT fields
    wt_bullish_3m = (wt1_3m > wt2_3m).astype(np.int8)
    wt_velocity_3m = np.zeros(n3, dtype=np.float32)
    for i in range(3, n3):
        wt_velocity_3m[i] = wt1_3m[i] - wt1_3m[i - 3]
    wt_cross_bull = ((wt1_3m > wt2_3m) & (np.roll(wt1_3m, 1) <= np.roll(wt2_3m, 1))).astype(np.int8)
    wt_cross_bear = ((wt1_3m < wt2_3m) & (np.roll(wt1_3m, 1) >= np.roll(wt2_3m, 1))).astype(np.int8)
    # DC channels on 3m
    dc_period = 20
    dc_high_3m = np.zeros(n3, dtype=np.float32)
    dc_low_3m = np.zeros(n3, dtype=np.float32)
    for i in range(dc_period, n3):
        dc_high_3m[i] = np.max(high_3m[i - dc_period + 1:i + 1])
        dc_low_3m[i] = np.min(low_3m[i - dc_period + 1:i + 1])
    dc_basis_3m = (dc_high_3m + dc_low_3m) / 2
    dc_width_3m = np.where(dc_basis_3m > 0, (dc_high_3m - dc_low_3m) / dc_basis_3m, 0)
    dc_position_3m = np.where((dc_high_3m - dc_low_3m) > 0, (close_3m - dc_low_3m) / (dc_high_3m - dc_low_3m), 0.5)
    # Map HTF indicators (15m, 1h, 4h, D) to 3m index via forward-fill
    # Each 15m bar maps to 5 consecutive 3m bars
    htf_arrays_3m = {}
    for key in arrays:
        if key == "timestamps" or key.endswith("_3m") or key == "close":
            continue
        arr = arrays[key]
        if len(arr) == n:
            # Expand to 3m by repeating each value 5 times
            expanded = np.repeat(arr, 5)
            htf_arrays_3m[key] = expanded[:n3]
    # Build output
    out = {}
    out["timestamps"] = ts_3m
    out["close"] = close_3m
    out["open_3m"] = open_3m
    out["high_3m"] = high_3m
    out["low_3m"] = low_3m
    out["close_3m"] = close_3m
    out["stoch_k_3m"] = k3.astype(np.float32)
    out["stoch_d_3m"] = d3.astype(np.float32)
    out["k_3m_prev"] = k3_prev.astype(np.float32)
    out["d_3m_prev"] = d3_prev.astype(np.float32)
    out["stoch_crossover_3m"] = crossover_3m
    out["stoch_crossunder_3m"] = crossunder_3m
    out["wt1_3m"] = wt1_3m.astype(np.float32)
    out["wt2_3m"] = wt2_3m.astype(np.float32)
    out["wt_bullish_3m"] = wt_bullish_3m
    out["wt_velocity_3m"] = wt_velocity_3m
    out["wt_cross_bull_3m"] = wt_cross_bull
    out["wt_cross_bear_3m"] = wt_cross_bear
    out["ha_3m"] = ha_3m
    out["dc_high_3m"] = dc_high_3m
    out["dc_low_3m"] = dc_low_3m
    out["dc_basis_3m"] = dc_basis_3m
    out["dc_width_3m"] = dc_width_3m
    out["dc_position_3m"] = dc_position_3m
    out["high_3m_prev"] = np.roll(high_3m, 1).astype(np.float32)
    out["low_3m_prev"] = np.roll(low_3m, 1).astype(np.float32)
    # Add HTF arrays (forward-filled to 3m index)
    for key, arr in htf_arrays_3m.items():
        out[key] = arr
    # Save
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"{symbol}.npz"
    np.savez_compressed(str(out_path), **out)
    elapsed = time.time() - t0
    logger.info(f"{symbol}: saved {len(out)} arrays to {out_path} ({elapsed:.1f}s)")
    # Quick verify — just check file size
    file_size = os.path.getsize(str(out_path))
    logger.info(f"{symbol}: file size {file_size/1024/1024:.1f}MB")
    data.close()
    # Explicitly free memory
    del out, htf_arrays_3m, open_3m, high_3m, low_3m, close_3m, ts_3m
    del k3, d3, wt1_3m, wt2_3m, ha_3m, dc_high_3m, dc_low_3m
    import gc
    gc.collect()
    return True


def main():
    parser = argparse.ArgumentParser(description="Interpolate 3m candles from 15m OHLC")
    parser.add_argument("--symbol", type=str, default="")
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()
    if args.symbol:
        symbols = [args.symbol]
    elif args.all:
        symbols = sorted([p.stem for p in NPZ_DIR.glob("*.npz")])
    else:
        symbols = sorted([p.stem for p in NPZ_DIR.glob("*.npz")])
    logger.info(f"Processing {len(symbols)} symbols")
    t0 = time.time()
    ok = 0
    for sym in symbols:
        if process_symbol(sym):
            ok += 1
    elapsed = time.time() - t0
    logger.info(f"Done: {ok}/{len(symbols)} symbols in {elapsed:.1f}s")


if __name__ == "__main__":
    main()
