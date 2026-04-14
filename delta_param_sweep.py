"""
delta_param_sweep.py — Full Delta Engine Parameter Sweep for Stocks
====================================================================
Tests 5 parameter groups in isolation (one group varies, rest default):
  1. TF weight variations (4 configs)
  2. Entry strictness: min_tf x z_threshold (20 configs)
  3. Exit sensitivity: decay_ratio x min_tf_lost (18 configs)
  4. Speed smoothing: smooth x accel_lookback (12 configs)
  5. Z window (3 configs)

Per config: run all 13 symbols, compute weekly Sharpe, trades, WR, PF.
Output sorted by weekly Sharpe to delta_param_sweep_results.txt

Usage:
    python3 delta_param_sweep.py
"""
import numpy as np
import os
import sys
import time
from datetime import datetime, timezone

BASE_PATH = "/Users/niels/Documents/binance"
NPZ_DIR = os.path.join(BASE_PATH, "backtest_v8", "indicators")
OUTPUT_FILE = os.path.join(BASE_PATH, "delta_param_sweep_results.txt")

SYMBOLS = ["MU", "AAPL", "TTD", "FIVN", "AMZN", "MRVL", "XOM", "CVX", "GLD", "USO", "NVDA", "MSFT", "ASTS"]
STOCK_TFS = ["5m", "15m", "1h", "4h", "D"]

MARKET_OPEN_H, MARKET_OPEN_M = 13, 30
MARKET_CLOSE_H, MARKET_CLOSE_M = 20, 0
START_TS = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())

# ===== DEFAULT CONFIG (current production values) =====
DEFAULT_CFG = {
    "tf_weights": {"5m": 0.30, "15m": 0.25, "1h": 0.20, "4h": 0.15, "D": 0.10},
    "speed_smooth": 3,
    "accel_lookback": 5,
    "tf_z_threshold": 1.0,
    "entry_z_threshold": 1.5,
    "entry_accel_threshold": 0.2,
    "entry_min_tf": 4,
    "exit_decay_ratio": 0.15,
    "exit_min_tf_lost": 2,
    "z_window": 200,
}

# ===== SWEEP GROUPS =====
TF_WEIGHT_VARIATIONS = {
    "LTF-heavy": {"5m": 3.0, "15m": 2.0, "1h": 1.0, "4h": 0.5, "D": 0.3},
    "Balanced": {"5m": 1.0, "15m": 1.0, "1h": 1.0, "4h": 1.0, "D": 1.0},
    "HTF-heavy": {"5m": 0.3, "15m": 0.5, "1h": 1.0, "4h": 2.0, "D": 3.0},
    "Mid-focus": {"5m": 0.5, "15m": 1.5, "1h": 2.0, "4h": 1.5, "D": 0.5},
}

ENTRY_MIN_TF_VALS = [2, 3, 4, 5]
ENTRY_Z_THRESHOLD_VALS = [0.5, 1.0, 1.5, 2.0, 2.5]

EXIT_DECAY_RATIO_VALS = [0.05, 0.10, 0.15, 0.20, 0.30, 0.50]
EXIT_MIN_TF_LOST_VALS = [1, 2, 3]

SPEED_SMOOTH_VALS = [1, 2, 3, 5]
ACCEL_LOOKBACK_VALS = [3, 5, 8]

Z_WINDOW_VALS = [100, 200, 500]


def build_market_hours_mask_fast(timestamps):
    """Vectorized market hours mask."""
    ts = timestamps.astype(np.int64)
    days_since_epoch = ts // 86400
    secs_of_day = ts % 86400
    dow = (days_since_epoch + 3) % 7
    mins_of_day = secs_of_day // 60
    open_min = MARKET_OPEN_H * 60 + MARKET_OPEN_M
    close_min = MARKET_CLOSE_H * 60 + MARKET_CLOSE_M
    mask = (dow < 5) & (mins_of_day >= open_min) & (mins_of_day < close_min)
    return mask


