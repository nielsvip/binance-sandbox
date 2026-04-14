#!/usr/bin/env python3
"""
EVALUATE FUNCTION ABLATION TEST — Disable each evaluate_ function one by one.
Simulates the logic of each evaluate function using real indicator data, then tests:
1. ALL enabled (baseline)
2. Each function disabled individually
3. Only one function enabled at a time
4. MTS gate ON vs OFF for each function
5. Different MTS threshold combos

This tells us which evaluate functions HELP and which HURT performance.

Usage:
  python3 test_evaluate_ablation.py --workers 4 --limit 30
  python3 test_evaluate_ablation.py --workers 4 --limit 50 --full
"""
import argparse, json, math, sys, time, itertools
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import backtest_engine
FEE = 0.0

# ═══════════════════════════════════════════════════════════════
# SIMULATED EVALUATE FUNCTIONS — recreate each function's logic
# ═══════════════════════════════════════════════════════════════

def _g(ind, n, key, idx, default=0.0):
    v = ind.get(key)
    if v is not None and isinstance(v, np.ndarray) and len(v) == n:
        return float(v[idx])
    return default

def _gb(ind, n, key, idx, default=False):
    v = ind.get(key)
    if v is not None and isinstance(v, np.ndarray) and len(v) == n:
        return bool(v[idx])
    return default

def eval_technical_signals(ind, n, idx, is_long, tf_map):
    """Simulates evaluate_technical_indicator_signals: structure breaks + HA color + crossovers."""
    focus = tf_map['scalp']
    htf1 = tf_map['htf1']
    # HA color confirmation
    ha_ok = _gb(ind, n, f'ha_green_{focus}', idx) if is_long else _gb(ind, n, f'ha_red_{focus}', idx)
    # WT cross as primary signal (replaces stoch crossover)
    wt_cross = _gb(ind, n, f'wt_cross_bull_{focus}', idx) if is_long else _gb(ind, n, f'wt_cross_bear_{focus}', idx)
    wt_cross_htf = _gb(ind, n, f'wt_cross_bull_{htf1}', idx) if is_long else _gb(ind, n, f'wt_cross_bear_{htf1}', idx)
    # K crossover as fallback
    k = _g(ind, n, f'stoch_k_{focus}', idx, 50); d = _g(ind, n, f'stoch_d_{focus}', idx, 50)
    k_prev = _g(ind, n, f'stoch_k_{focus}', max(0, idx-1), 50)
    k_cross = (k > d and k_prev <= d and k < 30) if is_long else (k < d and k_prev >= d and k > 70)
    # Structure break: higher high (long) or lower low (short)
    h = _g(ind, n, f'high_{htf1}', idx); h_prev = _g(ind, n, f'high_{htf1}', max(0, idx-1))
    l = _g(ind, n, f'low_{htf1}', idx); l_prev = _g(ind, n, f'low_{htf1}', max(0, idx-1))
    struct_break = (h > h_prev) if is_long else (l < l_prev)
    has_signal = wt_cross or wt_cross_htf or k_cross
    return has_signal and (ha_ok or struct_break), 75.0

def eval_ranking_momentum(ind, n, idx, is_long, tf_map):
    """Simulates evaluate_ranking_momentum_trade: WT alignment across TFs + trend confirmation."""
    wt1_3m = _g(ind, n, 'wt1_3m', idx); wt2_3m = _g(ind, n, 'wt2_3m', idx)
    wt1_15m = _g(ind, n, 'wt1_15m', idx); wt2_15m = _g(ind, n, 'wt2_15m', idx)
    wt1_1h = _g(ind, n, 'wt1_1h', idx); wt2_1h = _g(ind, n, 'wt2_1h', idx)
    ha_3m_g = _gb(ind, n, 'ha_green_3m', idx); ha_3m_r = _gb(ind, n, 'ha_red_3m', idx)
    if is_long:
        wt_aligned = sum([wt1_3m > wt2_3m, wt1_15m > wt2_15m, wt1_1h > wt2_1h])
        ha_ok = ha_3m_g
        trend_bad = (wt1_3m < wt2_3m and wt1_15m < wt2_15m)
    else:
        wt_aligned = sum([wt1_3m < wt2_3m, wt1_15m < wt2_15m, wt1_1h < wt2_1h])
        ha_ok = ha_3m_r
        trend_bad = (wt1_3m > wt2_3m and wt1_15m > wt2_15m)
    if trend_bad: return False, 0
    return wt_aligned >= 2 and ha_ok, 70.0

