#!/usr/bin/env python3
"""
Reverse-Engineer Trader Strategies — Find EXACT indicator settings that fire 100% on each trader's entries.

For each trader:
1. Load their closed trades with entry timestamps
2. Compute ALL indicators (1h + 4h) at each entry bar using klines
3. For each indicator, find the [min, max] range across ALL entries
4. Sweep thresholds: tighten bounds until we get 100% recall
5. Score rules by selectivity (% of all bars that fire — lower = better)
6. Combine most selective rules into a "strategy fingerprint"
7. Validate: check if the combined rules fire on ONLY the entry bars

Output: data/reverse_engineered/strategies.json + per-trader reports
"""
import argparse
import csv
import json
import logging
import math
import os
import sys
import warnings
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import Config
from ez_indicators import (
    adx_value, atr_series, bb_features, choppiness_index, donchian,
    ema_pair, ha_streak_count, heikin_ashi, macd_values, mfi_value,
    relative_volume, rsi_series, rsi_value, sma_pair, stoch_rsi,
)

config = Config()
BASE_PATH = config.BASE_PATH
KLINES_DIR = config.KLINES_CACHE_DIR
OUTPUT_DIR = BASE_PATH / "data" / "reverse_engineered"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("reverse_engineer")

KLINE_CACHE: Dict[str, Optional[pd.DataFrame]] = {}
SERIES_CACHE: Dict[str, Optional[Tuple]] = {}  # symbol_tf -> (timestamps, arrays)
MIN_KLINE_BARS = 210
MIN_TRADES_PER_TRADER = 5
SELECTIVITY_PERCENTILE_BINS = 20


# ═══════════════════════════════════════════════════════════════════
# SECTION 1 — DATA LOADING
# ═══════════════════════════════════════════════════════════════════

def load_merged_trades(path: str) -> List[Dict]:
    """Load trades from merged CSV."""
    trades = []
    with open(path) as f:
        for row in csv.DictReader(f):
            entry_time = row.get("entry_time", "")
            if not entry_time or entry_time == "":
                continue
            try:
                et = pd.to_datetime(entry_time, utc=True)
            except Exception:
                continue
            exit_time = row.get("exit_time", "")
            try:
                xt = pd.to_datetime(exit_time, utc=True) if exit_time else None
            except Exception:
                xt = None
            pnl = float(row.get("pnl", 0) or 0)
            pnl_pct = float(row.get("pnl_pct", 0) or 0)
            ep = float(row.get("entry_price", 0) or 0)
            xp = float(row.get("exit_price", 0) or 0)
            side = row.get("side", "UNKNOWN").upper()
            trades.append({
                "trader_id": row.get("trader_id", ""),
                "symbol": row.get("symbol", ""),
                "side": side,
                "entry_price": ep,
                "exit_price": xp,
                "entry_time": et,
                "exit_time": xt,
                "pnl": pnl,
                "pnl_pct": pnl_pct,
                "leverage": float(row.get("leverage", 1) or 1),
                "exchange": row.get("exchange", ""),
            })
    logger.info(f"Loaded {len(trades)} trades from {path}")
    return trades


def load_okx_trades(path: str) -> List[Dict]:
    """Load OKX JSONL trades."""
    trades = []
    with open(path) as f:
        for line in f:
            row = json.loads(line)
            inst = row.get("inst", "")
            # OKX format: ETH-USDT-SWAP → ETHUSDC
            symbol = inst.replace("-SWAP", "").replace("-", "")
            open_ts = row.get("open_time", "")
            close_ts = row.get("close_time", "")
            try:
                if open_ts and str(open_ts).isdigit():
                    et = pd.to_datetime(int(open_ts), unit="ms", utc=True)
                else:
                    et = pd.to_datetime(open_ts, utc=True)
            except Exception:
                continue
            try:
                if close_ts and str(close_ts).isdigit():
                    xt = pd.to_datetime(int(close_ts), unit="ms", utc=True)
                else:
                    xt = pd.to_datetime(close_ts, utc=True) if close_ts else None
            except Exception:
                xt = None
            pnl = float(row.get("pnl", 0) or 0)
            roe = float(row.get("roe", 0) or 0)
            side = row.get("side", "?").upper()
            if side not in ("LONG", "SHORT"):
                # Infer from entry/exit
                ep = float(row.get("entry", 0) or 0)
                xp = float(row.get("close", 0) or 0)
                if ep > 0 and xp > 0 and pnl != 0:
                    side = "LONG" if (pnl > 0) == (xp > ep) else "SHORT"
                else:
                    side = "UNKNOWN"
            trades.append({
                "trader_id": row.get("uid", row.get("trader", "")),
                "trader_name": row.get("trader", ""),
                "symbol": symbol,
                "side": side,
                "entry_price": float(row.get("entry", 0) or 0),
                "exit_price": float(row.get("close", 0) or 0),
                "entry_time": et,
                "exit_time": xt,
                "pnl": pnl,
                "pnl_pct": roe,
                "leverage": float(str(row.get("lever", "1")).replace("E+", "e")),
                "exchange": "okx",
            })
    logger.info(f"Loaded {len(trades)} OKX trades from {path}")
    return trades


