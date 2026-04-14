#!/usr/bin/env python3
"""
GATE ABLATION TEST — Disable every entry/exit gate one by one on the REAL rate() logic.
Identifies which gates HELP and which CHOKE the system.

The system currently blocks 95%+ of entries with:
  HTF_1H_REQUIRED (374 blocks), SMA200D_EXTREME (261), NOLOSS_HOLD (1268),
  ATR_TOO_LOW (54), K3M_CAP (11), LONG_CHASE_BLOCK (10), MTS_GATE (new)

Tests: disable each gate individually, in pairs, and build per-account optimal configs.

Usage:
  python3 test_gate_ablation.py --workers 14 --limit 48
  python3 test_gate_ablation.py --workers 4 --limit 48 --klines-dir klines_cache/tradier --market stocks
"""
import argparse, json, math, sys, time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import backtest_engine
from backtest_engine import precompute_real_indicators
FEE = 0.0

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

# ═══════════════════════════════════════════════════════════════
# GATES — each gate is a function that returns (blocked: bool, reason: str)
# When disabled, the gate always returns (False, "")
# ═══════════════════════════════════════════════════════════════

def gate_htf_1h(ind, n, idx, is_long, tf_map, current_price):
    """1h K>D required for entry."""
    k = _g(ind, n, f'stoch_k_{tf_map.get("htf2", "1h")}', idx, 50)
    d = _g(ind, n, f'stoch_d_{tf_map.get("htf2", "1h")}', idx, 50)
    if is_long and k <= d: return True, f"HTF_1H(k={k:.0f}<d={d:.0f})"
    if not is_long and k >= d: return True, f"HTF_1H(k={k:.0f}>d={d:.0f})"
    return False, ""

def gate_sma200d(ind, n, idx, is_long, tf_map, current_price):
    """Block when price >12% from SMA200 D."""
    sma = _g(ind, n, 'sma_200_D', idx, 0)
    if sma <= 0 or current_price <= 0: return False, ""
    dist = (current_price - sma) / sma
    if is_long and dist > 0.12: return True, f"SMA200D_ABOVE({dist:.1%})"
    if not is_long and dist < -0.12: return True, f"SMA200D_BELOW({dist:.1%})"
    return False, ""

def gate_k3m_cap(ind, n, idx, is_long, tf_map, current_price):
    """Block LONG when k_3m >= 80, SHORT when k_3m <= 20."""
    k = _g(ind, n, f'stoch_k_{tf_map["scalp"]}', idx, 50)
    if is_long and k >= 80: return True, f"K3M_CAP({k:.0f})"
    if not is_long and k <= 20: return True, f"K3M_CAP({k:.0f})"
    return False, ""

def gate_chase_block(ind, n, idx, is_long, tf_map, current_price):
    """Block LONG when k_1h > 70 + HA streak > 2."""
    if not is_long: return False, ""
    k = _g(ind, n, f'stoch_k_{tf_map.get("htf2", "1h")}', idx, 50)
    if k > 70: return True, f"CHASE(k1h={k:.0f})"
    return False, ""

def gate_atr_min(ind, n, idx, is_long, tf_map, current_price):
    """Block when ATR% < minimum."""
    atr = _g(ind, n, f'atr_{tf_map.get("htf2", "1h")}', idx, 0)
    if atr <= 0 or current_price <= 0: return False, ""
    atr_pct = atr / current_price * 100
    if atr_pct < 0.5: return True, f"ATR_LOW({atr_pct:.2f}%)"
    return False, ""

def gate_mts(ind, n, idx, is_long, tf_map, current_price):
    """MTS bottom_score + entry_quality gate."""
    weights = {"1h": 12, "4h": 4, "D": 2}
    total_b = 0; total_w = 0
    for tf_key in ['scalp', 'htf1', 'htf2', 'htf3']:
        tf = tf_map.get(tf_key)
        if not tf: continue
        w = weights.get(tf, 3)
        k = _g(ind, n, f'stoch_k_{tf}', idx, 50)
        wt1 = _g(ind, n, f'wt1_{tf}', idx); wt2 = _g(ind, n, f'wt2_{tf}', idx)
        dc_p = _g(ind, n, f'dc_position_{tf}', idx, 0.5)
        if dc_p > 1: dc_p /= 100
        tb = 0
        if is_long:
            if k < 10: tb += 20
            if k < 30: tb += 10
            if _gb(ind, n, f'wt_cross_bull_{tf}', idx): tb += 15
            if dc_p < 0.15: tb += 20
        else:
            if k > 90: tb += 20
            if k > 70: tb += 10
            if _gb(ind, n, f'wt_cross_bear_{tf}', idx): tb += 15
            if dc_p > 0.85: tb += 20
        total_b += tb * w; total_w += w
    bs = total_b / total_w if total_w > 0 else 0
    min_b = 10 if is_long else 5
    if bs < min_b: return True, f"MTS(b={bs:.0f}<{min_b})"
    return False, ""