def compute_delta_signals(d, cfg):
    """Vectorized delta computation for stock NPZ with configurable z_window."""
    keys = list(d.keys())
    wt_dc_keys = sorted([k for k in keys if k.startswith("wt") or k.startswith("dc")])
    n = len(d["close"])
    tw = cfg["tf_weights"]
    smooth = cfg["speed_smooth"]
    accel_lb = cfg["accel_lookback"]
    z_window = cfg.get("z_window", 200)
    tf_bull = {tf: np.zeros(n) for tf in STOCK_TFS}
    tf_bear = {tf: np.zeros(n) for tf in STOCK_TFS}
    tf_c = {tf: 0 for tf in STOCK_TFS}
    for k in wt_dc_keys:
        arr = d[k].astype(np.float64)
        prev_k = k + "_prev"
        if prev_k in d:
            delta = arr - d[prev_k].astype(np.float64)
        elif d[k].dtype in (np.float32, np.float64) and not k.endswith("_prev") and not k.endswith("_ant"):
            delta = np.empty(n)
            delta[0] = 0
            delta[1:] = arr[1:] - arr[:-1]
        else:
            continue
        delta = np.nan_to_num(delta, 0)
        mt = None
        for tf in STOCK_TFS:
            if f"_{tf}" in k:
                mt = tf
                break
        if mt:
            tf_bull[mt] += np.maximum(delta, 0)
            tf_bear[mt] += np.maximum(-delta, 0)
            tf_c[mt] += 1
        else:
            for tf in STOCK_TFS:
                tf_bull[tf] += np.maximum(delta, 0) * 0.2
                tf_bear[tf] += np.maximum(-delta, 0) * 0.2
                tf_c[tf] += 1
    for tf in STOCK_TFS:
        if tf_c[tf] > 0:
            tf_bull[tf] /= tf_c[tf]
            tf_bear[tf] /= tf_c[tf]
    if smooth > 1:
        kernel = np.ones(smooth) / smooth
        for tf in STOCK_TFS:
            tf_bull[tf] = np.convolve(tf_bull[tf], kernel, mode="same")
            tf_bear[tf] = np.convolve(tf_bear[tf], kernel, mode="same")
    # Z-score normalize per TF — rolling or global based on z_window
    tf_bull_z, tf_bear_z = {}, {}
    if z_window >= n:
        # Global z-score
        for tf in STOCK_TFS:
            m, s = np.mean(tf_bull[tf]), np.std(tf_bull[tf])
            tf_bull_z[tf] = (tf_bull[tf] - m) / s if s > 1e-10 else np.zeros(n)
            m, s = np.mean(tf_bear[tf]), np.std(tf_bear[tf])
            tf_bear_z[tf] = (tf_bear[tf] - m) / s if s > 1e-10 else np.zeros(n)
    else:
        # Rolling z-score
        for tf in STOCK_TFS:
            tf_bull_z[tf] = _rolling_zscore(tf_bull[tf], z_window)
            tf_bear_z[tf] = _rolling_zscore(tf_bear[tf], z_window)
    # Weighted total
    total_bull = sum(tf_bull_z[tf] * tw.get(tf, 1.0) for tf in STOCK_TFS)
    total_bear = sum(tf_bear_z[tf] * tw.get(tf, 1.0) for tf in STOCK_TFS)
    m, s = np.mean(total_bull), np.std(total_bull)
    total_bull_z = (total_bull - m) / s if s > 1e-10 else np.zeros(n)
    m, s = np.mean(total_bear), np.std(total_bear)
    total_bear_z = (total_bear - m) / s if s > 1e-10 else np.zeros(n)
    # Acceleration
    bull_accel = np.zeros(n)
    bear_accel = np.zeros(n)
    bull_accel[accel_lb:] = total_bull_z[accel_lb:] - total_bull_z[:-accel_lb]
    bear_accel[accel_lb:] = total_bear_z[accel_lb:] - total_bear_z[:-accel_lb]
    # TF count above z threshold
    zt = cfg["tf_z_threshold"]
    bull_tf_count = np.zeros(n, dtype=np.int8)
    bear_tf_count = np.zeros(n, dtype=np.int8)
    for tf in STOCK_TFS:
        bull_tf_count += (tf_bull_z[tf] > zt).astype(np.int8)
        bear_tf_count += (tf_bear_z[tf] > zt).astype(np.int8)
    entry_min_tf = cfg["entry_min_tf"]
    entry_z = cfg["entry_z_threshold"]
    entry_accel = cfg["entry_accel_threshold"]
    return {
        "total_bull_z": total_bull_z,
        "total_bear_z": total_bear_z,
        "bull_accel": bull_accel,
        "bear_accel": bear_accel,
        "bull_tf_count": bull_tf_count,
        "bear_tf_count": bear_tf_count,
        "entry_long": (total_bull_z > entry_z) & (bull_accel > entry_accel) & (bull_tf_count >= entry_min_tf),
        "entry_short": (total_bear_z > entry_z) & (bear_accel > entry_accel) & (bear_tf_count >= entry_min_tf),
        "tf_bull_z": tf_bull_z,
        "tf_bear_z": tf_bear_z,
    }


