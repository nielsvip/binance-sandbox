"""
test_delta_tradier.py — Delta Speed Engine Backtest for Stocks
==============================================================
Loads precomputed NPZ indicators, runs the DeltaTracker vectorized engine
(compute_npz_signals) on 13 stock symbols, sweeps config variations,
and compares vs WT/DC score-based entry/exit.

Usage:
    python3 test_delta_tradier.py
"""
import numpy as np
import os
import sys
import time
from datetime import datetime, timezone
from itertools import product

BASE_PATH = "/Users/niels/Documents/binance"
NPZ_DIR = os.path.join(BASE_PATH, "backtest_v8", "indicators")

SYMBOLS = ["MU", "AAPL", "TTD", "FIVN", "AMZN", "MRVL", "XOM", "CVX", "GLD", "USO", "NVDA", "MSFT", "ASTS"]

# Stock TFs — NPZ has 5m not 3m
STOCK_TFS = ["5m", "15m", "1h", "4h", "D"]

# Sweep grid
ENTRY_MIN_TF_VALS = [3, 4, 5]
EXIT_DECAY_RATIO_VALS = [0.10, 0.15, 0.20, 0.25]
Z_THRESHOLD_VALS = [1.0, 1.5, 2.0]

# Baseline WT/DC scorer thresholds
SCORE_ENTRY_THRESHOLD = 37
SCORE_EXIT_THRESHOLD = 20

# Trading hours filter (UTC)
MARKET_OPEN_H, MARKET_OPEN_M = 13, 30
MARKET_CLOSE_H, MARKET_CLOSE_M = 20, 0

# Start date
START_TS = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp())


def load_symbol(symbol):
    """Load NPZ and return dict of arrays + metadata."""
    path = os.path.join(NPZ_DIR, f"{symbol}.npz")
    if not os.path.exists(path):
        print(f"  SKIP {symbol}: NPZ not found")
        return None
    d = dict(np.load(path, allow_pickle=True))
    return d


def build_market_hours_mask(timestamps):
    """Boolean mask: True if bar is during market hours (13:30-20:00 UTC, weekdays)."""
    n = len(timestamps)
    mask = np.zeros(n, dtype=bool)
    for i in range(n):
        dt = datetime.utcfromtimestamp(int(timestamps[i]))
        wd = dt.weekday()
        if wd >= 5:  # weekend
            continue
        t_min = dt.hour * 60 + dt.minute
        open_min = MARKET_OPEN_H * 60 + MARKET_OPEN_M
        close_min = MARKET_CLOSE_H * 60 + MARKET_CLOSE_M
        if open_min <= t_min < close_min:
            mask[i] = True
    return mask


def build_market_hours_mask_fast(timestamps):
    """Vectorized market hours mask."""
    # Convert to datetime components using numpy
    ts = timestamps.astype(np.int64)
    # Use a vectorized approach: compute seconds-of-day and day-of-week
    # epoch 0 = Thursday (1970-01-01)
    days_since_epoch = ts // 86400
    secs_of_day = ts % 86400
    # day of week: 0=Mon ... 6=Sun. epoch day 0 = Thursday = 3
    dow = (days_since_epoch + 3) % 7  # 0=Mon, 1=Tue, ..., 4=Fri, 5=Sat, 6=Sun
    mins_of_day = secs_of_day // 60
    open_min = MARKET_OPEN_H * 60 + MARKET_OPEN_M
    close_min = MARKET_CLOSE_H * 60 + MARKET_CLOSE_M
    mask = (dow < 5) & (mins_of_day >= open_min) & (mins_of_day < close_min)
    return mask