def eval_reentry(ind, n, idx, is_long, tf_map):
    """Simulates evaluate_reentry: WT cross + K recovery + DC position."""
    focus = tf_map['scalp']; htf1 = tf_map['htf1']
    # WT cross on scalp TF
    wt_cross = _gb(ind, n, f'wt_cross_bull_{focus}', idx) if is_long else _gb(ind, n, f'wt_cross_bear_{focus}', idx)
    # K recovery from extreme
    k = _g(ind, n, f'stoch_k_{focus}', idx, 50); d = _g(ind, n, f'stoch_d_{focus}', idx, 50)
    k_htf = _g(ind, n, f'stoch_k_{htf1}', idx, 50); d_htf = _g(ind, n, f'stoch_d_{htf1}', idx, 50)
    if is_long:
        k_ok = k > d or k < 20  # Crossing up or deeply oversold
        htf_ok = k_htf > d_htf
    else:
        k_ok = k < d or k > 80
        htf_ok = k_htf < d_htf
    # DC position (near bottom of channel for longs)
    dc_pos = _g(ind, n, f'dc_position_{focus}', idx, 0.5)
    dc_ok = (dc_pos < 0.3) if is_long else (dc_pos > 0.7)
    return (wt_cross or k_ok) and (htf_ok or dc_ok), 80.0

def eval_augmentation(ind, n, idx, is_long, tf_map):
    """Simulates evaluate_augmentation: WT direction across TFs + K runway."""
    wt1_3m = _g(ind, n, 'wt1_3m', idx); wt2_3m = _g(ind, n, 'wt2_3m', idx)
    wt1_15m = _g(ind, n, 'wt1_15m', idx); wt2_15m = _g(ind, n, 'wt2_15m', idx)
    wt1_1h = _g(ind, n, 'wt1_1h', idx); wt2_1h = _g(ind, n, 'wt2_1h', idx)
    k_3m = _g(ind, n, 'stoch_k_3m', idx, 50); k_15m = _g(ind, n, 'stoch_k_15m', idx, 50)
    if is_long:
        wt_aligned = sum([wt1_3m > wt2_3m, wt1_15m > wt2_15m, wt1_1h > wt2_1h])
        k_runway = min(100 - k_3m, 100 - k_15m) / 100.0
        k_not_exhausted = k_3m < 85 and k_15m < 85
    else:
        wt_aligned = sum([wt1_3m < wt2_3m, wt1_15m < wt2_15m, wt1_1h < wt2_1h])
        k_runway = min(k_3m, k_15m) / 100.0
        k_not_exhausted = k_3m > 15 and k_15m > 15
    return wt_aligned >= 2 and k_not_exhausted, 70.0 * k_runway