def _rolling_zscore(arr, window):
    """Rolling z-score with cumsum trick for speed."""
    n = len(arr)
    out = np.zeros(n)
    cumsum = np.cumsum(arr)
    cumsum2 = np.cumsum(arr ** 2)
    for i in range(window, n):
        s = cumsum[i] - cumsum[i - window]
        s2 = cumsum2[i] - cumsum2[i - window]
        mu = s / window
        var = s2 / window - mu * mu
        if var > 1e-20:
            out[i] = (arr[i] - mu) / np.sqrt(var)
    return out


def simulate_trades(close, timestamps, entry_long, entry_short, exit_decay_ratio, exit_min_tf_lost, signals, market_mask, start_mask):
    """Simulate long/short trades with configurable exit sensitivity."""
    n = len(close)
    trades = []
    in_trade = False
    side = None
    entry_price = 0.0
    entry_idx = 0
    peak_speed = 0.0
    total_bull_z = signals["total_bull_z"]
    total_bear_z = signals["total_bear_z"]
    bull_tf_count = signals["bull_tf_count"]
    bear_tf_count = signals["bear_tf_count"]
    for i in range(n):
        if not start_mask[i]:
            continue
        if not market_mask[i]:
            if in_trade:
                pnl = (close[i] / entry_price - 1) * (1 if side == "L" else -1)
                trades.append((timestamps[entry_idx], timestamps[i], side, entry_price, close[i], pnl * 100))
                in_trade = False
            continue
        if in_trade:
            cur_spd = total_bull_z[i] if side == "L" else total_bear_z[i]
            if cur_spd > peak_speed:
                peak_speed = cur_spd
            speed_decayed = peak_speed > 0.5 and cur_spd < peak_speed * exit_decay_ratio
            opposite_entry = entry_short[i] if side == "L" else entry_long[i]
            cur_tf = bull_tf_count[i] if side == "L" else bear_tf_count[i]
            tf_lost = cur_tf < exit_min_tf_lost
            if speed_decayed or opposite_entry or tf_lost:
                pnl = (close[i] / entry_price - 1) * (1 if side == "L" else -1)
                trades.append((timestamps[entry_idx], timestamps[i], side, entry_price, close[i], pnl * 100))
                in_trade = False
                if opposite_entry:
                    side = "S" if side == "L" else "L"
                    entry_price = close[i]
                    entry_idx = i
                    peak_speed = total_bear_z[i] if side == "S" else total_bull_z[i]
                    in_trade = True
        else:
            if entry_long[i]:
                in_trade = True
                side = "L"
                entry_price = close[i]
                entry_idx = i
                peak_speed = total_bull_z[i]
            elif entry_short[i]:
                in_trade = True
                side = "S"
                entry_price = close[i]
                entry_idx = i
                peak_speed = total_bear_z[i]
    return trades


