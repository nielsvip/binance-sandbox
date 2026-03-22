#!/usr/bin/env python3
# pylint: disable=W,C,R,I
"""
CONTINUOUS BACKTEST SWEEP — Runs for N hours testing systematic variations.

Variation axes (tested in rounds):
  Round 1: K-zone thresholds (20,25,30,35,40,45) × TP (0.25-8%) × TF (3m,15m,1h,4h,D) — crypto
  Round 2: Stoch parameters (K period 9,14,21; smoothing 3,5,7) × best TPs from R1 — crypto
  Round 3: Multi-filter combos (K+RSI, K+BB, K+DC, K+HA+RSI) × best from R1 — crypto
  Round 4: Volume filters (rv>1.0, 1.3, 1.5, 2.0) on top entries from R1 — crypto
  Round 5: Walk-forward validation (70/30 split) on top combos — crypto
  Round 6: Tradier stocks — K thresholds × TP × TF (1h,4h,D) with LONG-only HODL
  Round 7: Tradier stocks — entry timing (open zone, mid zone, close zone)
  Round 8: ATR-scaled TP (TP = N × ATR instead of fixed %) — both crypto + stocks
  Round 9: Multi-position portfolio sim (max N concurrent, capital allocation) — crypto
  Round 10: Market regime filter (trend vs range via ADX/BB squeeze) — both

Saves: data/backtest_sweep/round_N.json + data/backtest_sweep/summary.json
"""
import os, sys, json, math, time, signal, logging, warnings, argparse, hashlib
import numpy as np
warnings.filterwarnings("ignore", category=RuntimeWarning)
from pathlib import Path
from datetime import datetime
from multiprocessing import Pool, cpu_count
from collections import defaultdict
try:
    import config
    BASE_PATH = Path(config.BASE_PATH)
except Exception:
    import platform
    BASE_PATH = Path("/Users/niels/Documents/binance") if platform.system() == "Darwin" else Path("/home/niels/binance")
CRYPTO_KLINES = BASE_PATH / "klines_cache"
TRADIER_KLINES = BASE_PATH / "klines_cache" / "tradier"
RESULTS_DIR = BASE_PATH / "data" / "backtest_sweep"
SUMMARY_FILE = RESULTS_DIR / "summary.json"
ANNUAL_BARS_CRYPTO = {"3m": 175200, "5m": 105120, "15m": 35040, "1h": 8760, "4h": 2190, "D": 365}
ANNUAL_BARS_STOCK = {"15m": 6552, "1h": 1638, "4h": 410, "D": 252}
FEE_CRYPTO = 0.08
FEE_STOCK = 0.0
MIN_TRADES = 8
WARMUP = 100
N_WORKERS = max(1, cpu_count() - 2)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [SWEEP] %(message)s', stream=sys.stdout)
logger = logging.getLogger(__name__)
shutdown_flag = False
start_time = time.time()
max_runtime = 16 * 3600  # 16 hours

def _handle_signal(sig, frame):
    pass  # Ignore signals — this is a long-running background process

signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)

def time_remaining():
    return max(0, max_runtime - (time.time() - start_time))

def hours_elapsed():
    return (time.time() - start_time) / 3600

# ═══ INDICATORS (shared) ════════════════════════════════════════════════════

def _ema_np(arr, period):
    result = np.empty_like(arr); result[:] = np.nan
    if len(arr) < period: return result
    mult = 2.0 / (period + 1)
    result[period - 1] = np.mean(arr[:period])
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

def ind_rsi(c, period=14):
    delta = np.diff(c, prepend=c[0]); gain = np.where(delta > 0, delta, 0.0); loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = _ema_np(gain, period); avg_loss = _ema_np(loss, period)
    rs = np.where(avg_loss > 0, avg_gain / avg_loss, 100.0); rsi = 100 - 100 / (1 + rs)
    np.nan_to_num(rsi, copy=False, nan=50.0); return rsi

