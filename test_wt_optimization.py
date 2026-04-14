#!/usr/bin/env python3
"""
WT PARAMETER OPTIMIZATION — Sweep ESA length, channel width, MA types, signal length
across ALL timeframes simultaneously. Uses 48 symbols with 4+ years of 1h data.

Tests every combination of:
  - ESA length (MA period): 6, 8, 10, 12, 14
  - Channel length: 8, 10, 14, 18, 21, 25
  - Signal length: 12, 15, 18, 21, 30
  - ESA MA type: ema, dema, sma
  - Channel MA type: ema, dema, sma
  - Smooth: 3, 4, 5
  - CI multiplier: 0.015, 0.020, 0.025
  - TF weights: multiple presets

Each combo is tested as entry (WT cross + MTS gate) + exit (WT velocity/exhaustion).

Usage:
  python3 test_wt_optimization.py --workers 14 --limit 48 --tf 1h
  python3 test_wt_optimization.py --workers 14 --limit 48 --tf 1h --phase 1  # ESA+Channel only
  python3 test_wt_optimization.py --workers 14 --limit 48 --tf 1h --phase 2  # MA types only
  python3 test_wt_optimization.py --workers 14 --limit 48 --tf 1h --phase 3  # Signal+Smooth+CI
  python3 test_wt_optimization.py --workers 14 --limit 48 --tf 1h --phase 4  # TF weights
"""
import argparse, json, math, sys, time, itertools
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

KLINES_DIR = Path("/Users/niels/Documents/binance/klines_cache")
if Path("/home/niels/binance-sandbox/klines_cache").exists():
    KLINES_DIR = Path("/home/niels/binance-sandbox/klines_cache")
WARMUP = 250
FEE = 0.0

# ═══════════════════════════════════════════════════════════════
# CUSTOM WAVETREND — parameterized for sweeping
# ═══════════════════════════════════════════════════════════════

def _dema(arr, span):
    """Double EMA on numpy array."""
    alpha = 2.0 / (span + 1)
    e1 = np.zeros_like(arr); e1[0] = arr[0]
    for i in range(1, len(arr)): e1[i] = alpha * arr[i] + (1 - alpha) * e1[i-1]
    e2 = np.zeros_like(e1); e2[0] = e1[0]
    for i in range(1, len(e1)): e2[i] = alpha * e1[i] + (1 - alpha) * e2[i-1]
    return 2.0 * e1 - e2

def _ema(arr, span):
    """EMA on numpy array."""
    alpha = 2.0 / (span + 1)
    out = np.zeros_like(arr); out[0] = arr[0]
    for i in range(1, len(arr)): out[i] = alpha * arr[i] + (1 - alpha) * out[i-1]
    return out

def _sma(arr, span):
    """SMA on numpy array."""
    out = np.zeros_like(arr)
    cs = np.cumsum(arr)
    out[span-1:] = (cs[span-1:] - np.concatenate([[0], cs[:-span]])) / span
    out[:span-1] = cs[:span-1] / np.arange(1, span)
    return out

def _ma(arr, span, ma_type):
    if ma_type == "dema": return _dema(arr, span)
    elif ma_type == "sma": return _sma(arr, span)
    return _ema(arr, span)

def custom_wavetrend(high, low, close, esa_len, chan_len, sig_len, smooth, esa_ma, chan_ma, ci_mult):
    """Compute WT1/WT2 with fully parameterized settings."""
    n = len(close)
    typical = (high + low + close) / 3.0
    esa = _ma(typical, esa_len, esa_ma)
    d = _ma(np.abs(typical - esa), chan_len, chan_ma)
    d[d == 0] = 1e-10
    ci = (typical - esa) / (ci_mult * d)
    wt1 = _ema(ci, sig_len)
    # Smooth wt2
    if smooth > 1:
        wt2 = _sma(wt1, smooth)
    else:
        wt2 = wt1.copy()
    return wt1, wt2