def compute_delta_signals(d, cfg):
    """
    Vectorized delta computation adapted for stock NPZ (5m bars, stock TFs).
    Returns entry/exit signal arrays.
    """
    keys = list(d.keys())
    wt_dc_keys = sorted([k for k in keys if k.startswith("wt") or k.startswith("dc")])
    n = len(d["close"])

    tw = cfg.get("tf_weights", {"5m": 3.0, "15m": 2.0, "1h": 1.0, "4h": 1.0, "D": 0.5})
    smooth = cfg.get("speed_smooth", 3)
    accel_lb = cfg.get("accel_lookback", 5)

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

    # Z-score normalize per TF
    tf_bull_z, tf_bear_z = {}, {}
    for tf in STOCK_TFS:
        m, s = np.mean(tf_bull[tf]), np.std(tf_bull[tf])
        tf_bull_z[tf] = (tf_bull[tf] - m) / s if s > 1e-10 else np.zeros(n)
        m, s = np.mean(tf_bear[tf]), np.std(tf_bear[tf])
        tf_bear_z[tf] = (tf_bear[tf] - m) / s if s > 1e-10 else np.zeros(n)

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
    zt = cfg.get("tf_z_threshold", 1.0)
    bull_tf_count = np.zeros(n, dtype=np.int8)
    bear_tf_count = np.zeros(n, dtype=np.int8)
    for tf in STOCK_TFS:
        bull_tf_count += (tf_bull_z[tf] > zt).astype(np.int8)
        bear_tf_count += (tf_bear_z[tf] > zt).astype(np.int8)

    entry_min_tf = cfg.get("entry_min_tf", 4)
    entry_z = cfg.get("entry_z_threshold", 2.0)
    entry_accel = cfg.get("entry_accel_threshold", 0.3)

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


def compute_score_signals(d):
    """
    Baseline WT/DC scorer: composite long/short scores.
    Entry when score > threshold, exit when opposite score > exit threshold.
    Uses wt_composite_long / wt_composite_short if available,
    else sums wt_score across TFs.
    """
    n = len(d["close"])
    if "wt_composite_long" in d and "wt_composite_short" in d:
        long_score = d["wt_composite_long"].astype(np.float64)
        short_score = d["wt_composite_short"].astype(np.float64)
    else:
        long_score = np.zeros(n)
        short_score = np.zeros(n)
        for tf in STOCK_TFS:
            k = f"wt_score_{tf}"
            if k in d:
                s = d[k].astype(np.float64)
                pos = np.maximum(s, 0)
                neg = np.maximum(-s, 0)
                long_score += pos
                short_score += neg
    return {
        "entry_long": long_score > SCORE_ENTRY_THRESHOLD,
        "entry_short": short_score > SCORE_ENTRY_THRESHOLD,
        "long_score": long_score,
        "short_score": short_score,
    }


def simulate_trades(close, timestamps, entry_long, entry_short, exit_decay_ratio, signals, market_mask, start_mask):
    """
    Simulate long/short trades from delta signals.
    Entry: signal fires during market hours.
    Exit: speed decays to exit_decay_ratio of peak speed, OR next signal in opposite direction.

    Returns list of (entry_ts, exit_ts, side, entry_price, exit_price, pnl_pct).
    """
    n = len(close)
    trades = []
    in_trade = False
    side = None
    entry_price = 0.0
    entry_idx = 0
    peak_speed = 0.0

    total_bull_z = signals["total_bull_z"]
    total_bear_z = signals["total_bear_z"]

    for i in range(n):
        if not start_mask[i]:
            continue
        if not market_mask[i]:
            # Force exit at market close
            if in_trade:
                pnl = (close[i] / entry_price - 1) * (1 if side == "L" else -1)
                trades.append((timestamps[entry_idx], timestamps[i], side, entry_price, close[i], pnl * 100))
                in_trade = False
            continue

        if in_trade:
            # Track peak speed
            cur_spd = total_bull_z[i] if side == "L" else total_bear_z[i]
            if cur_spd > peak_speed:
                peak_speed = cur_spd

            # Exit conditions
            speed_decayed = peak_speed > 0.5 and cur_spd < peak_speed * exit_decay_ratio
            opposite_entry = entry_short[i] if side == "L" else entry_long[i]

            if speed_decayed or opposite_entry:
                pnl = (close[i] / entry_price - 1) * (1 if side == "L" else -1)
                trades.append((timestamps[entry_idx], timestamps[i], side, entry_price, close[i], pnl * 100))
                in_trade = False
                # Allow immediate re-entry on opposite signal
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


