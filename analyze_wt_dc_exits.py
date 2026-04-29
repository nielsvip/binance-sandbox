"""
WT+DC Exit Signal Analysis — VECTORIZED for speed.

Loads real NPZ indicator data for 14 symbols, simulates entries on 1h WT bull/bear crosses,
tests multiple exit strategies using numpy-vectorized score computation.
"""

import numpy as np
import sys
import os
import time
from collections import defaultdict

BASE_PATH = "/Users/niels/Documents/binance"
NPZ_DIR = os.path.join(BASE_PATH, "backtest_v8/indicators")

SYMBOLS = ["AAPL", "NVDA", "MSFT", "XOM", "GLD", "META", "CVX", "AVGO", "MU", "AMD", "AMZN", "COST", "DIS", "HD"]
TFS = ["5m", "15m", "1h", "4h", "D"]
TF_WEIGHTS = {"5m": 0.05, "15m": 0.10, "1h": 0.20, "4h": 0.30, "D": 0.35}
BARS_PER_HOUR = 12  # 5m bars
MAX_HOLD = BARS_PER_HOUR * 24 * 5  # 5 days max hold
MIN_BARS_AFTER_ENTRY = BARS_PER_HOUR * 2  # 2 hours minimum
WARMUP_BARS = 500
MIN_SPACING = BARS_PER_HOUR * 4  # 4h between entries


def load_symbol(symbol):
    path = os.path.join(NPZ_DIR, f"{symbol}.npz")
    f = np.load(path, allow_pickle=True)
    return {k: f[k].astype(np.float32) for k in f.files}


def safe_get(data, key, n):
    if key in data:
        return data[key]
    return np.zeros(n, dtype=np.float32)


def compute_vectorized_exit_scores_long(data, params=None):
    """Compute exit score for LONG positions at every bar, vectorized.
    Returns array of shape (n,) with exit scores 0-100."""
    if params is None:
        params = {"vel_w": 1.0, "div_w": 1.0, "dc_w": 1.0, "tf_w": 1.0, "comp_w": 1.0}
    n = len(data["close"])
    score = np.zeros(n, dtype=np.float32)
    # === 1. VELOCITY AGAINST (for longs: negative velocity = bad) ===
    vel_component = np.zeros(n, dtype=np.float32)
    for tf in TFS:
        vel = safe_get(data, f"wt_velocity_{tf}", n)
        acc = safe_get(data, f"wt_acceleration_{tf}", n)
        w = TF_WEIGHTS[tf]
        # Negative velocity hurts longs
        neg_vel = np.clip(-vel, 0, None)
        vel_contrib = np.minimum(neg_vel * 1.2, 10.0) * w * 3
        # Negative acceleration + negative velocity = getting worse
        neg_acc = np.clip(-acc, 0, None)
        acc_contrib = np.where(vel < 0, np.minimum(neg_acc * 2, 8.0) * w * 3, 0)
        vel_component += vel_contrib + acc_contrib
    score += np.minimum(vel_component, 35.0) * params["vel_w"]
    # === 2. PEAK DIVERGENCE (lower peaks = bearish for longs) ===
    div_component = np.zeros(n, dtype=np.float32)
    for tf in ["15m", "1h", "4h", "D"]:
        peak = safe_get(data, f"wt_peak_{tf}", n)
        peak_prev = safe_get(data, f"wt_peak_prev_{tf}", n)
        w = TF_WEIGHTS[tf]
        div = np.clip(peak_prev - peak, 0, None)  # Positive when current peak < previous
        div_contrib = np.where(div > 3, np.minimum((div - 3) * 0.4, 8.0) * w * 3, 0)
        div_component += div_contrib
    score += np.minimum(div_component, 20.0) * params["div_w"]
    # === 3. DC CHANNEL — position dropping (momentum dying) ===
    dc_component = np.zeros(n, dtype=np.float32)
    for tf in TFS:
        dc_pos = safe_get(data, f"dc_position_{tf}", n)
        w = TF_WEIGHTS[tf]
        # For longs: low dc_position = bad (price near channel bottom)
        # We use 1-dc_pos as "weakness" signal when it drops below 0.5
        weakness = np.clip(0.5 - dc_pos, 0, None) * 2  # 0 when pos>=0.5, 1 when pos=0
        dc_component += np.minimum(weakness * 15, 8.0) * w * 2
    score += np.minimum(dc_component, 15.0) * params["dc_w"]
    # === 4. MULTI-TF VELOCITY ALIGNMENT (how many TFs have negative velocity) ===
    tf_against = np.zeros(n, dtype=np.float32)
    for tf in TFS:
        vel = safe_get(data, f"wt_velocity_{tf}", n)
        tf_against += (vel < 0).astype(np.float32)
    score += np.minimum(tf_against * 3, 15.0) * params["tf_w"]
    # === 5. COMPOSITE SIGNAL ===
    comp_short = safe_get(data, "wt_composite_short", n)
    comp_long = safe_get(data, "wt_composite_long", n)
    comp_signal = np.where(
        (comp_short > comp_long) & (comp_short > 5),
        np.minimum((comp_short - 5) * 1.5, 10.0),
        0
    )
    score += comp_signal * params["comp_w"]
    # === 6. BEAR CROSS on multiple TFs (bonus) ===
    bear_cross_count = np.zeros(n, dtype=np.float32)
    for tf in TFS:
        bc = safe_get(data, f"wt_cross_bear_{tf}", n)
        bear_cross_count += (bc == 1).astype(np.float32)
    score += np.where(bear_cross_count >= 2, 10.0, np.where(bear_cross_count >= 1, 3.0, 0))
    # === 7. WT EXTREME (overbought) ===
    ob_count = safe_get(data, "wt_overbought_tf_count", n)
    score += np.minimum(ob_count * 2, 8.0)
    # === 8. STOCH OVERBOUGHT DECLINING ===
    for tf in ["1h", "4h"]:
        sk = safe_get(data, f"stoch_k_{tf}", n)
        sk_prev = safe_get(data, f"stoch_k_{tf}_prev", n)
        declining_from_ob = ((sk > 70) & (sk < sk_prev)).astype(np.float32)
        score += declining_from_ob * 3
    return np.minimum(score, 100.0)