def eval_mts_gate(ind, n, idx, is_long, current_price, tf_map, mts_params):
    """MTS gate: analyze_multi_tf_state scores must pass thresholds."""
    weights = {"1m": 1, "3m": 2, "15m": 3, "1h": 5, "4h": 8, "D": 5}
    min_bottom = mts_params.get("min_bottom", 20)
    min_eq = mts_params.get("min_eq", 10)
    tf_configs = [('1m', 1), ('3m', 2), ('15m', 3), ('1h', 5), ('4h', 8)]
    total_bottom = 0.0; total_entry = 0.0; total_dir = 0.0; total_weight = 0.0
    extreme_count = 0
    for tf_name, weight in tf_configs:
        k_val = _g(ind, n, f'stoch_k_{tf_name}', idx, 50)
        d_val = _g(ind, n, f'stoch_d_{tf_name}', idx, 50)
        wt1 = _g(ind, n, f'wt1_{tf_name}', idx); wt2 = _g(ind, n, f'wt2_{tf_name}', idx)
        wt_score = wt1 - wt2
        wt_cross_bull = _gb(ind, n, f'wt_cross_bull_{tf_name}', idx)
        wt_cross_bear = _gb(ind, n, f'wt_cross_bear_{tf_name}', idx)
        wt_velocity = _g(ind, n, f'wt_velocity_{tf_name}', idx)
        k_extreme = (k_val < 10) if is_long else (k_val > 90)
        k_zone = (k_val < 30) if is_long else (k_val > 70)
        if k_extreme: extreme_count += 1
        tf_bottom = 0.0
        if is_long:
            if k_extreme: tf_bottom += 20
            if k_zone: tf_bottom += 10
            if wt_cross_bull: tf_bottom += 15
            if wt1 < -40: tf_bottom += 15
            elif wt1 < -20: tf_bottom += 8
            if wt_velocity > 0 and k_val > d_val: tf_bottom += 5
        else:
            if k_extreme: tf_bottom += 20
            if k_zone: tf_bottom += 10
            if wt_cross_bear: tf_bottom += 15
            if wt1 > 40: tf_bottom += 15
            elif wt1 > 20: tf_bottom += 8
            if wt_velocity < 0 and k_val < d_val: tf_bottom += 5
        tf_entry = 0.0
        if (wt_cross_bull if is_long else wt_cross_bear): tf_entry += 15
        total_bottom += tf_bottom * weight; total_entry += tf_entry * weight; total_weight += weight
    if total_weight > 0:
        bottom_score = total_bottom / total_weight
        entry_quality = total_entry / total_weight
    else:
        return False, 0
    if extreme_count >= 4: bottom_score *= 2.0
    elif extreme_count >= 3: bottom_score *= 1.5
    return bottom_score >= min_bottom and entry_quality >= min_eq, bottom_score

# ═══════════════════════════════════════════════════════════════
# EXIT CONDITIONS
# ═══════════════════════════════════════════════════════════════

def check_exit(ind, n, idx, is_long, entry_price, current_price, mg, tf_map, exit_params):
    """Check exit conditions — WT velocity declining + K exhaustion + NOLOSS safety."""
    pnl = (current_price - entry_price) / entry_price if is_long else (entry_price - current_price) / entry_price
    min_gain = exit_params.get("min_gain", 0.3) / 100.0
    focus = tf_map['scalp']
    # WT velocity declining (primary exit)
    wt_vel = _g(ind, n, f'wt_velocity_{focus}', idx)
    wt_declining = (is_long and wt_vel < -1.5) or (not is_long and wt_vel > 1.5)
    # K declining (secondary)
    k = _g(ind, n, f'stoch_k_{focus}', idx, 50)
    k_prev = _g(ind, n, f'stoch_k_{focus}', max(0, idx-1), 50)
    k_declining = (is_long and k < k_prev) or (not is_long and k > k_prev)
    # WT exhaustion on HTF
    wt1_1h = _g(ind, n, 'wt1_1h', idx); wt2_1h = _g(ind, n, 'wt2_1h', idx)
    wt_exhaust = (is_long and wt1_1h > 60) or (not is_long and wt1_1h < -60)
    # Deep TP
    deep_tp = pnl >= min_gain * 2
    if pnl > min_gain and (wt_declining or k_declining or deep_tp or wt_exhaust):
        return True, pnl
    # NOLOSS safety
    if mg > 0.005 and pnl < 0.003:
        return True, pnl
    return False, pnl

# ═══════════════════════════════════════════════════════════════
# ABLATION SIMULATION
# ═══════════════════════════════════════════════════════════════

# Which evaluate functions to include in each ablation config
EVAL_FUNCTIONS = {
    "tech": eval_technical_signals,
    "ranking": eval_ranking_momentum,
    "reentry": eval_reentry,
    "augment": eval_augmentation,
}

