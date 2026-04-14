#!/usr/bin/env python3
"""
Adaptive Parameter Optimizer — per-symbol optimal configs for live trading.

Re-optimizes every 4 hours on the last 7 days of 1h klines, weighting recent
hours exponentially more (24h half-life). Outputs per-symbol optimal indicator
thresholds for SHORT and LONG entries.

Outputs:
    data/adaptive/current_config.json     — latest per-symbol configs
    data/adaptive/history/config_*.json   — historical snapshots
    data/adaptive/tradeable_short.json    — symbols with Sharpe > 2.0 SHORT
    data/adaptive/tradeable_long.json     — symbols with Sharpe > 2.0 LONG

Usage:
    python3 adaptive_optimizer.py              # Single optimization run
    python3 adaptive_optimizer.py --daemon     # Run every 4h continuously
    python3 adaptive_optimizer.py --status     # Show current status
"""
import argparse
import json
import logging
import os
import signal
import sys
import time
import warnings
from datetime import datetime, timezone
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import Config

config = Config()
BASE_PATH = config.BASE_PATH
KLINES_DIR = config.KLINES_CACHE_DIR

DATA_DIR = BASE_PATH / "data" / "adaptive"
HISTORY_DIR = DATA_DIR / "history"
DATA_DIR.mkdir(parents=True, exist_ok=True)
HISTORY_DIR.mkdir(parents=True, exist_ok=True)

CURRENT_CONFIG = DATA_DIR / "current_config.json"
SHORT_LIST = DATA_DIR / "tradeable_short.json"
LONG_LIST = DATA_DIR / "tradeable_long.json"

LOG_DIR = BASE_PATH / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

def _setup_logging(daemon_mode=False):
    """Setup logging. In daemon mode, only StreamHandler (LaunchAgent captures stdout).
    In single-run mode, both file and stream."""
    global logger
    logger = logging.getLogger("adaptive_optimizer")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s [ADAPTIVE] %(message)s")
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    if not daemon_mode:
        fh = logging.FileHandler(LOG_DIR / "adaptive_optimizer.log")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    logger.propagate = False

logger = logging.getLogger("adaptive_optimizer")

# ═══════════════════════════════════════════════════════════════
# SWEEP GRID
# ═══════════════════════════════════════════════════════════════
RSI_GRID = np.array([35, 40, 45, 50, 55, 60, 65], dtype=np.float64)
STOCH_GRID = np.array([25, 35, 45, 55, 65, 75], dtype=np.float64)
MFI_GRID = np.array([35, 40, 45, 50, 55, 60, 65], dtype=np.float64)
ADX_GRID = np.array([15, 20, 25, 30, 35], dtype=np.float64)
BB_GRID = np.array([0.3, 0.4, 0.5, 0.6, 0.7], dtype=np.float64)
HA_MODES = ["required_red", "required_green", "any"]

LOOKBACK_HOURS = 168  # 7 days
DECAY_HALFLIFE_HOURS = 24
MIN_SIGNALS = 5

# Pre-build non-HA combos (7*6*7*5*5 = 7350 combos)
_RSI_IDX, _STOCH_IDX, _MFI_IDX, _ADX_IDX, _BB_IDX = np.meshgrid(
    np.arange(len(RSI_GRID)),
    np.arange(len(STOCH_GRID)),
    np.arange(len(MFI_GRID)),
    np.arange(len(ADX_GRID)),
    np.arange(len(BB_GRID)),
    indexing="ij",
)
COMBO_RSI = RSI_GRID[_RSI_IDX.ravel()]
COMBO_STOCH = STOCH_GRID[_STOCH_IDX.ravel()]
COMBO_MFI = MFI_GRID[_MFI_IDX.ravel()]
COMBO_ADX = ADX_GRID[_ADX_IDX.ravel()]
COMBO_BB = BB_GRID[_BB_IDX.ravel()]
NUM_COMBOS = len(COMBO_RSI)  # 7350