# ═══════════════════════════════════════════════════════════════════
# SECTION 2 — KLINE LOADING + INDICATOR COMPUTATION
# ═══════════════════════════════════════════════════════════════════

def find_kline_symbol(raw_symbol: str) -> Optional[str]:
    """Map trade symbol to klines filename."""
    base = raw_symbol.replace("/", "").replace("-", "").upper()
    for cand in [base, base + "USDT", base.replace("USDT", "USDC")]:
        for tf in ["15m", "1h"]:
            if (KLINES_DIR / f"{cand}_{tf}.json").exists():
                return cand
    return None


def load_klines(symbol: str, tf: str) -> Optional[pd.DataFrame]:
    """Load klines from cache dir."""
    key = f"{symbol}_{tf}"
    if key in KLINE_CACHE:
        return KLINE_CACHE[key]
    path = KLINES_DIR / f"{key}.json"
    if not path.exists():
        KLINE_CACHE[key] = None
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        df = pd.DataFrame(data)
        if df.empty:
            KLINE_CACHE[key] = None
            return None
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.sort_values("timestamp").reset_index(drop=True)
        KLINE_CACHE[key] = df
        return df
    except Exception as e:
        logger.warning(f"Failed to load {path}: {e}")
        KLINE_CACHE[key] = None
        return None


def get_klines_slice(symbol: str, timestamp: pd.Timestamp, tf: str, lookback: int = 250) -> Optional[pd.DataFrame]:
    """Get klines ending at or before timestamp."""
    df = load_klines(symbol, tf)
    if df is None or df.empty:
        return None
    mask = df["timestamp"] <= timestamp
    subset = df[mask]
    if len(subset) < MIN_KLINE_BARS:
        return None
    return subset.iloc[-lookback:].copy().reset_index(drop=True)