def ind_atr(h, lo, c, period=14):
    tr = np.maximum(h - lo, np.maximum(np.abs(h - np.roll(c, 1)), np.abs(lo - np.roll(c, 1)))); tr[0] = h[0] - lo[0]
    atr = _ema_np(tr, period); np.nan_to_num(atr, copy=False, nan=0.0); return atr

def ind_heikin_ashi(o, h, lo, c):
    ha_c = (o + h + lo + c) / 4; ha_o = np.empty_like(o); ha_o[0] = (o[0] + c[0]) / 2
    for i in range(1, len(o)): ha_o[i] = (ha_o[i - 1] + ha_c[i - 1]) / 2
    return np.where(ha_c >= ha_o, 1, -1)

def ind_donchian(h, lo, period=20):
    n = len(h); dc_h = np.full(n, np.nan); dc_l = np.full(n, np.nan)
    for i in range(period - 1, n): dc_h[i] = np.max(h[i - period + 1:i + 1]); dc_l[i] = np.min(lo[i - period + 1:i + 1])
    return dc_h, dc_l, (dc_h + dc_l) / 2

def ind_bollinger(c, period=20, std_mult=2.0):
    mid = _sma_np(c, period); std = np.full_like(c, np.nan)
    for i in range(period - 1, len(c)): std[i] = np.std(c[i - period + 1:i + 1])
    return mid + std_mult * std, mid, mid - std_mult * std

def ind_adx(h, lo, c, period=14):
    """ADX for trend strength detection."""
    n = len(c); tr = np.maximum(h - lo, np.maximum(np.abs(h - np.roll(c, 1)), np.abs(lo - np.roll(c, 1)))); tr[0] = h[0] - lo[0]
    up = h - np.roll(h, 1); down = np.roll(lo, 1) - lo; up[0] = 0; down[0] = 0
    pdm = np.where((up > down) & (up > 0), up, 0.0); ndm = np.where((down > up) & (down > 0), down, 0.0)
    atr = _ema_np(tr, period); pdi = _ema_np(pdm, period); ndi = _ema_np(ndm, period)
    np.nan_to_num(atr, copy=False, nan=1.0)
    pdi_pct = np.where(atr > 0, pdi / atr * 100, 0); ndi_pct = np.where(atr > 0, ndi / atr * 100, 0)
    dx = np.where((pdi_pct + ndi_pct) > 0, np.abs(pdi_pct - ndi_pct) / (pdi_pct + ndi_pct) * 100, 0)
    adx = _ema_np(dx, period); np.nan_to_num(adx, copy=False, nan=20.0)
    return adx

def load_klines(klines_dir, symbol, tf):
    path = klines_dir / f"{symbol}_{tf}.json"
    if not path.exists(): return None
    try:
        bars = json.loads(path.read_text())
        if not isinstance(bars, list) or len(bars) < 30: return None
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
        if valid.sum() < 30: return None
        return np.column_stack([o[valid], h[valid], lo[valid], c[valid], v[valid]])
    except Exception: return None