def compute_vectorized_exit_scores_short(data, params=None):
    """Compute exit score for SHORT positions at every bar, vectorized."""
    if params is None:
        params = {"vel_w": 1.0, "div_w": 1.0, "dc_w": 1.0, "tf_w": 1.0, "comp_w": 1.0}
    n = len(data["close"])
    score = np.zeros(n, dtype=np.float32)
    # === 1. VELOCITY AGAINST (for shorts: positive velocity = bad) ===
    vel_component = np.zeros(n, dtype=np.float32)
    for tf in TFS:
        vel = safe_get(data, f"wt_velocity_{tf}", n)
        acc = safe_get(data, f"wt_acceleration_{tf}", n)
        w = TF_WEIGHTS[tf]
        pos_vel = np.clip(vel, 0, None)
        vel_contrib = np.minimum(pos_vel * 1.2, 10.0) * w * 3
        pos_acc = np.clip(acc, 0, None)
        acc_contrib = np.where(vel > 0, np.minimum(pos_acc * 2, 8.0) * w * 3, 0)
        vel_component += vel_contrib + acc_contrib
    score += np.minimum(vel_component, 35.0) * params["vel_w"]
    # === 2. TROUGH DIVERGENCE (higher troughs = bullish for shorts = bad) ===
    div_component = np.zeros(n, dtype=np.float32)
    for tf in ["15m", "1h", "4h", "D"]:
        trough = safe_get(data, f"wt_trough_{tf}", n)
        trough_prev = safe_get(data, f"wt_trough_prev_{tf}", n)
        w = TF_WEIGHTS[tf]
        div = np.clip(trough - trough_prev, 0, None)  # Positive when current trough > previous (higher low)
        div_contrib = np.where(div > 3, np.minimum((div - 3) * 0.4, 8.0) * w * 3, 0)
        div_component += div_contrib
    score += np.minimum(div_component, 20.0) * params["div_w"]
    # === 3. DC CHANNEL — position rising (for shorts: high dc_pos = bad) ===
    dc_component = np.zeros(n, dtype=np.float32)
    for tf in TFS:
        dc_pos = safe_get(data, f"dc_position_{tf}", n)
        w = TF_WEIGHTS[tf]
        strength = np.clip(dc_pos - 0.5, 0, None) * 2
        dc_component += np.minimum(strength * 15, 8.0) * w * 2
    score += np.minimum(dc_component, 15.0) * params["dc_w"]
    # === 4. MULTI-TF VELOCITY ALIGNMENT ===
    tf_against = np.zeros(n, dtype=np.float32)
    for tf in TFS:
        vel = safe_get(data, f"wt_velocity_{tf}", n)
        tf_against += (vel > 0).astype(np.float32)
    score += np.minimum(tf_against * 3, 15.0) * params["tf_w"]
    # === 5. COMPOSITE SIGNAL ===
    comp_short = safe_get(data, "wt_composite_short", n)
    comp_long = safe_get(data, "wt_composite_long", n)
    comp_signal = np.where(
        (comp_long > comp_short) & (comp_long > 5),
        np.minimum((comp_long - 5) * 1.5, 10.0),
        0
    )
    score += comp_signal * params["comp_w"]
    # === 6. BULL CROSS on multiple TFs ===
    bull_cross_count = np.zeros(n, dtype=np.float32)
    for tf in TFS:
        bc = safe_get(data, f"wt_cross_bull_{tf}", n)
        bull_cross_count += (bc == 1).astype(np.float32)
    score += np.where(bull_cross_count >= 2, 10.0, np.where(bull_cross_count >= 1, 3.0, 0))
    # === 7. WT OVERSOLD ===
    os_count = safe_get(data, "wt_oversold_tf_count", n)
    score += np.minimum(os_count * 2, 8.0)
    # === 8. STOCH OVERSOLD RISING ===
    for tf in ["1h", "4h"]:
        sk = safe_get(data, f"stoch_k_{tf}", n)
        sk_prev = safe_get(data, f"stoch_k_{tf}_prev", n)
        rising_from_os = ((sk < 30) & (sk > sk_prev)).astype(np.float32)
        score += rising_from_os * 3
    return np.minimum(score, 100.0)


