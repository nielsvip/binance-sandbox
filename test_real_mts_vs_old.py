#!/usr/bin/env python3
"""
REAL MTS vs OLD — Test ACTUAL entry/exit logic from the live scripts.
NOT a simplified simulation — uses the REAL decision conditions from rate().

Compares:
  1. OLD: K-based entry (is_long_stoch_ok with K zones)
  2. NEW: WT+MTS entry (wt_bullish + velocity + MTS gate)
  3. NEW+DC: WT+MTS+DC range (full system with dc_position per TF)
  4. Sweeps MTS thresholds on the REAL logic

For each bar, evaluates REAL entry conditions and REAL exit conditions.

Usage:
  python3 test_real_mts_vs_old.py --market crypto --workers 4 --limit 48
  python3 test_real_mts_vs_old.py --market stocks --workers 4 --limit 48
"""
import argparse, json, math, sys, time, itertools
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import backtest_engine
from backtest_engine import precompute_real_indicators

FEE = 0.0
KLINES_DIR = None  # Set in main()

# ═══════════════════════════════════════════════════════════════
# REAL ENTRY CONDITIONS — extracted from the actual scripts
# ═══════════════════════════════════════════════════════════════

def _g(ind, n, key, idx, default=0.0):
    v = ind.get(key)
    if v is not None and isinstance(v, np.ndarray) and len(v) == n:
        val = v[idx]
        if val is None or (isinstance(val, float) and math.isnan(val)): return default
        return float(val)
    return default

def _gb(ind, n, key, idx, default=False):
    v = ind.get(key)
    if v is not None and isinstance(v, np.ndarray) and len(v) == n:
        return bool(v[idx])
    return default

def old_entry_check(ind, n, idx, is_long, tf_map):
    """OLD system: K-based is_long_stoch_ok / is_short_stoch_ok from rate()."""
    k_15m = _g(ind, n, f'stoch_k_{tf_map["htf1"]}', idx, 50)
    k_15m_prev = _g(ind, n, f'stoch_k_{tf_map["htf1"]}', max(0, idx-1), 50)
    k_3m = _g(ind, n, f'stoch_k_{tf_map["scalp"]}', idx, 50)
    d_3m = _g(ind, n, f'stoch_d_{tf_map["scalp"]}', idx, 50)
    if is_long:
        return (k_15m > 50 and k_15m < 70 and k_3m > d_3m and k_3m < 80) or (k_15m <= 32 and k_3m > d_3m)
    else:
        return (k_15m < 50 and k_3m < d_3m and k_3m > 20) or (k_15m > 70 and k_15m < k_15m_prev and k_3m < d_3m)

def new_wt_entry_check(ind, n, idx, is_long, tf_map):
    """NEW system: WT-based entry from rate() — wt_bullish + velocity + cross."""
    scalp = tf_map['scalp']
    wt_bullish = _gb(ind, n, f'wt_bullish_{scalp}', idx)
    wt_score = _g(ind, n, f'wt_score_{scalp}', idx) if f'wt_score_{scalp}' in ind else _g(ind, n, f'wt1_{scalp}', idx) - _g(ind, n, f'wt2_{scalp}', idx)
    wt_velocity = _g(ind, n, f'wt_velocity_{scalp}', idx)
    wt_cross_bull = _gb(ind, n, f'wt_cross_bull_{scalp}', idx)
    wt_cross_bear = _gb(ind, n, f'wt_cross_bear_{scalp}', idx)
    # Also keep K fallback
    k_15m = _g(ind, n, f'stoch_k_{tf_map["htf1"]}', idx, 50)
    k_3m = _g(ind, n, f'stoch_k_{tf_map["scalp"]}', idx, 50)
    d_3m = _g(ind, n, f'stoch_d_{tf_map["scalp"]}', idx, 50)
    if is_long:
        return (wt_bullish and wt_score > -20 and wt_velocity > -1.0) or (wt_cross_bull and wt_score < -30) or (k_15m <= 32 and k_3m > d_3m)
    else:
        mom_1h = ind.get(f'wt_momentum_state_{tf_map["htf2"]}')
        mom_exhaust = False
        if mom_1h is not None and isinstance(mom_1h, np.ndarray) and len(mom_1h) == n:
            mom_exhaust = str(mom_1h[idx]) == 'EXHAUST_UP'
        k_15m_prev = _g(ind, n, f'stoch_k_{tf_map["htf1"]}', max(0, idx-1), 50)
        return (not wt_bullish and wt_score < -10 and wt_velocity < 1.0) or (mom_exhaust and not wt_bullish) or (k_15m > 70 and k_15m < k_15m_prev and k_3m < d_3m)

