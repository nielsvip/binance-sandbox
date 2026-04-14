#!/usr/bin/env python3
"""
MTS (Multi-TF State) PARAMETER SWEEP — Test analyze_multi_tf_state() thresholds.
Sweeps bottom_score, entry_quality, direction thresholds, TF weights, K extreme bonuses.
Uses real klines + real indicators via backtest_engine.
Compares Sharpe/WR across parameter combos.

Usage:
  python3 test_mts_sweep.py --workers 4 --limit 30
  python3 test_mts_sweep.py --workers 4 --limit 30 --quick
"""
import argparse, json, math, sys, time, itertools
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import backtest_engine
FEE = 0.0

# TF weight presets to sweep
TF_WEIGHT_PRESETS = {
    "default": {"1m": 1, "3m": 2, "15m": 3, "1h": 5, "4h": 8, "D": 5},
    "htf_heavy": {"1m": 1, "3m": 1, "15m": 2, "1h": 8, "4h": 12, "D": 8},
    "ltf_heavy": {"1m": 3, "3m": 5, "15m": 4, "1h": 3, "4h": 2, "D": 1},
    "balanced": {"1m": 2, "3m": 3, "15m": 4, "1h": 4, "4h": 3, "D": 2},
    "4h_dom": {"1m": 1, "3m": 2, "15m": 3, "1h": 4, "4h": 15, "D": 3},
}

def analyze_multi_tf_state_backtest(ind, n, idx, is_long, current_price, tf_map, weights):
    """Vectorized version of analyze_multi_tf_state for backtesting."""
    def _g(key, default=0.0):
        v = ind.get(key)
        if v is not None and isinstance(v, np.ndarray) and len(v) == n:
            return float(v[idx])
        return default
    def _gb(key, default=False):
        v = ind.get(key)
        if v is not None and isinstance(v, np.ndarray) and len(v) == n:
            return bool(v[idx])
        return default
    tf_configs = []
    for tf_key in ['micro', 'scalp', 'htf1', 'htf2', 'htf3']:
        tf = tf_map.get(tf_key)
        if not tf: continue
        tf_configs.append((tf, weights.get(tf, 3)))
    total_bottom = 0.0; total_entry = 0.0; total_dir = 0.0; total_weight = 0.0
    extreme_count = 0; htf_bullish_count = 0
    for tf_name, weight in tf_configs:
        wt1 = _g(f'wt1_{tf_name}'); wt2 = _g(f'wt2_{tf_name}')
        wt_score = wt1 - wt2
        wt_bullish = wt1 > wt2
        k_val = _g(f'stoch_k_{tf_name}', 50); d_val = _g(f'stoch_d_{tf_name}', 50)
        # Simplified — no cross persistence in backtest arrays, use wt_cross_bull/bear
        wt_cross_bull = _gb(f'wt_cross_bull_{tf_name}')
        wt_cross_bear = _gb(f'wt_cross_bear_{tf_name}')
        wt_velocity = _g(f'wt_velocity_{tf_name}')
        # K zone analysis
        k_extreme = (k_val < 10) if is_long else (k_val > 90)
        k_zone = (k_val < 30) if is_long else (k_val > 70)
        if k_extreme: extreme_count += 1
        # Bottom score per TF
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
        # Entry quality per TF
        tf_entry = 0.0
        if wt_cross_bull if is_long else wt_cross_bear: tf_entry += 15
        # Direction per TF
        tf_dir = 0.0
        if wt_bullish: tf_dir += 30; htf_bullish_count += 1
        if wt_score > 10: tf_dir += 20
        elif wt_score > 0: tf_dir += 10
        elif wt_score < -10: tf_dir -= 20
        elif wt_score < 0: tf_dir -= 10
        if k_val > d_val: tf_dir += 10
        else: tf_dir -= 10
        if wt_velocity > 2: tf_dir += 15
        elif wt_velocity < -2: tf_dir -= 15
        total_bottom += tf_bottom * weight; total_entry += tf_entry * weight; total_dir += tf_dir * weight; total_weight += weight
    if total_weight > 0:
        bottom_score = total_bottom / total_weight
        entry_quality = total_entry / total_weight
        direction = total_dir / total_weight
    else:
        return 0, 0, 0, 1.0
    # K extreme stacking
    if extreme_count >= 4: bottom_score *= 2.0
    elif extreme_count >= 3: bottom_score *= 1.5
    entry_quality += htf_bullish_count * 5
    bottom_score = max(-100, min(100, bottom_score))
    entry_quality = max(-100, min(100, entry_quality))
    direction = max(-100, min(100, direction))
    # Gain potential from DC width
    dc_h = _g('dc_high_1h'); dc_l = _g('dc_low_1h')
    dc_width_pct = ((dc_h - dc_l) / current_price * 100) if current_price > 0 and dc_l > 0 else 2.0
    gain_potential = min(100, dc_width_pct * 20)
    size_multiplier = max(0.3, min(3.0, 0.5 + gain_potential / 50))
    return bottom_score, entry_quality, direction, size_multiplier

