#!/usr/bin/env python3
# pylint: disable=W,C,R,I
"""
BACKTEST: MOVER DETECTION + NO-LOSS — Scan all symbols for sudden jumps, enter mean-reversion.

Strategy:
  - Every bar, compute a "mover score" (similar to winners_30R/losers_30R)
  - When score spikes above threshold → symbol is a "mover" (sudden jump)
  - SHORT movers that spike UP (they often fall back) — mean reversion
  - LONG movers that dump DOWN (they often bounce) — mean reversion
  - Never sell at a loss — wait for natural close above min_profit_pct
  - Test different sensitivity thresholds for mover detection

Mover Score Components (mirrors ez_rankings.py):
  - Price slope (linear regression) on recent N bars — weighted by TF
  - Linearity (R²) — how "straight-line" is the move (high = clean spike, low = choppy)
  - Relative volume — spikes accompany real moves
  - Price vs SMA deviation — how far from mean (further = more likely to revert)
  - BB position — Bollinger band breakout detection

Sensitivity Settings:
  - mover_threshold: min score to qualify as a "mover" (lower = more signals)
  - linearity_min: min R² for clean moves (higher = fewer but cleaner signals)
  - lookback: bars to look back for slope calculation (3, 5, 8, 13, 21)
  - vol_spike_min: min relative volume to confirm the move is real

Usage:
  python3 backtest_mover_noloss.py                    # All symbols, all variations
  python3 backtest_mover_noloss.py --symbols 50       # Limit symbols
  python3 backtest_mover_noloss.py --report            # Print last results
"""
import os, sys, json, math, time, signal, logging, warnings, argparse
import numpy as np
warnings.filterwarnings("ignore", category=RuntimeWarning)
from pathlib import Path
from multiprocessing import Pool, cpu_count
from collections import defaultdict
try:
    import config
    BASE_PATH = Path(config.BASE_PATH)
except Exception:
    import platform
    BASE_PATH = Path("/Users/niels/Documents/binance") if platform.system() == "Darwin" else Path("/home/niels/binance")
KLINES_DIR = BASE_PATH / "klines_cache"
DATA_DIR = BASE_PATH / "data"
RESULTS_DIR = DATA_DIR / "backtest_mover_noloss"
RESULTS_FILE = RESULTS_DIR / "results.json"
ANNUAL_BARS = {"3m": 175200, "5m": 105120, "15m": 35040, "1h": 8760, "4h": 2190, "D": 365}
FEE_PCT = 0.08
MIN_TRADES = 8
WARMUP = 100
N_WORKERS = max(1, cpu_count() - 2)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [MOVER] %(message)s', stream=sys.stdout)
logger = logging.getLogger(__name__)
shutdown_flag = False

def _handle_signal(sig, frame):
    global shutdown_flag
    shutdown_flag = True

signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)

# ═══ INDICATORS ══════════════════════════════════════════════════════════════

def _ema_np(arr, period):
    result = np.empty_like(arr); result[:] = np.nan
    if len(arr) < period: return result
    mult = 2.0 / (period + 1); result[period - 1] = np.mean(arr[:period])
    for i in range(period, len(arr)): result[i] = arr[i] * mult + result[i - 1] * (1 - mult)
    return result

def _sma_np(arr, period):
    if len(arr) < period: return np.full_like(arr, np.nan)
    cum = np.cumsum(arr); cum[period:] = cum[period:] - cum[:-period]
    result = np.full_like(arr, np.nan); result[period - 1:] = cum[period - 1:] / period
    return result

def ind_stoch(h, lo, c, k_period=14, sk=5, sd=5):
    n = len(c); raw_k = np.full(n, 50.0)
    for i in range(k_period - 1, n):
        hh = np.max(h[i - k_period + 1:i + 1]); ll = np.min(lo[i - k_period + 1:i + 1])
        raw_k[i] = (c[i] - ll) / (hh - ll) * 100 if hh > ll else 50.0
    k = _ema_np(raw_k, sk); np.nan_to_num(k, copy=False, nan=50.0)
    d = _ema_np(k, sd); np.nan_to_num(d, copy=False, nan=50.0)
    return k, d