def mts_gate_check(ind, n, idx, is_long, tf_map, mts_params):
    """MTS gate: compute multi-TF bottom_score + entry_quality, check thresholds."""
    min_bottom = mts_params.get("min_bottom", 10)
    min_eq = mts_params.get("min_eq", 5)
    # TF weights (1h_dom for crypto)
    weights = mts_params.get("weights", {"1m": 1, "3m": 2, "15m": 3, "1h": 12, "4h": 4, "D": 2})
    total_bottom = 0.0; total_entry = 0.0; total_weight = 0.0
    extreme_count = 0
    for tf_key in ['micro', 'scalp', 'htf1', 'htf2', 'htf3']:
        tf = tf_map.get(tf_key)
        if not tf: continue
        w = weights.get(tf, 3)
        k = _g(ind, n, f'stoch_k_{tf}', idx, 50); d = _g(ind, n, f'stoch_d_{tf}', idx, 50)
        wt1 = _g(ind, n, f'wt1_{tf}', idx); wt2 = _g(ind, n, f'wt2_{tf}', idx)
        wt_cross_bull = _gb(ind, n, f'wt_cross_bull_{tf}', idx)
        wt_cross_bear = _gb(ind, n, f'wt_cross_bear_{tf}', idx)
        wt_velocity = _g(ind, n, f'wt_velocity_{tf}', idx)
        # DC position per TF
        dc_pos = _g(ind, n, f'dc_position_{tf}', idx, 0.5)
        if dc_pos > 1.0: dc_pos /= 100.0
        dc_pos = max(0.0, min(1.0, dc_pos))
        k_extreme = (k < 10) if is_long else (k > 90)
        k_zone = (k < 30) if is_long else (k > 70)
        if k_extreme: extreme_count += 1
        tb = 0.0
        if is_long:
            if k_extreme: tb += 20
            if k_zone: tb += 10
            if wt_cross_bull: tb += 15
            if wt1 < -40: tb += 15
            elif wt1 < -20: tb += 8
            if wt_velocity > 0 and k > d: tb += 5
            if dc_pos < 0.10: tb += 25
            elif dc_pos < 0.20: tb += 18
            elif dc_pos < 0.30: tb += 12
            elif dc_pos < 0.40: tb += 5
            elif dc_pos > 0.80: tb -= 10
        else:
            if k_extreme: tb += 20
            if k_zone: tb += 10
            if wt_cross_bear: tb += 15
            if wt1 > 40: tb += 15
            elif wt1 > 20: tb += 8
            if wt_velocity < 0 and k < d: tb += 5
            if dc_pos > 0.90: tb += 25
            elif dc_pos > 0.80: tb += 18
            elif dc_pos > 0.70: tb += 12
            elif dc_pos > 0.60: tb += 5
            elif dc_pos < 0.20: tb -= 10
        te = 0.0
        if (wt_cross_bull if is_long else wt_cross_bear): te += 15
        if is_long and dc_pos < 0.25 and wt1 > wt2: te += 15
        elif not is_long and dc_pos > 0.75 and wt1 < wt2: te += 15
        total_bottom += tb * w; total_entry += te * w; total_weight += w
    if total_weight > 0:
        bs = total_bottom / total_weight; eq = total_entry / total_weight
    else:
        return False, 0, 0
    if extreme_count >= 3: bs *= 1.5
    return bs >= min_bottom and eq >= min_eq, bs, eq

