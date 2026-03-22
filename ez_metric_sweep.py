# pylint: disable=W,C,R,I
"""ez_metric_sweep — Test every indicator metric × every TF as entry/exit signal.
Finds combos with Sharpe >= 75 (annualized). Runs on all symbols with klines data.
Uses vectorized numpy for speed. Outputs ranked results to data/metric_sweep_results.json.
Usage: python ez_metric_sweep.py [--min-sharpe 75] [--symbols 50] [--workers 8]
"""
import json
import math
import os
import sys
import time
import signal
import multiprocessing as mp
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import numpy as np

# Use binance-sandbox for backtesting (more data, no interference with live)
SANDBOX_PATH = Path("/home/niels/binance-sandbox" if os.path.exists("/home/niels/binance-sandbox") else "/Users/niels/Documents/binance")
LIVE_PATH = Path("/home/niels/binance" if os.path.exists("/home/niels/binance") else "/Users/niels/Documents/binance")
BASE_PATH = LIVE_PATH
KLINES_DIR = SANDBOX_PATH / "klines_cache"  # Use sandbox klines for testing
DATA_DIR = BASE_PATH / "data"
RESULTS_FILE = DATA_DIR / "metric_sweep_results.json"
TOP_FILE = DATA_DIR / "metric_sweep_top.json"
ANNUAL_BARS = {"1m": 525600, "3m": 175200, "5m": 105120, "15m": 35040, "1h": 8760, "4h": 2190, "D": 365, "W": 52, "M": 12}
FEE_PCT = 0.08  # Round-trip fee 0.08%
TFS = ["15m", "1h", "4h", "D"]  # >= 15m only per user request
# We test every metric as entry signal. These are the indicators we can compute from OHLCV.
shutdown = mp.Event()


def load_klines(symbol: str, tf: str) -> Optional[np.ndarray]:
    """Load klines as structured numpy array. Returns None if insufficient data."""
    path = KLINES_DIR / f"{symbol}_{tf}.json"
    if not path.exists():
        return None
    try:
        bars = json.loads(path.read_text())
        if not isinstance(bars, list) or len(bars) < 50:
            return None
        o = np.array([float(b["open"]) for b in bars], dtype=np.float64)
        h = np.array([float(b["high"]) for b in bars], dtype=np.float64)
        lo = np.array([float(b["low"]) for b in bars], dtype=np.float64)
        c = np.array([float(b["close"]) for b in bars], dtype=np.float64)
        v = np.array([float(b.get("volume", 0)) for b in bars], dtype=np.float64)
        return np.column_stack([o, h, lo, c, v])
    except Exception:
        return None


def ema(arr: np.ndarray, period: int) -> np.ndarray:
    result = np.empty_like(arr)
    result[:] = np.nan
    if len(arr) < period:
        return result
    mult = 2.0 / (period + 1)
    result[period - 1] = np.mean(arr[:period])
    for i in range(period, len(arr)):
        result[i] = arr[i] * mult + result[i - 1] * (1 - mult)
    return result


def sma(arr: np.ndarray, period: int) -> np.ndarray:
    if len(arr) < period:
        return np.full_like(arr, np.nan)
    cum = np.cumsum(arr)
    cum[period:] = cum[period:] - cum[:-period]
    result = np.full_like(arr, np.nan)
    result[period - 1:] = cum[period - 1:] / period
    return result


def stochastic(h: np.ndarray, lo: np.ndarray, c: np.ndarray, k_period: int = 14, d_period: int = 3) -> Tuple[np.ndarray, np.ndarray]:
    n = len(c)
    k = np.full(n, np.nan)
    for i in range(k_period - 1, n):
        hh = np.max(h[i - k_period + 1:i + 1])
        ll = np.min(lo[i - k_period + 1:i + 1])
        k[i] = (c[i] - ll) / (hh - ll) * 100 if hh != ll else 50.0
    d = sma(k, d_period)
    return k, d


def rsi(c: np.ndarray, period: int = 14) -> np.ndarray:
    delta = np.diff(c, prepend=c[0])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = ema(gain, period)
    avg_loss = ema(loss, period)
    rs = np.where(avg_loss > 0, avg_gain / avg_loss, 100.0)
    return 100 - 100 / (1 + rs)


def atr(h: np.ndarray, lo: np.ndarray, c: np.ndarray, period: int = 14) -> np.ndarray:
    tr = np.maximum(h - lo, np.maximum(np.abs(h - np.roll(c, 1)), np.abs(lo - np.roll(c, 1))))
    tr[0] = h[0] - lo[0]
    return ema(tr, period)