def ind_heikin_ashi(o, h, lo, c):
    ha_c = (o + h + lo + c) / 4; ha_o = np.empty_like(o); ha_o[0] = (o[0] + c[0]) / 2
    for i in range(1, len(o)): ha_o[i] = (ha_o[i - 1] + ha_c[i - 1]) / 2
    return np.where(ha_c >= ha_o, 1, -1)

def load_klines(symbol, tf):
    path = KLINES_DIR / f"{symbol}_{tf}.json"
    if not path.exists(): return None
    try:
        bars = json.loads(path.read_text())
        if not isinstance(bars, list) or len(bars) < 60: return None
        if isinstance(bars[0], dict):
            o = np.array([float(b.get("open", 0)) for b in bars], dtype=np.float64)
            h = np.array([float(b.get("high", 0)) for b in bars], dtype=np.float64)
            lo = np.array([float(b.get("low", 0)) for b in bars], dtype=np.float64)
            c = np.array([float(b.get("close", 0)) for b in bars], dtype=np.float64)
            v = np.array([float(b.get("volume", 0)) for b in bars], dtype=np.float64)
        else:
            arr = np.array(bars, dtype=np.float64)
            if arr.ndim == 2 and arr.shape[1] >= 5: o, h, lo, c, v = arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 3], arr[:, 4]
            else: return None
        valid = c > 0
        if valid.sum() < 60: return None
        return np.column_stack([o[valid], h[valid], lo[valid], c[valid], v[valid]])
    except Exception: return None

# ═══ MOVER DETECTION ENGINE ═════════════════════════════════════════════════

def compute_mover_scores(data, lookback=8):
    """
    Compute per-bar "mover score" — how much this bar represents a sudden directional move.

    Components (mirrors ez_rankings.py scoring):
      1. Slope: normalized price change over lookback bars (linear regression slope / price * 100)
      2. Linearity: R² of the price move (1.0 = perfectly linear, 0 = choppy)
      3. Relative volume: vol / avg_vol (spikes = real moves)
      4. SMA deviation: distance from SMA20 as % (further = more extended)
      5. BB position: >1.0 or <0.0 means outside Bollinger bands

    Score = slope × linearity × volume_factor × (1 + sma_deviation_factor)
    Positive score = price moving UP (short opportunity for mean reversion)
    Negative score = price moving DOWN (long opportunity for mean reversion)
    """
    o, h, lo, c, v = data[:, 0], data[:, 1], data[:, 2], data[:, 3], data[:, 4]
    n = len(c)
    scores = np.zeros(n)
    linearities = np.zeros(n)
    slopes = np.zeros(n)
    rel_vols = np.zeros(n)
    sma_devs = np.zeros(n)
    sma20 = _sma_np(c, 20)
    vol_sma = _sma_np(v, 20)
    bb_u, bb_m, bb_l = _sma_np(c, 20), _sma_np(c, 20), _sma_np(c, 20)
    # Compute BB properly
    bb_std = np.full(n, np.nan)
    for i in range(19, n):
        bb_std[i] = np.std(c[i - 19:i + 1])
    np.nan_to_num(bb_std, copy=False, nan=0.0)
    bb_u = sma20 + 2 * bb_std
    bb_l = sma20 - 2 * bb_std
    x = np.arange(lookback, dtype=float)
    xm = x - x.mean()
    xm_sq_sum = (xm ** 2).sum()
    for i in range(max(lookback, WARMUP), n):
        window = c[i - lookback:i]
        if window[0] <= 0: continue
        # Slope (normalized % per bar)
        slope = np.sum((window - window.mean()) * xm) / (xm_sq_sum + 1e-9)
        slope_pct = slope / window[0] * 100
        slopes[i] = slope_pct
        # Linearity (R²)
        predicted = window.mean() + slope * xm
        ss_res = np.sum((window - predicted) ** 2)
        ss_tot = np.sum((window - window.mean()) ** 2)
        r2 = 1 - ss_res / (ss_tot + 1e-9) if ss_tot > 0 else 0
        r2 = max(0, min(1, r2))
        linearities[i] = r2
        # Relative volume
        rv = v[i] / vol_sma[i] if vol_sma[i] > 0 and not np.isnan(vol_sma[i]) else 1.0
        rel_vols[i] = rv
        # SMA deviation
        sd = (c[i] - sma20[i]) / sma20[i] * 100 if sma20[i] > 0 and not np.isnan(sma20[i]) else 0
        sma_devs[i] = sd
        # Composite score: slope × linearity boost × volume factor
        lin_boost = 1 + 3.0 * r2  # 1x to 4x
        vol_factor = min(rv, 5.0) / 2.0  # 0.5x at rv=1, 2.5x at rv=5
        vol_factor = max(vol_factor, 0.5)
        scores[i] = slope_pct * lin_boost * vol_factor
    return scores, slopes, linearities, rel_vols, sma_devs

