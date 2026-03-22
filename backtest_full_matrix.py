#!/usr/bin/env python3
# pylint: disable=W,C,R,I
"""FULL MATRIX BACKTEST — 242 symbols × ALL indicators × ALL TFs × 100 param combos → XLSX
Outputs one spreadsheet per TF with every symbol × indicator × param combo result.
Target: complete by morning. Uses ALL CPUs.
"""
import os, sys, json, math, time, signal, logging, warnings
import numpy as np
from pathlib import Path
from datetime import datetime, timezone
from multiprocessing import Pool, cpu_count
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

warnings.filterwarnings("ignore", category=RuntimeWarning)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [MATRIX] %(message)s', stream=sys.stdout)
logger = logging.getLogger(__name__)

BASE_PATH = Path("/Users/niels/Documents/binance")
KLINES_DIR = BASE_PATH / "klines_cache"
OUT_DIR = BASE_PATH / "data" / "backtest_matrix"
OUT_DIR.mkdir(parents=True, exist_ok=True)
TFS = ["3m", "15m", "1h", "4h", "D"]
FEE_PCT = 0.08
ANNUAL_BARS = {"3m": 175200, "15m": 35040, "1h": 8760, "4h": 2190, "D": 365}
N_WORKERS = max(1, cpu_count() - 1)
shutdown = False

def _sig(s, f):
    global shutdown
    shutdown = True
signal.signal(signal.SIGINT, _sig)
signal.signal(signal.SIGTERM, _sig)

# ═══ INDICATOR ENGINE ═════════════════════════════════════════════════════════
def _ema(arr, p):
    r = np.empty_like(arr); r[:] = np.nan
    if len(arr) < p: return r
    m = 2.0 / (p + 1); r[p-1] = np.mean(arr[:p])
    for i in range(p, len(arr)): r[i] = arr[i]*m + r[i-1]*(1-m)
    return r

def _sma(arr, p):
    if len(arr) < p: return np.full_like(arr, np.nan)
    c = np.cumsum(arr); c[p:] = c[p:] - c[:-p]
    r = np.full_like(arr, np.nan); r[p-1:] = c[p-1:] / p
    return r

def stoch(h, lo, c, kp=14, sk=5, sd=5):
    n = len(c); raw = np.full(n, 50.0)
    for i in range(kp-1, n):
        hh = np.max(h[i-kp+1:i+1]); ll = np.min(lo[i-kp+1:i+1])
        raw[i] = (c[i]-ll)/(hh-ll)*100 if hh>ll else 50.0
    return _sma(raw, sk), _sma(_sma(raw, sk), sd)

def rsi(c, p=14):
    d = np.diff(c, prepend=c[0])
    g = np.where(d>0, d, 0.0); l = np.where(d<0, -d, 0.0)
    ag = _ema(g, p); al = _ema(l, p)
    rs = np.where(al>0, ag/al, 100.0)
    return 100 - 100/(1+rs)

def atr(h, lo, c, p=14):
    tr = np.maximum(h-lo, np.maximum(np.abs(h-np.roll(c,1)), np.abs(lo-np.roll(c,1))))
    tr[0] = h[0]-lo[0]
    return _ema(tr, p)

def bb_pct(c, p=20, m=2.0):
    mid = _sma(c, p); std = np.full_like(c, np.nan)
    for i in range(p-1, len(c)): std[i] = np.std(c[i-p+1:i+1])
    u = mid + m*std; l = mid - m*std
    return np.where((u-l)>0, (c-l)/(u-l), 0.5)

def dc_pct(h, lo, p=20):
    n = len(h); dh = np.full(n, np.nan); dl = np.full(n, np.nan)
    for i in range(p-1, n): dh[i] = np.max(h[i-p+1:i+1]); dl[i] = np.min(lo[i-p+1:i+1])
    return np.where((dh-dl)>0, (h[:len(dh)]-dl)/(dh-dl), 0.5)  # use close=h for simplicity

def ha_color(o, h, lo, c):
    hc = (o+h+lo+c)/4; ho = np.empty_like(o); ho[0] = (o[0]+c[0])/2
    for i in range(1, len(o)): ho[i] = (ho[i-1]+hc[i-1])/2
    return np.where(hc>=ho, 1, -1)