def compute_indicators(df: pd.DataFrame, price: float) -> Dict[str, float]:
    """Compute ALL indicators at the last bar. Returns flat numeric dict."""
    ind = {}
    if df is None or len(df) < MIN_KLINE_BARS:
        return ind
    close = df["close"]
    high = df["high"]
    low = df["low"]
    # RSI
    for period in [14, 2]:
        val = rsi_value(close, period)
        if val is not None:
            ind[f"rsi_{period}"] = float(val)
    # Stochastic RSI
    stoch = stoch_rsi(close, 14, 5, 5)
    if stoch is not None and not stoch.empty:
        k_val = stoch["k"].iloc[-1] if "k" in stoch.columns else (stoch["%K"].iloc[-1] if "%K" in stoch.columns else None)
        d_val = stoch["d"].iloc[-1] if "d" in stoch.columns else (stoch["%D"].iloc[-1] if "%D" in stoch.columns else None)
        if pd.notna(k_val): ind["stoch_k"] = float(k_val)
        if pd.notna(d_val): ind["stoch_d"] = float(d_val)
        # Stoch cross state
        if "k" in stoch.columns and len(stoch) >= 2:
            k_prev = stoch["k"].iloc[-2]
            d_prev = stoch["d"].iloc[-2]
            if pd.notna(k_prev) and pd.notna(d_prev) and pd.notna(k_val) and pd.notna(d_val):
                ind["stoch_cross"] = 1 if (k_val > d_val and k_prev <= d_prev) else (-1 if (k_val < d_val and k_prev >= d_prev) else 0)
    # EMAs
    for length in [9, 14, 20]:
        curr, prev = ema_pair(close, length)
        if curr is not None: ind[f"ema_{length}"] = float(curr)
        if prev is not None: ind[f"ema_{length}_prev"] = float(prev)
    # EMA distance from price
    if price > 0:
        for length in [9, 20]:
            ema_val = ind.get(f"ema_{length}")
            if ema_val and ema_val > 0:
                ind[f"ema_dist_{length}"] = ((price - ema_val) / ema_val) * 100.0
    # SMA 200
    sma200_curr, sma200_prev = sma_pair(close, 200)
    if sma200_curr: ind["sma_200"] = float(sma200_curr)
    if sma200_curr and price > 0:
        ind["pct_from_sma200"] = ((price - sma200_curr) / sma200_curr) * 100.0
        ind["above_sma200"] = 1.0 if price > sma200_curr else 0.0
    # ATR
    atr = atr_series(df, 14)
    if atr is not None and not atr.empty:
        atr_val = atr.iloc[-1]
        if pd.notna(atr_val):
            ind["atr_14"] = float(atr_val)
            if price > 0:
                ind["atr_pct"] = (float(atr_val) / price) * 100.0
    # MACD
    macd_line, signal, hist, crossover, crossunder = macd_values(close)
    if macd_line is not None: ind["macd_line"] = float(macd_line)
    if signal is not None: ind["macd_signal"] = float(signal)
    if hist is not None: ind["macd_hist"] = float(hist)
    ind["macd_crossover"] = 1.0 if crossover else 0.0
    ind["macd_crossunder"] = 1.0 if crossunder else 0.0
    # ADX
    adx = adx_value(df, 14)
    if adx is not None: ind["adx_14"] = float(adx)
    # Bollinger Bands
    bb_upper, bb_lower, bb_pct_b = bb_features(close, 20, 2.0)
    if bb_pct_b is not None: ind["bb_pct_b"] = float(bb_pct_b)
    if bb_upper is not None and bb_lower is not None and price > 0:
        ind["bb_width"] = ((bb_upper - bb_lower) / price) * 100.0
    # Donchian
    dc_high, dc_low, dc_basis = donchian(high, low, 20)
    if dc_high is not None and dc_low is not None and price > 0:
        ind["dc_width"] = ((dc_high - dc_low) / price) * 100.0
        dc_range = dc_high - dc_low
        if dc_range > 0:
            ind["dc_position"] = (price - dc_low) / dc_range
    # Heikin Ashi
    ha_streak = ha_streak_count(df)
    if ha_streak is not None: ind["ha_streak"] = float(ha_streak)
    ha_color, _ = heikin_ashi(df)
    ind["ha_color"] = 1.0 if ha_color == "green" else -1.0
    # Choppiness
    chop = choppiness_index(df, 14)
    if chop is not None: ind["choppiness"] = float(chop)
    # Relative Volume
    rvol = relative_volume(df, 20)
    if rvol is not None: ind["rvol"] = float(rvol)
    # MFI
    mfi = mfi_value(df)
    if mfi is not None: ind["mfi"] = float(mfi)
    # EMA spread
    ema9 = ind.get("ema_9")
    ema20 = ind.get("ema_20")
    if ema9 and ema20 and price > 0:
        ind["ema_9_20_spread"] = ((ema9 - ema20) / price) * 100.0
    return ind


def compute_indicators_multi_tf(symbol: str, timestamp: pd.Timestamp, price: float) -> Dict[str, float]:
    """Compute indicators across 1h and 4h timeframes at given timestamp."""
    result = {}
    for tf in ["1h", "4h"]:
        df = get_klines_slice(symbol, timestamp, tf)
        if df is None:
            continue
        suffix = f"_{tf}" if tf != "1h" else ""
        indicators = compute_indicators(df, price)
        for k, v in indicators.items():
            result[f"{k}{suffix}"] = v
    return result