def compute_metrics(trades):
    """Compute weekly Sharpe, n_trades, total_pnl, win_rate, profit_factor."""
    if not trades:
        return 0.0, 0, 0.0, 0.0, 0.0
    weekly_pnl = {}
    for t in trades:
        entry_ts = int(t[0])
        week = datetime.utcfromtimestamp(entry_ts).isocalendar()[:2]
        weekly_pnl.setdefault(week, 0.0)
        weekly_pnl[week] += t[5]
    n_trades = len(trades)
    total_pnl = sum(t[5] for t in trades)
    wins = sum(1 for t in trades if t[5] > 0)
    wr = wins / n_trades if n_trades else 0.0
    gross_profit = sum(t[5] for t in trades if t[5] > 0)
    gross_loss = abs(sum(t[5] for t in trades if t[5] < 0))
    pf = gross_profit / gross_loss if gross_loss > 0.01 else (99.9 if gross_profit > 0 else 0.0)
    if len(weekly_pnl) < 2:
        return 0.0, n_trades, total_pnl, wr, pf
    returns = np.array(list(weekly_pnl.values()))
    mu = np.mean(returns)
    sigma = np.std(returns)
    sharpe = (mu / sigma * np.sqrt(52)) if sigma > 1e-10 else 0.0
    return sharpe, n_trades, total_pnl, wr, pf


def run_one_config(cfg, sym_data, masks, start_masks):
    """Run one config across all symbols. Returns aggregated metrics."""
    all_trades = []
    per_sym = {}
    for sym, d in sym_data.items():
        signals = compute_delta_signals(d, cfg)
        close = d["close"].astype(np.float64)
        ts = d["timestamps"]
        trades = simulate_trades(
            close, ts,
            signals["entry_long"], signals["entry_short"],
            cfg["exit_decay_ratio"], cfg.get("exit_min_tf_lost", 2),
            signals, masks[sym], start_masks[sym],
        )
        per_sym[sym] = compute_metrics(trades)
        all_trades.extend(trades)
    agg = compute_metrics(all_trades)
    return agg, per_sym


def make_cfg(overrides):
    """Create config from defaults + overrides."""
    cfg = dict(DEFAULT_CFG)
    cfg.update(overrides)
    return cfg