def compute_indicators(close, high, low, volume):
    """Vectorized indicator computation for full 1h array. Returns dict of arrays."""
    n = len(close)
    # RSI 14
    delta = np.diff(close, prepend=close[0])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    alpha = 2.0 / 15.0
    avg_gain = np.zeros(n)
    avg_loss = np.zeros(n)
    avg_gain[0] = gain[0]
    avg_loss[0] = loss[0]
    for i in range(1, n):
        avg_gain[i] = alpha * gain[i] + (1 - alpha) * avg_gain[i - 1]
        avg_loss[i] = alpha * loss[i] + (1 - alpha) * avg_loss[i - 1]
    rsi = 100.0 - 100.0 / (1.0 + avg_gain / np.maximum(avg_loss, 1e-10))
    # Stochastic K (14-period)
    k_period = 14
    stoch_k = np.full(n, 50.0)
    for i in range(k_period - 1, n):
        hh = np.max(high[i - k_period + 1 : i + 1])
        ll = np.min(low[i - k_period + 1 : i + 1])
        rng = hh - ll
        if rng > 0:
            stoch_k[i] = (close[i] - ll) / rng * 100.0
    # Smooth K with 3-period SMA
    stoch_k_smooth = np.convolve(stoch_k, np.ones(3) / 3.0, mode="same")
    # MFI 14
    tp = (high + low + close) / 3.0
    raw_mf = tp * volume
    tp_delta = np.diff(tp, prepend=tp[0])
    pos_mf = np.where(tp_delta > 0, raw_mf, 0.0)
    neg_mf = np.where(tp_delta < 0, raw_mf, 0.0)
    pos_sum = pd.Series(pos_mf).rolling(14, min_periods=1).sum().values
    neg_sum = pd.Series(neg_mf).rolling(14, min_periods=1).sum().values
    mfi = 100.0 - 100.0 / (1.0 + pos_sum / np.maximum(neg_sum, 1e-10))
    # ADX (simplified)
    pdm = np.diff(high, prepend=high[0])
    mdm = -np.diff(low, prepend=low[0])
    pdm_clean = np.where((pdm > 0) & (pdm > mdm), pdm, 0.0)
    mdm_clean = np.where((mdm > 0) & (mdm > pdm), mdm, 0.0)
    prev_close = np.roll(close, 1)
    prev_close[0] = close[0]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
    atr14 = pd.Series(tr).ewm(span=14, adjust=False).mean().values
    safe_atr = np.maximum(atr14, 1e-10)
    pdi = 100.0 * pd.Series(pdm_clean).ewm(span=14, adjust=False).mean().values / safe_atr
    mdi = 100.0 * pd.Series(mdm_clean).ewm(span=14, adjust=False).mean().values / safe_atr
    dx = 100.0 * np.abs(pdi - mdi) / np.maximum(pdi + mdi, 1e-10)
    adx = pd.Series(dx).ewm(span=14, adjust=False).mean().values
    # Bollinger %B
    sma20 = pd.Series(close).rolling(20, min_periods=1).mean().values
    std20 = pd.Series(close).rolling(20, min_periods=1).std(ddof=0).values
    bb_upper = sma20 + 2.0 * std20
    bb_lower = sma20 - 2.0 * std20
    bb_range = np.maximum(bb_upper - bb_lower, 1e-10)
    bb_pctb = (close - bb_lower) / bb_range
    # Heikin-Ashi color
    ha_close = (close + high + low + close) / 4.0  # simplified: using OHLC with open~close
    ha_open = np.zeros(n)
    ha_open[0] = close[0]
    for i in range(1, n):
        ha_open[i] = (ha_open[i - 1] + ha_close[i - 1]) / 2.0
    ha_green = ha_close > ha_open  # True=green, False=red
    return {
        "rsi": rsi,
        "stoch_k": stoch_k_smooth,
        "mfi": mfi,
        "adx": adx,
        "bb_pctb": bb_pctb,
        "ha_green": ha_green,
    }