def compute_full_indicator_series(symbol: str, tf: str) -> Optional[Tuple[pd.DatetimeIndex, Dict[str, np.ndarray]]]:
    """Compute full indicator arrays for a symbol/tf. Returns (timestamps, {name: array}). Cached."""
    cache_key = f"{symbol}_{tf}"
    if cache_key in SERIES_CACHE:
        return SERIES_CACHE[cache_key]
    df = load_klines(symbol, tf)
    if df is None or len(df) < MIN_KLINE_BARS:
        return None
    timestamps = df["timestamp"]
    close = df["close"]
    high = df["high"]
    low = df["low"]
    n = len(df)
    arrays = {}
    # RSI 14
    rsi = rsi_series(close, 14)
    if rsi is not None and len(rsi) == n:
        arrays["rsi_14"] = rsi.values.astype(np.float64)
    # Stochastic
    stoch = stoch_rsi(close, 14, 5, 5)
    if stoch is not None and not stoch.empty:
        k_col = "k" if "k" in stoch.columns else ("%K" if "%K" in stoch.columns else None)
        d_col = "d" if "d" in stoch.columns else ("%D" if "%D" in stoch.columns else None)
        if k_col and len(stoch) == n:
            arrays["stoch_k"] = stoch[k_col].values.astype(np.float64)
            if d_col:
                arrays["stoch_d"] = stoch[d_col].values.astype(np.float64)
    # MFI (rolling)
    tp = (high + low + close) / 3.0
    raw_mf = tp * df["volume"]
    pos_mf = raw_mf.where(tp.diff() > 0, 0)
    neg_mf = raw_mf.where(tp.diff() < 0, 0)
    pos_sum = pos_mf.rolling(14, min_periods=1).sum()
    neg_sum = neg_mf.rolling(14, min_periods=1).sum()
    mfi_arr = 100.0 - (100.0 / (1.0 + pos_sum / neg_sum.replace(0, 1e-10)))
    arrays["mfi"] = mfi_arr.values.astype(np.float64)
    # ATR
    atr = atr_series(df, 14)
    if atr is not None and len(atr) == n:
        arrays["atr_14"] = atr.values.astype(np.float64)
        arrays["atr_pct"] = (atr / close.replace(0, 1e-10) * 100.0).values.astype(np.float64)
    # MACD (full series)
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    macd_sig = macd_line.ewm(span=9, adjust=False).mean()
    macd_h = macd_line - macd_sig
    arrays["macd_hist"] = macd_h.values.astype(np.float64)
    arrays["macd_line"] = macd_line.values.astype(np.float64)
    # ADX (vectorized via ta-lib style DI+/DI-/ADX)
    try:
        plus_dm = high.diff().clip(lower=0)
        minus_dm = (-low.diff()).clip(lower=0)
        # Zero out where the other is larger
        plus_dm[plus_dm < minus_dm] = 0
        minus_dm[minus_dm < plus_dm] = 0
        tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
        atr14_adx = tr.ewm(span=14, adjust=False).mean()
        plus_di = 100 * plus_dm.ewm(span=14, adjust=False).mean() / atr14_adx.replace(0, 1e-10)
        minus_di = 100 * minus_dm.ewm(span=14, adjust=False).mean() / atr14_adx.replace(0, 1e-10)
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1e-10)
        adx_arr = dx.ewm(span=14, adjust=False).mean()
        arrays["adx_14"] = adx_arr.values.astype(np.float64)
    except Exception:
        pass
    # Bollinger %B
    sma20 = close.rolling(20, min_periods=1).mean()
    std20 = close.rolling(20, min_periods=1).std()
    bb_up = sma20 + 2 * std20
    bb_lo = sma20 - 2 * std20
    bb_range = bb_up - bb_lo
    bb_pctb = ((close - bb_lo) / bb_range.replace(0, 1e-10)).clip(-0.5, 1.5)
    arrays["bb_pct_b"] = bb_pctb.values.astype(np.float64)
    arrays["bb_width"] = (bb_range / close.replace(0, 1e-10) * 100).values.astype(np.float64)
    # Choppiness
    atr_1 = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
    atr_sum = atr_1.rolling(14, min_periods=1).sum()
    hh = high.rolling(14, min_periods=1).max()
    ll = low.rolling(14, min_periods=1).min()
    chop_arr = 100.0 * np.log10(atr_sum / (hh - ll).replace(0, 1e-10)) / np.log10(14)
    arrays["choppiness"] = chop_arr.values.astype(np.float64)
    # Donchian position
    dc_h = high.rolling(20, min_periods=1).max()
    dc_l = low.rolling(20, min_periods=1).min()
    dc_range = dc_h - dc_l
    dc_pos = ((close - dc_l) / dc_range.replace(0, 1e-10)).clip(0, 1)
    arrays["dc_position"] = dc_pos.values.astype(np.float64)
    dc_width = (dc_range / close.replace(0, 1e-10) * 100)
    arrays["dc_width"] = dc_width.values.astype(np.float64)
    # HA streak (simplified)
    ha_o = close.copy()
    ha_c = (df["open"] + high + low + close) / 4.0
    ha_green = (ha_c > ha_o).astype(int)
    arrays["ha_color"] = np.where(ha_green, 1.0, -1.0)
    # Relative volume
    vol_sma = df["volume"].rolling(20, min_periods=1).mean()
    rvol_arr = df["volume"] / vol_sma.replace(0, 1e-10)
    arrays["rvol"] = rvol_arr.values.astype(np.float64)
    # EMA distances
    for length in [9, 20]:
        ema = close.ewm(span=length, adjust=False).mean()
        dist = ((close - ema) / ema.replace(0, 1e-10)) * 100.0
        arrays[f"ema_dist_{length}"] = dist.values.astype(np.float64)
    # EMA 9-20 spread
    ema9 = close.ewm(span=9, adjust=False).mean()
    ema20 = close.ewm(span=20, adjust=False).mean()
    arrays["ema_9_20_spread"] = ((ema9 - ema20) / close.replace(0, 1e-10) * 100).values.astype(np.float64)
    # SMA200 distance
    sma200 = close.rolling(200, min_periods=1).mean()
    arrays["pct_from_sma200"] = ((close - sma200) / sma200.replace(0, 1e-10) * 100).values.astype(np.float64)
    result = (timestamps, arrays)
    SERIES_CACHE[cache_key] = result
    return result