def build_ablation_configs(mts_params_list):
    """Build ablation configs: all on, each off, each solo, MTS gate combos."""
    configs = []
    all_funcs = list(EVAL_FUNCTIONS.keys())
    # 1. ALL enabled (baseline)
    configs.append(("ALL_ON", all_funcs, None))
    # 2. Each function DISABLED (one at a time)
    for func_name in all_funcs:
        remaining = [f for f in all_funcs if f != func_name]
        configs.append((f"NO_{func_name.upper()}", remaining, None))
    # 3. Each function SOLO (only one enabled)
    for func_name in all_funcs:
        configs.append((f"ONLY_{func_name.upper()}", [func_name], None))
    # 4. ALL + MTS gate with different params
    for mts_p in mts_params_list:
        name = f"ALL+MTS_b{mts_p['min_bottom']}_eq{mts_p['min_eq']}"
        configs.append((name, all_funcs, mts_p))
    # 5. Best combos + MTS
    for func_name in all_funcs:
        remaining = [f for f in all_funcs if f != func_name]
        for mts_p in mts_params_list[:3]:  # Top 3 MTS params only
            name = f"NO_{func_name.upper()}+MTS_b{mts_p['min_bottom']}"
            configs.append((name, remaining, mts_p))
    return configs

def simulate_ablation(precomputed, config_name, enabled_funcs, mts_params, tf_map, is_long, exit_params):
    """Run one ablation config on one symbol."""
    ind = precomputed["indicators"]
    n = precomputed["n"]
    c = ind.get("current_price")
    if c is None or not isinstance(c, np.ndarray) or len(c) < 300: return None
    h = ind.get(f"high_{tf_map['focus']}", c)
    l = ind.get(f"low_{tf_map['focus']}", c)
    o = ind.get(f"open_{tf_map['focus']}", c)
    eval_func_map = {name: EVAL_FUNCTIONS[name] for name in enabled_funcs}
    trades = []
    last_exit = 250
    for ei in range(251, n - 2):
        if ei <= last_exit: continue
        cp = float(c[ei])
        if cp <= 0: continue
        # Run all enabled evaluate functions — ANY passing = entry signal
        any_signal = False
        best_conviction = 0.0
        for name, func in eval_func_map.items():
            ok, conv = func(ind, n, ei, is_long, tf_map)
            if ok:
                any_signal = True
                best_conviction = max(best_conviction, conv)
        if not any_signal: continue
        # MTS gate (if enabled)
        if mts_params:
            mts_ok, mts_score = eval_mts_gate(ind, n, ei, is_long, cp, tf_map, mts_params)
            if not mts_ok: continue
        # ENTER at next bar open
        ep = float(o[ei + 1]) if ei + 1 < n else cp
        if ep <= 0: continue
        mg = 0.0
        for bi in range(ei + 1, n):
            _c = float(c[bi]); _h = float(h[bi]); _l = float(l[bi])
            if _c <= 0: continue
            hp = (_h - ep) / ep if is_long else (ep - _l) / ep
            mg = max(mg, hp)
            exit_sig, pnl = check_exit(ind, n, bi, is_long, ep, _c, mg, tf_map, exit_params)
            if exit_sig:
                trades.append(pnl - 2 * FEE)
                last_exit = bi
                break
    if len(trades) < 5: return None
    t = np.array(trades)
    sharpe = np.mean(t) / np.std(t) * math.sqrt(len(t)) if np.std(t) > 1e-10 else 0
    return {"sharpe": round(sharpe, 3), "wr": round(np.mean(t > 0) * 100, 1), "trades": len(trades), "pnl": round(np.sum(t) * 100, 2), "avg_ret": round(np.mean(t) * 100, 3)}