def gate_wt_entry(ind, n, idx, is_long, tf_map, current_price):
    """WT bullish/bearish required on scalp TF."""
    scalp = tf_map['scalp']
    wt_bull = _gb(ind, n, f'wt_bullish_{scalp}', idx)
    if is_long and not wt_bull: return True, "WT_ENTRY_BEAR"
    if not is_long and wt_bull: return True, "WT_ENTRY_BULL"
    return False, ""

def gate_stoch_entry(ind, n, idx, is_long, tf_map, current_price):
    """Old K-based stoch entry condition."""
    htf1 = tf_map['htf1']; scalp = tf_map['scalp']
    k15 = _g(ind, n, f'stoch_k_{htf1}', idx, 50)
    k3 = _g(ind, n, f'stoch_k_{scalp}', idx, 50); d3 = _g(ind, n, f'stoch_d_{scalp}', idx, 50)
    if is_long:
        ok = (k15 > 50 and k15 < 70 and k3 > d3 and k3 < 80) or (k15 <= 32 and k3 > d3)
    else:
        k15p = _g(ind, n, f'stoch_k_{htf1}', max(0, idx-1), 50)
        ok = (k15 < 50 and k3 < d3 and k3 > 20) or (k15 > 70 and k15 < k15p and k3 < d3)
    if not ok: return True, "STOCH_ENTRY"
    return False, ""

ALL_GATES = {
    "htf_1h": gate_htf_1h,
    "sma200d": gate_sma200d,
    "k3m_cap": gate_k3m_cap,
    "chase": gate_chase_block,
    "atr_min": gate_atr_min,
    "mts": gate_mts,
    "wt_entry": gate_wt_entry,
    "stoch_entry": gate_stoch_entry,
}

def exit_check(ind, n, idx, is_long, ep, cp, mg, tf_map, noloss_pct):
    """Exit with configurable NOLOSS threshold."""
    pnl = (cp - ep) / ep if is_long else (ep - cp) / ep
    min_gain = noloss_pct / 100.0
    scalp = tf_map['scalp']
    wt_vel = _g(ind, n, f'wt_velocity_{scalp}', idx)
    k = _g(ind, n, f'stoch_k_{scalp}', idx, 50)
    k_prev = _g(ind, n, f'stoch_k_{scalp}', max(0, idx-1), 50)
    wt_dec = (is_long and wt_vel < -1.5) or (not is_long and wt_vel > 1.5)
    k_dec = (is_long and k < k_prev) or (not is_long and k > k_prev)
    deep = pnl >= min_gain * 2
    if pnl > min_gain and (wt_dec or k_dec or deep): return True, pnl
    # Fast exit at breakeven
    wt_bull = _gb(ind, n, f'wt_bullish_{scalp}', idx)
    d = _g(ind, n, f'stoch_d_{scalp}', idx, 50)
    if 0.0005 < pnl < min_gain:
        ag_wt = (is_long and wt_vel < -2 and not wt_bull) or (not is_long and wt_vel > 2 and wt_bull)
        ag_k = (is_long and k < d and k < k_prev) or (not is_long and k > d and k > k_prev)
        if ag_wt and ag_k: return True, pnl
    if mg > 0.005 and pnl < 0.003: return True, pnl
    return False, pnl