def _build_config_dict(side, sharpe, wr, avg_ret, count, rsi_val, stoch_val, mfi_val, adx_val, bb_val, ha_mode):
    """Build the config dict for a given combo."""
    ha_str = ha_mode.replace("required_", "")
    base = {
        "sharpe": round(float(sharpe), 3),
        "wr": round(float(wr), 1),
        "avg_ret": round(float(avg_ret), 4),
        "signals": int(count),
        "adx_min": float(adx_val),
        "ha": ha_str,
    }
    if side == "short":
        base["rsi_min"] = float(rsi_val)
        base["stoch_min"] = float(stoch_val)
        base["mfi_min"] = float(mfi_val)
        base["bb_min"] = float(bb_val)
    else:
        base["rsi_max"] = float(rsi_val)
        base["stoch_max"] = float(stoch_val)
        base["mfi_max"] = float(mfi_val)
        base["bb_max"] = float(bb_val)
    return base


def optimize_symbol(symbol, close, high, low, volume, timestamps, now_utc):
    """Sweep all combos for one symbol. Returns dict with short/long optimal configs.
    Uses vectorized inner loops: for each HA+RSI+Stoch combo, evaluates all
    MFI x ADX x BB combos (7*5*5=175) via 2D boolean broadcasting."""
    n = len(close)
    if n < 200:
        return None
    indicators = compute_indicators(close, high, low, volume)
    # Time-based window: last 7 days
    hours_ago = np.array([(now_utc - t).total_seconds() / 3600.0 for t in timestamps])
    in_window = hours_ago <= LOOKBACK_HOURS
    # Exponential decay weights (24h half-life)
    decay_rate = np.log(2) / DECAY_HALFLIFE_HOURS
    weights = np.exp(-decay_rate * hours_ago)
    weights[~in_window] = 0.0
    # Forward 1h return (shift by -1)
    fwd_ret = np.zeros(n)
    if n > 1:
        fwd_ret[:-1] = (close[1:] - close[:-1]) / np.maximum(close[:-1], 1e-10)
    fwd_ret[-1] = 0.0
    # Extract windowed data
    rsi = indicators["rsi"]
    stoch_k = indicators["stoch_k"]
    mfi = indicators["mfi"]
    adx = indicators["adx"]
    bb_pctb = indicators["bb_pctb"]
    ha_green = indicators["ha_green"]
    valid = in_window.copy()
    valid[-1] = False
    valid_idx = np.where(valid)[0]
    if len(valid_idx) < MIN_SIGNALS:
        return None
    v_rsi = rsi[valid_idx]
    v_stoch = stoch_k[valid_idx]
    v_mfi = mfi[valid_idx]
    v_adx = adx[valid_idx]
    v_bb = bb_pctb[valid_idx]
    v_ha = ha_green[valid_idx]
    v_weights = weights[valid_idx]
    v_fwd = fwd_ret[valid_idx]
    nb = len(valid_idx)  # number of bars
    # Pre-build MFI/ADX/BB threshold masks: shape (num_thresholds, num_bars)
    # We'll flip direction per side inside the loop
    results = {}
    for side in ("short", "long"):
        best_sharpe = -999.0
        best_config = None
        if side == "short":
            directional_ret = -v_fwd
        else:
            directional_ret = v_fwd
        # Pre-compute per-threshold masks for this side
        # MFI masks: (len(MFI_GRID), nb)
        if side == "short":
            mfi_masks = v_mfi[np.newaxis, :] >= MFI_GRID[:, np.newaxis]
            bb_masks = v_bb[np.newaxis, :] >= BB_GRID[:, np.newaxis]
        else:
            mfi_masks = v_mfi[np.newaxis, :] <= MFI_GRID[:, np.newaxis]
            bb_masks = v_bb[np.newaxis, :] <= BB_GRID[:, np.newaxis]
        adx_masks = v_adx[np.newaxis, :] >= ADX_GRID[:, np.newaxis]
        # Pre-compute weighted return and positive-return arrays
        wr_arr = directional_ret * v_weights  # element-wise: ret * weight
        pos_wr_arr = (directional_ret > 0).astype(np.float64) * v_weights
        for ha_mode_idx, ha_mode in enumerate(HA_MODES):
            if ha_mode == "required_red":
                ha_mask = ~v_ha
            elif ha_mode == "required_green":
                ha_mask = v_ha
            else:
                ha_mask = np.ones(nb, dtype=bool)
            ha_count = np.sum(ha_mask)
            if ha_count < MIN_SIGNALS:
                continue
            for ri, rsi_val in enumerate(RSI_GRID):
                if side == "short":
                    rsi_mask = v_rsi >= rsi_val
                else:
                    rsi_mask = v_rsi <= rsi_val
                ha_rsi = ha_mask & rsi_mask
                if np.sum(ha_rsi) < MIN_SIGNALS:
                    continue
                for si, stoch_val in enumerate(STOCH_GRID):
                    if side == "short":
                        stoch_mask = v_stoch >= stoch_val
                    else:
                        stoch_mask = v_stoch <= stoch_val
                    base_mask = ha_rsi & stoch_mask
                    base_count = np.sum(base_mask)
                    if base_count < MIN_SIGNALS:
                        continue
                    # Vectorized: evaluate all MFI x ADX x BB combos at once
                    # base_mask shape: (nb,)
                    # mfi_masks shape: (n_mfi, nb), adx_masks: (n_adx, nb), bb_masks: (n_bb, nb)
                    # Combined: base_mask AND mfi AND adx AND bb
                    # Use broadcasting: (n_mfi, 1, 1, nb) & (1, n_adx, 1, nb) & (1, 1, n_bb, nb)
                    # This gives (n_mfi, n_adx, n_bb, nb) bool array
                    bm = base_mask[np.newaxis, np.newaxis, np.newaxis, :]  # (1,1,1,nb)
                    mm = mfi_masks[:, np.newaxis, np.newaxis, :]  # (n_mfi,1,1,nb)
                    am = adx_masks[np.newaxis, :, np.newaxis, :]  # (1,n_adx,1,nb)
                    bm2 = bb_masks[np.newaxis, np.newaxis, :, :]  # (1,1,n_bb,nb)
                    # Combined signal: shape (n_mfi, n_adx, n_bb, nb)
                    signals = bm & mm & am & bm2
                    # Count signals per combo: (n_mfi, n_adx, n_bb)
                    counts = signals.sum(axis=3)
                    # Skip combos with < MIN_SIGNALS
                    enough = counts >= MIN_SIGNALS
                    if not np.any(enough):
                        continue
                    # Weighted stats: sum(ret*weight) and sum(weight) per combo
                    # signals shape: (n_mfi, n_adx, n_bb, nb), wr_arr shape: (nb,)
                    w_ret_sum = (signals * wr_arr[np.newaxis, np.newaxis, np.newaxis, :]).sum(axis=3)
                    w_sum = (signals * v_weights[np.newaxis, np.newaxis, np.newaxis, :]).sum(axis=3)
                    w_sum_safe = np.maximum(w_sum, 1e-10)
                    w_mean = w_ret_sum / w_sum_safe
                    # Weighted variance: sum(w * (r - mean)^2) / sum(w)
                    # Expand w_mean: (n_mfi, n_adx, n_bb, 1)
                    mean_exp = w_mean[:, :, :, np.newaxis]
                    dev = directional_ret[np.newaxis, np.newaxis, np.newaxis, :] - mean_exp
                    w_var = (signals * v_weights[np.newaxis, np.newaxis, np.newaxis, :] * dev ** 2).sum(axis=3) / w_sum_safe
                    w_std = np.sqrt(np.maximum(w_var, 1e-10))
                    sharpe_arr = np.where((w_std > 1e-8) & enough, w_mean / w_std, -999.0)
                    # Find best in this batch
                    best_idx_flat = np.argmax(sharpe_arr)
                    best_s = sharpe_arr.flat[best_idx_flat]
                    if best_s > best_sharpe:
                        best_sharpe = best_s
                        mi, ai, bi = np.unravel_index(best_idx_flat, sharpe_arr.shape)
                        mfi_val = MFI_GRID[mi]
                        adx_val = ADX_GRID[ai]
                        bb_val = BB_GRID[bi]
                        # Compute WR for best combo
                        sig_mask = signals[mi, ai, bi]
                        pos_w = (pos_wr_arr * sig_mask).sum()
                        wr = pos_w / w_sum_safe[mi, ai, bi] * 100.0
                        avg_ret_val = w_mean[mi, ai, bi] * 100.0
                        best_config = _build_config_dict(
                            side, best_s, wr, avg_ret_val, int(counts[mi, ai, bi]),
                            rsi_val, stoch_val, mfi_val, adx_val, bb_val, ha_mode,
                        )
        if best_config is not None:
            results[side] = best_config
        else:
            results[side] = {"sharpe": 0, "wr": 0, "avg_ret": 0, "signals": 0}
    if not results.get("short") and not results.get("long"):
        return None
    short_s = results.get("short", {}).get("sharpe", 0)
    long_s = results.get("long", {}).get("sharpe", 0)
    if short_s > long_s:
        best_side = "SHORT"
    elif long_s > short_s:
        best_side = "LONG"
    else:
        best_side = "NEUTRAL"
    return {"best_side": best_side, "short": results.get("short", {}), "long": results.get("long", {})}