def old_exit_check(ind, n, idx, is_long, entry_price, current_price, max_gain, tf_map, min_gain_pct):
    """OLD exit: K declining + K exhaustion."""
    pnl = (current_price - entry_price) / entry_price if is_long else (entry_price - current_price) / entry_price
    min_gain = min_gain_pct / 100.0
    k_3m = _g(ind, n, f'stoch_k_{tf_map["scalp"]}', idx, 50)
    k_3m_prev = _g(ind, n, f'stoch_k_{tf_map["scalp"]}', max(0, idx-1), 50)
    k_declining = (is_long and k_3m < k_3m_prev) or (not is_long and k_3m > k_3m_prev)
    deep_tp = pnl >= min_gain * 2
    if pnl > min_gain and (k_declining or deep_tp): return True, pnl
    if max_gain > 0.005 and pnl < 0.003: return True, pnl
    return False, pnl

def new_exit_check(ind, n, idx, is_long, entry_price, current_price, max_gain, tf_map, min_gain_pct):
    """NEW exit: WT velocity declining + convergence failure fast exit."""
    pnl = (current_price - entry_price) / entry_price if is_long else (entry_price - current_price) / entry_price
    min_gain = min_gain_pct / 100.0
    scalp = tf_map['scalp']; htf2 = tf_map.get('htf2', tf_map['htf1'])
    wt_vel_scalp = _g(ind, n, f'wt_velocity_{scalp}', idx)
    wt_vel_1h = _g(ind, n, f'wt_velocity_{htf2}', idx)
    wt_bull_scalp = _gb(ind, n, f'wt_bullish_{scalp}', idx)
    k_3m = _g(ind, n, f'stoch_k_{scalp}', idx, 50)
    d_3m = _g(ind, n, f'stoch_d_{scalp}', idx, 50)
    k_3m_prev = _g(ind, n, f'stoch_k_{scalp}', max(0, idx-1), 50)
    wt_declining = (is_long and (wt_vel_scalp < -1.5 or wt_vel_1h < -2)) or (not is_long and (wt_vel_scalp > 1.5 or wt_vel_1h > 2))
    k_declining = (is_long and k_3m < k_3m_prev) or (not is_long and k_3m > k_3m_prev)
    # CONVERGENCE FAILURE FAST EXIT — get out at breakeven when thesis breaks
    # If WT + K both against us and we're barely profitable → cut immediately
    if 0.0005 < pnl < min_gain:
        _wt_against = (is_long and wt_vel_scalp < -2.0 and not wt_bull_scalp) or (not is_long and wt_vel_scalp > 2.0 and wt_bull_scalp)
        _k_against = (is_long and k_3m < d_3m and k_3m < k_3m_prev) or (not is_long and k_3m > d_3m and k_3m > k_3m_prev)
        if _wt_against and _k_against: return True, pnl  # Fast exit at breakeven
    deep_tp = pnl >= min_gain * 2
    if pnl > min_gain and (wt_declining or k_declining or deep_tp): return True, pnl
    if max_gain > 0.005 and pnl < 0.003: return True, pnl
    return False, pnl

# ═══════════════════════════════════════════════════════════════
# SIMULATION — run each system config through real price data
# ═══════════════════════════════════════════════════════════════

