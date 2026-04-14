#!/usr/bin/env python3
"""Pre-compute tradier rankings into NPZ: tradeable_long/tradeable_short per bar.

Mirrors the REAL ranking logic from tradier_rankings.py:
- Linear regression slope on close prices (multi-TF weighted)
- SMA200 distance scoring
- BB pct_b as band score
- Weighted gains (exponential decay on returns)
- Relative volume weighting
- Cross-symbol normalization (log-signed)
- Top 25 by final_score_norm = tradeable LONG
- Bottom 25 by final_score_norm = tradeable SHORT

Usage:
    python precompute_rankings.py /path/to/npz/dir [--top-n 25] [--rank-every 12]
"""
import argparse, sys, time, math
from pathlib import Path
import numpy as np


def linreg_slope(arr, n=20):
    """Linear regression slope on last n values, normalized to % per bar.
    Same as ez_rankings.calculate_slope_and_rvalue but vectorized."""
    if len(arr) < n:
        return 0.0, 0.0
    y = arr[-n:]
    if y[0] == 0 or np.isnan(y).any():
        return 0.0, 0.0
    # Normalize to % change from first value
    y_norm = (y / y[0] - 1.0) * 100
    x = np.arange(n, dtype=np.float64)
    x_mean = x.mean()
    y_mean = y_norm.mean()
    ss_xy = np.sum((x - x_mean) * (y_norm - y_mean))
    ss_xx = np.sum((x - x_mean) ** 2)
    ss_yy = np.sum((y_norm - y_mean) ** 2)
    if ss_xx == 0:
        return 0.0, 0.0
    slope = ss_xy / ss_xx
    # R-value
    if ss_yy == 0:
        r = 0.0
    else:
        r = ss_xy / math.sqrt(ss_xx * ss_yy)
    return float(slope), abs(float(r))


def normalize_log_signed(val, global_min, global_max):
    """Same normalization as tradier_rankings.normalize_log_signed.
    Positive → log scale to [0, +100], negative → log scale to [-100, 0]."""
    if val > 0 and global_max > 0:
        return (math.log1p(val) / math.log1p(global_max)) * 100
    elif val < 0 and global_min < 0:
        return -(math.log1p(abs(val)) / math.log1p(abs(global_min))) * 100
    return 0.0


def weighted_gains(closes, n_bars, decay_factor=0.98):
    """Exponentially decayed returns — same as tradier_rankings.calculate_weighted_gains."""
    if len(closes) < n_bars + 1:
        return 0.0
    recent = closes[-(n_bars + 1):]
    returns = np.diff(recent) / recent[:-1]
    returns = returns[~np.isnan(returns)]
    if len(returns) == 0:
        return 0.0
    weights = np.array([decay_factor ** (len(returns) - i - 1) for i in range(len(returns))])
    weights = weights / weights.sum()
    return float((returns * weights).sum()) * 100