def load_klines_1h(symbol):
    """Load 1h klines, return (close, high, low, volume, timestamps) numpy arrays."""
    path = KLINES_DIR / f"{symbol}_1h.json"
    if not path.exists():
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        if len(data) < 200:
            return None
        close = np.array([float(d["close"]) for d in data])
        high = np.array([float(d["high"]) for d in data])
        low = np.array([float(d["low"]) for d in data])
        volume = np.array([float(d["volume"]) for d in data])
        timestamps = [datetime.fromisoformat(d["timestamp"].replace("Z", "+00:00")) for d in data]
        return close, high, low, volume, timestamps
    except Exception as e:
        logger.warning(f"Failed loading {symbol}: {e}")
        return None


def discover_symbols():
    """Find all USDT/USDC symbols with 1h klines."""
    files = list(KLINES_DIR.glob("*USDT_1h.json")) + list(KLINES_DIR.glob("*USDC_1h.json"))
    symbols = sorted(set(f.stem.replace("_1h", "") for f in files))
    return symbols


def run_optimization():
    """Run full optimization across all symbols."""
    t0 = time.time()
    now_utc = datetime.now(timezone.utc)
    symbols = discover_symbols()
    logger.info(f"Starting optimization: {len(symbols)} symbols, lookback={LOOKBACK_HOURS}h, decay={DECAY_HALFLIFE_HOURS}h")
    all_results = {}
    short_favored = 0
    long_favored = 0
    neutral = 0
    skipped = 0
    for i, symbol in enumerate(symbols):
        data = load_klines_1h(symbol)
        if data is None:
            skipped += 1
            continue
        close, high, low, volume, timestamps = data
        result = optimize_symbol(symbol, close, high, low, volume, timestamps, now_utc)
        if result is None:
            skipped += 1
            continue
        all_results[symbol] = result
        if result["best_side"] == "SHORT":
            short_favored += 1
        elif result["best_side"] == "LONG":
            long_favored += 1
        else:
            neutral += 1
        if (i + 1) % 100 == 0:
            elapsed = time.time() - t0
            logger.info(f"Progress: {i+1}/{len(symbols)} ({elapsed:.1f}s)")
    elapsed = time.time() - t0
    ts_str = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    output = {
        "generated_utc": ts_str,
        "lookback_hours": LOOKBACK_HOURS,
        "decay_halflife_hours": DECAY_HALFLIFE_HOURS,
        "elapsed_seconds": round(elapsed, 1),
        "symbols": all_results,
        "summary": {
            "total_symbols": len(all_results),
            "short_favored": short_favored,
            "long_favored": long_favored,
            "neutral": neutral,
            "skipped": skipped,
        },
    }
    # Write current config
    with open(CURRENT_CONFIG, "w") as f:
        json.dump(output, f, indent=2)
    # Write historical snapshot
    hist_name = f"config_{now_utc.strftime('%Y%m%d_%H%M')}.json"
    with open(HISTORY_DIR / hist_name, "w") as f:
        json.dump(output, f, indent=2)
    # Write tradeable lists (Sharpe > 2.0)
    short_list = sorted(
        [s for s, r in all_results.items() if r.get("short", {}).get("sharpe", 0) > 2.0],
        key=lambda s: -all_results[s]["short"]["sharpe"],
    )
    long_list = sorted(
        [s for s, r in all_results.items() if r.get("long", {}).get("sharpe", 0) > 2.0],
        key=lambda s: -all_results[s]["long"]["sharpe"],
    )
    with open(SHORT_LIST, "w") as f:
        json.dump(short_list, f, indent=2)
    with open(LONG_LIST, "w") as f:
        json.dump(long_list, f, indent=2)
    logger.info(f"Optimization complete: {len(all_results)} symbols in {elapsed:.1f}s")
    logger.info(f"SHORT favored: {short_favored}, LONG favored: {long_favored}, NEUTRAL: {neutral}, skipped: {skipped}")
    logger.info(f"Tradeable SHORT (Sharpe>2): {len(short_list)}, Tradeable LONG (Sharpe>2): {len(long_list)}")
    logger.info(f"Output: {CURRENT_CONFIG}")
    return output