def load_klines(symbol, tf):
    """Load klines as numpy arrays. Handles both dict and list formats."""
    f = KLINES_DIR / f"{symbol}_{tf}.json"
    if not f.exists(): return None
    data = json.loads(f.read_text())
    if len(data) < WARMUP + 100: return None
    if isinstance(data[0], dict):
        o = np.array([float(d.get("open", 0)) for d in data])
        h = np.array([float(d.get("high", 0)) for d in data])
        l = np.array([float(d.get("low", 0)) for d in data])
        c = np.array([float(d.get("close", 0)) for d in data])
        v = np.array([float(d.get("volume", 0)) for d in data])
        ts = np.arange(len(data), dtype=float)  # Use index as timestamp proxy
        for i, d in enumerate(data):
            t = d.get("timestamp", "")
            if isinstance(t, (int, float)): ts[i] = float(t)
            elif isinstance(t, str) and t:
                try:
                    from datetime import datetime as _dt, timezone as _tz
                    ts[i] = _dt.fromisoformat(t.replace('Z', '+00:00')).timestamp()
                except: ts[i] = float(i)
    else:
        arr = np.array(data, dtype=float)
        ts = arr[:, 0]; o = arr[:, 1]; h = arr[:, 2]; l = arr[:, 3]; c = arr[:, 4]; v = arr[:, 5]
    return {"open": o, "high": h, "low": l, "close": c, "volume": v, "n": len(o), "timestamps": ts}

# ═══════════════════════════════════════════════════════════════
# MTS SCORING with custom WT params
# ═══════════════════════════════════════════════════════════════

def compute_mts_arrays(klines_by_tf, wt_params, tf_weights, primary_tf):
    """Compute MTS bottom_score and entry_quality arrays for full timeseries."""
    primary = klines_by_tf.get(primary_tf)
    if primary is None: return None, None, None, None
    n = primary["n"]
    # Compute WT for each TF
    wt_data = {}
    for tf_name, kl in klines_by_tf.items():
        p = wt_params.get(tf_name, wt_params.get("default", {}))
        wt1, wt2 = custom_wavetrend(kl["high"], kl["low"], kl["close"], p["esa"], p["chan"], p["sig"], p["smooth"], p["esa_ma"], p["chan_ma"], p["ci"])
        # Map to primary TF if different
        if tf_name != primary_tf and kl["n"] != n:
            primary_ts = primary["timestamps"]
            tf_ts = kl["timestamps"]
            idx_map = np.searchsorted(tf_ts, primary_ts, side="right") - 1
            np.clip(idx_map, 0, len(tf_ts) - 1, out=idx_map)
            wt1 = wt1[idx_map]; wt2 = wt2[idx_map]
        wt_data[tf_name] = {"wt1": wt1, "wt2": wt2, "wt_score": wt1 - wt2, "wt_bullish": wt1 > wt2}
        # Cross detection
        wt1_prev = np.roll(wt1, 1); wt2_prev = np.roll(wt2, 1)
        wt_data[tf_name]["cross_bull"] = (wt1_prev <= wt2_prev) & (wt1 > wt2)
        wt_data[tf_name]["cross_bear"] = (wt1_prev >= wt2_prev) & (wt1 < wt2)
        # Velocity
        lag = 3; vel = np.zeros(n)
        if len(wt1) > lag: vel[lag:] = wt1[lag:] - wt1[:-lag]
        wt_data[tf_name]["velocity"] = vel
    # Compute MTS scores per bar (vectorized)
    bottom_long = np.zeros(n); bottom_short = np.zeros(n)
    entry_long = np.zeros(n); entry_short = np.zeros(n)
    dir_arr = np.zeros(n)
    total_w = sum(tf_weights.get(tf, 1) for tf in wt_data)
    for tf_name, wd in wt_data.items():
        w = tf_weights.get(tf_name, 1)
        # Bottom scores
        bl = np.zeros(n); bs = np.zeros(n)
        bl += np.where(wd["wt1"] < -40, 15, np.where(wd["wt1"] < -20, 8, 0))
        bl += np.where(wd["cross_bull"], 15, 0)
        bl += np.where(wd["velocity"] > 0, 5, 0)
        bs += np.where(wd["wt1"] > 40, 15, np.where(wd["wt1"] > 20, 8, 0))
        bs += np.where(wd["cross_bear"], 15, 0)
        bs += np.where(wd["velocity"] < 0, 5, 0)
        bottom_long += bl * w; bottom_short += bs * w
        # Entry quality
        el = np.where(wd["cross_bull"], 15, 0) * w
        es = np.where(wd["cross_bear"], 15, 0) * w
        entry_long += el; entry_short += es
        # Direction
        d = np.where(wd["wt_bullish"], 30, -30) + np.where(wd["wt_score"] > 10, 20, np.where(wd["wt_score"] > 0, 10, np.where(wd["wt_score"] < -10, -20, -10)))
        dir_arr += d * w
    if total_w > 0:
        bottom_long /= total_w; bottom_short /= total_w
        entry_long /= total_w; entry_short /= total_w
        dir_arr /= total_w
    return bottom_long, bottom_short, entry_long, entry_short