def convergence_check(ind, n, idx, is_long, tf_map, weights):
    """Detect multi-TF convergence at DC bottom/top. Returns (conv_count, btb_mult, is_breakout).
    Convergence = 2+ of 4 signals per TF: DC near extreme + WT low + K low + fresh WT cross.
    If trading WITH convergence → bounce/reversal play.
    If price breaks THROUGH after convergence → breakout/breakdown (even bigger)."""
    _conv_dc = 0.15; _conv_bars = 12
    bot_c = 0; top_c = 0
    for tf_key in ['micro', 'scalp', 'htf1', 'htf2', 'htf3']:
        tf = tf_map.get(tf_key)
        if not tf: continue
        dc_p = _g(ind, n, f'dc_position_{tf}', idx, 0.5)
        if dc_p > 1.0: dc_p /= 100.0
        k = _g(ind, n, f'stoch_k_{tf}', idx, 50)
        wt1 = _g(ind, n, f'wt1_{tf}', idx); wt2 = _g(ind, n, f'wt2_{tf}', idx)
        wt_bull = _gb(ind, n, f'wt_cross_bull_{tf}', idx); wt_bear = _gb(ind, n, f'wt_cross_bear_{tf}', idx)
        # Bottom: DC low + WT low + K low + recent bull cross
        if sum([dc_p < _conv_dc, (wt1 - wt2) < -15, k < 25, wt_bull]) >= 2: bot_c += 1
        # Top: DC high + WT high + K high + recent bear cross
        if sum([dc_p > 1.0 - _conv_dc, (wt1 - wt2) > 15, k > 75, wt_bear]) >= 2: top_c += 1
    # Focus TF price position
    focus_dc = _g(ind, n, f'dc_position_{tf_map["scalp"]}', idx, 0.5)
    if focus_dc > 1.0: focus_dc /= 100.0
    below = focus_dc < 0.05; above = focus_dc > 0.95; near_low = focus_dc < 0.15; near_high = focus_dc > 0.85
    btb = 1.0; conv = 0; is_breakout = False
    if is_long:
        if bot_c >= 2 and (near_low or below):  # LONG bounce at bottom convergence
            conv = bot_c; btb = 3.0 if bot_c >= 4 else (2.5 if bot_c >= 3 else 1.8)
            if below: btb *= 1.2
        elif top_c >= 2 and above:  # LONG breakout — resistance smashed
            conv = top_c; btb = 3.5 if top_c >= 4 else (3.0 if top_c >= 3 else 2.0); is_breakout = True
    else:
        if top_c >= 2 and (near_high or above):  # SHORT reversal at top convergence
            conv = top_c; btb = 3.0 if top_c >= 4 else (2.5 if top_c >= 3 else 1.8)
            if above: btb *= 1.2
        elif bot_c >= 2 and below:  # SHORT breakdown — support smashed
            conv = bot_c; btb = 3.5 if bot_c >= 4 else (3.0 if bot_c >= 3 else 2.0); is_breakout = True
    return conv, btb, is_breakout

CONFIGS = {
    "OLD_K_ONLY": {"entry": "old", "exit": "old", "mts": None, "conv": False},
    "NEW_WT+MTS_b10": {"entry": "new", "exit": "new", "mts": {"min_bottom": 10, "min_eq": 5}, "conv": False},
    "CONV_noMTS": {"entry": "new", "exit": "new", "mts": None, "conv": True},
    "CONV+MTS_b10": {"entry": "new", "exit": "new", "mts": {"min_bottom": 10, "min_eq": 5}, "conv": True},
    "CONV+MTS_b5": {"entry": "new", "exit": "new", "mts": {"min_bottom": 5, "min_eq": 0}, "conv": True},
    "CONV+FAST_EXIT": {"entry": "new", "exit": "new_fast", "mts": None, "conv": True},
    "CONV+MTS+FAST": {"entry": "new", "exit": "new_fast", "mts": {"min_bottom": 10, "min_eq": 5}, "conv": True},
    "CONV+MTS_b5+FAST": {"entry": "new", "exit": "new_fast", "mts": {"min_bottom": 5, "min_eq": 0}, "conv": True},
}