def build_mover_entries(data, scores, linearities, rel_vols, direction, mover_threshold=2.0, linearity_min=0.3, vol_spike_min=1.0):
    """
    Build entry signals from mover scores.

    Mean reversion logic:
      - LONG: score < -threshold (dump detected) → buy the dip
      - SHORT: score > +threshold (spike detected) → sell the rip
    """
    n = len(scores)
    if direction == "LONG":
        # Dump detected — enter long for bounce
        entries = (scores < -mover_threshold) & (linearities > linearity_min) & (rel_vols > vol_spike_min)
    else:
        # Spike detected — enter short for reversion
        entries = (scores > mover_threshold) & (linearities > linearity_min) & (rel_vols > vol_spike_min)
    entries[:WARMUP] = False
    # Dedupe: min 3 bars between entries
    last_entry = -10
    for i in range(n):
        if entries[i]:
            if i - last_entry < 3:
                entries[i] = False
            else:
                last_entry = i
    return entries

# ═══ SIMULATION (no-loss natural exit) ═══════════════════════════════════════

def simulate_noloss(closes, highs, lows, entries, direction, min_tp, k=None, k_reset_thr=35):
    n = len(closes); trades = []; in_pos = False; ep = 0.0; ei = 0; mx_adv = 0.0; mx_fav = 0.0; last_ex = -1; k_reset = False
    for i in range(WARMUP, n):
        if in_pos:
            if direction == "LONG":
                pnl = (closes[i] - ep) / ep * 100; best = (highs[i] - ep) / ep * 100; worst = (lows[i] - ep) / ep * 100
            else:
                pnl = (ep - closes[i]) / ep * 100; best = (ep - lows[i]) / ep * 100; worst = (ep - highs[i]) / ep * 100
            mx_fav = max(mx_fav, best); mx_adv = min(mx_adv, worst)
            if pnl >= min_tp:
                trades.append({"pnl": round(pnl - FEE_PCT, 4), "hold": i - ei, "adv": round(mx_adv, 4), "fav": round(mx_fav, 4), "reason": "TP", "re": last_ex > 0})
                in_pos = False; last_ex = i; k_reset = False
        else:
            # Bounce reentry: K must reset
            if k is not None and last_ex > 0 and not k_reset:
                if direction == "LONG" and k[i] < k_reset_thr: k_reset = True
                elif direction == "SHORT" and k[i] > (100 - k_reset_thr): k_reset = True
            can = False
            if last_ex < 0:
                can = entries[i]
            elif k is not None:
                can = k_reset and entries[i]
            else:
                can = entries[i]
            if can:
                in_pos = True; ep = closes[i]; ei = i; mx_adv = 0.0; mx_fav = 0.0
    if in_pos:
        if direction == "LONG": pnl = (closes[n-1] - ep) / ep * 100
        else: pnl = (ep - closes[n-1]) / ep * 100
        trades.append({"pnl": round(pnl - FEE_PCT, 4), "hold": (n-1) - ei, "adv": round(mx_adv, 4), "fav": round(mx_fav, 4), "reason": "END", "re": last_ex > 0})
    return trades