def simulate_wt(klines_by_tf, wt_params, tf_weights, primary_tf, mts_thresholds, is_long):
    """Simulate trading using custom WT params + MTS gate."""
    primary = klines_by_tf.get(primary_tf)
    if primary is None: return None
    n = primary["n"]
    c = primary["close"]; h = primary["high"]; l = primary["low"]; o = primary["open"]
    result = compute_mts_arrays(klines_by_tf, wt_params, tf_weights, primary_tf)
    if result is None: return None
    bottom_long, bottom_short, entry_long, entry_short = result
    bottom = bottom_long if is_long else bottom_short
    entry_q = entry_long if is_long else entry_short
    min_bottom = mts_thresholds.get("min_bottom", 10)
    min_eq = mts_thresholds.get("min_eq", 5)
    min_gain = mts_thresholds.get("min_gain", 0.3) / 100.0
    # Get primary TF WT for exit signal
    p = wt_params.get(primary_tf, wt_params.get("default", {}))
    wt1, wt2 = custom_wavetrend(h, l, c, p["esa"], p["chan"], p["sig"], p["smooth"], p["esa_ma"], p["chan_ma"], p["ci"])
    vel = np.zeros(n); vel[3:] = wt1[3:] - wt1[:-3]
    trades = []
    last_exit = WARMUP
    for ei in range(WARMUP + 1, n - 2):
        if ei <= last_exit: continue
        if bottom[ei] < min_bottom: continue
        if entry_q[ei] < min_eq: continue
        ep = o[ei + 1] if ei + 1 < n else c[ei]
        if ep <= 0: continue
        mg = 0.0
        for bi in range(ei + 1, min(ei + 500, n)):  # Max 500 bars hold
            _c = c[bi]; _h = h[bi]; _l = l[bi]
            if _c <= 0: continue
            pnl = (_c - ep) / ep if is_long else (ep - _c) / ep
            hp = (_h - ep) / ep if is_long else (ep - _l) / ep
            mg = max(mg, hp)
            exit_sig = False
            if pnl > min_gain:
                wt_dec = (is_long and vel[bi] < -1.5) or (not is_long and vel[bi] > 1.5)
                wt_exhaust = (is_long and wt1[bi] > 60) or (not is_long and wt1[bi] < -60)
                deep_tp = pnl >= min_gain * 2
                if wt_dec or wt_exhaust or deep_tp: exit_sig = True
            if mg > 0.005 and pnl < 0.003: exit_sig = True
            if exit_sig:
                trades.append(pnl - 2 * FEE)
                last_exit = bi
                break
    if len(trades) < 5: return None
    t = np.array(trades)
    sharpe = np.mean(t) / np.std(t) * math.sqrt(len(t)) if np.std(t) > 1e-10 else 0
    return {"sharpe": round(sharpe, 3), "wr": round(np.mean(t > 0) * 100, 1), "trades": len(trades), "pnl": round(np.sum(t) * 100, 2), "avg_ret": round(np.mean(t) * 100, 3)}

# ═══════════════════════════════════════════════════════════════
# PARAMETER GRID — phased approach
# ═══════════════════════════════════════════════════════════════

TF_WEIGHT_PRESETS = {
    "default": {"1h": 5, "4h": 8, "D": 5},
    "htf_heavy": {"1h": 3, "4h": 12, "D": 8},
    "balanced": {"1h": 4, "4h": 4, "D": 4},
    "4h_dom": {"1h": 2, "4h": 15, "D": 3},
    "1h_dom": {"1h": 12, "4h": 4, "D": 2},
    "D_dom": {"1h": 3, "4h": 5, "D": 12},
}