def donchian(h: np.ndarray, lo: np.ndarray, period: int = 20) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    n = len(h)
    dc_h = np.full(n, np.nan)
    dc_l = np.full(n, np.nan)
    for i in range(period - 1, n):
        dc_h[i] = np.max(h[i - period + 1:i + 1])
        dc_l[i] = np.min(lo[i - period + 1:i + 1])
    dc_mid = (dc_h + dc_l) / 2
    return dc_h, dc_l, dc_mid


def bollinger(c: np.ndarray, period: int = 20, std_mult: float = 2.0) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    mid = sma(c, period)
    std = np.full_like(c, np.nan)
    for i in range(period - 1, len(c)):
        std[i] = np.std(c[i - period + 1:i + 1])
    upper = mid + std_mult * std
    lower = mid - std_mult * std
    return upper, mid, lower


def heikin_ashi(o: np.ndarray, h: np.ndarray, lo: np.ndarray, c: np.ndarray) -> np.ndarray:
    """Returns 1 for green, -1 for red."""
    ha_c = (o + h + lo + c) / 4
    ha_o = np.empty_like(o)
    ha_o[0] = (o[0] + c[0]) / 2
    for i in range(1, len(o)):
        ha_o[i] = (ha_o[i - 1] + ha_c[i - 1]) / 2
    return np.where(ha_c >= ha_o, 1, -1)


def mfi(h: np.ndarray, lo: np.ndarray, c: np.ndarray, v: np.ndarray, period: int = 14) -> np.ndarray:
    tp = (h + lo + c) / 3
    mf = tp * v
    n = len(c)
    result = np.full(n, np.nan)
    for i in range(period, n):
        pos = sum(mf[j] for j in range(i - period + 1, i + 1) if tp[j] > tp[j - 1])
        neg = sum(mf[j] for j in range(i - period + 1, i + 1) if tp[j] < tp[j - 1])
        result[i] = 100 - 100 / (1 + pos / neg) if neg > 0 else 100.0
    return result


def relative_volume(v: np.ndarray, period: int = 20) -> np.ndarray:
    avg = sma(v, period)
    return np.where(avg > 0, v / avg, 1.0)