# ═══════════════════════════════════════════════════════════════════
# SECTION 3 — REVERSE ENGINEERING CORE
# ═══════════════════════════════════════════════════════════════════

def find_entry_bar_indices(timestamps: pd.Series, entry_times: List[pd.Timestamp], tf: str) -> List[int]:
    """Find the kline bar index closest to each entry timestamp."""
    indices = []
    ts_np = timestamps.values.astype("datetime64[ns]")
    for et in entry_times:
        et_np = np.datetime64(et)
        # Find the last bar that starts before or at entry time
        mask = ts_np <= et_np
        if mask.any():
            idx = np.where(mask)[0][-1]
            indices.append(idx)
    return indices


def compute_rule_bounds(values: np.ndarray, entry_indices: List[int], all_valid_mask: np.ndarray) -> Optional[Dict]:
    """Find the tightest [min, max] that covers 100% of entries, then compute selectivity."""
    entry_vals = values[entry_indices]
    entry_vals = entry_vals[~np.isnan(entry_vals)]
    if len(entry_vals) < 3:
        return None
    lo = float(np.min(entry_vals))
    hi = float(np.max(entry_vals))
    # How many of ALL valid bars fall within [lo, hi]?
    all_vals = values[all_valid_mask]
    all_vals = all_vals[~np.isnan(all_vals)]
    if len(all_vals) == 0:
        return None
    fires_count = int(np.sum((all_vals >= lo) & (all_vals <= hi)))
    selectivity = fires_count / len(all_vals)  # lower = more discriminative
    return {
        "lo": lo,
        "hi": hi,
        "entry_count": len(entry_vals),
        "fires_on": fires_count,
        "total_bars": len(all_vals),
        "selectivity": selectivity,  # 0.0 = perfect, 1.0 = fires on everything
        "mean": float(np.mean(entry_vals)),
        "std": float(np.std(entry_vals)),
    }


def try_split_rules(values: np.ndarray, entry_indices: List[int], all_valid_mask: np.ndarray, indicator_name: str) -> List[Dict]:
    """Try both < threshold and > threshold rules across percentile bins."""
    rules = []
    entry_vals = values[entry_indices]
    entry_vals = entry_vals[~np.isnan(entry_vals)]
    if len(entry_vals) < 3:
        return rules
    all_vals = values[all_valid_mask]
    all_vals = all_vals[~np.isnan(all_vals)]
    if len(all_vals) < 50:
        return rules
    # Generate candidate thresholds from percentiles of ALL bars
    percentiles = np.linspace(5, 95, SELECTIVITY_PERCENTILE_BINS)
    thresholds = np.percentile(all_vals, percentiles)
    # Try "indicator > threshold" rules
    for thr in thresholds:
        recall = np.sum(entry_vals > thr) / len(entry_vals)
        if recall >= 1.0:
            fires = np.sum(all_vals > thr) / len(all_vals)
            rules.append({
                "indicator": indicator_name,
                "op": ">",
                "threshold": float(thr),
                "recall": 1.0,
                "selectivity": float(fires),
                "fires_on": int(np.sum(all_vals > thr)),
            })
    # Try "indicator < threshold" rules
    for thr in thresholds:
        recall = np.sum(entry_vals < thr) / len(entry_vals)
        if recall >= 1.0:
            fires = np.sum(all_vals < thr) / len(all_vals)
            rules.append({
                "indicator": indicator_name,
                "op": "<",
                "threshold": float(thr),
                "recall": 1.0,
                "selectivity": float(fires),
                "fires_on": int(np.sum(all_vals < thr)),
            })
    # Try "lo < indicator < hi" (band rule)
    bounds = compute_rule_bounds(values, entry_indices, all_valid_mask)
    if bounds and bounds["selectivity"] < 0.95:
        rules.append({
            "indicator": indicator_name,
            "op": "band",
            "lo": bounds["lo"],
            "hi": bounds["hi"],
            "recall": 1.0,
            "selectivity": bounds["selectivity"],
            "fires_on": bounds["fires_on"],
        })
    return rules