def mfi(h, lo, c, v, p=14):
    tp = (h+lo+c)/3; mf = tp*v; n = len(c); r = np.full(n, np.nan)
    for i in range(p, n):
        pos = sum(mf[j] for j in range(i-p+1,i+1) if tp[j]>tp[j-1])
        neg = sum(mf[j] for j in range(i-p+1,i+1) if tp[j]<tp[j-1])
        r[i] = 100-100/(1+pos/neg) if neg>0 else 100.0
    return r

def wavetrend(h, lo, c, n1=10, n2=21):
    hlc3 = (h+lo+c)/3.0; esa = _ema(hlc3, n1); d = _ema(np.abs(hlc3-esa), n1)
    ci = np.where(d>0, (hlc3-esa)/(0.015*d), 0.0)
    return _ema(ci, n2), _sma(_ema(ci, n2), 4)

def linreg_slope(c, p=50):
    n = len(c); r = np.full(n, np.nan)
    xm = np.arange(p, dtype=float) - (p-1)/2
    denom = np.sum(xm**2) + 1e-9
    for i in range(p-1, n):
        r[i] = np.sum(c[i-p+1:i+1] * xm) / denom / (c[i]+1e-9) * 100
    return r

def rel_vol(v, p=20):
    avg = _sma(v, p)
    return np.where(avg>0, v/avg, 1.0)

def compute_all(data):
    o, h, lo, c, v = data[:,0], data[:,1], data[:,2], data[:,3], data[:,4]
    I = {}
    # Stochastic variants
    for kp, sk, sd, tag in [(14,5,5,""), (9,3,3,"_fast"), (21,5,5,"_slow")]:
        k, d = stoch(h, lo, c, kp, sk, sd)
        I[f"k{tag}"] = k; I[f"d{tag}"] = d
    I["rsi14"] = rsi(c, 14); I["rsi9"] = rsi(c, 9)
    I["atr_pct"] = np.where(c>0, atr(h,lo,c,14)/c*100, 0)
    I["atr_ratio"] = np.where(_sma(atr(h,lo,c,14),50)>0, atr(h,lo,c,14)/_sma(atr(h,lo,c,14),50), 1)
    I["bb"] = bb_pct(c)
    I["dc"] = dc_pct(h, lo)
    I["ha"] = ha_color(o, h, lo, c).astype(float)
    I["mfi"] = mfi(h, lo, c, v)
    wt1, wt2 = wavetrend(h, lo, c)
    I["wt1"] = wt1; I["wt2"] = wt2
    I["lr"] = linreg_slope(c)
    I["rvol"] = rel_vol(v)
    ema20 = _ema(c, 20); sma200 = _sma(c, 200)
    I["ema_dist"] = np.where(ema20>0, (c-ema20)/ema20*100, 0)
    I["sma200_dist"] = np.where(sma200>0, (c-sma200)/sma200*100, 0)
    I["mom3"] = np.where(np.roll(c,3)>0, (c-np.roll(c,3))/np.roll(c,3)*100, 0)
    I["mom5"] = np.where(np.roll(c,5)>0, (c-np.roll(c,5))/np.roll(c,5)*100, 0)
    I["close"] = c
    return I

# ═══ BACKTEST ENGINE ═════════════════════════════════════════════════════════
def backtest_signal(c, entries, direction, hold_bars):
    n = len(c); warmup = 60
    entries[:warmup] = False; entries[-(hold_bars+1):] = False
    for i in range(1, n):
        if entries[i] and entries[i-1]: entries[i] = False
    idxs = np.where(entries)[0]
    if len(idxs) < 5: return None
    rets = []
    last_exit = -1
    for idx in idxs:
        if idx <= last_exit: continue
        ep = c[idx]
        if ep <= 0: continue
        exit_idx = min(idx+hold_bars, n-1)
        if direction == "LONG":
            ret = (c[exit_idx]-ep)/ep*100 - FEE_PCT
        else:
            ret = (ep-c[exit_idx])/ep*100 - FEE_PCT
        rets.append(ret)
        last_exit = exit_idx
    if len(rets) < 5: return None
    rets = np.array(rets)
    mean_r = rets.mean(); std_r = rets.std()
    if std_r <= 0: return None
    annual = ANNUAL_BARS.get("1h", 8760)
    sharpe = mean_r / std_r * math.sqrt(annual)
    wr = np.sum(rets>0) / len(rets)
    wins = rets[rets>0]; losses = rets[rets<0]
    pf = wins.sum() / abs(losses.sum()) if len(losses)>0 and losses.sum()!=0 else 99.0
    cum = np.cumsum(rets); peak = np.maximum.accumulate(cum)
    mdd = float((peak-cum).max()) if len(cum)>0 else 0.0
    return {"sharpe": round(sharpe, 4), "wr": round(wr*100, 2), "pf": round(pf, 4), "mdd": round(mdd, 4), "trades": len(rets), "total_ret": round(float(cum[-1]), 4), "mean_ret": round(float(mean_r), 6)}