def get_entries(data, min_spacing=MIN_SPACING):
    """Get filtered entry indices for both long and short."""
    n = len(data["close"])
    max_idx = n - MAX_HOLD - 1
    # Long entries: 1h bull cross
    bull_crosses = np.where(data["wt_cross_bull_1h"] == 1)[0]
    bull_crosses = bull_crosses[(bull_crosses > WARMUP_BARS) & (bull_crosses < max_idx)]
    long_entries = []
    last = -min_spacing
    for bc in bull_crosses:
        if bc - last >= min_spacing:
            long_entries.append(bc)
            last = bc
    # Short entries: 1h bear cross
    bear_crosses = np.where(data["wt_cross_bear_1h"] == 1)[0]
    bear_crosses = bear_crosses[(bear_crosses > WARMUP_BARS) & (bear_crosses < max_idx)]
    short_entries = []
    last = -min_spacing
    for bc in bear_crosses:
        if bc - last >= min_spacing:
            short_entries.append(bc)
            last = bc
    return np.array(long_entries, dtype=int), np.array(short_entries, dtype=int)


def simulate_trades(data, entries, is_long, exit_scores, threshold):
    """For each entry, find first bar where exit_scores >= threshold after MIN_BARS_AFTER_ENTRY.
    Returns arrays of (pnl, hold_bars, exit_idx)."""
    close = data["close"]
    n = len(close)
    pnls = []
    holds = []
    for entry_idx in entries:
        start = entry_idx + MIN_BARS_AFTER_ENTRY
        end = min(entry_idx + MAX_HOLD, n - 1)
        # Find first bar where score >= threshold
        scores_window = exit_scores[start:end + 1]
        trigger_bars = np.where(scores_window >= threshold)[0]
        if len(trigger_bars) > 0:
            exit_idx = start + trigger_bars[0]
        else:
            exit_idx = end  # Force exit at max hold
        entry_price = close[entry_idx]
        exit_price = close[exit_idx]
        if is_long:
            pnl = (exit_price - entry_price) / entry_price * 100
        else:
            pnl = (entry_price - exit_price) / entry_price * 100
        pnls.append(pnl)
        holds.append(exit_idx - entry_idx)
    return np.array(pnls), np.array(holds)


def compute_optimal_exits(data, entries, is_long):
    """Find the maximum possible PnL for each entry."""
    close = data["close"]
    n = len(close)
    best_pnls = []
    best_holds = []
    for entry_idx in entries:
        end = min(entry_idx + MAX_HOLD, n - 1)
        entry_price = close[entry_idx]
        window = close[entry_idx:end + 1]
        if is_long:
            pnl = (window - entry_price) / entry_price * 100
        else:
            pnl = (entry_price - window) / entry_price * 100
        best_bar = np.nanargmax(pnl)
        best_pnls.append(pnl[best_bar])
        best_holds.append(best_bar)
    return np.array(best_pnls), np.array(best_holds)


def calc_sharpe(pnls, hold_bars=None):
    """pool_sharpe per CANONICAL_METRICS.md — mean(per-trade returns)/std, no annualization.
    The OLD implementation multiplied by sqrt(trades_per_year) which was BANNED for inflating
    high-frequency configs. `hold_bars` arg kept for API compatibility but ignored."""
    if len(pnls) == 0:
        return 0
    mean_r = np.mean(pnls)
    std_r = np.std(pnls)
    if std_r == 0:
        return 0
    return mean_r / std_r


# ============================================================
# PHASE 1: Data exploration
# ============================================================