def reverse_engineer_trader(
    trader_id: str,
    trades: List[Dict],
    side_filter: str = "LONG",
) -> Optional[Dict]:
    """
    Core algorithm: find indicator rules that fire on 100% of a trader's entries.

    Returns strategy dict with rules, selectivity scores, and combined fire rate.
    """
    # Filter trades by side
    side_trades = [t for t in trades if t["side"] == side_filter]
    if len(side_trades) < MIN_TRADES_PER_TRADER:
        return None
    # Group trades by symbol
    by_symbol = defaultdict(list)
    for t in side_trades:
        by_symbol[t["symbol"]].append(t)
    # For each symbol: compute indicator arrays + find entry bar indices
    all_rules_by_indicator = defaultdict(list)
    total_entries = 0
    symbol_results = {}
    for symbol, sym_trades in by_symbol.items():
        if len(sym_trades) < 3:
            continue  # Need at least 3 entries per symbol to avoid overfitting
        kline_sym = find_kline_symbol(symbol)
        if not kline_sym:
            continue
        for tf in ["1h", "4h"]:
            result = compute_full_indicator_series(kline_sym, tf)
            if result is None:
                continue
            timestamps, arrays = result
            suffix = f"_{tf}" if tf != "1h" else ""
            # Find entry bar indices
            entry_times = [t["entry_time"] for t in sym_trades]
            entry_indices = find_entry_bar_indices(timestamps, entry_times, tf)
            if len(entry_indices) < 2:
                continue
            valid_mask = np.ones(len(timestamps), dtype=bool)
            valid_mask[:MIN_KLINE_BARS] = False  # Skip warmup bars
            # For each indicator array, find rules
            for ind_name, ind_array in arrays.items():
                full_name = f"{ind_name}{suffix}"
                rules = try_split_rules(ind_array, entry_indices, valid_mask, full_name)
                all_rules_by_indicator[full_name].extend(rules)
            total_entries += len(entry_indices)
            symbol_results[f"{symbol}_{tf}"] = {
                "entries": len(entry_indices),
                "total_bars": int(valid_mask.sum()),
            }
    if total_entries < MIN_TRADES_PER_TRADER:
        return None
    # For each indicator, pick the BEST rule (lowest selectivity with 100% recall)
    # FILTER: reject single-point bands (overfit to 1 bar)
    best_rules = []
    for ind_name, rules in all_rules_by_indicator.items():
        if not rules:
            continue
        # Remove single-point overfits: band with hi-lo < 0.1% of midpoint
        filtered = []
        for r in rules:
            if r["op"] == "band":
                mid = (r["lo"] + r["hi"]) / 2 if (r["lo"] + r["hi"]) != 0 else 1
                width = abs(r["hi"] - r["lo"])
                if mid != 0 and (width / abs(mid)) < 0.005:
                    continue  # Skip single-point overfit
            filtered.append(r)
        if not filtered:
            continue
        filtered.sort(key=lambda r: r["selectivity"])
        best = filtered[0]
        if best["selectivity"] < 0.9:  # Only keep discriminative rules
            best_rules.append(best)
    # Sort all rules by selectivity
    best_rules.sort(key=lambda r: r["selectivity"])
    # Build combined strategy: greedily add rules that reduce false-positive rate
    strategy_rules = []
    if not best_rules:
        return None
    # Take top-N most selective rules
    for rule in best_rules[:30]:
        strategy_rules.append(rule)
    # Estimate combined selectivity (product of individual selectivities, approximate)
    combined_selectivity = 1.0
    for r in strategy_rules[:10]:
        combined_selectivity *= r["selectivity"]
    return {
        "trader_id": trader_id,
        "side": side_filter,
        "total_trades": len(side_trades),
        "matched_entries": total_entries,
        "symbols": list(by_symbol.keys()),
        "n_symbols": len(by_symbol),
        "rules": strategy_rules,
        "n_rules": len(strategy_rules),
        "best_single_selectivity": strategy_rules[0]["selectivity"] if strategy_rules else 1.0,
        "top5_combined_selectivity": combined_selectivity,
        "symbol_details": symbol_results,
    }


# ═══════════════════════════════════════════════════════════════════
# SECTION 4 — CROSS-SYMBOL VALIDATION
# ═══════════════════════════════════════════════════════════════════