def simulate_mts(precomputed, entry_params, exit_params, tf_map, is_long, weights):
    """Simulate entry/exit using MTS scores as gates."""
    ind = precomputed["indicators"]
    n = precomputed["n"]
    c = ind.get("current_price")
    if c is None or not isinstance(c, np.ndarray) or len(c) < 300: return None
    h = ind.get(f"high_{tf_map['focus']}", c)
    l = ind.get(f"low_{tf_map['focus']}", c)
    o = ind.get(f"open_{tf_map['focus']}", c)
    # Entry thresholds
    min_bottom = entry_params.get("min_bottom", 20)
    min_eq = entry_params.get("min_entry_quality", 10)
    min_dir = entry_params.get("min_direction", 0)
    # Exit thresholds
    exit_bottom_flip = exit_params.get("exit_bottom_flip", -10)
    min_gain = exit_params.get("min_gain", 0.3) / 100.0
    trades = []
    last_exit = 250
    for ei in range(251, n - 2):
        if ei <= last_exit: continue
        cp = float(c[ei])
        if cp <= 0: continue
        bs, eq, dr, sm = analyze_multi_tf_state_backtest(ind, n, ei, is_long, cp, tf_map, weights)
        # Entry gate
        if bs < min_bottom: continue
        if eq < min_eq: continue
        if is_long and dr < min_dir: continue
        if not is_long and dr > -min_dir: continue
        # ENTER at next bar's open
        ep = float(o[ei + 1]) if ei + 1 < n else cp
        if ep <= 0: continue
        mg = 0.0
        for bi in range(ei + 1, n):
            _c = float(c[bi]); _h = float(h[bi]); _l = float(l[bi])
            if _c <= 0: continue
            pnl = (_c - ep) / ep if is_long else (ep - _c) / ep
            hp = (_h - ep) / ep if is_long else (ep - _l) / ep
            mg = max(mg, hp)
            exit_signal = False
            if pnl > min_gain:
                # Check MTS for exit (bottom score flipping = momentum exhaustion)
                bs_exit, _, dr_exit, _ = analyze_multi_tf_state_backtest(ind, n, bi, is_long, _c, tf_map, weights)
                if bs_exit < exit_bottom_flip: exit_signal = True
                if is_long and dr_exit < -20: exit_signal = True
                if not is_long and dr_exit > 20: exit_signal = True
            # NOLOSS safety
            if mg > 0.005 and pnl < 0.003: exit_signal = True
            if exit_signal:
                trades.append(pnl - 2 * FEE)
                last_exit = bi
                break
    if len(trades) < 5: return None
    t = np.array(trades)
    sharpe = np.mean(t) / np.std(t) * math.sqrt(len(t)) if np.std(t) > 1e-10 else 0
    return {"sharpe": round(sharpe, 3), "wr": round(np.mean(t > 0) * 100, 1), "trades": len(trades), "pnl": round(np.sum(t) * 100, 2), "avg_ret": round(np.mean(t) * 100, 3)}

def build_param_grid(quick=False):
    """Build parameter grid for MTS sweep."""
    if quick:
        bottom_thresholds = [15, 25, 40]
        eq_thresholds = [5, 15, 30]
        dir_thresholds = [0, 15, 30]
        exit_bottom_flips = [-20, -5, 10]
        min_gains = [0.1, 0.3]
        weight_presets = ["default", "htf_heavy", "balanced"]
    else:
        bottom_thresholds = [10, 15, 20, 25, 30, 40, 50]
        eq_thresholds = [0, 5, 10, 15, 20, 30]
        dir_thresholds = [0, 10, 20, 30]
        exit_bottom_flips = [-30, -20, -10, -5, 0, 10]
        min_gains = [0.1, 0.3, 0.5]
        weight_presets = list(TF_WEIGHT_PRESETS.keys())
    combos = []
    for bt, eq, dr, ebf, mg, wp in itertools.product(bottom_thresholds, eq_thresholds, dir_thresholds, exit_bottom_flips, min_gains, weight_presets):
        name = f"b{bt}_eq{eq}_d{dr}_ef{ebf}_mg{mg}_{wp}"
        combos.append((name, {"min_bottom": bt, "min_entry_quality": eq, "min_direction": dr}, {"exit_bottom_flip": ebf, "min_gain": mg}, wp))
    return combos