def simulate_config(pre, config_name, cfg, tf_map, is_long, min_gain_pct, weights):
    """Simulate one config on one symbol using REAL indicator data. Conv sizing = PnL weighted by btb_mult."""
    ind = pre["indicators"]
    n = pre["n"]
    c = ind.get("current_price")
    if c is None or not isinstance(c, np.ndarray) or len(c) < 300: return None
    focus = tf_map['focus']
    h = ind.get(f"high_{focus}", c); l = ind.get(f"low_{focus}", c); o = ind.get(f"open_{focus}", c)
    entry_fn = old_entry_check if cfg["entry"] == "old" else new_wt_entry_check
    _exit_type = cfg.get("exit", "new")
    exit_fn = old_exit_check if _exit_type == "old" else new_exit_check
    mts_params = cfg.get("mts")
    if mts_params: mts_params["weights"] = weights
    use_conv = cfg.get("conv", False)
    trades = []
    last_exit = 250
    for ei in range(251, n - 2):
        if ei <= last_exit: continue
        cp = float(c[ei])
        if cp <= 0: continue
        # Entry check (REAL logic)
        if not entry_fn(ind, n, ei, is_long, tf_map): continue
        # MTS gate (if enabled)
        if mts_params:
            ok, bs, eq = mts_gate_check(ind, n, ei, is_long, tf_map, mts_params)
            if not ok: continue
        # Convergence sizing: weight PnL by btb_mult (simulates bigger position)
        btb = 1.0
        if use_conv:
            conv_n, btb, is_brk = convergence_check(ind, n, ei, is_long, tf_map, weights)
        ep = float(o[ei + 1]) if ei + 1 < n else cp
        if ep <= 0: continue
        mg = 0.0
        for bi in range(ei + 1, min(ei + 500, n)):
            _c = float(c[bi]); _h = float(h[bi]); _l = float(l[bi])
            if _c <= 0: continue
            hp = (_h - ep) / ep if is_long else (ep - _l) / ep
            mg = max(mg, hp)
            exit_sig, pnl = exit_fn(ind, n, bi, is_long, ep, _c, mg, tf_map, min_gain_pct)
            if exit_sig:
                trades.append((pnl - 2 * FEE) * btb)  # PnL weighted by convergence multiplier
                last_exit = bi
                break
    if len(trades) < 5: return None
    t = np.array(trades)
    sharpe = np.mean(t) / np.std(t) * math.sqrt(len(t)) if np.std(t) > 1e-10 else 0
    return {"sharpe": round(sharpe, 3), "wr": round(np.mean(t > 0) * 100, 1), "trades": len(trades), "pnl": round(np.sum(t) * 100, 2), "avg_ret": round(np.mean(t) * 100, 3)}