def compute_all_metrics(data: np.ndarray) -> Dict[str, np.ndarray]:
    """Compute all indicators from OHLCV data. Returns dict of metric_name → array."""
    o, h, lo, c, v = data[:, 0], data[:, 1], data[:, 2], data[:, 3], data[:, 4]
    n = len(c)
    metrics = {}
    # Stochastic
    k14, d14 = stochastic(h, lo, c, 14, 3)
    k9, d9 = stochastic(h, lo, c, 9, 3)
    k21, d21 = stochastic(h, lo, c, 21, 3)
    metrics["stoch_k14"] = k14
    metrics["stoch_d14"] = d14
    metrics["stoch_k9"] = k9
    metrics["stoch_d9"] = d9
    metrics["stoch_k21"] = k21
    metrics["stoch_d21"] = d21
    metrics["stoch_kd_diff"] = k14 - d14
    metrics["stoch_k_slope"] = np.concatenate([[0], np.diff(k14)])
    metrics["stoch_crossover"] = np.where((np.roll(k14, 1) <= np.roll(d14, 1)) & (k14 > d14), 1, 0).astype(float)
    metrics["stoch_crossunder"] = np.where((np.roll(k14, 1) >= np.roll(d14, 1)) & (k14 < d14), 1, 0).astype(float)
    # RSI
    rsi14 = rsi(c, 14)
    rsi9 = rsi(c, 9)
    metrics["rsi14"] = rsi14
    metrics["rsi9"] = rsi9
    metrics["rsi_slope"] = np.concatenate([[0], np.diff(rsi14)])
    # ATR
    atr14 = atr(h, lo, c, 14)
    metrics["atr14"] = atr14
    metrics["atr_pct"] = np.where(c > 0, atr14 / c * 100, 0)
    metrics["atr_ratio"] = np.where(sma(atr14, 50) > 0, atr14 / sma(atr14, 50), 1.0)
    # Donchian
    dc_h, dc_l, dc_mid = donchian(h, lo, 20)
    metrics["dc_pct"] = np.where((dc_h - dc_l) > 0, (c - dc_l) / (dc_h - dc_l), 0.5)
    metrics["dc_high_cross"] = np.where(c > dc_h, 1, 0).astype(float)
    metrics["dc_low_cross"] = np.where(c < dc_l, 1, 0).astype(float)
    metrics["dc_basis_cross"] = np.where((np.roll(c, 1) <= dc_mid) & (c > dc_mid), 1, 0).astype(float)
    metrics["dc_basis_crossunder"] = np.where((np.roll(c, 1) >= dc_mid) & (c < dc_mid), 1, 0).astype(float)
    # Bollinger
    bb_u, bb_m, bb_l = bollinger(c, 20, 2.0)
    metrics["bb_pct"] = np.where((bb_u - bb_l) > 0, (c - bb_l) / (bb_u - bb_l), 0.5)
    metrics["bb_squeeze"] = np.where(bb_m > 0, (bb_u - bb_l) / bb_m, 0)
    # EMA / SMA
    ema20 = ema(c, 20)
    sma200 = sma(c, 200)
    metrics["ema20_dist"] = np.where(ema20 > 0, (c - ema20) / ema20 * 100, 0)
    metrics["sma200_dist"] = np.where(sma200 > 0, (c - sma200) / sma200 * 100, 0)
    metrics["ema20_slope"] = np.concatenate([[0], np.diff(ema20)])
    metrics["price_above_ema20"] = np.where(c > ema20, 1, 0).astype(float)
    metrics["price_above_sma200"] = np.where(c > sma200, 1, 0).astype(float)
    # Heikin-Ashi
    ha = heikin_ashi(o, h, lo, c)
    metrics["ha_green"] = np.where(ha == 1, 1, 0).astype(float)
    metrics["ha_red"] = np.where(ha == -1, 1, 0).astype(float)
    ha_streak = np.zeros(n)
    for i in range(1, n):
        if ha[i] == ha[i - 1]:
            ha_streak[i] = ha_streak[i - 1] + ha[i]
        else:
            ha_streak[i] = ha[i]
    metrics["ha_streak"] = ha_streak
    # MFI
    metrics["mfi14"] = mfi(h, lo, c, v, 14)
    # Volume
    metrics["rel_vol"] = relative_volume(v, 20)
    metrics["vol_surge"] = np.where(relative_volume(v, 20) > 1.5, 1, 0).astype(float)
    # Price action
    metrics["candle_body_pct"] = np.where((h - lo) > 0, np.abs(c - o) / (h - lo), 0)
    metrics["upper_wick_pct"] = np.where((h - lo) > 0, (h - np.maximum(o, c)) / (h - lo), 0)
    metrics["lower_wick_pct"] = np.where((h - lo) > 0, (np.minimum(o, c) - lo) / (h - lo), 0)
    metrics["green_candle"] = np.where(c > o, 1, 0).astype(float)
    metrics["momentum_3bar"] = np.where(np.roll(c, 3) > 0, (c - np.roll(c, 3)) / np.roll(c, 3) * 100, 0)
    metrics["momentum_5bar"] = np.where(np.roll(c, 5) > 0, (c - np.roll(c, 5)) / np.roll(c, 5) * 100, 0)
    # Engulfing patterns
    body_curr = c - o
    body_prev = np.roll(c, 1) - np.roll(o, 1)
    metrics["bull_engulf"] = np.where((body_curr > 0) & (body_prev < 0) & (np.abs(body_curr) > np.abs(body_prev) * 1.1), 1, 0).astype(float)
    metrics["bear_engulf"] = np.where((body_curr < 0) & (body_prev > 0) & (np.abs(body_curr) > np.abs(body_prev) * 1.1), 1, 0).astype(float)
    # Pin bars
    metrics["bull_pin"] = np.where((metrics["lower_wick_pct"] > 0.6) & (metrics["candle_body_pct"] < 0.25), 1, 0).astype(float)
    metrics["bear_pin"] = np.where((metrics["upper_wick_pct"] > 0.6) & (metrics["candle_body_pct"] < 0.25), 1, 0).astype(float)
    return metrics