def show_status():
    """Print current optimization status."""
    print(f"\n{'='*70}")
    print(f"ADAPTIVE OPTIMIZER STATUS — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*70}")
    if not CURRENT_CONFIG.exists():
        print("No optimization results found. Run: python3 adaptive_optimizer.py")
        print()
        return
    with open(CURRENT_CONFIG) as f:
        data = json.load(f)
    summary = data.get("summary", {})
    print(f"Last optimization: {data.get('generated_utc', 'unknown')}")
    print(f"Elapsed: {data.get('elapsed_seconds', 0)}s")
    print(f"Lookback: {data.get('lookback_hours', 0)}h | Decay half-life: {data.get('decay_halflife_hours', 0)}h")
    print(f"\nSymbols: {summary.get('total_symbols', 0)} total | "
          f"SHORT: {summary.get('short_favored', 0)} | "
          f"LONG: {summary.get('long_favored', 0)} | "
          f"NEUTRAL: {summary.get('neutral', 0)} | "
          f"Skipped: {summary.get('skipped', 0)}")
    symbols = data.get("symbols", {})
    # Top 10 SHORT by Sharpe
    short_ranked = sorted(
        [(s, r["short"]["sharpe"], r["short"].get("wr", 0), r["short"].get("signals", 0))
         for s, r in symbols.items() if r.get("short", {}).get("sharpe", 0) > 0],
        key=lambda x: -x[1],
    )
    print(f"\n{'─'*70}")
    print(f"TOP 10 SHORT (by Sharpe)")
    print(f"{'Symbol':<20} {'Sharpe':>8} {'WR%':>6} {'Signals':>8}")
    print(f"{'─'*70}")
    for sym, sharpe, wr, sigs in short_ranked[:10]:
        cfg = symbols[sym]["short"]
        filters = []
        if "rsi_min" in cfg:
            filters.append(f"RSI>{cfg['rsi_min']:.0f}")
        if "stoch_min" in cfg:
            filters.append(f"SK>{cfg['stoch_min']:.0f}")
        if "mfi_min" in cfg:
            filters.append(f"MFI>{cfg['mfi_min']:.0f}")
        if "adx_min" in cfg:
            filters.append(f"ADX>{cfg['adx_min']:.0f}")
        if "bb_min" in cfg:
            filters.append(f"BB>{cfg['bb_min']:.1f}")
        if cfg.get("ha"):
            filters.append(f"HA={cfg['ha']}")
        print(f"{sym:<20} {sharpe:>8.2f} {wr:>5.1f}% {sigs:>7d}   {' '.join(filters)}")
    # Top 10 LONG by Sharpe
    long_ranked = sorted(
        [(s, r["long"]["sharpe"], r["long"].get("wr", 0), r["long"].get("signals", 0))
         for s, r in symbols.items() if r.get("long", {}).get("sharpe", 0) > 0],
        key=lambda x: -x[1],
    )
    print(f"\n{'─'*70}")
    print(f"TOP 10 LONG (by Sharpe)")
    print(f"{'Symbol':<20} {'Sharpe':>8} {'WR%':>6} {'Signals':>8}")
    print(f"{'─'*70}")
    for sym, sharpe, wr, sigs in long_ranked[:10]:
        cfg = symbols[sym]["long"]
        filters = []
        if "rsi_max" in cfg:
            filters.append(f"RSI<{cfg['rsi_max']:.0f}")
        if "stoch_max" in cfg:
            filters.append(f"SK<{cfg['stoch_max']:.0f}")
        if "mfi_max" in cfg:
            filters.append(f"MFI<{cfg['mfi_max']:.0f}")
        if "adx_min" in cfg:
            filters.append(f"ADX>{cfg['adx_min']:.0f}")
        if "bb_max" in cfg:
            filters.append(f"BB<{cfg['bb_max']:.1f}")
        if cfg.get("ha"):
            filters.append(f"HA={cfg['ha']}")
        print(f"{sym:<20} {sharpe:>8.2f} {wr:>5.1f}% {sigs:>7d}   {' '.join(filters)}")
    # Tradeable lists
    if SHORT_LIST.exists():
        with open(SHORT_LIST) as f:
            sl = json.load(f)
        print(f"\nTradeable SHORT (Sharpe>2.0): {len(sl)} symbols")
    if LONG_LIST.exists():
        with open(LONG_LIST) as f:
            ll = json.load(f)
        print(f"Tradeable LONG  (Sharpe>2.0): {len(ll)} symbols")
    # History
    history_files = sorted(HISTORY_DIR.glob("config_*.json"))
    if history_files:
        print(f"\nHistory: {len(history_files)} snapshots")
        print(f"  Oldest: {history_files[0].name}")
        print(f"  Newest: {history_files[-1].name}")
    print()