def load_klines(symbol, tf):
    path = KLINES_DIR / f"{symbol}_{tf}.json"
    if not path.exists(): return None
    try:
        bars = json.loads(path.read_text())
        if not isinstance(bars, list) or len(bars) < 60: return None
        if isinstance(bars[0], dict):
            o = np.array([float(b["open"]) for b in bars], dtype=np.float64)
            h = np.array([float(b["high"]) for b in bars], dtype=np.float64)
            lo = np.array([float(b["low"]) for b in bars], dtype=np.float64)
            c = np.array([float(b["close"]) for b in bars], dtype=np.float64)
            v = np.array([float(b.get("volume", 0)) for b in bars], dtype=np.float64)
        else: return None
        return np.column_stack([o, h, lo, c, v])
    except: return None

# ═══ WORKER ═══════════════════════════════════════════════════════════════════
# Thresholds to test per indicator
INDICATOR_THRESHOLDS = {
    "k": [10, 15, 20, 25, 30, 35, 40, 50, 60, 65, 70, 75, 80, 85, 90],
    "d": [10, 15, 20, 25, 30, 35, 40, 50, 60, 65, 70, 75, 80, 85, 90],
    "k_fast": [10, 15, 20, 25, 30, 40, 50, 60, 70, 75, 80, 85, 90],
    "d_fast": [10, 15, 20, 25, 30, 40, 50, 60, 70, 75, 80, 85, 90],
    "k_slow": [10, 15, 20, 25, 30, 40, 50, 60, 70, 75, 80, 85, 90],
    "d_slow": [10, 15, 20, 25, 30, 40, 50, 60, 70, 75, 80, 85, 90],
    "rsi14": [20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80],
    "rsi9": [20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80],
    "bb": [-0.2, -0.1, 0.0, 0.1, 0.2, 0.3, 0.5, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2],
    "dc": [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.50, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95],
    "ha": [0.5],  # > 0.5 = green
    "mfi": [20, 30, 40, 50, 60, 70, 80],
    "wt1": [-80, -60, -40, -20, 0, 20, 40, 60, 80],
    "wt2": [-80, -60, -40, -20, 0, 20, 40, 60, 80],
    "lr": [-0.5, -0.2, -0.1, 0, 0.1, 0.2, 0.5],
    "rvol": [0.5, 0.8, 1.0, 1.2, 1.5, 2.0, 2.5, 3.0],
    "ema_dist": [-5, -3, -2, -1, -0.5, 0, 0.5, 1, 2, 3, 5],
    "sma200_dist": [-10, -5, -3, -1, 0, 1, 3, 5, 10],
    "mom3": [-3, -2, -1, -0.5, 0, 0.5, 1, 2, 3],
    "mom5": [-5, -3, -2, -1, 0, 1, 2, 3, 5],
    "atr_pct": [0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0],
    "atr_ratio": [0.5, 0.7, 1.0, 1.3, 1.5, 2.0, 2.5],
}
HOLD_BARS = [3, 5, 8, 13, 21]

def worker(args):
    symbol, tf = args
    data = load_klines(symbol, tf)
    if data is None: return []
    c = data[:, 3]
    I = compute_all(data)
    results = []
    for ind_name, ind_arr in I.items():
        if np.all(np.isnan(ind_arr)): continue
        thresholds = INDICATOR_THRESHOLDS.get(ind_name, [])
        if not thresholds:
            valid = ind_arr[~np.isnan(ind_arr)]
            if len(valid) < 50: continue
            thresholds = list(np.percentile(valid, [10, 25, 50, 75, 90]))
        for direction in ["LONG", "SHORT"]:
            for thr in thresholds:
                for op in [">", "<"]:
                    if op == ">":
                        entries = ind_arr > thr
                    else:
                        entries = ind_arr < thr
                    for hold in HOLD_BARS:
                        r = backtest_signal(c, entries.copy(), direction, hold)
                        if r and r["trades"] >= 5:
                            r.update({"symbol": symbol, "tf": tf, "indicator": ind_name, "direction": direction, "operator": op, "threshold": round(thr, 4), "hold_bars": hold})
                            results.append(r)
    return results