def worker(args):
    sym, ablation_configs, tf_map, primary_tf, exit_params = args
    from backtest_engine import precompute_real_indicators
    pre = precompute_real_indicators(sym, primary_tf)
    if pre is None: return sym, {}
    # Map HTFs
    all_tfs = set(v for v in tf_map.values() if v and v != primary_tf)
    for htf in all_tfs:
        htf_pre = precompute_real_indicators(sym, htf)
        if htf_pre:
            htf_ts = htf_pre["df"]['timestamp_dt'].values.astype('datetime64[s]').astype(np.int64)
            primary_ts = pre["df"]['timestamp_dt'].values.astype('datetime64[s]').astype(np.int64)
            idx_map = np.searchsorted(htf_ts, primary_ts, side="right") - 1
            np.clip(idx_map, 0, len(htf_ts) - 1, out=idx_map)
            for key, arr in htf_pre["indicators"].items():
                if isinstance(arr, np.ndarray):
                    try: pre["indicators"][key] = arr[idx_map]
                    except: pass
    results = {}
    for side in ["LONG", "SHORT"]:
        is_long = side == "LONG"
        for config_name, enabled_funcs, mts_params in ablation_configs:
            combo = f"{side}|{config_name}"
            r = simulate_ablation(pre, config_name, enabled_funcs, mts_params, tf_map, is_long, exit_params)
            if r: results[combo] = r
    return sym, results

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--tf", default="15m", help="Primary TF (default: 15m for 4+ year data)")
    parser.add_argument("--symbols", default=None, help="Path to symbols JSON file")
    parser.add_argument("--klines-dir", default=None, help="Override klines directory (e.g. klines_cache/tradier)")
    args = parser.parse_args()
    backtest_engine.KLINES_DIR = Path("/Users/niels/Documents/binance/klines_cache")
    if Path("/home/niels/binance-sandbox/klines_cache").exists():
        backtest_engine.KLINES_DIR = Path("/home/niels/binance-sandbox/klines_cache")
    if args.klines_dir:
        backtest_engine.KLINES_DIR = Path(args.klines_dir)
    primary_tf = args.tf
    if primary_tf == "15m":
        tf_map = {"micro": "15m", "scalp": "15m", "focus": "15m", "htf1": "1h", "htf2": "4h", "htf3": "D"}
    elif primary_tf == "3m":
        tf_map = {"micro": "1m", "scalp": "3m", "focus": "3m", "htf1": "15m", "htf2": "1h", "htf3": "4h"}
    else:
        tf_map = {"micro": primary_tf, "scalp": primary_tf, "focus": primary_tf, "htf1": "1h", "htf2": "4h", "htf3": "D"}
    sym_file = Path(args.symbols) if args.symbols else None
    if not sym_file or not sym_file.exists():
        sym_file = Path(backtest_engine.KLINES_DIR).parent / "backtest_48_symbols.json"
    if not sym_file.exists():
        sym_file = Path(backtest_engine.KLINES_DIR).parent / "og_symbols.json"
    if not sym_file.exists(): sym_file = Path("/home/niels/binance-sandbox/backtest_48_symbols.json")
    if sym_file.exists():
        syms = json.loads(sym_file.read_text())
    else:
        syms = sorted([f.stem.replace(f"_{primary_tf}", "") for f in backtest_engine.KLINES_DIR.glob(f"*_{primary_tf}.json") if len(json.loads(f.read_text())) > 300])
    import random; random.seed(42); random.shuffle(syms)
    syms = syms[:args.limit]
    # MTS parameter combos to test
    if args.full:
        mts_params_list = [{"min_bottom": b, "min_eq": eq} for b, eq in itertools.product([10, 15, 20, 25, 30, 40], [0, 5, 10, 15, 20, 30])]
    else:
        mts_params_list = [{"min_bottom": 15, "min_eq": 5}, {"min_bottom": 20, "min_eq": 10}, {"min_bottom": 25, "min_eq": 15}, {"min_bottom": 30, "min_eq": 10}, {"min_bottom": 40, "min_eq": 20}]
    exit_params = {"min_gain": 0.3}
    ablation_configs = build_ablation_configs(mts_params_list)
    total_combos = len(ablation_configs) * 2  # LONG + SHORT
    print(f"EVALUATE FUNCTION ABLATION TEST")
    print(f"Symbols: {len(syms)} | Ablation configs: {len(ablation_configs)} | Total: {total_combos * len(syms):,}")
    print(f"Functions: {list(EVAL_FUNCTIONS.keys())}")
    print(f"MTS params: {len(mts_params_list)} combos")
    start = time.time()
    all_results = {}
    tasks = [(sym, ablation_configs, tf_map, primary_tf, exit_params) for sym in syms]
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
    # ═══════════════════════════════════════════════════════════════
    # ABLATION ANALYSIS — compare each config vs baseline
    # ═══════════════════════════════════════════════════════════════
    baseline_map = {}
    for name, sharpe, wr, avg, trades, n in ranked:
        side = name.split("|")[0]
        config = name.split("|")[1]
        baseline_map[name] = (sharpe, wr, avg, trades, n)
    print(f"\n{'='*120}")
    print(f"ABLATION RESULTS — What happens when each evaluate function is disabled?")
    print(f"{'='*120}")
    print(f"\n{'Config':50s} {'Sharpe':>7s} {'WR':>5s} {'AvgRet':>7s} {'Trades':>6s} {'N':>3s} {'Delta':>7s}")
    print("-" * 90)
    # Get baselines
    for side in ["LONG", "SHORT"]:
        print(f"\n--- {side} ---")
        baseline_key = f"{side}|ALL_ON"
        bl = baseline_map.get(baseline_key, (0, 0, 0, 0, 0))
        bl_sharpe = bl[0]
        print(f"  {'ALL_ON (baseline)':48s} {bl[0]:>+7.3f} {bl[1]:>4.1f}% {bl[2]:>+6.3f}% {bl[3]:>5.0f} {bl[4]:>3d}   {'---':>7s}")
        # Show each ablation config
        for config_name, _, _ in ablation_configs:
            if config_name == "ALL_ON": continue
            key = f"{side}|{config_name}"
            if key in baseline_map:
                s, w, a, t, n = baseline_map[key]
                delta = s - bl_sharpe
                marker = " ⭐" if delta > 0.3 else (" ⚠️" if delta < -0.3 else "")
                print(f"  {config_name:48s} {s:>+7.3f} {w:>4.1f}% {a:>+6.3f}% {t:>5.0f} {n:>3d} {delta:>+7.3f}{marker}")
    # ═══════════════════════════════════════════════════════════════
    # TOP COMBOS OVERALL
    # ═══════════════════════════════════════════════════════════════
    print(f"\n{'='*120}")
    print(f"TOP 30 OVERALL (by Sharpe)")
    print(f"{'='*120}")
    print(f"{'Combo':60s} {'Sharpe':>7s} {'WR':>5s} {'AvgRet':>7s} {'Trades':>6s} {'N':>3s}")
    for name, sharpe, wr, avg, trades, n in ranked[:30]:
        print(f"{name:60s} {sharpe:>+7.3f} {wr:>4.1f}% {avg:>+6.3f}% {trades:>5.0f} {n:>3d}")
    # ═══════════════════════════════════════════════════════════════
    # FUNCTION IMPACT ANALYSIS
    # ═══════════════════════════════════════════════════════════════
    print(f"\n{'='*120}")
    print(f"FUNCTION IMPACT SUMMARY")
    print(f"{'='*120}")
    for func_name in EVAL_FUNCTIONS:
        print(f"\n--- {func_name.upper()} ---")
        for side in ["LONG", "SHORT"]:
            bl = baseline_map.get(f"{side}|ALL_ON", (0,0,0,0,0))
            no_func = baseline_map.get(f"{side}|NO_{func_name.upper()}", (0,0,0,0,0))
            only_func = baseline_map.get(f"{side}|ONLY_{func_name.upper()}", (0,0,0,0,0))
            delta_remove = no_func[0] - bl[0]
            impact = "HELPS" if delta_remove < -0.1 else ("HURTS" if delta_remove > 0.1 else "NEUTRAL")
            print(f"  {side}: ALL={bl[0]:+.3f} → NO_{func_name.upper()}={no_func[0]:+.3f} (delta={delta_remove:+.3f} = {impact}) | ONLY={only_func[0]:+.3f}")
    # Save
    out = Path("data") / f"eval_ablation_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"ranked": [(k, s, w, a, t, n) for k, s, w, a, t, n in ranked[:300]], "total_combos": total_combos, "symbols": len(all_results), "elapsed": elapsed}, indent=2))
    print(f"\nSaved: {out}")

if __name__ == "__main__":
    main()