def compute_metrics(trades, tf):
    if len(trades) < MIN_TRADES: return None
    rets = np.array([t["pnl"] for t in trades]); holds = np.array([t["hold"] for t in trades])
    mean_r = rets.mean(); std_r = rets.std()
    annual = ANNUAL_BARS.get(tf, 8760)
    sharpe = (mean_r / std_r * math.sqrt(annual)) if std_r > 0 else (999.0 if mean_r > 0 else 0.0)
    wins = rets[rets > 0]; losses = rets[rets < 0]
    wr = len(wins) / len(rets)
    pf = wins.sum() / abs(losses.sum()) if len(losses) > 0 and losses.sum() != 0 else 99.0
    cum = np.cumsum(rets); peak = np.maximum.accumulate(cum); max_dd = float((peak - cum).max()) if len(cum) > 0 else 0.0
    tp_ct = sum(1 for t in trades if t["reason"] == "TP")
    return {"sharpe": round(sharpe, 4), "wr": round(wr, 4), "pf": round(pf, 4), "dd": round(max_dd, 4), "n": len(rets), "ret": round(float(cum[-1]), 4), "mean_ret": round(float(mean_r), 6), "avg_hold": round(float(holds.mean()), 1), "med_hold": round(float(np.median(holds)), 1), "max_hold": int(holds.max()), "avg_adv": round(float(np.mean([t["adv"] for t in trades])), 4), "tp_rate": round(tp_ct / len(rets), 4), "n_re": sum(1 for t in trades if t["re"]), "cap_util": round(holds.sum() / max(len(rets[0:1]) and 1, len(trades) * 100), 4)}

# ═══ WORKER ══════════════════════════════════════════════════════════════════

def _worker(args):
    symbol, tf, tp_list, lookback_list, threshold_list, linearity_list, vol_list = args
    data = load_klines(symbol, tf)
    if data is None or len(data) < WARMUP + 100: return []
    c = data[:, 3]; h = data[:, 1]; lo = data[:, 2]
    k_arr, _ = ind_stoch(h, lo, c, 14, 5, 5)
    results = []
    for lookback in lookback_list:
        scores, slopes, lins, rvols, sdevs = compute_mover_scores(data, lookback)
        for direction in ["LONG", "SHORT"]:
            for threshold in threshold_list:
                for lin_min in linearity_list:
                    for vol_min in vol_list:
                        entries = build_mover_entries(data, scores, lins, rvols, direction, threshold, lin_min, vol_min)
                        if entries.sum() < MIN_TRADES: continue
                        for tp in tp_list:
                            trades = simulate_noloss(c, h, lo, entries, direction, tp, k_arr, 35)
                            m = compute_metrics(trades, tf)
                            if m is None: continue
                            m.update({"symbol": symbol, "tf": tf, "dir": direction, "tp": tp, "lookback": lookback, "threshold": threshold, "lin_min": lin_min, "vol_min": vol_min})
                            results.append(m)
    return results

# ═══ DISCOVER & REPORT ═══════════════════════════════════════════════════════

def discover(tf, max_n=0):
    syms = set()
    for f in KLINES_DIR.glob(f"*_{tf}.json"):
        s = f.name.replace(f"_{tf}.json", "")
        if s.endswith("USDT") and not s.startswith("."): syms.add(s)
    syms = sorted(syms)
    return syms[:max_n] if max_n > 0 else syms