def compute_indicators(data, stoch_k=14, stoch_sk=5, stoch_sd=5):
    o, h, lo, c, v = data[:, 0], data[:, 1], data[:, 2], data[:, 3], data[:, 4]
    n = len(c); I = {}
    k, d = ind_stoch(h, lo, c, stoch_k, stoch_sk, stoch_sd)
    I["k"] = k; I["d"] = d
    kp = np.roll(k, 1); kp[0] = k[0]
    I["k_rising"] = (k > kp).astype(np.int8); I["k_falling"] = (k < kp).astype(np.int8)
    I["rsi"] = ind_rsi(c, 14)
    ha = ind_heikin_ashi(o, h, lo, c); I["ha"] = ha
    ha_prev = np.roll(ha, 1); ha_prev[0] = ha[0]
    I["ha_flip_green"] = ((ha == 1) & (ha_prev == -1)).astype(np.int8)
    I["ha_flip_red"] = ((ha == -1) & (ha_prev == 1)).astype(np.int8)
    atr = ind_atr(h, lo, c, 14); I["atr"] = atr
    I["atr_pct"] = np.where(c > 0, atr / c * 100, 0)
    dc_h, dc_l, dc_mid = ind_donchian(h, lo, 20)
    I["dc_pct"] = np.where((dc_h - dc_l) > 0, (c - dc_l) / (dc_h - dc_l), 0.5)
    bb_u, bb_m, bb_l = ind_bollinger(c, 20, 2.0)
    I["bb_pct"] = np.where((bb_u - bb_l) > 0, (c - bb_l) / (bb_u - bb_l), 0.5)
    I["bb_width"] = np.where(bb_m > 0, (bb_u - bb_l) / bb_m, 0)
    sma200 = _sma_np(c, 200); np.nan_to_num(sma200, copy=False, nan=0.0); I["sma200"] = sma200
    I["adx"] = ind_adx(h, lo, c, 14)
    # Relative volume
    vol_sma = _sma_np(v, 20); np.nan_to_num(vol_sma, copy=False, nan=1.0)
    I["rel_vol"] = np.where(vol_sma > 0, v / vol_sma, 1.0)
    # Engulfing / hammer
    body = np.abs(c - o); range_ = h - lo
    prev_body = np.roll(body, 1); prev_body[0] = 0
    green = (c > o).astype(np.int8); prev_green = np.roll(green, 1); prev_green[0] = 0
    I["bull_engulf"] = ((green == 1) & (prev_green == 0) & (body > prev_body * 1.1)).astype(np.int8)
    I["bear_engulf"] = ((green == 0) & (prev_green == 1) & (body > prev_body * 1.1)).astype(np.int8)
    lower_wick = np.where(range_ > 0, (np.minimum(o, c) - lo) / range_, 0)
    upper_wick = np.where(range_ > 0, (h - np.maximum(o, c)) / range_, 0)
    body_ratio = np.where(range_ > 0, body / range_, 0)
    I["hammer"] = ((lower_wick > 0.6) & (body_ratio < 0.3) & (green == 1)).astype(np.int8)
    I["inv_hammer"] = ((upper_wick > 0.6) & (body_ratio < 0.3) & (green == 0)).astype(np.int8)
    I["close"] = c; I["high"] = h; I["low"] = lo; I["open"] = o; I["volume"] = v
    return I

# ═══ ENTRY SIGNALS ═══════════════════════════════════════════════════════════

def build_entries(I, direction, k_threshold, entry_filter="candle", vol_min=0, adx_min=0):
    k = I["k"]; ha = I["ha"]; rsi = I["rsi"]; n = len(k)
    if direction == "LONG":
        k_zone = k < k_threshold
        k_turn = I["k_rising"].astype(bool)
    else:
        k_zone = k > (100 - k_threshold)
        k_turn = I["k_falling"].astype(bool)
    base = k_zone & k_turn
    if entry_filter == "candle":
        if direction == "LONG":
            candle = I["ha_flip_green"].astype(bool) | I["hammer"].astype(bool) | I["bull_engulf"].astype(bool)
        else:
            candle = I["ha_flip_red"].astype(bool) | I["inv_hammer"].astype(bool) | I["bear_engulf"].astype(bool)
        sig = base & candle
    elif entry_filter == "ha":
        sig = base & ((ha == 1) if direction == "LONG" else (ha == -1))
    elif entry_filter == "rsi":
        if direction == "LONG":
            sig = base & (rsi < 40) & (ha == 1)
        else:
            sig = base & (rsi > 60) & (ha == -1)
    elif entry_filter == "bb":
        bb = I["bb_pct"]
        if direction == "LONG":
            sig = base & (bb < 0.2)
        else:
            sig = base & (bb > 0.8)
    elif entry_filter == "dc":
        dc = I["dc_pct"]
        if direction == "LONG":
            sig = base & (dc < 0.25) & (ha == 1)
        else:
            sig = base & (dc > 0.75) & (ha == -1)
    elif entry_filter == "multi":
        dc = I["dc_pct"]; bb = I["bb_pct"]
        if direction == "LONG":
            sig = base & (ha == 1) & (rsi < 45) & (dc < 0.35)
        else:
            sig = base & (ha == -1) & (rsi > 55) & (dc > 0.65)
    elif entry_filter == "none":
        sig = base
    else:
        sig = base
    # Volume filter
    if vol_min > 0:
        sig = sig & (I["rel_vol"] > vol_min)
    # ADX filter (trend strength)
    if adx_min > 0:
        sig = sig & (I["adx"] > adx_min)
    sig[:WARMUP] = False
    return sig