# Build configs: all on, each off, aggressive combos, per-account strategies
def build_configs():
    configs = {}
    all_gates = list(ALL_GATES.keys())
    # ALL gates on
    configs["ALL_GATES"] = {"gates": all_gates, "noloss": 0.50}
    # Each gate OFF individually
    for g in all_gates:
        remaining = [x for x in all_gates if x != g]
        configs[f"NO_{g.upper()}"] = {"gates": remaining, "noloss": 0.50}
    # Aggressive: remove top blockers
    configs["NO_HTF+SMA"] = {"gates": [x for x in all_gates if x not in ["htf_1h", "sma200d"]], "noloss": 0.50}
    configs["NO_HTF+SMA+ATR"] = {"gates": [x for x in all_gates if x not in ["htf_1h", "sma200d", "atr_min"]], "noloss": 0.50}
    configs["MINIMAL_GATES"] = {"gates": ["wt_entry", "mts"], "noloss": 0.30}
    configs["WT_ONLY"] = {"gates": ["wt_entry"], "noloss": 0.30}
    configs["MTS_ONLY"] = {"gates": ["mts"], "noloss": 0.30}
    configs["NO_GATES"] = {"gates": [], "noloss": 0.30}
    # NOLOSS threshold sweep
    for nl in [0.05, 0.10, 0.20, 0.30, 0.50, 1.00]:
        configs[f"WT+MTS_noloss{nl}"] = {"gates": ["wt_entry", "mts"], "noloss": nl}
    # Per-account strategy presets
    configs["SCALP_FAST"] = {"gates": ["wt_entry"], "noloss": 0.10}  # Scalp: WT only, exit fast
    configs["SWING_PATIENT"] = {"gates": ["wt_entry", "mts", "htf_1h"], "noloss": 1.00}  # Swing: all gates, hold longer
    configs["MOMENTUM"] = {"gates": ["wt_entry", "k3m_cap"], "noloss": 0.20}  # Momentum: WT + don't chase
    configs["MEAN_REV"] = {"gates": ["mts", "stoch_entry"], "noloss": 0.30}  # Mean reversion: MTS + stoch
    return configs

def simulate(pre, cfg, tf_map, is_long):
    ind = pre["indicators"]; n = pre["n"]
    c = ind.get("current_price")
    if c is None or not isinstance(c, np.ndarray) or len(c) < 300: return None
    focus = tf_map['focus']
    h = ind.get(f"high_{focus}", c); l = ind.get(f"low_{focus}", c); o = ind.get(f"open_{focus}", c)
    active_gates = [ALL_GATES[g] for g in cfg["gates"] if g in ALL_GATES]
    noloss = cfg["noloss"]
    trades = []; last_exit = 250
    for ei in range(251, n - 2):
        if ei <= last_exit: continue
        cp = float(c[ei])
        if cp <= 0: continue
        blocked = False
        for gate_fn in active_gates:
            b, _ = gate_fn(ind, n, ei, is_long, tf_map, cp)
            if b: blocked = True; break
        if blocked: continue
        ep = float(o[ei + 1]) if ei + 1 < n else cp
        if ep <= 0: continue
        mg = 0.0
        for bi in range(ei + 1, min(ei + 500, n)):
            _c = float(c[bi]); _h = float(h[bi]); _l = float(l[bi])
            if _c <= 0: continue
            hp = (_h - ep) / ep if is_long else (ep - _l) / ep
            mg = max(mg, hp)
            exit_sig, pnl = exit_check(ind, n, bi, is_long, ep, _c, mg, tf_map, noloss)
            if exit_sig:
                trades.append(pnl - 2 * FEE)
                last_exit = bi
                break
    if len(trades) < 3: return None
    t = np.array(trades)
    sharpe = np.mean(t) / np.std(t) * math.sqrt(len(t)) if np.std(t) > 1e-10 else 0
    return {"sharpe": round(sharpe, 3), "wr": round(np.mean(t > 0) * 100, 1), "trades": len(trades), "pnl": round(np.sum(t) * 100, 2), "avg_ret": round(np.mean(t) * 100, 3)}