def phase1_explore():
    print("=" * 100)
    print("PHASE 1: POST-ENTRY PATTERN ANALYSIS")
    print("=" * 100)
    all_best_pnls = []
    all_hold_forever = []
    all_opt_holds = []
    sym_stats = {}
    for sym in SYMBOLS:
        print(f"Loading {sym}...", end=" ", flush=True)
        try:
            data = load_symbol(sym)
        except Exception as e:
            print(f"SKIP ({e})")
            continue
        long_entries, short_entries = get_entries(data)
        if len(long_entries) == 0 and len(short_entries) == 0:
            print("0 entries")
            continue
        close = data["close"]
        n = len(close)
        # Optimal exits
        if len(long_entries) > 0:
            bp_l, bh_l = compute_optimal_exits(data, long_entries, True)
            # Hold-forever PnL
            hf_l = np.array([(close[min(e + MAX_HOLD, n - 1)] - close[e]) / close[e] * 100 for e in long_entries])
            all_best_pnls.extend(bp_l)
            all_hold_forever.extend(hf_l)
            all_opt_holds.extend(bh_l / BARS_PER_HOUR)
        else:
            bp_l, bh_l, hf_l = np.array([]), np.array([]), np.array([])
        if len(short_entries) > 0:
            bp_s, bh_s = compute_optimal_exits(data, short_entries, False)
            hf_s = np.array([(close[e] - close[min(e + MAX_HOLD, n - 1)]) / close[e] * 100 for e in short_entries])
            all_best_pnls.extend(bp_s)
            all_hold_forever.extend(hf_s)
            all_opt_holds.extend(bh_s / BARS_PER_HOUR)
        else:
            bp_s, bh_s, hf_s = np.array([]), np.array([]), np.array([])
        total = len(long_entries) + len(short_entries)
        all_bp = np.concatenate([bp_l, bp_s]) if len(bp_l) > 0 or len(bp_s) > 0 else np.array([])
        all_hf = np.concatenate([hf_l, hf_s]) if len(hf_l) > 0 or len(hf_s) > 0 else np.array([])
        sym_stats[sym] = {"longs": len(long_entries), "shorts": len(short_entries), "best_pnl": np.mean(all_bp) if len(all_bp) > 0 else 0, "hf_pnl": np.mean(all_hf) if len(all_hf) > 0 else 0}
        print(f"{total} entries (L={len(long_entries)}, S={len(short_entries)}), best_avg={np.mean(all_bp):.3f}%, hf_avg={np.mean(all_hf):.3f}%")
    all_best_pnls = np.array(all_best_pnls)
    all_hold_forever = np.array(all_hold_forever)
    all_opt_holds = np.array(all_opt_holds)
    print(f"\nTotal entries across all symbols: {len(all_best_pnls)}")
    print(f"\n--- OPTIMAL EXIT TIMING ---")
    print(f"Median optimal hold:  {np.median(all_opt_holds):.1f} hours")
    print(f"Mean optimal hold:    {np.mean(all_opt_holds):.1f} hours")
    print(f"25th pctl:            {np.percentile(all_opt_holds, 25):.1f} hours")
    print(f"75th pctl:            {np.percentile(all_opt_holds, 75):.1f} hours")
    print(f"\n--- PnL COMPARISON ---")
    print(f"Mean best possible PnL:  {np.mean(all_best_pnls):.3f}%")
    print(f"Mean hold-forever PnL:   {np.mean(all_hold_forever):.3f}%")
    print(f"PnL left on table:       {np.mean(all_best_pnls) - np.mean(all_hold_forever):.3f}%")
    print(f"Win rate (best exit):    {100 * np.mean(all_best_pnls > 0):.1f}%")
    print(f"Win rate (hold forever): {100 * np.mean(all_hold_forever > 0):.1f}%")
    return sym_stats


# ============================================================
# PHASE 2: Feature importance at optimal exit points
# ============================================================

def phase2_feature_importance():
    print("\n" + "=" * 100)
    print("PHASE 2: WHAT'S DIFFERENT AT OPTIMAL EXITS vs RANDOM POINTS?")
    print("=" * 100)
    # For each symbol, at optimal exit points vs random points, compare key indicators
    opt_vals = defaultdict(list)
    rand_vals = defaultdict(list)
    entry_vals = defaultdict(list)
    feature_keys = []
    for tf in TFS:
        feature_keys.extend([
            f"wt_velocity_{tf}", f"wt_acceleration_{tf}", f"wt_score_{tf}",
            f"wt_peak_{tf}", f"wt_peak_prev_{tf}", f"dc_position_{tf}",
            f"stoch_k_{tf}", f"bb_pct_b_{tf}", f"wt1_{tf}",
        ])
    feature_keys.extend(["wt_bear_alignment", "wt_bull_alignment", "wt_overbought_tf_count",
                          "wt_oversold_tf_count", "wt_composite_long", "wt_composite_short",
                          "wt_composite_delta", "wt_velocity_down_count", "wt_velocity_up_count"])
    np.random.seed(42)
    for sym in SYMBOLS:
        try:
            data = load_symbol(sym)
        except:
            continue
        long_entries, short_entries = get_entries(data)
        n = len(data["close"])
        close = data["close"]
        # Process LONG trades
        for entry_idx in long_entries:
            end = min(entry_idx + MAX_HOLD, n - 1)
            window = close[entry_idx:end + 1]
            pnl = (window - close[entry_idx]) / close[entry_idx] * 100
            opt_bar = entry_idx + np.nanargmax(pnl)
            rand_bar = entry_idx + np.random.randint(MIN_BARS_AFTER_ENTRY, max(MIN_BARS_AFTER_ENTRY + 1, end - entry_idx))
            for k in feature_keys:
                if k in data:
                    opt_vals[k].append(float(data[k][opt_bar]))
                    rand_vals[k].append(float(data[k][rand_bar]))
                    entry_vals[k].append(float(data[k][entry_idx]))
    # Compare
    print(f"\n{'Feature':<35} {'AtEntry':>10} {'AtOptExit':>10} {'AtRandom':>10} {'Exit-Entry':>12} {'Exit-Rand':>10}")
    print("-" * 95)
    diffs = []
    for k in feature_keys:
        if k not in opt_vals or len(opt_vals[k]) == 0:
            continue
        entry_m = np.nanmean(entry_vals[k])
        opt_m = np.nanmean(opt_vals[k])
        rand_m = np.nanmean(rand_vals[k])
        diff_entry = opt_m - entry_m
        diff_rand = opt_m - rand_m
        diffs.append((k, entry_m, opt_m, rand_m, diff_entry, diff_rand))
    # Sort by absolute diff from random
    diffs.sort(key=lambda x: abs(x[5]), reverse=True)
    for k, entry_m, opt_m, rand_m, diff_e, diff_r in diffs:
        print(f"{k:<35} {entry_m:>10.3f} {opt_m:>10.3f} {rand_m:>10.3f} {diff_e:>12.3f} {diff_r:>10.3f}")