def simulate_score_trades(close, timestamps, score_signals, market_mask, start_mask):
    """
    Simulate trades using WT/DC composite score entry/exit.
    Entry: score > SCORE_ENTRY_THRESHOLD.
    Exit: opposite score > SCORE_EXIT_THRESHOLD, or market close.
    """
    n = len(close)
    trades = []
    in_trade = False
    side = None
    entry_price = 0.0
    entry_idx = 0

    long_score = score_signals["long_score"]
    short_score = score_signals["short_score"]

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
            # Exit on opposite score
            if side == "L" and short_score[i] > SCORE_EXIT_THRESHOLD:
                pnl = (close[i] / entry_price - 1) * (1 if side == "L" else -1)
                trades.append((timestamps[entry_idx], timestamps[i], side, entry_price, close[i], pnl * 100))
                in_trade = False
            elif side == "S" and long_score[i] > SCORE_EXIT_THRESHOLD:
                pnl = (close[i] / entry_price - 1) * (1 if side == "S" else -1)
                trades.append((timestamps[entry_idx], timestamps[i], side, entry_price, close[i], pnl * 100))
                in_trade = False
        else:
            if score_signals["entry_long"][i]:
                in_trade = True
                side = "L"
                entry_price = close[i]
                entry_idx = i
            elif score_signals["entry_short"][i]:
                in_trade = True
                side = "S"
                entry_price = close[i]
                entry_idx = i

    return trades


def weekly_sharpe(trades, annualize=True):
    """Compute weekly Sharpe from trade list. Returns (sharpe, n_trades, total_pnl, win_rate)."""
    if not trades:
        return 0.0, 0, 0.0, 0.0

    # Bucket PnL by week
    weekly_pnl = {}
    for t in trades:
        entry_ts = int(t[0])
        week = datetime.utcfromtimestamp(entry_ts).isocalendar()[:2]
        weekly_pnl.setdefault(week, 0.0)
        weekly_pnl[week] += t[5]

    if len(weekly_pnl) < 2:
        total = sum(t[5] for t in trades)
        wins = sum(1 for t in trades if t[5] > 0)
        return 0.0, len(trades), total, wins / len(trades) if trades else 0.0

    returns = np.array(list(weekly_pnl.values()))
    mu = np.mean(returns)
    sigma = np.std(returns)
    sharpe = mu / sigma if sigma > 1e-10 else 0.0
    if annualize:
        sharpe *= np.sqrt(52)

    total = sum(t[5] for t in trades)
    wins = sum(1 for t in trades if t[5] > 0)
    wr = wins / len(trades) if trades else 0.0
    return sharpe, len(trades), total, wr