def worker(args):
    sym, param_grid, tf_map, primary_tf = args
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
        for name, entry_params, exit_params, weight_preset in param_grid:
            combo = f"{side}|{name}"
            weights = TF_WEIGHT_PRESETS[weight_preset]
            r = simulate_mts(pre, entry_params, exit_params, tf_map, is_long, weights)
            if r: results[combo] = r
    return sym, results

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--tf", default="15m", help="Primary TF (default: 15m for 4+ year data)")
    parser.add_argument("--symbols", default=None, help="Path to symbols JSON file")
    args = parser.parse_args()
    backtest_engine.KLINES_DIR = Path("/Users/niels/Documents/binance/klines_cache")
    if Path("/home/niels/binance-sandbox/klines_cache").exists():
        backtest_engine.KLINES_DIR = Path("/home/niels/binance-sandbox/klines_cache")
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
    param_grid = build_param_grid(quick=args.quick)
    total_combos = len(param_grid) * 2  # LONG + SHORT
    print(f"MTS PARAMETER SWEEP")
    print(f"Symbols: {len(syms)} | Param combos: {len(param_grid)} | Total: {total_combos * len(syms):,}")
    start = time.time()
    all_results = {}
    tasks = [(sym, param_grid, tf_map, primary_tf) for sym in syms]
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
    print(f"\n{'Combo':80s} {'Sharpe':>7s} {'WR':>5s} {'AvgRet':>7s} {'Trades':>6s} {'N':>3s}")
    print("=" * 110)
    # Top 20 LONG
    long_ranked = [r for r in ranked if r[0].startswith("LONG")]
    print(f"\n--- TOP 20 LONG ---")
    for name, sharpe, wr, avg, trades, n in long_ranked[:20]:
        print(f"{name:80s} {sharpe:>+7.3f} {wr:>4.1f}% {avg:>+6.3f}% {trades:>5.0f} {n:>3d}")
    # Top 20 SHORT
    short_ranked = [r for r in ranked if r[0].startswith("SHORT")]
    print(f"\n--- TOP 20 SHORT ---")
    for name, sharpe, wr, avg, trades, n in short_ranked[:20]:
        print(f"{name:80s} {sharpe:>+7.3f} {wr:>4.1f}% {avg:>+6.3f}% {trades:>5.0f} {n:>3d}")
    # Top 20 OVERALL
    print(f"\n--- TOP 20 OVERALL ---")
    for name, sharpe, wr, avg, trades, n in ranked[:20]:
        print(f"{name:80s} {sharpe:>+7.3f} {wr:>4.1f}% {avg:>+6.3f}% {trades:>5.0f} {n:>3d}")
    # Parameter analysis — which parameters appear most in top 50
    print(f"\n--- PARAMETER ANALYSIS (top 50) ---")
    param_counts = {}
    for name, sharpe, wr, avg, trades, n in ranked[:50]:
        parts = name.split("|", 1)[-1].split("_")  # Remove LONG/SHORT prefix
        # Parse params from name
        for p in parts:
            if p.startswith("b") and p[1:].isdigit(): param_counts.setdefault(f"bottom={p[1:]}", []).append(sharpe)
            elif p.startswith("eq") and p[2:].isdigit(): param_counts.setdefault(f"entry_q={p[2:]}", []).append(sharpe)
            elif p.startswith("d") and p[1:].isdigit(): param_counts.setdefault(f"direction={p[1:]}", []).append(sharpe)
            elif p.startswith("ef"): param_counts.setdefault(f"exit_flip={p[2:]}", []).append(sharpe)
            elif p.startswith("mg"): param_counts.setdefault(f"min_gain={p[2:]}", []).append(sharpe)
            elif p in TF_WEIGHT_PRESETS: param_counts.setdefault(f"weights={p}", []).append(sharpe)
    for param, sharpes in sorted(param_counts.items(), key=lambda x: np.mean(x[1]), reverse=True):
        print(f"  {param:30s} count={len(sharpes):>3d} avg_sharpe={np.mean(sharpes):>+.3f}")
    # Save
    out = Path("data") / f"mts_sweep_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"ranked": [(k, s, w, a, t, n) for k, s, w, a, t, n in ranked[:200]], "total_combos": total_combos, "symbols": len(all_results), "elapsed": elapsed}, indent=2))
    print(f"\nSaved: {out}")

if __name__ == "__main__":
    main()