# ============================================================
# PHASE 3: Backtest exit strategies
# ============================================================

def phase3_backtest():
    print("\n" + "=" * 100)
    print("PHASE 3: EXIT STRATEGY BACKTESTING")
    print("=" * 100)
    param_sets = {
        "Default": {"vel_w": 1.0, "div_w": 1.0, "dc_w": 1.0, "tf_w": 1.0, "comp_w": 1.0},
        "VelHeavy": {"vel_w": 1.5, "div_w": 0.75, "dc_w": 0.75, "tf_w": 1.0, "comp_w": 1.0},
        "DivHeavy": {"vel_w": 0.75, "div_w": 1.5, "dc_w": 0.75, "tf_w": 1.0, "comp_w": 1.0},
        "DCHeavy": {"vel_w": 0.75, "div_w": 0.75, "dc_w": 1.5, "tf_w": 1.0, "comp_w": 1.0},
        "TFHeavy": {"vel_w": 1.0, "div_w": 1.0, "dc_w": 1.0, "tf_w": 1.5, "comp_w": 1.0},
        "VelDiv": {"vel_w": 1.3, "div_w": 1.3, "dc_w": 0.5, "tf_w": 1.0, "comp_w": 0.5},
        "AllEqual": {"vel_w": 1.0, "div_w": 1.0, "dc_w": 1.0, "tf_w": 1.0, "comp_w": 1.0},
    }
    thresholds = [20, 25, 30, 35, 40, 45, 50, 60]
    # Also test simple bear cross baseline
    results = {}
    # Preload data and compute scores
    all_data = {}
    all_entries = {}
    for sym in SYMBOLS:
        try:
            data = load_symbol(sym)
            all_data[sym] = data
            le, se = get_entries(data)
            all_entries[sym] = (le, se)
        except:
            continue
    # --- BASELINE: simple 1h bear cross ---
    print("\nComputing baselines...")
    base_pnl_all = []
    base_hold_all = []
    hf_pnl_all = []
    for sym, data in all_data.items():
        le, se = all_entries[sym]
        close = data["close"]
        n = len(close)
        bear_cross = safe_get(data, "wt_cross_bear_1h", n)
        bull_cross = safe_get(data, "wt_cross_bull_1h", n)
        # Long: exit on bear cross
        for entry_idx in le:
            start = entry_idx + MIN_BARS_AFTER_ENTRY
            end = min(entry_idx + MAX_HOLD, n - 1)
            window = bear_cross[start:end + 1]
            triggers = np.where(window == 1)[0]
            exit_idx = start + triggers[0] if len(triggers) > 0 else end
            pnl = (close[exit_idx] - close[entry_idx]) / close[entry_idx] * 100
            base_pnl_all.append(pnl)
            base_hold_all.append(exit_idx - entry_idx)
            hf_pnl = (close[end] - close[entry_idx]) / close[entry_idx] * 100
            hf_pnl_all.append(hf_pnl)
        # Short: exit on bull cross
        for entry_idx in se:
            start = entry_idx + MIN_BARS_AFTER_ENTRY
            end = min(entry_idx + MAX_HOLD, n - 1)
            window = bull_cross[start:end + 1]
            triggers = np.where(window == 1)[0]
            exit_idx = start + triggers[0] if len(triggers) > 0 else end
            pnl = (close[entry_idx] - close[exit_idx]) / close[entry_idx] * 100
            base_pnl_all.append(pnl)
            base_hold_all.append(exit_idx - entry_idx)
            hf_pnl = (close[entry_idx] - close[end]) / close[entry_idx] * 100
            hf_pnl_all.append(hf_pnl)
    base_pnl = np.array(base_pnl_all)
    base_hold = np.array(base_hold_all)
    hf_pnl = np.array(hf_pnl_all)
    results["BearCross_1h"] = {
        "sharpe": calc_sharpe(base_pnl, base_hold),
        "win_rate": np.mean(base_pnl > 0) * 100,
        "mean_pnl": np.mean(base_pnl),
        "median_pnl": np.median(base_pnl),
        "avg_hold_h": np.mean(base_hold) / BARS_PER_HOUR,
        "total_pnl": np.sum(base_pnl),
        "n_trades": len(base_pnl),
        "max_dd": np.min(base_pnl),
    }
    results["HoldForever_5d"] = {
        "sharpe": calc_sharpe(hf_pnl, np.full(len(hf_pnl), MAX_HOLD)),
        "win_rate": np.mean(hf_pnl > 0) * 100,
        "mean_pnl": np.mean(hf_pnl),
        "median_pnl": np.median(hf_pnl),
        "avg_hold_h": MAX_HOLD / BARS_PER_HOUR,
        "total_pnl": np.sum(hf_pnl),
        "n_trades": len(hf_pnl),
        "max_dd": np.min(hf_pnl),
    }
    # --- SCORER-BASED EXITS ---
    for pname, params in param_sets.items():
        print(f"\nTesting {pname}...", flush=True)
        for thresh in thresholds:
            key = f"{pname}_T{thresh}"
            all_pnl = []
            all_hold = []
            for sym, data in all_data.items():
                le, se = all_entries[sym]
                long_scores = compute_vectorized_exit_scores_long(data, params)
                short_scores = compute_vectorized_exit_scores_short(data, params)
                if len(le) > 0:
                    pnl_l, hold_l = simulate_trades(data, le, True, long_scores, thresh)
                    all_pnl.extend(pnl_l)
                    all_hold.extend(hold_l)
                if len(se) > 0:
                    pnl_s, hold_s = simulate_trades(data, se, False, short_scores, thresh)
                    all_pnl.extend(pnl_s)
                    all_hold.extend(hold_s)
            pnl_arr = np.array(all_pnl)
            hold_arr = np.array(all_hold)
            results[key] = {
                "sharpe": calc_sharpe(pnl_arr, hold_arr),
                "win_rate": np.mean(pnl_arr > 0) * 100,
                "mean_pnl": np.mean(pnl_arr),
                "median_pnl": np.median(pnl_arr),
                "avg_hold_h": np.mean(hold_arr) / BARS_PER_HOUR,
                "total_pnl": np.sum(pnl_arr),
                "n_trades": len(pnl_arr),
                "max_dd": np.min(pnl_arr),
            }
    # Print sorted results
    print(f"\n{'Strategy':<25} {'Sharpe':>8} {'WR':>7} {'AvgPnL':>8} {'MedPnL':>8} {'AvgHold':>8} {'TotalPnL':>10} {'Trades':>7} {'MaxDD':>8}")
    print("-" * 100)
    sorted_results = sorted(results.items(), key=lambda x: x[1]["sharpe"], reverse=True)
    for key, r in sorted_results:
        print(f"{key:<25} {r['sharpe']:>8.2f} {r['win_rate']:>6.1f}% {r['mean_pnl']:>7.3f}% {r['median_pnl']:>7.3f}% {r['avg_hold_h']:>7.1f}h {r['total_pnl']:>9.1f}% {r['n_trades']:>7} {r['max_dd']:>7.2f}%")
    return results, all_data, all_entries