def run_backtest():
    t0 = time.time()
    print("=" * 90)
    print("DELTA SPEED ENGINE — Stock Backtest")
    print("=" * 90)
    print(f"Symbols: {', '.join(SYMBOLS)}")
    print(f"NPZ dir: {NPZ_DIR}")
    print(f"Bars: 5-minute | Market hours: 13:30-20:00 UTC | Start: 2024-01-01")
    print()

    # Pre-load all symbols
    print("Loading NPZ files...")
    sym_data = {}
    for sym in SYMBOLS:
        d = load_symbol(sym)
        if d is not None:
            sym_data[sym] = d
            print(f"  {sym}: {len(d['close']):,} bars loaded")
    print(f"  {len(sym_data)} symbols loaded in {time.time()-t0:.1f}s")
    print()

    # Pre-compute market hours masks and start masks
    print("Computing market hours masks...")
    masks = {}
    start_masks = {}
    for sym, d in sym_data.items():
        ts = d["timestamps"]
        masks[sym] = build_market_hours_mask_fast(ts)
        start_masks[sym] = ts >= START_TS
        mh_bars = np.sum(masks[sym] & start_masks[sym])
        print(f"  {sym}: {mh_bars:,} market-hours bars after 2024-01-01")
    print()

    # ===== DELTA ENGINE SWEEP =====
    configs = list(product(ENTRY_MIN_TF_VALS, EXIT_DECAY_RATIO_VALS, Z_THRESHOLD_VALS))
    print(f"Delta sweep: {len(configs)} configs x {len(sym_data)} symbols = {len(configs)*len(sym_data)} runs")
    print()

    # Store results: (cfg_tuple) -> {sym -> (sharpe, n_trades, total_pnl, wr)}
    delta_results = {}

    for ci, (min_tf, decay, zt) in enumerate(configs):
        cfg = {
            "tf_weights": {"5m": 3.0, "15m": 2.0, "1h": 1.0, "4h": 1.0, "D": 0.5},
            "entry_min_tf": min_tf,
            "entry_z_threshold": zt,
            "entry_accel_threshold": 0.3,
            "tf_z_threshold": zt,
            "speed_smooth": 3,
            "accel_lookback": 5,
        }
        cfg_key = (min_tf, decay, zt)
        delta_results[cfg_key] = {}

        for sym, d in sym_data.items():
            signals = compute_delta_signals(d, cfg)
            close = d["close"].astype(np.float64)
            ts = d["timestamps"]

            trades = simulate_trades(
                close, ts,
                signals["entry_long"], signals["entry_short"],
                decay, signals,
                masks[sym], start_masks[sym],
            )
            sharpe, n_trades, total_pnl, wr = weekly_sharpe(trades)
            delta_results[cfg_key][sym] = (sharpe, n_trades, total_pnl, wr)

        if (ci + 1) % 6 == 0 or ci == len(configs) - 1:
            print(f"  Delta sweep: {ci+1}/{len(configs)} configs done ({time.time()-t0:.0f}s)")

    # ===== SCORE BASELINE =====
    print()
    print("Running WT/DC score baseline (entry>37, exit>20)...")
    score_results = {}
    for sym, d in sym_data.items():
        score_sig = compute_score_signals(d)
        close = d["close"].astype(np.float64)
        ts = d["timestamps"]
        trades = simulate_score_trades(close, ts, score_sig, masks[sym], start_masks[sym])
        sharpe, n_trades, total_pnl, wr = weekly_sharpe(trades)
        score_results[sym] = (sharpe, n_trades, total_pnl, wr)
        print(f"  {sym}: Sharpe={sharpe:.2f}, trades={n_trades}, PnL={total_pnl:.2f}%, WR={wr:.1%}")

    # ===== RESULTS TABLE =====
    print()
    print("=" * 90)
    print("DELTA ENGINE RESULTS — Top 15 configs by portfolio Sharpe")
    print("=" * 90)

    # Rank configs by average Sharpe across symbols
    ranked = []
    for cfg_key, sym_res in delta_results.items():
        sharpes = [v[0] for v in sym_res.values()]
        avg_sharpe = np.mean(sharpes) if sharpes else 0
        total_trades = sum(v[1] for v in sym_res.values())
        total_pnl = sum(v[2] for v in sym_res.values())
        avg_wr = np.mean([v[3] for v in sym_res.values() if v[1] > 0]) if any(v[1] > 0 for v in sym_res.values()) else 0
        ranked.append((avg_sharpe, cfg_key, total_trades, total_pnl, avg_wr, sym_res))

    ranked.sort(key=lambda x: -x[0])

    print(f"{'Rank':<5} {'MinTF':<6} {'Decay':<7} {'Zthresh':<8} {'AvgSharpe':>10} {'Trades':>7} {'TotalPnL%':>10} {'AvgWR':>7}")
    print("-" * 65)
    for i, (avg_sh, cfg_key, tot_tr, tot_pnl, avg_wr, _) in enumerate(ranked[:15]):
        min_tf, decay, zt = cfg_key
        print(f"{i+1:<5} {min_tf:<6} {decay:<7.2f} {zt:<8.1f} {avg_sh:>10.2f} {tot_tr:>7} {tot_pnl:>10.2f} {avg_wr:>7.1%}")

    # ===== PER-SYMBOL DETAIL for best config =====
    if ranked:
        best = ranked[0]
        best_cfg = best[1]
        best_sym_res = best[5]
        print()
        print(f"BEST CONFIG: entry_min_tf={best_cfg[0]}, exit_decay={best_cfg[1]}, z_thresh={best_cfg[2]}")
        print(f"{'Symbol':<8} {'Sharpe':>8} {'Trades':>7} {'PnL%':>9} {'WinRate':>8}")
        print("-" * 45)
        for sym in SYMBOLS:
            if sym in best_sym_res:
                sh, nt, pnl, wr = best_sym_res[sym]
                print(f"{sym:<8} {sh:>8.2f} {nt:>7} {pnl:>9.2f} {wr:>8.1%}")

    # ===== SCORE BASELINE SUMMARY =====
    print()
    print("=" * 90)
    print("WT/DC SCORE BASELINE (entry>37, exit>20)")
    print("=" * 90)
    print(f"{'Symbol':<8} {'Sharpe':>8} {'Trades':>7} {'PnL%':>9} {'WinRate':>8}")
    print("-" * 45)
    score_sharpes = []
    for sym in SYMBOLS:
        if sym in score_results:
            sh, nt, pnl, wr = score_results[sym]
            print(f"{sym:<8} {sh:>8.2f} {nt:>7} {pnl:>9.2f} {wr:>8.1%}")
            score_sharpes.append(sh)

    score_avg = np.mean(score_sharpes) if score_sharpes else 0
    print(f"{'AVG':<8} {score_avg:>8.2f}")

    # ===== HEAD-TO-HEAD =====
    print()
    print("=" * 90)
    print("HEAD-TO-HEAD: Delta Engine vs WT/DC Score")
    print("=" * 90)
    if ranked:
        best_delta_sharpe = ranked[0][0]
        best_cfg = ranked[0][1]
        print(f"  Delta Engine best avg Sharpe : {best_delta_sharpe:.2f} (min_tf={best_cfg[0]}, decay={best_cfg[1]}, z={best_cfg[2]})")
        print(f"  WT/DC Score avg Sharpe       : {score_avg:.2f}")
        print()
        if best_delta_sharpe > score_avg:
            print(f"  >>> DELTA ENGINE WINS by {best_delta_sharpe - score_avg:.2f} Sharpe points <<<")
        elif score_avg > best_delta_sharpe:
            print(f"  >>> WT/DC SCORE WINS by {score_avg - best_delta_sharpe:.2f} Sharpe points <<<")
        else:
            print(f"  >>> TIE <<<")

        # Per-symbol comparison
        print()
        print(f"  {'Symbol':<8} {'Delta':>8} {'Score':>8} {'Winner':>8}")
        print(f"  {'-'*36}")
        delta_wins = 0
        score_wins = 0
        for sym in SYMBOLS:
            d_sh = best_sym_res.get(sym, (0,))[0] if sym in best_sym_res else 0
            s_sh = score_results.get(sym, (0,))[0] if sym in score_results else 0
            winner = "DELTA" if d_sh > s_sh else ("SCORE" if s_sh > d_sh else "TIE")
            if d_sh > s_sh:
                delta_wins += 1
            elif s_sh > d_sh:
                score_wins += 1
            print(f"  {sym:<8} {d_sh:>8.2f} {s_sh:>8.2f} {winner:>8}")
        print(f"\n  Delta wins: {delta_wins}/{len(SYMBOLS)}, Score wins: {score_wins}/{len(SYMBOLS)}")

    print(f"\nTotal runtime: {time.time()-t0:.1f}s")


if __name__ == "__main__":
    run_backtest()