# ═══ SIMULATION ══════════════════════════════════════════════════════════════

def simulate(I, direction, min_tp, entries, reentry_mode="bounce", k_reset_thr=35, fee=0.08, atr_tp_mult=0, walk_forward_pct=0):
    closes = I["close"]; highs = I["high"]; lows = I["low"]; k = I["k"]; ha = I["ha"]; atr = I["atr"]
    n = len(closes)
    # Walk-forward: only trade on test portion
    start_bar = WARMUP
    if walk_forward_pct > 0:
        train_end = int(n * walk_forward_pct)
        start_bar = max(WARMUP, train_end)
    trades = []; in_pos = False; ep = 0.0; ei = 0; mx_adv = 0.0; mx_fav = 0.0; last_ex = -1; k_reset = False
    for i in range(start_bar, n):
        if in_pos:
            if direction == "LONG":
                pnl = (closes[i] - ep) / ep * 100; best = (highs[i] - ep) / ep * 100; worst = (lows[i] - ep) / ep * 100
            else:
                pnl = (ep - closes[i]) / ep * 100; best = (ep - lows[i]) / ep * 100; worst = (ep - highs[i]) / ep * 100
            mx_fav = max(mx_fav, best); mx_adv = min(mx_adv, worst)
            # ATR-scaled TP
            tp_here = min_tp
            if atr_tp_mult > 0 and I["atr_pct"][ei] > 0:
                tp_here = I["atr_pct"][ei] * atr_tp_mult
            if pnl >= tp_here:
                trades.append({"pnl": round(pnl - fee, 4), "hold": i - ei, "adv": round(mx_adv, 4), "fav": round(mx_fav, 4), "reason": "TP", "re": last_ex > 0})
                in_pos = False; last_ex = i; k_reset = False
        else:
            if last_ex > 0 and not k_reset:
                if direction == "LONG" and k[i] < k_reset_thr: k_reset = True
                elif direction == "SHORT" and k[i] > (100 - k_reset_thr): k_reset = True
            can = False
            if last_ex < 0:
                can = entries[i]
            elif reentry_mode == "immediate":
                can = entries[i]
            elif reentry_mode == "bounce":
                can = k_reset and entries[i]
            elif reentry_mode == "deep":
                can = (k[i] < 20 if direction == "LONG" else k[i] > 80) and I["k_rising" if direction == "LONG" else "k_falling"][i]
            if can:
                in_pos = True; ep = closes[i]; ei = i; mx_adv = 0.0; mx_fav = 0.0
    if in_pos:
        if direction == "LONG": pnl = (closes[n-1] - ep) / ep * 100
        else: pnl = (ep - closes[n-1]) / ep * 100
        trades.append({"pnl": round(pnl - fee, 4), "hold": (n-1) - ei, "adv": round(mx_adv, 4), "fav": round(mx_fav, 4), "reason": "END", "re": last_ex > 0})
    return trades