def backtest_metric_threshold(c: np.ndarray, metric: np.ndarray, direction: str, operator: str, threshold: float, tf: str, hold_bars: int = 5) -> Dict:
    """Test a single metric threshold as entry signal. Returns stats dict."""
    n = len(c)
    warmup = 50
    if n < warmup + hold_bars + 10:
        return None
    # Generate entry signals
    if operator == ">":
        entries = metric > threshold
    elif operator == "<":
        entries = metric < threshold
    elif operator == "cross_above":
        entries = (np.roll(metric, 1) <= threshold) & (metric > threshold)
    elif operator == "cross_below":
        entries = (np.roll(metric, 1) >= threshold) & (metric < threshold)
    elif operator == "==1":
        entries = metric == 1.0
    else:
        return None
    entries[:warmup] = False
    entries[-(hold_bars + 1):] = False
    # Remove consecutive signals (only first)
    for i in range(1, n):
        if entries[i] and entries[i - 1]:
            entries[i] = False
    entry_indices = np.where(entries)[0]
    if len(entry_indices) < 5:
        return None
    # Calculate returns
    returns = []
    for idx in entry_indices:
        exit_idx = min(idx + hold_bars, n - 1)
        if direction == "LONG":
            ret = (c[exit_idx] - c[idx]) / c[idx] * 100 - FEE_PCT
        else:
            ret = (c[idx] - c[exit_idx]) / c[idx] * 100 - FEE_PCT
        returns.append(ret)
    returns = np.array(returns)
    if len(returns) < 5:
        return None
    mean_ret = np.mean(returns)
    std_ret = np.std(returns)
    if std_ret <= 0:
        return None
    annual_factor = ANNUAL_BARS.get(tf, 8760)
    bars_per_trade = hold_bars
    trades_per_year = annual_factor / bars_per_trade
    sharpe = (mean_ret / std_ret) * math.sqrt(trades_per_year)
    win_rate = np.sum(returns > 0) / len(returns)
    wins = returns[returns > 0]
    losses = returns[returns < 0]
    pf = np.sum(wins) / abs(np.sum(losses)) if len(losses) > 0 and np.sum(losses) != 0 else 99.0
    max_dd = 0
    equity = 0
    peak = 0
    for r in returns:
        equity += r
        peak = max(peak, equity)
        dd = peak - equity
        max_dd = max(max_dd, dd)
    return {"sharpe": round(sharpe, 2), "mean_ret": round(mean_ret, 4), "std_ret": round(std_ret, 4), "win_rate": round(win_rate, 4), "pf": round(pf, 2), "max_dd": round(max_dd, 2), "n_trades": len(returns), "total_return": round(np.sum(returns), 2)}


def sweep_symbol(args) -> List[Dict]:
    """Sweep all metrics × thresholds × directions for one symbol × one TF."""
    symbol, tf, min_sharpe = args
    data = load_klines(symbol, tf)
    if data is None:
        return []
    metrics = compute_all_metrics(data)
    c = data[:, 3]
    results = []
    hold_bars_options = [3, 5, 8, 13, 21]
    for metric_name, metric_arr in metrics.items():
        if np.all(np.isnan(metric_arr)):
            continue
        # Determine threshold candidates based on metric range
        valid = metric_arr[~np.isnan(metric_arr)]
        if len(valid) < 50:
            continue
        p10, p25, p50, p75, p90 = np.percentile(valid, [10, 25, 50, 75, 90])
        # For binary metrics (0/1), just test ==1
        unique = np.unique(valid)
        if len(unique) <= 3:
            thresholds_ops = [("==1", 1.0)]
        else:
            thresholds_ops = [(">", p75), (">", p90), ("<", p25), ("<", p10), ("cross_above", p50), ("cross_below", p50)]
        for direction in ["LONG", "SHORT"]:
            for operator, threshold in thresholds_ops:
                for hold_bars in hold_bars_options:
                    try:
                        result = backtest_metric_threshold(c, metric_arr, direction, operator, threshold, tf, hold_bars)
                        if result and result["sharpe"] >= min_sharpe and result["n_trades"] >= 10:
                            result["symbol"] = symbol
                            result["tf"] = tf
                            result["metric"] = metric_name
                            result["direction"] = direction
                            result["operator"] = operator
                            result["threshold"] = round(threshold, 4)
                            result["hold_bars"] = hold_bars
                            results.append(result)
                    except Exception:
                        pass
    return results


