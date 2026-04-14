#!/usr/bin/env python3
"""CRYPTO VECTORIZED SPIKE FADE SWEEP — Millions of combos per hour.

Pure numpy on 3m NPZ arrays. No process_position, no async, no mocking.
Tests spike fade: SHORT pumps (price spike + structure break), LONG dumps (price crash + reversal).

48 symbols × 836K bars × thousands of param combos = results in minutes.

Usage:
    python3 crypto_vectorized_spike_fade_sweep.py                    # Full sweep
    python3 crypto_vectorized_spike_fade_sweep.py --threshold 3.0    # Single threshold
"""
import argparse, csv, itertools, json, logging, os, platform, sys, time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple
import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("sf_sweep")

IS_SERVER = platform.system() == "Linux"
if IS_SERVER:
    BASE = Path("/home/niels/binance-sandbox")
    RESULTS_DIR = Path("/home/niels/crypto_sf_sweep_results")
else:
    BASE = Path("/Users/niels/Documents/binance")
    RESULTS_DIR = BASE / "data" / "sweep_results"
NPZ_DIR = BASE / "backtest_v5" / "indicators_3m"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

def arr(data, key, n, default=0.0):
    a = data.get(key)
    if a is None: return np.full(n, default, dtype=np.float64)
    a = np.asarray(a, dtype=np.float64)
    if len(a) < n: a = np.concatenate([a, np.full(n - len(a), default)])
    return a[:n]

def list_symbols():
    return sorted([f.stem for f in NPZ_DIR.glob("*.npz")])

def load_symbol(sym):
    f = NPZ_DIR / f"{sym}.npz"
    return dict(np.load(str(f), allow_pickle=True))

def precompute_symbol(data, n, close):
    """Precompute ALL masks for a symbol once. Configs only change thresholds on these."""
    high_3m = arr(data, "high_3m", n)
    low_3m = arr(data, "low_3m", n)
    high_3m_prev = arr(data, "high_3m_prev", n)
    low_3m_prev = arr(data, "low_3m_prev", n)
    k_3m = arr(data, "stoch_k_3m", n, 50)
    pump_fading = (high_3m > 0) & (high_3m_prev > 0) & (high_3m < high_3m_prev) & (low_3m > 0) & (low_3m_prev > 0) & (low_3m < low_3m_prev)
    dump_reversing = (low_3m > 0) & (low_3m_prev > 0) & (low_3m > low_3m_prev) & (high_3m > 0) & (high_3m_prev > 0) & (high_3m > high_3m_prev)
    price_below_prev_high = (close < high_3m_prev) & (high_3m_prev > 0)
    price_above_prev_low = (close > low_3m_prev) & (low_3m_prev > 0)
    long_struct_exit = (high_3m < high_3m_prev) & (close < high_3m_prev) & (high_3m_prev > 0)
    short_struct_exit = (low_3m > low_3m_prev) & (close > low_3m_prev) & (low_3m_prev > 0)
    return {"pump_fading": pump_fading, "dump_reversing": dump_reversing, "px_below_prevh": price_below_prev_high, "px_above_prevl": price_above_prev_low, "long_struct_exit": long_struct_exit, "short_struct_exit": short_struct_exit, "k_3m": k_3m}