def metrics(trades, tf, ab, total_bars):
    if len(trades) < MIN_TRADES: return None
    rets = np.array([t["pnl"] for t in trades]); holds = np.array([t["hold"] for t in trades])
    mean_r = rets.mean(); std_r = rets.std()
    sharpe = (mean_r / std_r * math.sqrt(ab.get(tf, 252))) if std_r > 0 else (999.0 if mean_r > 0 else 0.0)
    wins = rets[rets > 0]; losses = rets[rets < 0]
    wr = len(wins) / len(rets)
    pf = wins.sum() / abs(losses.sum()) if len(losses) > 0 and losses.sum() != 0 else 99.0
    cum = np.cumsum(rets); peak = np.maximum.accumulate(cum)
    max_dd = float((peak - cum).max()) if len(cum) > 0 else 0.0
    tp_ct = sum(1 for t in trades if t["reason"] == "TP")
    re_ct = sum(1 for t in trades if t["re"])
    re_rets = [t["pnl"] for t in trades if t["re"]]
    return {"sharpe": round(sharpe, 4), "wr": round(wr, 4), "pf": round(pf, 4), "dd": round(max_dd, 4), "n": len(rets), "ret": round(float(cum[-1]), 4), "avg_hold": round(float(holds.mean()), 1), "med_hold": round(float(np.median(holds)), 1), "max_hold": int(holds.max()), "avg_adv": round(float(np.mean([t["adv"] for t in trades])), 4), "tp_rate": round(tp_ct / len(rets), 4), "n_re": re_ct, "re_wr": round((sum(1 for r in re_rets if r > 0) / len(re_rets)) if re_rets else 0, 4), "cap_util": round(holds.sum() / total_bars, 4) if total_bars > 0 else 0}

# ═══ WORKER ══════════════════════════════════════════════════════════════════

def _worker(args):
    klines_dir, symbol, tf, direction, tp_list, k_thr, entry_filter, reentry, stoch_params, vol_min, adx_min, fee, ab, atr_tp, wf_pct = args
    data = load_klines(klines_dir, symbol, tf)
    if data is None or len(data) < WARMUP + 50: return []
    sk, ssk, ssd = stoch_params
    I = compute_indicators(data, sk, ssk, ssd)
    entries = build_entries(I, direction, k_thr, entry_filter, vol_min, adx_min)
    if entries.sum() < MIN_TRADES: return []
    total_bars = len(data) - WARMUP; results = []
    for tp in tp_list:
        trades = simulate(I, direction, tp, entries, reentry, k_thr, fee, atr_tp, wf_pct)
        m = metrics(trades, tf, ab, total_bars)
        if m is None: continue
        m.update({"symbol": symbol, "tf": tf, "dir": direction, "tp": tp, "k_thr": k_thr, "filter": entry_filter, "reentry": reentry, "stoch": f"{sk}/{ssk}/{ssd}", "vol_min": vol_min, "adx_min": adx_min, "atr_tp": atr_tp, "wf": wf_pct})
        results.append(m)
    return results

# ═══ DISCOVER ════════════════════════════════════════════════════════════════

def discover(kdir, tf, suffix="USDT", max_n=0):
    syms = set()
    for f in kdir.glob(f"*_{tf}.json"):
        s = f.name.replace(f"_{tf}.json", "")
        if suffix and not s.endswith(suffix): continue
        if not s.startswith("."): syms.add(s)
    syms = sorted(syms)
    return syms[:max_n] if max_n > 0 else syms

# ═══ RUN A ROUND ═════════════════════════════════════════════════════════════