def main():
    t0 = time.time()
    print("=" * 100)
    print("DELTA ENGINE FULL PARAMETER SWEEP — Stocks")
    print("=" * 100)
    print(f"Symbols: {', '.join(SYMBOLS)}")
    print(f"NPZ dir: {NPZ_DIR}")
    print(f"Start: 2024-01-01 | Market hours: 13:30-20:00 UTC weekdays")
    print()
    # Load symbols
    print("Loading NPZ files...")
    sym_data = {}
    for sym in SYMBOLS:
        path = os.path.join(NPZ_DIR, f"{sym}.npz")
        if not os.path.exists(path):
            print(f"  SKIP {sym}: not found")
            continue
        d = dict(np.load(path, allow_pickle=True))
        sym_data[sym] = d
        print(f"  {sym}: {len(d['close']):,} bars")
    print(f"  {len(sym_data)} symbols loaded in {time.time()-t0:.1f}s")
    print()
    # Build masks
    masks, start_masks = {}, {}
    for sym, d in sym_data.items():
        ts = d["timestamps"]
        masks[sym] = build_market_hours_mask_fast(ts)
        start_masks[sym] = ts >= START_TS
    # ===== BASELINE =====
    print("Running BASELINE (current production config)...")
    baseline_cfg = dict(DEFAULT_CFG)
    baseline_agg, baseline_per_sym = run_one_config(baseline_cfg, sym_data, masks, start_masks)
    print(f"  BASELINE: Sharpe={baseline_agg[0]:.2f}, trades={baseline_agg[1]}, PnL={baseline_agg[2]:.2f}%, WR={baseline_agg[3]:.1%}, PF={baseline_agg[4]:.2f}")
    print()
    # ===== ALL CONFIGS =====
    all_results = []
    # Tag: (group, label, cfg_dict, agg_metrics, per_sym_metrics)
    all_results.append(("BASELINE", "default", baseline_cfg, baseline_agg, baseline_per_sym))
    # --- GROUP 1: TF weights ---
    print("GROUP 1: TF weight variations (4 configs)...")
    for label, tw in TF_WEIGHT_VARIATIONS.items():
        cfg = make_cfg({"tf_weights": tw})
        agg, per_sym = run_one_config(cfg, sym_data, masks, start_masks)
        all_results.append(("TF_WEIGHTS", label, cfg, agg, per_sym))
        print(f"  {label}: Sharpe={agg[0]:.2f}, trades={agg[1]}, PnL={agg[2]:.2f}%, WR={agg[3]:.1%}, PF={agg[4]:.2f}")
    print(f"  Done in {time.time()-t0:.0f}s")
    print()
    # --- GROUP 2: Entry strictness ---
    n_entry = len(ENTRY_MIN_TF_VALS) * len(ENTRY_Z_THRESHOLD_VALS)
    print(f"GROUP 2: Entry strictness ({n_entry} configs)...")
    done = 0
    for min_tf in ENTRY_MIN_TF_VALS:
        for z_thresh in ENTRY_Z_THRESHOLD_VALS:
            cfg = make_cfg({"entry_min_tf": min_tf, "entry_z_threshold": z_thresh})
            agg, per_sym = run_one_config(cfg, sym_data, masks, start_masks)
            label = f"minTF={min_tf}_zT={z_thresh}"
            all_results.append(("ENTRY", label, cfg, agg, per_sym))
            done += 1
            if done % 5 == 0:
                print(f"  {done}/{n_entry} done ({time.time()-t0:.0f}s)")
    print(f"  Done in {time.time()-t0:.0f}s")
    print()
    # --- GROUP 3: Exit sensitivity ---
    n_exit = len(EXIT_DECAY_RATIO_VALS) * len(EXIT_MIN_TF_LOST_VALS)
    print(f"GROUP 3: Exit sensitivity ({n_exit} configs)...")
    done = 0
    for decay in EXIT_DECAY_RATIO_VALS:
        for mtfl in EXIT_MIN_TF_LOST_VALS:
            cfg = make_cfg({"exit_decay_ratio": decay, "exit_min_tf_lost": mtfl})
            agg, per_sym = run_one_config(cfg, sym_data, masks, start_masks)
            label = f"decay={decay}_mtfl={mtfl}"
            all_results.append(("EXIT", label, cfg, agg, per_sym))
            done += 1
            if done % 6 == 0:
                print(f"  {done}/{n_exit} done ({time.time()-t0:.0f}s)")
    print(f"  Done in {time.time()-t0:.0f}s")
    print()
    # --- GROUP 4: Speed smoothing ---
    n_smooth = len(SPEED_SMOOTH_VALS) * len(ACCEL_LOOKBACK_VALS)
    print(f"GROUP 4: Speed smoothing ({n_smooth} configs)...")
    done = 0
    for sm in SPEED_SMOOTH_VALS:
        for alb in ACCEL_LOOKBACK_VALS:
            cfg = make_cfg({"speed_smooth": sm, "accel_lookback": alb})
            agg, per_sym = run_one_config(cfg, sym_data, masks, start_masks)
            label = f"smooth={sm}_accelLB={alb}"
            all_results.append(("SMOOTH", label, cfg, agg, per_sym))
            done += 1
    print(f"  Done in {time.time()-t0:.0f}s")
    print()
    # --- GROUP 5: Z window ---
    print(f"GROUP 5: Z window ({len(Z_WINDOW_VALS)} configs)...")
    for zw in Z_WINDOW_VALS:
        cfg = make_cfg({"z_window": zw})
        agg, per_sym = run_one_config(cfg, sym_data, masks, start_masks)
        label = f"zwin={zw}"
        all_results.append(("Z_WINDOW", label, cfg, agg, per_sym))
        print(f"  zwin={zw}: Sharpe={agg[0]:.2f}, trades={agg[1]}, PnL={agg[2]:.2f}%, WR={agg[3]:.1%}, PF={agg[4]:.2f}")
    print(f"  Done in {time.time()-t0:.0f}s")
    print()
    # ===== SORT AND OUTPUT =====
    # Sort by weekly Sharpe descending
    all_results.sort(key=lambda x: -x[3][0])
    total_configs = len(all_results)
    runtime = time.time() - t0
    # Build output
    lines = []
    lines.append("=" * 120)
    lines.append("DELTA ENGINE FULL PARAMETER SWEEP — STOCKS")
    lines.append(f"Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append(f"Symbols: {', '.join(SYMBOLS)} ({len(sym_data)} loaded)")
    lines.append(f"Data: 5m NPZ bars, market hours 13:30-20:00 UTC, start 2024-01-01")
    lines.append(f"Total configs: {total_configs} | Runtime: {runtime:.0f}s")
    lines.append("=" * 120)
    lines.append("")
    lines.append("TOP 20 CONFIGS BY WEEKLY SHARPE (annualized)")
    lines.append("-" * 120)
    hdr = f"{'Rank':<5} {'Group':<11} {'Label':<30} {'Sharpe':>8} {'Trades':>7} {'PnL%':>9} {'WR':>7} {'PF':>7}"
    lines.append(hdr)
    lines.append("-" * 120)
    for i, (group, label, cfg, agg, per_sym) in enumerate(all_results[:20]):
        sharpe, n_trades, total_pnl, wr, pf = agg
        lines.append(f"{i+1:<5} {group:<11} {label:<30} {sharpe:>8.2f} {n_trades:>7} {total_pnl:>9.2f} {wr:>7.1%} {pf:>7.2f}")
    lines.append("")
    # ===== PER-GROUP BEST =====
    lines.append("=" * 120)
    lines.append("BEST CONFIG PER GROUP")
    lines.append("-" * 120)
    groups_seen = {}
    for group, label, cfg, agg, per_sym in all_results:
        if group not in groups_seen:
            groups_seen[group] = (label, agg)
    for group, (label, agg) in groups_seen.items():
        sharpe, n_trades, total_pnl, wr, pf = agg
        lines.append(f"  {group:<11} {label:<30} Sharpe={sharpe:.2f} trades={n_trades} PnL={total_pnl:.2f}% WR={wr:.1%} PF={pf:.2f}")
    lines.append("")
    # ===== PER-SYMBOL DETAIL FOR TOP 3 =====
    lines.append("=" * 120)
    lines.append("PER-SYMBOL DETAIL — TOP 3 CONFIGS")
    lines.append("=" * 120)
    for rank_i in range(min(3, len(all_results))):
        group, label, cfg, agg, per_sym = all_results[rank_i]
        lines.append(f"\n#{rank_i+1}: [{group}] {label} — Portfolio Sharpe={agg[0]:.2f}")
        # Show key config values that differ from default
        diffs = []
        for k, v in cfg.items():
            if k == "tf_weights":
                if v != DEFAULT_CFG["tf_weights"]:
                    diffs.append(f"tf_weights={v}")
            elif v != DEFAULT_CFG.get(k):
                diffs.append(f"{k}={v}")
        if diffs:
            lines.append(f"  Config changes: {', '.join(diffs)}")
        lines.append(f"  {'Symbol':<8} {'Sharpe':>8} {'Trades':>7} {'PnL%':>9} {'WR':>7} {'PF':>7}")
        lines.append(f"  {'-'*50}")
        for sym in SYMBOLS:
            if sym in per_sym:
                sh, nt, pnl, wr, pf = per_sym[sym]
                lines.append(f"  {sym:<8} {sh:>8.2f} {nt:>7} {pnl:>9.2f} {wr:>7.1%} {pf:>7.2f}")
    lines.append("")
    # ===== ALL CONFIGS SORTED =====
    lines.append("=" * 120)
    lines.append("ALL CONFIGS SORTED BY WEEKLY SHARPE")
    lines.append("-" * 120)
    lines.append(f"{'Rank':<5} {'Group':<11} {'Label':<30} {'Sharpe':>8} {'Trades':>7} {'PnL%':>9} {'WR':>7} {'PF':>7}")
    lines.append("-" * 120)
    for i, (group, label, cfg, agg, per_sym) in enumerate(all_results):
        sharpe, n_trades, total_pnl, wr, pf = agg
        lines.append(f"{i+1:<5} {group:<11} {label:<30} {sharpe:>8.2f} {n_trades:>7} {total_pnl:>9.2f} {wr:>7.1%} {pf:>7.2f}")
    lines.append("")
    lines.append(f"Total runtime: {runtime:.0f}s")
    output = "\n".join(lines)
    # Write to file
    with open(OUTPUT_FILE, "w") as f:
        f.write(output)
    print(output)
    print(f"\nResults written to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