def get_symbols(max_symbols: int = 999) -> List[str]:
    """Get ALL symbols with 1h klines data in sandbox."""
    symbols = set()
    for f in KLINES_DIR.glob("*_1h.json"):
        sym = f.stem.replace("_1h", "")
        symbols.add(sym)
    # Prioritize big caps
    priority = ["BTCUSDT", "BTCUSDC", "ETHUSDT", "ETHUSDC", "BNBUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDC", "LINKUSDC", "AVAXUSDT", "DOTUSDT", "MATICUSDT", "UNIUSDT"]
    ordered = [s for s in priority if s in symbols]
    remaining = sorted(symbols - set(ordered))
    all_syms = ordered + remaining
    return all_syms[:max_symbols]


def run_sweep(min_sharpe: float = 75, max_symbols: int = 50, workers: int = 8):
    symbols = get_symbols(max_symbols)
    print(f"[SWEEP] {len(symbols)} symbols × {len(TFS)} TFs × ~45 metrics × thresholds × directions × hold_bars")
    print(f"[SWEEP] Min Sharpe: {min_sharpe}, Workers: {workers}")
    tasks = []
    for sym in symbols:
        for tf in TFS:
            tasks.append((sym, tf, min_sharpe))
    print(f"[SWEEP] {len(tasks)} tasks total. Starting...")
    start = time.time()
    all_results = []
    with mp.Pool(workers) as pool:
        for i, batch_results in enumerate(pool.imap_unordered(sweep_symbol, tasks)):
            all_results.extend(batch_results)
            if (i + 1) % 50 == 0:
                elapsed = time.time() - start
                pct = (i + 1) / len(tasks) * 100
                print(f"  [{pct:.0f}%] {i+1}/{len(tasks)} done, {len(all_results)} results ≥ {min_sharpe} Sharpe | {elapsed:.0f}s")
    elapsed = time.time() - start
    print(f"\n[SWEEP] DONE in {elapsed:.0f}s. {len(all_results)} results with Sharpe >= {min_sharpe}")
    # Sort by Sharpe
    all_results.sort(key=lambda x: x["sharpe"], reverse=True)
    # Save full results
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(RESULTS_FILE, "w") as f:
        json.dump(all_results, f, indent=2)
    # Save top 200
    top = all_results[:200]
    with open(TOP_FILE, "w") as f:
        json.dump(top, f, indent=2)
    # Print summary
    print(f"\n{'='*120}")
    print(f"TOP 50 RESULTS (Sharpe >= {min_sharpe})")
    print(f"{'='*120}")
    print(f"{'Rank':>4} {'Sharpe':>8} {'WR':>6} {'PF':>6} {'MDD':>6} {'Trades':>7} {'TotRet':>8} {'Metric':<25} {'Op':>12} {'Thr':>10} {'Dir':>6} {'TF':>4} {'Hold':>5} {'Symbol':<15}")
    print("-" * 120)
    for i, r in enumerate(top[:50]):
        print(f"{i+1:>4} {r['sharpe']:>8.1f} {r['win_rate']:>6.1%} {r['pf']:>6.1f} {r['max_dd']:>6.1f} {r['n_trades']:>7} {r['total_return']:>8.1f} {r['metric']:<25} {r['operator']:>12} {r['threshold']:>10.4f} {r['direction']:>6} {r['tf']:>4} {r['hold_bars']:>5} {r['symbol']:<15}")
    # Aggregate: which metrics appear most in top results?
    print(f"\n{'='*80}")
    print(f"METRIC FREQUENCY IN TOP {min(200, len(all_results))} RESULTS")
    print(f"{'='*80}")
    from collections import Counter
    metric_counts = Counter(r["metric"] for r in all_results[:200])
    for metric, count in metric_counts.most_common(30):
        avg_sharpe = np.mean([r["sharpe"] for r in all_results[:200] if r["metric"] == metric])
        print(f"  {metric:<30} appears {count:>4}x | avg Sharpe: {avg_sharpe:.1f}")
    # TF frequency
    print(f"\nTF FREQUENCY:")
    tf_counts = Counter(r["tf"] for r in all_results[:200])
    for tf, count in tf_counts.most_common():
        print(f"  {tf:<6} {count:>4}x")
    return all_results


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Metric Sweep — test every indicator × TF")
    parser.add_argument("--min-sharpe", type=float, default=75, help="Minimum Sharpe ratio")
    parser.add_argument("--symbols", type=int, default=50, help="Number of symbols to test")
    parser.add_argument("--workers", type=int, default=max(1, mp.cpu_count() - 2), help="Parallel workers")
    args = parser.parse_args()
    run_sweep(min_sharpe=args.min_sharpe, max_symbols=args.symbols, workers=args.workers)


if __name__ == "__main__":
    main()