def run_round(round_num, round_name, tasks, workers):
    logger.info(f"{'='*80}")
    logger.info(f"ROUND {round_num}: {round_name} — {len(tasks)} worker tasks")
    logger.info(f"{'='*80}")
    results = []; batch_size = workers * 4; t0 = time.time()
    for i in range(0, len(tasks), batch_size):
        if shutdown_flag or time_remaining() < 60: break
        batch = tasks[i:i + batch_size]
        with Pool(workers) as pool:
            for batch_r in pool.map(_worker, batch):
                if batch_r: results.extend(batch_r)
        done = min(i + batch_size, len(tasks))
        elapsed = time.time() - t0; rate = done / elapsed if elapsed > 0 else 0
        logger.info(f"  R{round_num} progress: {done}/{len(tasks)} — {len(results)} valid — {rate:.1f}/s — total {hours_elapsed():.1f}h elapsed")
    # Save round results
    rfile = RESULTS_DIR / f"round_{round_num}.json"
    rfile.write_text(json.dumps(results, indent=2))
    logger.info(f"  R{round_num} DONE: {len(results)} results saved to {rfile}")
    # Print summary
    if results:
        by_key = defaultdict(list)
        for r in results:
            by_key[(r["dir"], r["tf"], r["tp"])].append(r)
        best = sorted(results, key=lambda x: x["ret"], reverse=True)[:5]
        top_strs = [f"{r['symbol']} {r['dir']} {r['tf']} tp={r['tp']} ret={r['ret']:.1f} wr={r['wr']:.0%}" for r in best]
        logger.info(f"  TOP 5: {', '.join(top_strs)}")
    return results