def build_param_grid(phase, primary_tf):
    """Build parameter grid by phase (each phase holds other params at best known)."""
    # Best known defaults (from ez_indicators WT_TF_PARAMS)
    base = {"esa": 10, "chan": 10, "sig": 21, "smooth": 3, "esa_ma": "ema", "chan_ma": "ema", "ci": 0.015}
    combos = []
    if phase == 1:
        # Phase 1: ESA length × Channel length (core WT shape)
        for esa in [6, 8, 10, 12, 14, 16]:
            for chan in [8, 10, 14, 18, 21, 25, 30]:
                p = {**base, "esa": esa, "chan": chan}
                name = f"esa{esa}_chan{chan}"
                combos.append((name, p, "default"))
    elif phase == 2:
        # Phase 2: MA types (with best ESA/chan from phase 1, or sweep a few combos)
        for esa_ma in ["ema", "dema", "sma"]:
            for chan_ma in ["ema", "dema", "sma"]:
                for esa in [8, 10, 12]:
                    for chan in [10, 18, 25]:
                        p = {**base, "esa": esa, "chan": chan, "esa_ma": esa_ma, "chan_ma": chan_ma}
                        name = f"esa{esa}_{esa_ma}_chan{chan}_{chan_ma}"
                        combos.append((name, p, "default"))
    elif phase == 3:
        # Phase 3: Signal length × Smooth × CI (fine-tuning)
        for sig in [12, 15, 18, 21, 25, 30]:
            for smooth in [3, 4, 5]:
                for ci in [0.010, 0.015, 0.020, 0.025, 0.030]:
                    p = {**base, "sig": sig, "smooth": smooth, "ci": ci}
                    name = f"sig{sig}_sm{smooth}_ci{ci}"
                    combos.append((name, p, "default"))
    elif phase == 4:
        # Phase 4: TF weight combinations
        for wp_name, wp in TF_WEIGHT_PRESETS.items():
            for esa in [8, 10]:
                for chan in [10, 18, 25]:
                    p = {**base, "esa": esa, "chan": chan}
                    name = f"esa{esa}_chan{chan}_{wp_name}"
                    combos.append((name, p, wp_name))
    elif phase == 0:
        # Phase 0: Quick scan — most impactful params only
        for esa in [6, 8, 10, 14]:
            for chan in [8, 12, 18, 25]:
                for esa_ma in ["ema", "dema"]:
                    for sig in [15, 21, 30]:
                        p = {**base, "esa": esa, "chan": chan, "esa_ma": esa_ma, "sig": sig}
                        name = f"esa{esa}_{esa_ma}_chan{chan}_sig{sig}"
                        combos.append((name, p, "default"))
    return combos