def validate_strategy(strategy: Dict, trades: List[Dict]) -> Dict:
    """
    Validate: apply the top rules to ALL bars per symbol and count TP/FP/FN.
    Rules are grouped by their TF suffix — only check 1h rules against 1h arrays, 4h against 4h.
    """
    if not strategy or not strategy.get("rules"):
        return {"valid": False, "reason": "no rules"}
    side = strategy["side"]
    side_trades = [t for t in trades if t["side"] == side and t["trader_id"] == strategy["trader_id"]]
    # Group rules by TF
    rules_by_tf = {"1h": [], "4h": []}
    for r in strategy["rules"][:15]:
        ind = r["indicator"]
        if ind.endswith("_4h"):
            rules_by_tf["4h"].append((ind.replace("_4h", ""), r))
        else:
            rules_by_tf["1h"].append((ind, r))
    tp = 0
    fp = 0
    fn = 0
    total_bars = 0
    for symbol in strategy["symbols"]:
        kline_sym = find_kline_symbol(symbol)
        if not kline_sym:
            continue
        sym_trades = [t for t in side_trades if t["symbol"] == symbol]
        entry_times = [t["entry_time"] for t in sym_trades]
        # Use 1h as primary validation TF
        tf = "1h"
        result = compute_full_indicator_series(kline_sym, tf)
        if result is None:
            continue
        timestamps, arrays = result
        entry_idx_set = set(find_entry_bar_indices(timestamps, entry_times, tf))
        n = len(timestamps)
        tf_rules = rules_by_tf.get(tf, [])
        if not tf_rules:
            continue
        for i in range(MIN_KLINE_BARS, n):
            all_fire = True
            for base_key, rule in tf_rules:
                if base_key not in arrays:
                    all_fire = False
                    break
                val = arrays[base_key][i]
                if np.isnan(val):
                    all_fire = False
                    break
                if rule["op"] == ">" and val <= rule["threshold"]:
                    all_fire = False; break
                elif rule["op"] == "<" and val >= rule["threshold"]:
                    all_fire = False; break
                elif rule["op"] == "band" and (val < rule["lo"] or val > rule["hi"]):
                    all_fire = False; break
            if all_fire:
                if i in entry_idx_set:
                    tp += 1
                else:
                    fp += 1
            elif i in entry_idx_set:
                fn += 1
            total_bars += 1
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0
    return {
        "valid": True,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "total_bars": total_bars,
        "fire_rate": (tp + fp) / total_bars if total_bars > 0 else 0,
    }


# ═══════════════════════════════════════════════════════════════════
# SECTION 5 — MAIN PIPELINE
# ═══════════════════════════════════════════════════════════════════