def score_symbol_at_bar(data, idx):
    """Score a symbol at bar idx using NPZ fields that mirror tradier_rankings.
    Returns final_score_raw_lt (before cross-symbol normalization)."""
    def g(key, default=0.0):
        arr = data.get(key)
        if arr is None:
            return default
        if idx >= len(arr):
            return default
        v = arr[idx]
        try:
            return float(v)
        except:
            return default

    def get_close_slice(tf_suffix, n):
        """Get last n close values ending at idx for a timeframe."""
        key = f"close_{tf_suffix}" if tf_suffix else "close"
        arr = data.get(key)
        if arr is None:
            return None
        start = max(0, idx - n + 1)
        end = idx + 1
        if end > len(arr):
            return None
        s = arr[start:end].astype(np.float64)
        if len(s) < n:
            return None
        return s

    # 1. TREND: Linear regression slope per TF (same weights as tradier_rankings)
    # D=200, 4h=150, 1h=200, 15m=200, 5m=300
    # Slope periods: D~20bars, 4h~20, 1h~20, 15m~20, 5m~20
    slopes = {}
    r_values = {}
    for tf, (period, weight) in [("D", (20, 200)), ("4h", (20, 150)), ("1h", (20, 200)),
                                   ("15m", (20, 200)), ("5m", (20, 300))]:
        cs = get_close_slice(tf, period)
        if cs is not None:
            s, r = linreg_slope(cs, period)
            slopes[tf] = s
            r_values[tf] = r
        else:
            slopes[tf] = 0.0
            r_values[tf] = 0.0

    trend_val_lt = sum(slopes[tf] * w for tf, (_, w) in
                       [("D", (20, 200)), ("4h", (20, 150)), ("1h", (20, 200)),
                        ("15m", (20, 200)), ("5m", (20, 300))])

    # Linearity multiplier from HTF R-values (D + 1h avg)
    htf_r = (r_values.get("D", 0) + r_values.get("1h", 0)) / 2.0
    linearity_mult = min(1.0 + (3.0 * htf_r), 4.0)

    # 2. SMA200 distance
    price = g("close")
    sma_1h = g("sma_200_1h")
    sma_score = 0.0
    if price > 0 and sma_1h > 0:
        sma_score = ((price - sma_1h) / sma_1h) * 100 * 15

    # 3. Band score from BB pct_b
    bb_1h = g("bb_pct_b_1h", 0.5)
    bb_D = g("bb_pct_b_D", 0.5)
    # Clamp crazy values (forward-fill artifacts)
    bb_1h = max(-2.0, min(2.0, bb_1h))
    bb_D = max(-2.0, min(2.0, bb_D))
    band_score = (bb_1h - 0.5) * 100 + (bb_D - 0.5) * 50

    # 4. Weighted gains
    close_d = get_close_slice("D", 46)
    wg_lt = weighted_gains(close_d, 45, 0.995) if close_d is not None else 0.0
    close_5m = get_close_slice("5m", 289)
    wg_st = weighted_gains(close_5m, 288, 0.98) if close_5m is not None else 0.0

    # 5. Relative volume factor
    rv_1h = g("relative_volume_1h", 1.0)
    rv_15m = g("relative_volume_15m", 1.0)
    rv_5m = g("relative_volume_5m", 1.0)
    rv_tot = (rv_1h * 2 + rv_15m * 8 + rv_5m * 14) / 24.0
    rv_factor = 1.4 if rv_tot > 1.5 else (1.1 if rv_tot > 1.1 else (0.8 if rv_tot < 0.85 else 1.0))

    # 6. Final score (mirrors tradier_rankings lines 2179)
    lt_raw = (trend_val_lt * linearity_mult) + sma_score + band_score * 0.5 + (wg_lt * 50)
    lt_final = lt_raw * rv_factor

    return lt_final


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("npz_dir", type=Path)
    ap.add_argument("--top-n", type=int, default=25, help="Top N = tradeable LONG, bottom N = tradeable SHORT")
    ap.add_argument("--rank-every", type=int, default=12, help="Rank every N bars (~1h at 5m base)")
    ap.add_argument("--mode", choices=["stocks", "crypto"], default="stocks")
    args = ap.parse_args()

    npz_dir = args.npz_dir
    if not npz_dir.is_dir():
        print(f"Not a directory: {npz_dir}"); sys.exit(1)

    t0 = time.time()
    files = sorted(npz_dir.glob("*.npz"))
    if args.mode == "stocks":
        files = [f for f in files if "USDT" not in f.stem and "USDC" not in f.stem]
    else:
        files = [f for f in files if "USDT" in f.stem or "USDC" in f.stem]
    print(f"Loading {len(files)} {args.mode} NPZ files...")

    symbols = {}
    all_timestamps = set()
    for f in files:
        try:
            d = dict(np.load(f, allow_pickle=True))
        except Exception as e:
            print(f"  SKIP {f.stem}: {e}")
            continue
        if "timestamps" not in d or "close" not in d:
            continue
        ts = d["timestamps"].astype(np.int64)
        n = len(ts)
        d["_ts"] = ts
        d["_n"] = n
        d["_path"] = f
        d["_tradeable_long"] = np.zeros(n, dtype=np.int8)
        d["_tradeable_short"] = np.zeros(n, dtype=np.int8)
        symbols[f.stem] = d
        all_timestamps.update(ts.tolist())

    # Count how many symbols share each timestamp (exact match)
    from collections import Counter
    ts_counts = Counter()
    for sym, data in symbols.items():
        for t in data["_ts"][300:]:  # skip first 300 bars (need history)
            ts_counts[int(t)] += 1

    # Keep timestamps with enough symbols for meaningful ranking (>= 10)
    min_coverage = 10
    good_ts = sorted([t for t, c in ts_counts.items() if c >= min_coverage])

    # Subsample to ~hourly (every 12 bars at 5min = 1h)
    RANK_INTERVAL = 3600  # seconds
    all_ts = []
    last_picked = -RANK_INTERVAL
    for t in good_ts:
        if t - last_picked >= RANK_INTERVAL:
            all_ts.append(t)
            last_picked = t
    print(f"Loaded {len(symbols)} symbols, {len(good_ts)} high-coverage timestamps, {len(all_ts)} ranking points [{time.time()-t0:.1f}s]")

    # Build fast lookup: symbol -> sorted timestamp array + searchsorted for nearest match
    sym_ts_arrays = {}
    for sym, data in symbols.items():
        sym_ts_arrays[sym] = data["_ts"]

    # Score and rank
    t1 = time.time()
    n_ranked = 0
    TOLERANCE = 600  # 10 minutes max gap for nearest-bar matching
    for bar_i in range(0, len(all_ts)):
        ts = all_ts[bar_i]
        scores = []
        for sym, data in symbols.items():
            ts_arr = sym_ts_arrays[sym]
            # Find nearest bar via searchsorted
            pos = np.searchsorted(ts_arr, ts)
            # Check both pos and pos-1 for closest
            best_idx = None
            best_dist = TOLERANCE + 1
            for candidate in [pos - 1, pos]:
                if 0 <= candidate < len(ts_arr):
                    dist = abs(int(ts_arr[candidate]) - ts)
                    if dist < best_dist:
                        best_dist = dist
                        best_idx = candidate
            if best_idx is None or best_dist > TOLERANCE or best_idx < 300:
                continue
            idx = best_idx
            score = score_symbol_at_bar(data, idx)
            if not math.isfinite(score):
                continue
            scores.append((sym, score))

        if len(scores) < 6:
            continue

        # Cross-symbol normalization (log-signed)
        raw_vals = [s[1] for s in scores]
        g_min, g_max = min(raw_vals), max(raw_vals)
        normalized = [(sym, normalize_log_signed(raw, g_min, g_max)) for sym, raw in scores]

        # Sort by normalized score
        normalized.sort(key=lambda x: x[1], reverse=True)

        # Top N = tradeable LONG, bottom N = tradeable SHORT
        # Scale N proportionally when fewer symbols available
        effective_n = min(args.top_n, max(1, len(scores) // 4))
        top_long = set(s for s, _ in normalized[:effective_n])
        top_short = set(s for s, _ in normalized[-effective_n:])

        # Fill forward until next ranking — mark all bars in this symbol's own ts array
        ts_lo = all_ts[bar_i]
        ts_hi = ts_lo + RANK_INTERVAL  # 1 hour forward

        for sym in top_long:
            data = symbols[sym]
            ts_arr = sym_ts_arrays[sym]
            lo = np.searchsorted(ts_arr, ts_lo - TOLERANCE)
            hi = np.searchsorted(ts_arr, ts_hi + TOLERANCE, side='right')
            data["_tradeable_long"][lo:hi] = 1

        for sym in top_short:
            data = symbols[sym]
            ts_arr = sym_ts_arrays[sym]
            lo = np.searchsorted(ts_arr, ts_lo - TOLERANCE)
            hi = np.searchsorted(ts_arr, ts_hi + TOLERANCE, side='right')
            data["_tradeable_short"][lo:hi] = 1

        n_ranked += 1
        if n_ranked % 2000 == 0:
            pct = bar_i * 100 // len(all_ts)
            print(f"  [{pct}%] {n_ranked} rankings, top_long={list(top_long)[:5]}, top_short={list(top_short)[:5]}")

    print(f"Computed {n_ranked} rankings [{time.time()-t1:.1f}s]")

    # Save back to NPZ files
    t2 = time.time()
    for i, (sym, data) in enumerate(symbols.items()):
        data["tradeable_long"] = data.pop("_tradeable_long")
        data["tradeable_short"] = data.pop("_tradeable_short")
        path = data.pop("_path")
        data.pop("_ts")
        data.pop("_n")
        np.savez_compressed(path, **data)
        if (i + 1) % 50 == 0:
            print(f"  Saved {i+1}/{len(symbols)}...")

    # Stats
    tl = sum(int(d["tradeable_long"].sum()) for d in symbols.values())
    ts_total = sum(len(d["tradeable_long"]) for d in symbols.values())
    ts_s = sum(int(d["tradeable_short"].sum()) for d in symbols.values())
    print(f"\nDone in {time.time()-t0:.1f}s")
    print(f"Tradeable LONG bars:  {tl}/{ts_total} ({tl*100/max(1,ts_total):.1f}%)")
    print(f"Tradeable SHORT bars: {ts_s}/{ts_total} ({ts_s*100/max(1,ts_total):.1f}%)")
    print(f"Expected: ~{args.top_n}/{len(symbols)} = {args.top_n*100/max(1,len(symbols)):.0f}% tradeable")


if __name__ == "__main__":
    main()