def worker(args):
    sym, param_grid, primary_tf, tf_list, mts_thresholds = args
    # Load klines for all TFs
    klines_by_tf = {}
    for tf in tf_list:
        kl = load_klines(sym, tf)
        if kl: klines_by_tf[tf] = kl
    if primary_tf not in klines_by_tf: return sym, {}
    results = {}
    for side in ["LONG", "SHORT"]:
        is_long = side == "LONG"
        for name, wt_p, wp_name in param_grid:
            combo = f"{side}|{name}"
            # Apply same WT params to all TFs (testing universal params)
            wt_params = {tf: wt_p for tf in klines_by_tf}
            tf_weights = TF_WEIGHT_PRESETS.get(wp_name, TF_WEIGHT_PRESETS["default"])
            r = simulate_wt(klines_by_tf, wt_params, tf_weights, primary_tf, mts_thresholds, is_long)
            if r: results[combo] = r
    return sym, results

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--limit", type=int, default=48)
    parser.add_argument("--tf", default="1h")
    parser.add_argument("--phase", type=int, default=0, help="0=quick scan, 1=esa+chan, 2=MA types, 3=sig+smooth+ci, 4=TF weights")
    parser.add_argument("--symbols", default=None)
    parser.add_argument("--klines-dir", default=None, help="Override klines directory (e.g. klines_cache/tradier)")
    args = parser.parse_args()
    global KLINES_DIR
    if args.klines_dir:
        KLINES_DIR = Path(args.klines_dir)
    primary_tf = args.tf
    if primary_tf == "1h":
        tf_list = ["1h", "4h", "D"]
    elif primary_tf == "15m":
        tf_list = ["15m", "1h", "4h"]
    else:
        tf_list = [primary_tf]
    sym_file = Path(args.symbols) if args.symbols else None
    if not sym_file or not sym_file.exists():
        sym_file = KLINES_DIR.parent / "backtest_48_symbols.json"
    if not sym_file.exists():
        sym_file = Path("/home/niels/binance-sandbox/backtest_48_symbols.json")
    if sym_file.exists():
        syms = json.loads(sym_file.read_text())
    else:
        syms = sorted([f.stem.replace(f"_{primary_tf}", "") for f in KLINES_DIR.glob(f"*_{primary_tf}.json")])
        syms = [s for s in syms if "USDT" in s]
    import random; random.seed(42); random.shuffle(syms)
    syms = syms[:args.limit]
    param_grid = build_param_grid(args.phase, primary_tf)
    mts_thresholds = {"min_bottom": 10, "min_eq": 5, "min_gain": 0.3}
    total_combos = len(param_grid) * 2
    print(f"WT PARAMETER OPTIMIZATION — Phase {args.phase}")
    print(f"Symbols: {len(syms)} | Param combos: {len(param_grid)} | Total: {total_combos * len(syms):,}")
    print(f"TFs: {tf_list} | Primary: {primary_tf}")
    if param_grid:
        print(f"Sample: {param_grid[0][0]} → {param_grid[0][1]}")
    start = time.time()
    all_results = {}
    tasks = [(sym, param_grid, primary_tf, tf_list, mts_thresholds) for sym in syms]
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(worker, t): t[0] for t in tasks}
        done = 0
        for f in as_completed(futures):
            sym, results = f.result()
            if results: all_results[sym] = results
            done += 1
            if done % 5 == 0:
                elapsed = time.time() - start
                print(f"  {done}/{len(syms)} ({len(all_results)} with data) {elapsed:.0f}s")
    elapsed = time.time() - start
    print(f"\nDone: {elapsed:.0f}s — {len(all_results)} symbols")
    # Aggregate
    agg = {}
    for sym, results in all_results.items():
        for name, r in results.items():
            if name not in agg: agg[name] = []
            agg[name].append(r)
    ranked = sorted([(k, np.mean([r["sharpe"] for r in v]), np.mean([r["wr"] for r in v]), np.mean([r["avg_ret"] for r in v]), np.mean([r["trades"] for r in v]), len(v)) for k, v in agg.items() if len(v) >= 5], key=lambda x: x[1], reverse=True)
    # Print results
    print(f"\n{'Combo':70s} {'Sharpe':>7s} {'WR':>5s} {'AvgRet':>7s} {'Trades':>6s} {'N':>3s}")
    print("=" * 100)
    long_ranked = [r for r in ranked if r[0].startswith("LONG")]
    print(f"\n--- TOP 20 LONG ---")
    for name, sharpe, wr, avg, trades, n in long_ranked[:20]:
        print(f"{name:70s} {sharpe:>+7.3f} {wr:>4.1f}% {avg:>+6.3f}% {trades:>5.0f} {n:>3d}")
    short_ranked = [r for r in ranked if r[0].startswith("SHORT")]
    print(f"\n--- TOP 20 SHORT ---")
    for name, sharpe, wr, avg, trades, n in short_ranked[:20]:
        print(f"{name:70s} {sharpe:>+7.3f} {wr:>4.1f}% {avg:>+6.3f}% {trades:>5.0f} {n:>3d}")
    print(f"\n--- TOP 20 OVERALL ---")
    for name, sharpe, wr, avg, trades, n in ranked[:20]:
        print(f"{name:70s} {sharpe:>+7.3f} {wr:>4.1f}% {avg:>+6.3f}% {trades:>5.0f} {n:>3d}")
    # ═══════════════════════════════════════════════════════════════
    # PARAMETER ANALYSIS — which values appear most in top results
    # ═══════════════════════════════════════════════════════════════
    print(f"\n{'='*100}")
    print(f"PARAMETER ANALYSIS (top 50 combos)")
    print(f"{'='*100}")
    param_scores = {}
    for name, sharpe, wr, avg, trades, n in ranked[:50]:
        parts = name.split("|", 1)[-1]
        # Parse params
        for token in parts.split("_"):
            if token.startswith("esa") and token[3:].isdigit(): param_scores.setdefault(f"esa={token[3:]}", []).append(sharpe)
            elif token.startswith("chan") and token[4:].isdigit(): param_scores.setdefault(f"chan={token[4:]}", []).append(sharpe)
            elif token.startswith("sig") and token[3:].isdigit(): param_scores.setdefault(f"sig={token[3:]}", []).append(sharpe)
            elif token.startswith("sm") and token[2:].isdigit(): param_scores.setdefault(f"smooth={token[2:]}", []).append(sharpe)
            elif token.startswith("ci"): param_scores.setdefault(f"ci={token[2:]}", []).append(sharpe)
            elif token in ["ema", "dema", "sma"]: param_scores.setdefault(f"ma_type={token}", []).append(sharpe)
            elif token in TF_WEIGHT_PRESETS: param_scores.setdefault(f"weights={token}", []).append(sharpe)
    for param, sharpes in sorted(param_scores.items(), key=lambda x: -np.mean(x[1])):
        print(f"  {param:30s} count={len(sharpes):>3d} avg_sharpe={np.mean(sharpes):>+.3f} min={np.min(sharpes):>+.3f} max={np.max(sharpes):>+.3f}")
    # Save
    out = Path("data") / f"wt_opt_phase{args.phase}_{primary_tf}_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"phase": args.phase, "ranked": [(k, s, w, a, t, n) for k, s, w, a, t, n in ranked[:200]], "total_combos": total_combos, "symbols": len(all_results), "elapsed": elapsed, "param_analysis": {k: {"count": len(v), "avg": np.mean(v), "min": np.min(v), "max": np.max(v)} for k, v in param_scores.items()}}, indent=2))
    print(f"\nSaved: {out}")

if __name__ == "__main__":
    main()