# ============================================================
# PHASE 4: Fine-grained weight optimization
# ============================================================

def phase4_optimize(all_data, all_entries):
    print("\n" + "=" * 100)
    print("PHASE 4: WEIGHT OPTIMIZATION (grid search)")
    print("=" * 100)
    weight_opts = [0.0, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0]
    thresh_opts = [25, 30, 35, 40, 45]
    best_sharpe = -999
    best_config = None
    total = len(weight_opts) ** 3 * len(thresh_opts)
    count = 0
    # Fix tf_w=1.0, comp_w=1.0 for first pass
    print(f"Phase 4a: Grid search over vel_w, div_w, dc_w x thresh ({total} combos)...")
    for vel_w in weight_opts:
        for div_w in weight_opts:
            for dc_w in weight_opts:
                for thresh in thresh_opts:
                    count += 1
                    params = {"vel_w": vel_w, "div_w": div_w, "dc_w": dc_w, "tf_w": 1.0, "comp_w": 1.0}
                    all_pnl = []
                    all_hold = []
                    for sym, data in all_data.items():
                        le, se = all_entries[sym]
                        if len(le) > 0:
                            ls = compute_vectorized_exit_scores_long(data, params)
                            p, h = simulate_trades(data, le, True, ls, thresh)
                            all_pnl.extend(p)
                            all_hold.extend(h)
                        if len(se) > 0:
                            ss = compute_vectorized_exit_scores_short(data, params)
                            p, h = simulate_trades(data, se, False, ss, thresh)
                            all_pnl.extend(p)
                            all_hold.extend(h)
                    sharpe = calc_sharpe(np.array(all_pnl), np.array(all_hold))
                    wr = np.mean(np.array(all_pnl) > 0) * 100
                    mean_pnl = np.mean(all_pnl)
                    if sharpe > best_sharpe:
                        best_sharpe = sharpe
                        best_config = (vel_w, div_w, dc_w, 1.0, 1.0, thresh)
                        avg_hold = np.mean(all_hold) / BARS_PER_HOUR
                        print(f"  [{count}/{total}] NEW BEST: vel={vel_w} div={div_w} dc={dc_w} T={thresh} -> Sharpe={sharpe:.2f} PnL={mean_pnl:.4f}% WR={wr:.1f}% Hold={avg_hold:.1f}h")
    # Phase 4b: Fine-tune tf_w and comp_w
    print(f"\nPhase 4b: Fine-tuning tf_w and comp_w around best...")
    vel_w, div_w, dc_w, _, _, best_thresh = best_config
    for tf_w in weight_opts:
        for comp_w in weight_opts:
            params = {"vel_w": vel_w, "div_w": div_w, "dc_w": dc_w, "tf_w": tf_w, "comp_w": comp_w}
            all_pnl = []
            all_hold = []
            for sym, data in all_data.items():
                le, se = all_entries[sym]
                if len(le) > 0:
                    ls = compute_vectorized_exit_scores_long(data, params)
                    p, h = simulate_trades(data, le, True, ls, best_thresh)
                    all_pnl.extend(p)
                    all_hold.extend(h)
                if len(se) > 0:
                    ss = compute_vectorized_exit_scores_short(data, params)
                    p, h = simulate_trades(data, se, False, ss, best_thresh)
                    all_pnl.extend(p)
                    all_hold.extend(h)
            sharpe = calc_sharpe(np.array(all_pnl), np.array(all_hold))
            wr = np.mean(np.array(all_pnl) > 0) * 100
            mean_pnl = np.mean(all_pnl)
            if sharpe > best_sharpe:
                best_sharpe = sharpe
                best_config = (vel_w, div_w, dc_w, tf_w, comp_w, best_thresh)
                avg_hold = np.mean(all_hold) / BARS_PER_HOUR
                print(f"  NEW BEST: tf_w={tf_w} comp_w={comp_w} -> Sharpe={sharpe:.2f} PnL={mean_pnl:.4f}% WR={wr:.1f}% Hold={avg_hold:.1f}h")
    # Phase 4c: Fine-tune threshold around best
    print(f"\nPhase 4c: Fine-tuning threshold...")
    vel_w, div_w, dc_w, tf_w, comp_w, _ = best_config
    for thresh in range(15, 65, 2):
        params = {"vel_w": vel_w, "div_w": div_w, "dc_w": dc_w, "tf_w": tf_w, "comp_w": comp_w}
        all_pnl = []
        all_hold = []
        for sym, data in all_data.items():
            le, se = all_entries[sym]
            if len(le) > 0:
                ls = compute_vectorized_exit_scores_long(data, params)
                p, h = simulate_trades(data, le, True, ls, thresh)
                all_pnl.extend(p)
                all_hold.extend(h)
            if len(se) > 0:
                ss = compute_vectorized_exit_scores_short(data, params)
                p, h = simulate_trades(data, se, False, ss, thresh)
                all_pnl.extend(p)
                all_hold.extend(h)
        sharpe = calc_sharpe(np.array(all_pnl), np.array(all_hold))
        wr = np.mean(np.array(all_pnl) > 0) * 100
        mean_pnl = np.mean(all_pnl)
        avg_hold = np.mean(all_hold) / BARS_PER_HOUR
        if sharpe > best_sharpe:
            best_sharpe = sharpe
            best_config = (vel_w, div_w, dc_w, tf_w, comp_w, thresh)
            print(f"  NEW BEST: T={thresh} -> Sharpe={sharpe:.2f} PnL={mean_pnl:.4f}% WR={wr:.1f}% Hold={avg_hold:.1f}h")
    print(f"\n=== FINAL OPTIMAL CONFIG ===")
    print(f"vel_w={best_config[0]}, div_w={best_config[1]}, dc_w={best_config[2]}, tf_w={best_config[3]}, comp_w={best_config[4]}, threshold={best_config[5]}")
    print(f"Sharpe={best_sharpe:.2f}")
    return best_config, best_sharpe