# ═══ MAIN ════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=float, default=16.0)
    parser.add_argument("--workers", type=int, default=N_WORKERS)
    parser.add_argument("--start-round", type=int, default=1)
    args = parser.parse_args()
    global max_runtime; max_runtime = args.hours * 3600
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    w = args.workers
    all_round_results = {}

    # ═══ ROUND 1: K-zone threshold sweep × TP × TF — CRYPTO ═════════════════
    if args.start_round <= 1 and time_remaining() > 120:
        tasks = []
        for tf in ["15m", "1h", "4h"]:
            syms = discover(CRYPTO_KLINES, tf, "USDT")
            for sym in syms:
                for d in ["LONG", "SHORT"]:
                    for k_thr in [20, 25, 30, 35, 40, 45]:
                        tasks.append((CRYPTO_KLINES, sym, tf, d, [0.25, 0.5, 1.0, 1.5, 2.0, 3.0, 5.0], k_thr, "candle", "bounce", (14, 5, 5), 0, 0, FEE_CRYPTO, ANNUAL_BARS_CRYPTO, 0, 0))
        all_round_results[1] = run_round(1, "K-zone threshold sweep (crypto)", tasks, w)

    # ═══ ROUND 2: Stoch parameter sweep — CRYPTO ════════════════════════════
    if args.start_round <= 2 and time_remaining() > 120:
        tasks = []
        for tf in ["15m", "1h"]:
            syms = discover(CRYPTO_KLINES, tf, "USDT")
            for sym in syms:
                for d in ["LONG", "SHORT"]:
                    for sk, ssk, ssd in [(9, 3, 3), (9, 5, 5), (14, 3, 3), (14, 5, 5), (14, 7, 7), (21, 5, 5), (21, 7, 7)]:
                        tasks.append((CRYPTO_KLINES, sym, tf, d, [0.5, 1.0, 2.0], 35, "candle", "bounce", (sk, ssk, ssd), 0, 0, FEE_CRYPTO, ANNUAL_BARS_CRYPTO, 0, 0))
        all_round_results[2] = run_round(2, "Stoch parameter sweep (crypto)", tasks, w)

    # ═══ ROUND 3: Entry filter combos — CRYPTO ══════════════════════════════
    if args.start_round <= 3 and time_remaining() > 120:
        tasks = []
        for tf in ["15m", "1h"]:
            syms = discover(CRYPTO_KLINES, tf, "USDT")
            for sym in syms:
                for d in ["LONG", "SHORT"]:
                    for filt in ["candle", "ha", "rsi", "bb", "dc", "multi", "none"]:
                        tasks.append((CRYPTO_KLINES, sym, tf, d, [0.5, 1.0, 2.0], 35, filt, "bounce", (14, 5, 5), 0, 0, FEE_CRYPTO, ANNUAL_BARS_CRYPTO, 0, 0))
        all_round_results[3] = run_round(3, "Entry filter combos (crypto)", tasks, w)

    # ═══ ROUND 4: Volume filter sweep — CRYPTO ══════════════════════════════
    if args.start_round <= 4 and time_remaining() > 120:
        tasks = []
        for tf in ["15m", "1h"]:
            syms = discover(CRYPTO_KLINES, tf, "USDT")
            for sym in syms:
                for d in ["LONG", "SHORT"]:
                    for rv in [0, 1.0, 1.3, 1.5, 2.0, 2.5]:
                        tasks.append((CRYPTO_KLINES, sym, tf, d, [0.5, 1.0, 2.0], 35, "candle", "bounce", (14, 5, 5), rv, 0, FEE_CRYPTO, ANNUAL_BARS_CRYPTO, 0, 0))
        all_round_results[4] = run_round(4, "Volume filter sweep (crypto)", tasks, w)

    # ═══ ROUND 5: Walk-forward validation — CRYPTO ══════════════════════════
    if args.start_round <= 5 and time_remaining() > 120:
        tasks = []
        for tf in ["15m", "1h"]:
            syms = discover(CRYPTO_KLINES, tf, "USDT")
            for sym in syms:
                for d in ["LONG", "SHORT"]:
                    for wf in [0.5, 0.6, 0.7]:
                        tasks.append((CRYPTO_KLINES, sym, tf, d, [0.5, 1.0, 2.0], 35, "candle", "bounce", (14, 5, 5), 0, 0, FEE_CRYPTO, ANNUAL_BARS_CRYPTO, 0, wf))
        all_round_results[5] = run_round(5, "Walk-forward validation (crypto)", tasks, w)

    # ═══ ROUND 6: Tradier stocks — K threshold × TP × TF ════════════════════
    if args.start_round <= 6 and time_remaining() > 120:
        tasks = []
        for tf in ["1h", "4h", "D"]:
            syms = discover(TRADIER_KLINES, tf, "")
            for sym in syms:
                for d in ["LONG"]:  # LONG only for stocks (HODL)
                    for k_thr in [25, 30, 35, 40]:
                        for filt in ["candle", "ha", "rsi"]:
                            tasks.append((TRADIER_KLINES, sym, tf, d, [0.5, 1.0, 2.0, 3.0, 5.0, 8.0], k_thr, filt, "bounce", (14, 5, 5), 0, 0, FEE_STOCK, ANNUAL_BARS_STOCK, 0, 0))
        all_round_results[6] = run_round(6, "Tradier LONG-only sweep", tasks, w)

    # ═══ ROUND 7: ADX trend filter — BOTH ═══════════════════════════════════
    if args.start_round <= 7 and time_remaining() > 120:
        tasks = []
        for tf in ["15m", "1h"]:
            syms = discover(CRYPTO_KLINES, tf, "USDT")
            for sym in syms:
                for d in ["LONG", "SHORT"]:
                    for adx in [0, 15, 20, 25, 30]:
                        tasks.append((CRYPTO_KLINES, sym, tf, d, [0.5, 1.0, 2.0], 35, "candle", "bounce", (14, 5, 5), 0, adx, FEE_CRYPTO, ANNUAL_BARS_CRYPTO, 0, 0))
        all_round_results[7] = run_round(7, "ADX trend filter (crypto)", tasks, w)

    # ═══ ROUND 8: ATR-scaled TP — BOTH ══════════════════════════════════════
    if args.start_round <= 8 and time_remaining() > 120:
        tasks = []
        for tf in ["15m", "1h"]:
            syms = discover(CRYPTO_KLINES, tf, "USDT")
            for sym in syms:
                for d in ["LONG", "SHORT"]:
                    for atr_mult in [0.25, 0.5, 0.75, 1.0, 1.5, 2.0]:
                        tasks.append((CRYPTO_KLINES, sym, tf, d, [99.0], 35, "candle", "bounce", (14, 5, 5), 0, 0, FEE_CRYPTO, ANNUAL_BARS_CRYPTO, atr_mult, 0))
        for tf in ["4h", "D"]:
            syms = discover(TRADIER_KLINES, tf, "")
            for sym in syms:
                for atr_mult in [0.5, 1.0, 1.5, 2.0, 3.0]:
                    tasks.append((TRADIER_KLINES, sym, tf, "LONG", [99.0], 35, "candle", "bounce", (14, 5, 5), 0, 0, FEE_STOCK, ANNUAL_BARS_STOCK, atr_mult, 0))
        all_round_results[8] = run_round(8, "ATR-scaled TP (both)", tasks, w)

    # ═══ ROUND 9: Reentry mode comparison — CRYPTO ═════════════════════════
    if args.start_round <= 9 and time_remaining() > 120:
        tasks = []
        for tf in ["15m", "1h"]:
            syms = discover(CRYPTO_KLINES, tf, "USDT")
            for sym in syms:
                for d in ["LONG", "SHORT"]:
                    for re in ["bounce", "immediate", "deep"]:
                        tasks.append((CRYPTO_KLINES, sym, tf, d, [0.5, 1.0, 2.0], 35, "candle", re, (14, 5, 5), 0, 0, FEE_CRYPTO, ANNUAL_BARS_CRYPTO, 0, 0))
        all_round_results[9] = run_round(9, "Reentry mode comparison (crypto)", tasks, w)

    # ═══ ROUND 10: Deep zone (K<20) vs standard (K<35) × all filters ═══════
    if args.start_round <= 10 and time_remaining() > 120:
        tasks = []
        for tf in ["15m", "1h", "4h"]:
            syms = discover(CRYPTO_KLINES, tf, "USDT")
            for sym in syms:
                for d in ["LONG", "SHORT"]:
                    for k_thr in [15, 20, 25]:
                        for filt in ["candle", "ha", "none"]:
                            tasks.append((CRYPTO_KLINES, sym, tf, d, [0.5, 1.0, 2.0, 3.0], k_thr, filt, "bounce", (14, 5, 5), 0, 0, FEE_CRYPTO, ANNUAL_BARS_CRYPTO, 0, 0))
        all_round_results[10] = run_round(10, "Deep zone K<20 vs standard (crypto)", tasks, w)

    # ═══ FINAL SUMMARY ══════════════════════════════════════════════════════
    logger.info(f"\n{'='*80}")
    logger.info(f"ALL ROUNDS COMPLETE — {hours_elapsed():.1f} hours elapsed")
    logger.info(f"{'='*80}")
    summary = {}
    for rn, results in all_round_results.items():
        if not results: continue
        by_dir = defaultdict(list)
        for r in results:
            by_dir[r["dir"]].append(r)
        for d, rs in by_dir.items():
            avg_wr = np.mean([r["wr"] for r in rs])
            avg_ret = np.mean([r["ret"] for r in rs])
            top = sorted(rs, key=lambda x: x["ret"], reverse=True)[:3]
            summary[f"R{rn}_{d}"] = {"n": len(rs), "avg_wr": round(avg_wr, 4), "avg_ret": round(avg_ret, 2), "top3": [{"sym": t["symbol"], "tf": t["tf"], "tp": t["tp"], "ret": t["ret"], "wr": t["wr"]} for t in top]}
            logger.info(f"  R{rn} {d}: {len(rs)} combos, avg WR={avg_wr:.1%}, avg ret={avg_ret:.1f}%, top={top[0]['symbol']} {top[0]['ret']:.1f}%")
    SUMMARY_FILE.write_text(json.dumps(summary, indent=2))
    logger.info(f"Summary saved to {SUMMARY_FILE}")
    # Save XLSX
    try:
        import pandas as pd
        all_results = []
        for rn, results in all_round_results.items():
            for r in results:
                r["round"] = rn
            all_results.extend(results)
        if all_results:
            df = pd.DataFrame(all_results)
            xlsx = RESULTS_DIR / "sweep_all.xlsx"
            df.to_excel(xlsx, index=False)
            logger.info(f"All results ({len(all_results)}) saved to {xlsx}")
    except Exception as e:
        logger.warning(f"Could not save XLSX: {e}")


if __name__ == "__main__":
    main()