def daemon_loop():
    """Run continuously, optimizing every 4 hours at :20 past."""
    _setup_logging(daemon_mode=True)
    logger.info("Starting daemon mode — optimizing every 4h at :20 past 0/4/8/12/16/20 UTC")
    running = True

    def handle_signal(sig, frame):
        nonlocal running
        running = False
        logger.info("Shutdown signal received")

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    # Run immediately on start
    try:
        run_optimization()
    except Exception as e:
        logger.error(f"Initial optimization error: {e}", exc_info=True)
    while running:
        now = datetime.now(timezone.utc)
        # Next 4h window at :20
        target_hours = [0, 4, 8, 12, 16, 20]
        current_h = now.hour
        current_m = now.minute
        next_run = None
        for h in target_hours:
            candidate = now.replace(hour=h, minute=20, second=0, microsecond=0)
            if candidate > now:
                next_run = candidate
                break
        if next_run is None:
            # Next day first slot
            next_run = (now + pd.Timedelta(days=1)).replace(hour=0, minute=20, second=0, microsecond=0)
        wait = (next_run - now).total_seconds()
        logger.info(f"Next optimization at {next_run.strftime('%Y-%m-%d %H:%M')} UTC ({wait:.0f}s)")
        # Sleep in 1s increments for clean shutdown
        for _ in range(int(wait)):
            if not running:
                break
            time.sleep(1)
        if not running:
            break
        try:
            run_optimization()
        except Exception as e:
            logger.error(f"Optimization error: {e}", exc_info=True)
    logger.info("Daemon stopped")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Adaptive Parameter Optimizer")
    parser.add_argument("--daemon", action="store_true", help="Run continuously every 4h")
    parser.add_argument("--status", action="store_true", help="Show current status")
    args = parser.parse_args()
    if args.status:
        show_status()
    elif args.daemon:
        daemon_loop()
    else:
        _setup_logging(daemon_mode=False)
        run_optimization()