def print_report():
    if not RESULTS_FILE.exists(): print("No results."); return
    results = json.loads(RESULTS_FILE.read_text())
    if not results: print("Empty."); return
    print(f"\n{'='*140}")
    print(f"MOVER DETECTION NO-LOSS BACKTEST — {len(results)} combos")
    print(f"{'='*140}")
    # Summary by threshold × lookback × direction
    print(f"\n{'Thr':>5} {'LB':>4} {'Lin':>5} {'Vol':>4} {'Dir':>6} {'#Sym':>5} {'AvgWR':>7} {'AvgRet':>8} {'AvgShp':>8} {'AvgHold':>8} {'AvgAdv':>8} {'TPRate':>7}")
    print("-" * 100)
    keys = set()
    for r in results: keys.add((r["threshold"], r["lookback"], r["lin_min"], r["vol_min"], r["dir"]))
    for thr, lb, lin, vol, d in sorted(keys):
        sub = [r for r in results if r["threshold"] == thr and r["lookback"] == lb and r["lin_min"] == lin and r["vol_min"] == vol and r["dir"] == d]
        if len(sub) < 3: continue
        print(f"{thr:>5.1f} {lb:>4} {lin:>5.2f} {vol:>4.1f} {d:>6} {len(sub):>5} {np.mean([r['wr'] for r in sub]):>6.1%} {np.mean([r['ret'] for r in sub]):>8.2f} {np.mean([r['sharpe'] for r in sub]):>8.2f} {np.mean([r['avg_hold'] for r in sub]):>8.1f} {np.mean([r['avg_adv'] for r in sub]):>8.2f} {np.mean([r['tp_rate'] for r in sub]):>6.1%}")
    # Top 30
    top = sorted(results, key=lambda x: x["ret"], reverse=True)
    print(f"\n{'='*140}")
    print("TOP 30 BY TOTAL RETURN")
    print(f"{'='*140}")
    print(f"{'Symbol':<16} {'TF':>4} {'Dir':>6} {'TP':>5} {'LB':>4} {'Thr':>5} {'Lin':>5} {'Vol':>4} {'WR':>6} {'Return':>8} {'Sharpe':>8} {'#Tr':>5} {'AvgHd':>7} {'Adv':>7} {'TPR':>5}")
    print("-" * 130)
    for r in top[:30]:
        print(f"{r['symbol']:<16} {r['tf']:>4} {r['dir']:>6} {r['tp']:>4.1f}% {r['lookback']:>4} {r['threshold']:>5.1f} {r['lin_min']:>5.2f} {r['vol_min']:>4.1f} {r['wr']:>5.1%} {r['ret']:>8.2f} {r['sharpe']:>8.2f} {r['n']:>5} {r['avg_hold']:>7.1f} {r['avg_adv']:>7.2f} {r['tp_rate']:>4.1%}")

# ═══ MAIN ════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Mover Detection No-Loss Backtest")
    parser.add_argument("--symbols", type=int, default=0)
    parser.add_argument("--tf", type=str, default="15m")
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--workers", type=int, default=N_WORKERS)
    args = parser.parse_args()
    if args.report: print_report(); return
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    tfs = [x.strip() for x in args.tf.split(",")]
    # Parameter sweep
    tp_list = [0.5, 1.0, 2.0, 3.0]
    lookback_list = [5, 8, 13]
    threshold_list = [2.0, 3.0, 5.0, 8.0]
    linearity_list = [0.3, 0.5]
    vol_list = [1.0, 1.5]
    all_tasks = []
    for tf in tfs:
        syms = discover(tf, args.symbols)
        logger.info(f"Found {len(syms)} symbols for {tf}")
        for sym in syms:
            all_tasks.append((sym, tf, tp_list, lookback_list, threshold_list, linearity_list, vol_list))
    combos_per = len(tp_list) * len(lookback_list) * len(threshold_list) * len(linearity_list) * len(vol_list) * 2
    logger.info(f"Worker tasks: {len(all_tasks)} symbols × {combos_per} combos each = {len(all_tasks) * combos_per} total")
    results = []; batch_size = args.workers * 2; t0 = time.time()
    for i in range(0, len(all_tasks), batch_size):
        if shutdown_flag: break
        batch = all_tasks[i:i + batch_size]
        with Pool(args.workers) as pool:
            for batch_r in pool.map(_worker, batch):
                if batch_r: results.extend(batch_r)
        done = min(i + batch_size, len(all_tasks)); elapsed = time.time() - t0
        rate = done / elapsed if elapsed > 0 else 0; eta = (len(all_tasks) - done) / rate if rate > 0 else 0
        logger.info(f"Progress: {done}/{len(all_tasks)} ({done/len(all_tasks)*100:.1f}%) — {len(results)} valid — {rate:.1f}/s — ETA {eta:.0f}s")
    RESULTS_FILE.write_text(json.dumps(results, indent=2))
    logger.info(f"Saved {len(results)} results to {RESULTS_FILE}")
    try:
        import pandas as pd
        pd.DataFrame(results).to_excel(RESULTS_DIR / "results.xlsx", index=False)
        logger.info(f"Saved XLSX")
    except Exception as e:
        logger.warning(f"XLSX: {e}")
    print_report()
    logger.info(f"Done in {time.time() - t0:.0f}s")

if __name__ == "__main__":
    main()