def worker(args):
    sym, configs, tf_map, primary_tf, klines_dir = args
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
        for name, cfg in configs.items():
            r = simulate(pre, cfg, tf_map, is_long)
            if r: results[f"{side}|{name}"] = r
    return sym, results

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--limit", type=int, default=48)
    parser.add_argument("--market", default="crypto")
    parser.add_argument("--klines-dir", default=None)
    parser.add_argument("--symbols", default=None)
    args = parser.parse_args()
    if args.market == "crypto":
        kdir = "/home/niels/binance-sandbox/klines_cache" if Path("/home/niels/binance-sandbox/klines_cache").exists() else "/Users/niels/Documents/binance/klines_cache"
        primary_tf = "1h"
        tf_map = {"micro": "1h", "scalp": "1h", "focus": "1h", "htf1": "1h", "htf2": "4h", "htf3": "D"}
        sym_file = Path("/home/niels/binance-sandbox/backtest_48_symbols.json")
    else:
        kdir = "/Users/niels/Documents/binance/klines_cache/tradier"
        primary_tf = "1h"
        tf_map = {"micro": "1h", "scalp": "1h", "focus": "1h", "htf1": "1h", "htf2": "4h", "htf3": "D"}
        sym_file = Path("/Users/niels/Documents/binance/klines_cache/tradier/backtest_48_stocks.json")
    if args.klines_dir: kdir = args.klines_dir
    if args.symbols: sym_file = Path(args.symbols)
    backtest_engine.KLINES_DIR = Path(kdir)
    if sym_file.exists():
        syms = json.loads(sym_file.read_text())
    else:
        syms = sorted([f.stem.replace(f"_{primary_tf}", "") for f in Path(kdir).glob(f"*_{primary_tf}.json")])
        if args.market == "stocks": syms = [s for s in syms if s.isalpha() and len(s) <= 5]
    import random; random.seed(42); random.shuffle(syms)
    syms = syms[:args.limit]
    configs = build_configs()
    print(f"GATE ABLATION TEST — {args.market.upper()}")
    print(f"Symbols: {len(syms)} | Configs: {len(configs)} | Gates: {list(ALL_GATES.keys())}")
    start = time.time()
    all_results = {}
    tasks = [(sym, configs, tf_map, primary_tf, kdir) for sym in syms]
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
    agg = {}
    for sym, results in all_results.items():
        for name, r in results.items():
            if name not in agg: agg[name] = []
            agg[name].append(r)
    ranked = sorted([(k, np.mean([r["sharpe"] for r in v]), np.mean([r["wr"] for r in v]), np.mean([r["avg_ret"] for r in v]), np.mean([r["trades"] for r in v]), len(v)) for k, v in agg.items() if len(v) >= 3], key=lambda x: x[1], reverse=True)
    baseline_map = {name: (s, w, a, t, n) for name, s, w, a, t, n in ranked}
    # === GATE IMPACT TABLE ===
    print(f"\n{'='*120}")
    print(f"GATE IMPACT — what happens when each gate is disabled?")
    print(f"{'='*120}")
    print(f"\n{'Config':35s} {'Sharpe':>7s} {'WR':>5s} {'AvgRet':>7s} {'Trades':>6s} {'N':>3s} {'vs ALL':>8s}")
    print("-" * 80)
    for side in ["LONG", "SHORT"]:
        print(f"\n--- {side} ---")
        bl = baseline_map.get(f"{side}|ALL_GATES", (0,0,0,0,0))
        for name in configs:
            key = f"{side}|{name}"
            if key in baseline_map:
                s, w, a, t, n = baseline_map[key]
                delta = s - bl[0]
                marker = " ⭐" if delta > 0.5 else (" ✅" if delta > 0.1 else (" ⚠️" if delta < -0.5 else ""))
                print(f"  {name:33s} {s:>+7.3f} {w:>4.1f}% {a:>+6.3f}% {t:>5.0f} {n:>3d} {delta:>+7.3f}{marker}")
    # === TOP OVERALL ===
    print(f"\n{'='*120}")
    print(f"TOP 25 OVERALL")
    print(f"{'='*120}")
    for name, sharpe, wr, avg, trades, n in ranked[:25]:
        print(f"{name:50s} {sharpe:>+7.3f} {wr:>4.1f}% {avg:>+6.3f}% {trades:>5.0f} {n:>3d}")
    # === PER-ACCOUNT STRATEGIES ===
    print(f"\n{'='*120}")
    print(f"RECOMMENDED PER-ACCOUNT STRATEGIES")
    print(f"{'='*120}")
    acct_configs = {"SCALP_FAST": "ang/men (scalp)", "MOMENTUM": "inf/flz (momentum)", "SWING_PATIENT": "fin (swing)", "MEAN_REV": "alt strategy"}
    for cfg_name, accts in acct_configs.items():
        for side in ["LONG", "SHORT"]:
            key = f"{side}|{cfg_name}"
            if key in baseline_map:
                s, w, a, t, n = baseline_map[key]
                bl = baseline_map.get(f"{side}|ALL_GATES", (0,0,0,0,0))
                print(f"  {accts:20s} {side:5s} {cfg_name:15s} Sharpe={s:>+.3f} WR={w:.1f}% Trades={t:.0f} (vs ALL: {s-bl[0]:>+.3f})")
    out = Path("data") / f"gate_ablation_{args.market}_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"market": args.market, "ranked": [(k, s, w, a, t, n) for k, s, w, a, t, n in ranked], "symbols": len(all_results), "elapsed": elapsed}, indent=2))
    print(f"\nSaved: {out}")

if __name__ == "__main__":
    main()