def worker(args):
    sym, tf_map, primary_tf, min_gain_pct, weights, klines_dir = args
    backtest_engine.KLINES_DIR = Path(klines_dir)
    pre = precompute_real_indicators(sym, primary_tf)
    if pre is None: return sym, {}
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
        for config_name, cfg in CONFIGS.items():
            combo = f"{side}|{config_name}"
            r = simulate_config(pre, config_name, cfg, tf_map, is_long, min_gain_pct, weights)
            if r: results[combo] = r
    return sym, results

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=["crypto", "stocks"], default="crypto")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--limit", type=int, default=48)
    parser.add_argument("--min-gain", type=float, default=0.3)
    parser.add_argument("--klines-dir", default=None)
    parser.add_argument("--symbols", default=None)
    args = parser.parse_args()
    if args.market == "crypto":
        backtest_engine.KLINES_DIR = Path("/Users/niels/Documents/binance/klines_cache")
        if Path("/home/niels/binance-sandbox/klines_cache").exists():
            backtest_engine.KLINES_DIR = Path("/home/niels/binance-sandbox/klines_cache")
        primary_tf = "1h"
        tf_map = {"micro": "1h", "scalp": "1h", "focus": "1h", "htf1": "1h", "htf2": "4h", "htf3": "D"}
        weights = {"1m": 1, "3m": 2, "15m": 3, "1h": 12, "4h": 4, "D": 2}  # 1h_dom
        sym_file = Path("/home/niels/binance-sandbox/backtest_48_symbols.json")
    else:
        backtest_engine.KLINES_DIR = Path("/Users/niels/Documents/binance/klines_cache/tradier")
        primary_tf = "1h"
        tf_map = {"micro": "1h", "scalp": "1h", "focus": "1h", "htf1": "1h", "htf2": "4h", "htf3": "D"}
        weights = {"5m": 2, "15m": 4, "1h": 3, "4h": 5, "D": 12}  # D_dom for stocks
        sym_file = Path("/Users/niels/Documents/binance/klines_cache/tradier/backtest_48_stocks.json")
    if args.klines_dir: backtest_engine.KLINES_DIR = Path(args.klines_dir)
    if args.symbols: sym_file = Path(args.symbols)
    if not sym_file.exists():
        syms = sorted([f.stem.replace(f"_{primary_tf}", "") for f in backtest_engine.KLINES_DIR.glob(f"*_{primary_tf}.json")])
        if args.market == "stocks": syms = [s for s in syms if s.isalpha() and len(s) <= 5 and "USDT" not in s]
        else: syms = [s for s in syms if "USDT" in s]
    else:
        syms = json.loads(sym_file.read_text())
    import random; random.seed(42); random.shuffle(syms)
    syms = syms[:args.limit]
    print(f"REAL MTS vs OLD — {args.market.upper()}")
    print(f"Symbols: {len(syms)} | Configs: {len(CONFIGS)} | Primary TF: {primary_tf}")
    print(f"Configs: {list(CONFIGS.keys())}")
    start = time.time()
    all_results = {}
    tasks = [(sym, tf_map, primary_tf, args.min_gain, weights, str(backtest_engine.KLINES_DIR)) for sym in syms]
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(worker, t): t[0] for t in tasks}
        done = 0
        for f in as_completed(futures):
            sym, results = f.result()
            if results: all_results[sym] = results
            done += 1
            if done % 5 == 0: print(f"  {done}/{len(syms)} ({len(all_results)} with data) {time.time()-start:.0f}s")
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
    # COMPARISON TABLE — OLD vs NEW vs NEW+MTS
    # ═══════════════════════════════════════════════════════════════
    baseline_map = {name: (s, w, a, t, n) for name, s, w, a, t, n in ranked}
    print(f"\n{'='*110}")
    print(f"OLD vs NEW — REAL entry/exit logic comparison")
    print(f"{'='*110}")
    print(f"\n{'Config':40s} {'Sharpe':>7s} {'WR':>5s} {'AvgRet':>7s} {'Trades':>6s} {'N':>3s} {'vs OLD':>8s}")
    print("-" * 80)
    for side in ["LONG", "SHORT"]:
        print(f"\n--- {side} ---")
        old_key = f"{side}|OLD_K_ONLY"
        old_sharpe = baseline_map.get(old_key, (0,0,0,0,0))[0]
        for config_name in CONFIGS:
            key = f"{side}|{config_name}"
            if key in baseline_map:
                s, w, a, t, n = baseline_map[key]
                delta = s - old_sharpe
                marker = " ⭐" if delta > 0.5 else (" ✅" if delta > 0.1 else (" ⚠️" if delta < -0.5 else ""))
                print(f"  {config_name:38s} {s:>+7.3f} {w:>4.1f}% {a:>+6.3f}% {t:>5.0f} {n:>3d} {delta:>+7.3f}{marker}")
    # Top overall
    print(f"\n{'='*110}")
    print(f"TOP 20 OVERALL")
    print(f"{'='*110}")
    for name, sharpe, wr, avg, trades, n in ranked[:20]:
        print(f"{name:50s} {sharpe:>+7.3f} {wr:>4.1f}% {avg:>+6.3f}% {trades:>5.0f} {n:>3d}")
    # Save
    out = Path("data") / f"real_mts_vs_old_{args.market}_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"market": args.market, "ranked": [(k, s, w, a, t, n) for k, s, w, a, t, n in ranked], "symbols": len(all_results), "elapsed": elapsed}, indent=2))
    print(f"\nSaved: {out}")

if __name__ == "__main__":
    main()