# ============================================================
# PHASE 5: Per-symbol breakdown
# ============================================================

def phase5_per_symbol(all_data, all_entries, best_config):
    print("\n" + "=" * 100)
    print("PHASE 5: PER-SYMBOL BREAKDOWN (Optimal Config)")
    print("=" * 100)
    vel_w, div_w, dc_w, tf_w, comp_w, thresh = best_config
    params = {"vel_w": vel_w, "div_w": div_w, "dc_w": dc_w, "tf_w": tf_w, "comp_w": comp_w}
    print(f"\n{'Symbol':<8} {'Trades':>7} {'WR':>7} {'AvgPnL':>8} {'MedPnL':>8} {'AvgHold':>8} {'TotPnL':>10} {'Sharpe':>8} {'MaxDD':>8}")
    print("-" * 80)
    total_pnl_all = []
    for sym in sorted(all_data.keys()):
        data = all_data[sym]
        le, se = all_entries[sym]
        all_pnl = []
        all_hold = []
        if len(le) > 0:
            ls = compute_vectorized_exit_scores_long(data, params)
            p, h = simulate_trades(data, le, True, ls, thresh)
            all_pnl.extend(p)
            all_hold.extend(h)
        if len(se) > 0:
            ss = compute_vectorized_exit_scores_short(data, params)
            p, h = simulate_trades(data, se, False, ss, thresh)
            all_pnl.extend(p)
            all_hold.extend(h)
        if not all_pnl:
            continue
        pnl_arr = np.array(all_pnl)
        hold_arr = np.array(all_hold)
        sharpe = calc_sharpe(pnl_arr, hold_arr)
        wr = np.mean(pnl_arr > 0) * 100
        total_pnl_all.extend(all_pnl)
        print(f"{sym:<8} {len(pnl_arr):>7} {wr:>6.1f}% {np.mean(pnl_arr):>7.3f}% {np.median(pnl_arr):>7.3f}% {np.mean(hold_arr)/BARS_PER_HOUR:>7.1f}h {np.sum(pnl_arr):>9.1f}% {sharpe:>8.2f} {np.min(pnl_arr):>7.2f}%")
    total_pnl_all = np.array(total_pnl_all)
    print(f"\n--- AGGREGATE ---")
    print(f"Total trades: {len(total_pnl_all)}")
    print(f"Overall WR: {np.mean(total_pnl_all > 0) * 100:.1f}%")
    print(f"Overall mean PnL: {np.mean(total_pnl_all):.4f}%")
    print(f"Total PnL: {np.sum(total_pnl_all):.1f}%")