def run_pipeline(args):
    """Main pipeline: load trades → reverse-engineer each trader → validate → report."""
    # Load all trades
    all_trades = []
    merged_path = BASE_PATH / "data" / "trader_sweep_merged" / "all_exchanges_merged.csv"
    if merged_path.exists():
        all_trades.extend(load_merged_trades(str(merged_path)))
    okx_path = BASE_PATH / "data" / "okx_traders" / "all_trades.jsonl"
    if okx_path.exists():
        all_trades.extend(load_okx_trades(str(okx_path)))
    logger.info(f"Total trades loaded: {len(all_trades)}")
    # Group by trader
    by_trader = defaultdict(list)
    for t in all_trades:
        by_trader[t["trader_id"]].append(t)
    # Filter: only traders with enough trades
    eligible = {tid: trades for tid, trades in by_trader.items() if len(trades) >= MIN_TRADES_PER_TRADER}
    logger.info(f"Eligible traders (>={MIN_TRADES_PER_TRADER} trades): {len(eligible)}")
    # Load trader health scores if available
    health = {}
    health_path = BASE_PATH / "data" / "trader_analysis" / "trader_health.json"
    if health_path.exists():
        with open(health_path) as f:
            health = json.load(f)
    # Reverse-engineer each trader
    strategies = []
    for i, (trader_id, trades) in enumerate(sorted(eligible.items(), key=lambda x: -len(x[1]))):
        # Determine which sides to test
        sides_present = set(t["side"] for t in trades)
        trader_health = health.get(trader_id, {})
        logger.info(f"[{i+1}/{len(eligible)}] Trader {trader_id[:16]}... ({len(trades)} trades, sides={sides_present}, score={trader_health.get('score', '?')})")
        for side in ["LONG", "SHORT"]:
            if side not in sides_present:
                continue
            side_count = sum(1 for t in trades if t["side"] == side)
            if side_count < MIN_TRADES_PER_TRADER:
                continue
            strategy = reverse_engineer_trader(trader_id, trades, side_filter=side)
            if strategy:
                # Validate
                validation = validate_strategy(strategy, trades)
                strategy["validation"] = validation
                strategy["trader_health_score"] = trader_health.get("score", None)
                strategy["trader_health_status"] = trader_health.get("status", None)
                strategies.append(strategy)
                n_rules = strategy["n_rules"]
                best_sel = strategy["best_single_selectivity"]
                prec = validation.get("precision", 0)
                rec = validation.get("recall", 0)
                logger.info(f"  {side}: {n_rules} rules, best_sel={best_sel:.3f}, precision={prec:.3f}, recall={rec:.3f}")
    # Sort by combined selectivity (best strategies first)
    strategies.sort(key=lambda s: s.get("top5_combined_selectivity", 1.0))
    # Save results
    output_path = OUTPUT_DIR / "strategies.json"
    with open(output_path, "w") as f:
        json.dump(strategies, f, indent=2, default=str)
    logger.info(f"Saved {len(strategies)} strategies to {output_path}")
    # Generate summary report
    report_path = OUTPUT_DIR / "REVERSE_ENGINEER_REPORT.md"
    with open(report_path, "w") as f:
        f.write(f"# Reverse-Engineered Trader Strategies — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n\n")
        f.write(f"**Traders analyzed**: {len(eligible)}\n")
        f.write(f"**Strategies extracted**: {len(strategies)}\n")
        f.write(f"**Total trades processed**: {len(all_trades)}\n\n")
        f.write("## Top Strategies by Selectivity\n\n")
        f.write("| # | Trader | Side | Trades | Rules | Best Selectivity | Precision | Recall | Fire Rate |\n")
        f.write("|---|--------|------|-------:|------:|-----------------:|----------:|-------:|----------:|\n")
        for i, s in enumerate(strategies[:50]):
            v = s.get("validation", {})
            f.write(f"| {i+1} | {s['trader_id'][:12]}... | {s['side']} | {s['total_trades']} | {s['n_rules']} | {s['best_single_selectivity']:.3f} | {v.get('precision', 0):.3f} | {v.get('recall', 0):.3f} | {v.get('fire_rate', 0):.4f} |\n")
        # Detail top strategies
        f.write("\n\n## Detailed Strategy Rules (Top 20)\n\n")
        for i, s in enumerate(strategies[:20]):
            v = s.get("validation", {})
            f.write(f"### #{i+1} — Trader `{s['trader_id'][:16]}` ({s['side']})\n\n")
            f.write(f"- **Trades**: {s['total_trades']} | **Matched entries**: {s['matched_entries']} | **Symbols**: {s['n_symbols']}\n")
            f.write(f"- **Health score**: {s.get('trader_health_score', '?')} ({s.get('trader_health_status', '?')})\n")
            f.write(f"- **Validation**: TP={v.get('tp',0)} FP={v.get('fp',0)} FN={v.get('fn',0)} | Precision={v.get('precision',0):.3f} Recall={v.get('recall',0):.3f}\n")
            f.write(f"- **Fire rate**: {v.get('fire_rate', 0):.4f} ({v.get('fire_rate', 0)*100:.2f}% of bars)\n\n")
            f.write("| Indicator | Rule | Threshold | Selectivity |\n")
            f.write("|-----------|------|----------:|------------:|\n")
            for r in s["rules"][:15]:
                if r["op"] == "band":
                    f.write(f"| {r['indicator']} | {r['lo']:.4f} < x < {r['hi']:.4f} | — | {r['selectivity']:.3f} |\n")
                else:
                    f.write(f"| {r['indicator']} | x {r['op']} {r['threshold']:.4f} | {r['threshold']:.4f} | {r['selectivity']:.3f} |\n")
            f.write("\n")
    logger.info(f"Report saved to {report_path}")
    # Print summary
    print(f"\n{'='*70}")
    print(f"REVERSE ENGINEERING COMPLETE")
    print(f"{'='*70}")
    print(f"Traders analyzed: {len(eligible)}")
    print(f"Strategies extracted: {len(strategies)}")
    good = [s for s in strategies if s.get("validation", {}).get("precision", 0) > 0.1]
    print(f"Strategies with >10% precision: {len(good)}")
    excellent = [s for s in strategies if s.get("validation", {}).get("precision", 0) > 0.5]
    print(f"Strategies with >50% precision: {len(excellent)}")
    if strategies:
        best = strategies[0]
        v = best.get("validation", {})
        print(f"\nBest strategy: {best['trader_id'][:16]}... {best['side']}")
        print(f"  Rules: {best['n_rules']}, Selectivity: {best['best_single_selectivity']:.4f}")
        print(f"  Precision: {v.get('precision', 0):.3f}, Recall: {v.get('recall', 0):.3f}")
        print(f"  Top 5 rules:")
        for r in best["rules"][:5]:
            if r["op"] == "band":
                print(f"    {r['indicator']}: {r['lo']:.4f} < x < {r['hi']:.4f} (sel={r['selectivity']:.3f})")
            else:
                print(f"    {r['indicator']}: x {r['op']} {r['threshold']:.4f} (sel={r['selectivity']:.3f})")
    print(f"\nFull report: {report_path}")
    print(f"Strategies JSON: {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reverse-engineer trader strategies from copy trade data")
    parser.add_argument("--min-trades", type=int, default=5, help="Min trades per trader per side")
    parser.add_argument("--trader", type=str, default=None, help="Specific trader ID to analyze")
    args = parser.parse_args()
    MIN_TRADES_PER_TRADER = args.min_trades
    run_pipeline(args)