# ═══ MAIN ════════════════════════════════════════════════════════════════════
def get_symbols():
    symbols = set()
    for f in KLINES_DIR.glob("*_1h.json"):
        symbols.add(f.stem.replace("_1h", ""))
    return sorted(symbols)

def run():
    global shutdown
    symbols = get_symbols()
    logger.info(f"FULL MATRIX: {len(symbols)} symbols × {len(TFS)} TFs × {len(INDICATOR_THRESHOLDS)} indicators × thresholds × 2 directions × {len(HOLD_BARS)} holds")
    tasks = [(sym, tf) for sym in symbols for tf in TFS]
    logger.info(f"Total tasks: {len(tasks)} | Workers: {N_WORKERS}")
    all_results = []
    start = time.time()
    with Pool(N_WORKERS, maxtasksperchild=10) as pool:
        for i, batch in enumerate(pool.imap_unordered(worker, tasks, chunksize=2)):
            all_results.extend(batch)
            if (i+1) % 50 == 0:
                elapsed = time.time() - start
                pct = (i+1)/len(tasks)*100
                rate = (i+1)/elapsed*3600
                eta_h = (len(tasks)-(i+1)) / (rate/3600) / 3600 if rate > 0 else 999
                logger.info(f"  [{pct:.1f}%] {i+1}/{len(tasks)} done | {len(all_results):,} results | {rate:.0f} tasks/h | ETA: {eta_h:.1f}h")
            if shutdown: break
    elapsed = time.time() - start
    logger.info(f"DONE: {len(all_results):,} results in {elapsed:.0f}s ({elapsed/60:.1f}m)")
    # Save to XLSX — one sheet per TF
    try:
        import pandas as pd
        df = pd.DataFrame(all_results)
        if len(df) == 0:
            logger.error("No results!")
            return
        # Save full CSV first (fast)
        csv_path = OUT_DIR / "full_matrix.csv"
        df.to_csv(csv_path, index=False)
        logger.info(f"Saved CSV: {csv_path} ({len(df):,} rows)")
        # Per-TF XLSX
        for tf in TFS:
            tf_df = df[df["tf"] == tf].sort_values("sharpe", ascending=False)
            if len(tf_df) == 0: continue
            xlsx_path = OUT_DIR / f"matrix_{tf}.xlsx"
            tf_df.to_excel(xlsx_path, index=False, engine="openpyxl")
            logger.info(f"Saved {xlsx_path} ({len(tf_df):,} rows)")
        # Top results XLSX
        top = df.nlargest(5000, "sharpe")
        top_path = OUT_DIR / "matrix_top_5000.xlsx"
        top.to_excel(top_path, index=False, engine="openpyxl")
        logger.info(f"Saved {top_path}")
        # Per-symbol summary
        summary = df.groupby(["symbol", "tf", "direction"]).agg({"sharpe": ["max", "mean", "count"], "wr": "max", "total_ret": "max"}).reset_index()
        summary.columns = ["symbol", "tf", "direction", "best_sharpe", "avg_sharpe", "combos_tested", "best_wr", "best_return"]
        summary = summary.sort_values("best_sharpe", ascending=False)
        summary_path = OUT_DIR / "matrix_symbol_summary.xlsx"
        summary.to_excel(summary_path, index=False, engine="openpyxl")
        logger.info(f"Saved {summary_path}")
        # Per-indicator summary
        ind_summary = df.groupby(["indicator", "tf", "direction"]).agg({"sharpe": ["max", "mean", "count"], "wr": "max"}).reset_index()
        ind_summary.columns = ["indicator", "tf", "direction", "best_sharpe", "avg_sharpe", "combos_tested", "best_wr"]
        ind_summary = ind_summary.sort_values("best_sharpe", ascending=False)
        ind_path = OUT_DIR / "matrix_indicator_summary.xlsx"
        ind_summary.to_excel(ind_path, index=False, engine="openpyxl")
        logger.info(f"Saved {ind_path}")
    except ImportError:
        logger.error("pandas/openpyxl not installed for XLSX output")
    logger.info("ALL SPREADSHEETS GENERATED")

if __name__ == "__main__":
    run()