# ============================================================
# PHASE 6: Score distribution analysis
# ============================================================

def phase6_score_analysis(all_data, all_entries, best_config):
    print("\n" + "=" * 100)
    print("PHASE 6: EXIT SCORE DISTRIBUTION ANALYSIS")
    print("=" * 100)
    vel_w, div_w, dc_w, tf_w, comp_w, thresh = best_config
    params = {"vel_w": vel_w, "div_w": div_w, "dc_w": dc_w, "tf_w": tf_w, "comp_w": comp_w}
    # At optimal exit points, what does the score look like?
    opt_scores = []
    entry_scores = []
    rand_scores = []
    np.random.seed(42)
    for sym, data in all_data.items():
        le, se = all_entries[sym]
        close = data["close"]
        n = len(close)
        long_scores = compute_vectorized_exit_scores_long(data, params)
        short_scores = compute_vectorized_exit_scores_short(data, params)
        for entry_idx in le:
            end = min(entry_idx + MAX_HOLD, n - 1)
            window = close[entry_idx:end + 1]
            pnl = (window - close[entry_idx]) / close[entry_idx] * 100
            opt_bar = entry_idx + np.nanargmax(pnl)
            rand_bar = entry_idx + np.random.randint(MIN_BARS_AFTER_ENTRY, max(MIN_BARS_AFTER_ENTRY + 1, end - entry_idx))
            opt_scores.append(long_scores[opt_bar])
            entry_scores.append(long_scores[entry_idx])
            rand_scores.append(long_scores[rand_bar])
        for entry_idx in se:
            end = min(entry_idx + MAX_HOLD, n - 1)
            window = close[entry_idx:end + 1]
            pnl = (close[entry_idx] - window) / close[entry_idx] * 100
            opt_bar = entry_idx + np.nanargmax(pnl)
            rand_bar = entry_idx + np.random.randint(MIN_BARS_AFTER_ENTRY, max(MIN_BARS_AFTER_ENTRY + 1, end - entry_idx))
            opt_scores.append(short_scores[opt_bar])
            entry_scores.append(short_scores[entry_idx])
            rand_scores.append(short_scores[rand_bar])
    opt_scores = np.array(opt_scores)
    entry_scores = np.array(entry_scores)
    rand_scores = np.array(rand_scores)
    print(f"\n{'Metric':<25} {'AtEntry':>10} {'AtOptExit':>10} {'AtRandom':>10}")
    print("-" * 60)
    print(f"{'Mean score':<25} {np.mean(entry_scores):>10.2f} {np.mean(opt_scores):>10.2f} {np.mean(rand_scores):>10.2f}")
    print(f"{'Median score':<25} {np.median(entry_scores):>10.2f} {np.median(opt_scores):>10.2f} {np.median(rand_scores):>10.2f}")
    print(f"{'P75 score':<25} {np.percentile(entry_scores, 75):>10.2f} {np.percentile(opt_scores, 75):>10.2f} {np.percentile(rand_scores, 75):>10.2f}")
    print(f"{'P90 score':<25} {np.percentile(entry_scores, 90):>10.2f} {np.percentile(opt_scores, 90):>10.2f} {np.percentile(rand_scores, 90):>10.2f}")
    print(f"{'% >= {thresh}':<25} {np.mean(entry_scores >= thresh) * 100:>9.1f}% {np.mean(opt_scores >= thresh) * 100:>9.1f}% {np.mean(rand_scores >= thresh) * 100:>9.1f}%")
    # Score buckets at optimal exit
    print(f"\nScore distribution at OPTIMAL EXIT points:")
    for lo, hi in [(0, 10), (10, 20), (20, 30), (30, 40), (40, 50), (50, 60), (60, 70), (70, 80), (80, 100)]:
        pct = np.mean((opt_scores >= lo) & (opt_scores < hi)) * 100
        print(f"  [{lo:>2}-{hi:>2}): {pct:>5.1f}%")


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    t0 = time.time()
    phase1_explore()
    phase2_feature_importance()
    results, all_data, all_entries = phase3_backtest()
    best_config, best_sharpe = phase4_optimize(all_data, all_entries)
    phase5_per_symbol(all_data, all_entries, best_config)
    phase6_score_analysis(all_data, all_entries, best_config)
    elapsed = time.time() - t0
    print(f"\n{'=' * 100}")
    print(f"TOTAL ANALYSIS TIME: {elapsed:.0f}s ({elapsed / 60:.1f}min)")
    print(f"BEST CONFIG: vel_w={best_config[0]}, div_w={best_config[1]}, dc_w={best_config[2]}, tf_w={best_config[3]}, comp_w={best_config[4]}, threshold={best_config[5]}")
    print(f"BEST SHARPE: {best_sharpe:.2f}")
    print(f"{'=' * 100}")