def run_spike_fade_fast(n, close, pre, cfg):
    """FAST spike fade using precomputed masks + numpy where possible. Still has a bar loop but minimal work per bar."""
    thresh = cfg["threshold"]; lb = cfg["lookback"]; k_exh = cfg["k_exhaustion"]; cd = cfg["cooldown_bars"]; noloss = cfg["noloss"]
    # Return over lookback
    ret = np.zeros(n); ret[lb:] = (close[lb:] - close[:-lb]) / np.maximum(close[:-lb], 1e-10) * 100
    short_entry = (ret > thresh) & (pre["pump_fading"] | pre["px_below_prevh"])
    long_entry = (ret < -thresh) & (pre["dump_reversing"] | pre["px_above_prevl"])
    long_exit = pre["long_struct_exit"] & (pre["k_3m"] > k_exh)
    short_exit = pre["short_struct_exit"] & (pre["k_3m"] < (100 - k_exh))
    # Find entry indices (much faster than checking every bar)
    short_entries = np.where(short_entry)[0]
    long_entries = np.where(long_entry)[0]
    all_entries = np.concatenate([np.column_stack([short_entries, np.full(len(short_entries), -1)]), np.column_stack([long_entries, np.full(len(long_entries), 1)])]) if len(short_entries) + len(long_entries) > 0 else np.empty((0, 2))
    if len(all_entries) == 0:
        return 0, 0, 0, 0.0
    all_entries = all_entries[all_entries[:, 0].argsort()]
    if len(all_entries) > 2000: all_entries = all_entries[::len(all_entries)//2000]  # Sample evenly if too many
    # Simulate with minimal loop — only iterate over entry candidates
    wins = 0; losses = 0; total_pnl = 0.0; last_exit = -cd - 1
    for entry_idx, side in all_entries:
        i = int(entry_idx); side = int(side)
        if i - last_exit < cd: continue
        px_entry = close[i]
        if px_entry <= 0: continue
        # Find next exit
        if side == 1:  # long
            exit_mask = long_exit[i+1:]
        else:
            exit_mask = short_exit[i+1:]
        exit_candidates = np.where(exit_mask)[0]
        if len(exit_candidates) == 0:
            continue  # no exit found — position stays open (noloss holds forever)
        exit_offset = exit_candidates[0]
        exit_i = i + 1 + exit_offset
        px_exit = close[exit_i]
        if px_exit <= 0: continue
        gain = ((px_exit - px_entry) / px_entry * 100) if side == 1 else ((px_entry - px_exit) / px_entry * 100)
        if gain > 0 or not noloss:
            total_pnl += gain
            if gain > 0: wins += 1
            else: losses += 1
            last_exit = exit_i
    return wins + losses, wins, losses, total_pnl

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2022-01-01")
    parser.add_argument("--threshold", type=float, default=None, help="Single threshold to test")
    parser.add_argument("--max-symbols", type=int, default=48)
    args = parser.parse_args()
    start_ts = int(datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
    all_symbols = list_symbols()[:args.max_symbols]
    if not all_symbols:
        log.error("No NPZ data found!"); return
    log.info(f"Found {len(all_symbols)} symbols")
    if args.threshold:
        thresholds = [args.threshold]
    else:
        thresholds = [2.0, 3.0, 4.0, 5.0, 7.0, 10.0]
    lookbacks = [3, 6, 10, 15, 20, 30, 40]
    k_exhaustions = [65, 70, 75, 80, 85]
    cooldowns = [3, 5, 10, 15]
    noloss_opts = [True, False]
    configs = list(itertools.product(thresholds, lookbacks, k_exhaustions, cooldowns, ["structure"], noloss_opts))
    log.info(f"SWEEP: {len(configs)} configs × {len(all_symbols)} symbols = {len(configs) * len(all_symbols)} runs")
    # Pre-aggregate per config
    agg_by_cfg = {ci: {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0, "gains": []} for ci in range(len(configs))}
    t0 = time.time()
    for si, sym in enumerate(all_symbols):
        data = load_symbol(sym)
        ts = data.get("timestamps", np.array([]))
        if len(ts) == 0: continue
        n = len(ts)
        close = arr(data, "close", n)
        pre = precompute_symbol(data, n, close)
        for ci, (thresh, lb, k_exh, cd, exit_mode, noloss) in enumerate(configs):
            cfg = {"threshold": thresh, "lookback": lb, "k_exhaustion": k_exh, "cooldown_bars": cd, "noloss": noloss}
            n_trades, wins, losses, pnl = run_spike_fade_fast(n, close, pre, cfg)
            agg = agg_by_cfg[ci]
            agg["trades"] += n_trades
            agg["wins"] += wins
            agg["losses"] += losses
            agg["pnl"] += pnl
            if n_trades > 0:
                agg["gains"].append(pnl / n_trades)
        del data
        elapsed = time.time() - t0
        rate = (si + 1) / elapsed
        log.info(f"[{si+1}/{len(all_symbols)}] {sym}: {len(configs)} configs in {elapsed:.0f}s ({rate:.1f} sym/sec, ETA {(len(all_symbols)-si-1)/rate:.0f}s)")
    results = []
    for ci, (thresh, lb, k_exh, cd, exit_mode, noloss) in enumerate(configs):
        cfg_name = f"t{thresh}_lb{lb}_k{k_exh}_cd{cd}_{exit_mode}_nl{'Y' if noloss else 'N'}"
        agg = agg_by_cfg[ci]
        total_trades = agg["trades"]
        wr = agg["wins"] / total_trades * 100 if total_trades > 0 else 0
        gains_arr = np.array(agg["gains"]) if agg["gains"] else np.array([0])
        sharpe = np.mean(gains_arr) / np.std(gains_arr) * np.sqrt(252) if np.std(gains_arr) > 0 and len(gains_arr) > 1 else 0
        row = {"config": cfg_name, "threshold": thresh, "lookback": lb, "k_exhaustion": k_exh, "cooldown": cd, "exit_mode": exit_mode, "noloss": noloss, "trades": total_trades, "wins": agg["wins"], "losses": agg["losses"], "wr": round(wr, 1), "pnl": round(agg["pnl"], 2), "sharpe": round(float(sharpe), 3), "avg_gain": round(float(np.mean(gains_arr)), 4) if len(gains_arr) > 0 else 0}
        results.append(row)
        if (ci + 1) % 50 == 0 or ci == 0:
            elapsed = time.time() - t0
            rate = (ci + 1) / elapsed
            eta = (len(configs) - ci - 1) / rate if rate > 0 else 0
            log.info(f"[{ci+1}/{len(configs)}] {cfg_name}: {total_trades} trades, WR={wr:.1f}%, PnL={agg['pnl']:+.1f}, Sharpe={sharpe:.3f} ({rate:.0f} configs/sec, ETA {eta:.0f}s)")
    elapsed = time.time() - t0
    log.info(f"DONE: {len(configs)} configs in {elapsed:.1f}s ({len(configs)/elapsed:.0f}/sec)")
    # Sort by Sharpe
    results.sort(key=lambda x: -x["sharpe"])
    # Print top 20
    print("\n" + "=" * 130)
    print(f"  CRYPTO SPIKE FADE SWEEP — {len(configs)} configs × {len(all_symbols)} symbols — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 130)
    print(f"  {'Config':<45s} {'Trades':>7s} {'WR%':>6s} {'PnL':>10s} {'Sharpe':>8s} {'AvgGain':>8s}")
    print("-" * 130)
    for r in results[:30]:
        print(f"  {r['config']:<45s} {r['trades']:>7d} {r['wr']:>5.1f}% {r['pnl']:>+9.2f} {r['sharpe']:>8.3f} {r['avg_gain']:>7.4f}%")
    print("-" * 130)
    print(f"  WORST 5:")
    for r in results[-5:]:
        print(f"  {r['config']:<45s} {r['trades']:>7d} {r['wr']:>5.1f}% {r['pnl']:>+9.2f} {r['sharpe']:>8.3f}")
    print("=" * 130)
    # Save CSV
    ts_str = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    csv_path = RESULTS_DIR / f"crypto_spike_fade_{ts_str}.csv"
    cols = ["config", "threshold", "lookback", "k_exhaustion", "cooldown", "exit_mode", "noloss", "trades", "wins", "losses", "wr", "pnl", "sharpe", "avg_gain"]
    with open(csv_path, "w") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(results)
    log.info(f"CSV: {csv_path}")
    print(f"\n  Results: {csv_path}")

if __name__ == "__main__":
    main()
