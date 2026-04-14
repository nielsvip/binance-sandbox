#!/usr/bin/env python3
"""
GOLDEN ANGLES TEST — Find optimal WT × DC × TF combinations for entries AND exits.
Tests both crypto (3m focus) and stocks (5m/15m focus).
With REENTRY: get in/out frequently since 0% commission.
STRICT_NO_LOSS: never exit at a loss.
Tests LONGS and SHORTS equally.

Usage:
  python3 test_golden_angles.py --market crypto --workers 4
  python3 test_golden_angles.py --market stocks --workers 4
"""
import argparse, json, math, sys, time, itertools
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import backtest_engine
FEE = 0.0  # 0% commission on USDC crypto and stocks

# ═══════════════════════════════════════════════════════════════
# ENTRY CONDITIONS: WT value × DC position × HTF confirmation
# ═══════════════════════════════════════════════════════════════
def build_entry_grid():
    """All possible entry conditions to test."""
    entries = []
    # WT cross value thresholds (how oversold at the cross)
    wt_cross_values = [None, -60, -40, -20, 0]  # None = any cross, -60 = deeply oversold only
    # DC position thresholds
    dc_positions = [None, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50]  # None = no DC filter
    # HTF confirmations required
    htf_confirms = [0, 1, 2, 3]  # Number of higher TFs that must be bullish
    # WT score minimums (wt1-wt2 spread)
    wt_scores = [None, 0, 5, 10]  # None = any, 0 = just bullish, 10 = strong momentum
    for wt_cv, dc_pos, htf_c, wt_sc in itertools.product(wt_cross_values, dc_positions, htf_confirms, wt_scores):
        name_parts = []
        if wt_cv is not None: name_parts.append(f"wtcv{wt_cv}")
        if dc_pos is not None: name_parts.append(f"dc{dc_pos}")
        if htf_c > 0: name_parts.append(f"htf{htf_c}")
        if wt_sc is not None: name_parts.append(f"wts{wt_sc}")
        if not name_parts: name_parts = ["ANY"]
        entries.append(("|".join(name_parts), {"wt_cross_value_max": wt_cv, "dc_pos_max": dc_pos, "htf_confirm_min": htf_c, "wt_score_min": wt_sc}))
    return entries

# ═══════════════════════════════════════════════════════════════
# EXIT CONDITIONS: WT cross against × MFI flip × HTF exhaustion
# ═══════════════════════════════════════════════════════════════
def build_exit_grid():
    """All possible exit conditions to test."""
    exits = []
    # WT cross against on which TF triggers exit
    wt_exit_tfs = ["micro", "scalp", "htf1", "htf2"]  # Which TF's WT cross triggers exit
    # Minimum gain before exit allowed
    min_gains = [0.1, 0.3, 0.5, 1.0]
    # MFI exhaustion threshold
    mfi_exits = [None, 65, 70, 75, 80]  # None = no MFI exit, 70 = exit when MFI > 70
    for wt_tf, mg, mfi in itertools.product(wt_exit_tfs, min_gains, mfi_exits):
        name_parts = [f"wt_{wt_tf}", f"mg{mg}"]
        if mfi is not None: name_parts.append(f"mfi{mfi}")
        exits.append(("|".join(name_parts), {"wt_exit_tf": wt_tf, "min_gain": mg, "mfi_exit": mfi}))
    # Also test: pure MFI exit (no WT)
    for mfi in [65, 70, 75]:
        for mg in [0.1, 0.3]:
            exits.append((f"mfi_only{mfi}|mg{mg}", {"wt_exit_tf": None, "min_gain": mg, "mfi_exit": mfi}))
    return exits

def simulate_golden(precomputed, entry_cfg, exit_cfg, tf_map, is_long):
    """Simulate one entry×exit combo with reentry."""
    ind = precomputed["indicators"]
    n = precomputed["n"]
    c = ind.get("current_price")
    if c is None or not isinstance(c, np.ndarray) or len(c) < 300: return None
    h = ind.get(f"high_{tf_map['focus']}", c)
    l = ind.get(f"low_{tf_map['focus']}", c)
    o = ind.get(f"open_{tf_map['focus']}", c)

    def _g(key, default=0.0):
        v = ind.get(key)
        if v is not None and isinstance(v, np.ndarray) and len(v) == n:
            return v
        return np.full(n, default)

    # Load WT and DC arrays for all TFs
    tf_list = list(set(filter(None, [tf_map.get('micro'), tf_map.get('scalp'), tf_map.get('htf1'), tf_map.get('htf2'), tf_map.get('htf3')])))
    wt1 = {tf: _g(f"wt1_{tf}") for tf in tf_list}
    wt2 = {tf: _g(f"wt2_{tf}") for tf in tf_list}
    wt_cross_bull = {tf: _g(f"wt_cross_bull_{tf}") for tf in tf_list}
    wt_cross_bear = {tf: _g(f"wt_cross_bear_{tf}") for tf in tf_list}
    dc_pos = {tf: _g(f"dc_position_{tf}", 0.5) for tf in tf_list}
    mfi = {tf: _g(f"mfi_{tf}", 50) for tf in tf_list}

    # Entry parameters
    wt_cv_max = entry_cfg.get("wt_cross_value_max")
    dc_pos_max = entry_cfg.get("dc_pos_max")
    htf_min = entry_cfg.get("htf_confirm_min", 0)
    wt_sc_min = entry_cfg.get("wt_score_min")

    # Exit parameters
    wt_exit_tf_name = exit_cfg.get("wt_exit_tf")
    min_gain = exit_cfg.get("min_gain", 0.3) / 100.0
    mfi_exit_th = exit_cfg.get("mfi_exit")

    focus_tf = tf_map['scalp']
    micro_tf = tf_map['micro']

    trades = []
    last_exit = 250
    reentries = 0

    for ei in range(251, n - 2):
        if ei <= last_exit: continue

        # CHECK ENTRY
        # 1. WT cross on micro or scalp TF
        cross_signal = False
        if is_long:
            cross_signal = wt_cross_bull[micro_tf][ei] > 0 or wt_cross_bull[focus_tf][ei] > 0
        else:
            cross_signal = wt_cross_bear[micro_tf][ei] > 0 or wt_cross_bear[focus_tf][ei] > 0
        if not cross_signal: continue

        # 2. WT cross value filter (how oversold at the cross)
        if wt_cv_max is not None:
            wt1_at_cross = float(wt1[focus_tf][ei])
            if is_long and wt1_at_cross > wt_cv_max: continue
            if not is_long and wt1_at_cross < -wt_cv_max: continue

        # 3. DC position filter
        if dc_pos_max is not None:
            dc_val = float(dc_pos[focus_tf][ei])
            if is_long and dc_val > dc_pos_max: continue
            if not is_long and dc_val < (1.0 - dc_pos_max): continue

        # 4. HTF confirmation (count of higher TFs where WT is bullish/bearish)
        if htf_min > 0:
            htf_count = 0
            for htf_key in ['htf1', 'htf2', 'htf3']:
                htf = tf_map.get(htf_key)
                if htf and htf in wt1:
                    if is_long and wt1[htf][ei] > wt2[htf][ei]: htf_count += 1
                    elif not is_long and wt1[htf][ei] < wt2[htf][ei]: htf_count += 1
            if htf_count < htf_min: continue

        # 5. WT score filter
        if wt_sc_min is not None:
            score = float(wt1[focus_tf][ei] - wt2[focus_tf][ei])
            if is_long and score < wt_sc_min: continue
            if not is_long and -score < wt_sc_min: continue

        # ENTER at next bar's open
        ep = float(o[ei + 1]) if ei + 1 < n else float(c[ei])
        if ep <= 0: continue

        # SIMULATE TRADE
        mg = 0.0
        for bi in range(ei + 1, n):
            _c = float(c[bi]); _h = float(h[bi]); _l = float(l[bi])
            if _c <= 0: continue
            pnl = (_c - ep) / ep if is_long else (ep - _c) / ep
            hp = (_h - ep) / ep if is_long else (ep - _l) / ep
            mg = max(mg, hp)

            exit_signal = False

            # WT cross against exit
            if wt_exit_tf_name and pnl > min_gain:
                etf = tf_map.get(wt_exit_tf_name, focus_tf)
                if is_long and wt_cross_bear[etf][bi] > 0: exit_signal = True
                elif not is_long and wt_cross_bull[etf][bi] > 0: exit_signal = True

            # MFI exhaustion exit
            if mfi_exit_th is not None and pnl > min_gain:
                mfi_val = float(mfi[tf_map['htf1']][bi])
                if is_long and mfi_val > mfi_exit_th: exit_signal = True
                elif not is_long and mfi_val < (100 - mfi_exit_th): exit_signal = True

            # NOLOSS safety: if we saw gain > 0.5% and it's dropping below 0.3%
            if mg > 0.005 and pnl < 0.003: exit_signal = True

            if exit_signal:
                trades.append(pnl - 2 * FEE)
                last_exit = bi
                reentries += 1
                break
        # If never exited: position still open (STRICT_NO_LOSS — don't count as loss)

    if len(trades) < 5: return None
    t = np.array(trades)
    sharpe = np.mean(t) / np.std(t) * math.sqrt(len(t)) if np.std(t) > 1e-10 else 0
    return {
        "sharpe": round(sharpe, 3),
        "wr": round(np.mean(t > 0) * 100, 1),
        "trades": len(trades),
        "pnl": round(np.sum(t) * 100, 2),
        "avg_ret": round(np.mean(t) * 100, 3),
        "max_ret": round(np.max(t) * 100, 2),
        "reentries": reentries,
    }

def worker(args):
    sym, entry_grid, exit_grid, tf_map, primary_tf = args
    from backtest_engine import precompute_real_indicators
    pre = precompute_real_indicators(sym, primary_tf)
    if pre is None: return sym, {}
    # Map ALL TFs from the hierarchy (every TF that differs from primary)
    all_tfs = set(v for v in tf_map.values() if v and v != primary_tf)
    for htf in all_tfs:
        htf_pre = precompute_real_indicators(sym, htf)
        if htf_pre:
            htf_df = htf_pre["df"]
            htf_ts = htf_df['timestamp_dt'].values.astype('datetime64[s]').astype(np.int64)
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
        for entry_name, entry_cfg in entry_grid:
            for exit_name, exit_cfg in exit_grid:
                combo = f"{side}|{entry_name}→{exit_name}"
                r = simulate_golden(pre, entry_cfg, exit_cfg, tf_map, is_long)
                if r: results[combo] = r
    return sym, results

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=["crypto", "stocks"], required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--quick", action="store_true")
    args = parser.parse_args()

    if args.market == "crypto":
        backtest_engine.KLINES_DIR = Path("/Users/niels/Documents/binance/klines_cache")
        if Path("/home/niels/binance-sandbox/klines_cache").exists():
            backtest_engine.KLINES_DIR = Path("/home/niels/binance-sandbox/klines_cache")
        primary_tf = "3m"  # 3m as primary (matches TF_FOCUS), 1m for micro timing
        tf_map = {"micro": "1m", "scalp": "3m", "focus": "3m", "htf1": "15m", "htf2": "1h", "htf3": "4h"}
        sym_file = Path(backtest_engine.KLINES_DIR).parent / "og_symbols.json"
        if not sym_file.exists(): sym_file = Path("/home/niels/binance-sandbox/og_symbols.json")
    else:
        backtest_engine.KLINES_DIR = Path("/Users/niels/Documents/binance/klines_cache/tradier")
        primary_tf = "1h"  # Most stock data
        tf_map = {"micro": "5m", "scalp": "15m", "focus": "1h", "htf1": "1h", "htf2": "4h"}
        sym_file = None

    if sym_file and sym_file.exists():
        syms = json.loads(sym_file.read_text())
    else:
        syms = sorted([f.stem.replace(f"_{primary_tf}", "") for f in backtest_engine.KLINES_DIR.glob(f"*_{primary_tf}.json") if len(json.loads(f.read_text())) > 300])
        if args.market == "stocks":
            syms = [s for s in syms if s.isalpha() and len(s) <= 5 and "USDT" not in s]

    import random; random.seed(42); random.shuffle(syms)
    syms = syms[:args.limit]

    entry_grid = build_entry_grid()
    exit_grid = build_exit_grid()

    if args.quick:
        # Reduce grid for quick testing
        entry_grid = entry_grid[::10]  # Every 10th
        exit_grid = exit_grid[::5]

    total_combos = len(entry_grid) * len(exit_grid) * 2  # ×2 for LONG+SHORT
    print(f"GOLDEN ANGLES TEST — {args.market.upper()}")
    print(f"Symbols: {len(syms)} | Entries: {len(entry_grid)} | Exits: {len(exit_grid)}")
    print(f"Combos per symbol: {total_combos} | Total: {total_combos * len(syms):,}")
    print(f"TFs: micro={tf_map['micro']} scalp={tf_map['scalp']} htf1={tf_map['htf1']} htf2={tf_map['htf2']}")

    start = time.time()
    all_results = {}
    tasks = [(sym, entry_grid, exit_grid, tf_map, primary_tf) for sym in syms]

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

    print(f"\n{'Combo':70s} {'Sharpe':>7s} {'WR':>5s} {'AvgRet':>7s} {'Trades':>6s} {'N':>3s}")
    print("=" * 100)

    # Top 20 LONG
    long_ranked = [r for r in ranked if r[0].startswith("LONG")]
    print(f"\n--- TOP 20 LONG ---")
    for name, sharpe, wr, avg, trades, n in long_ranked[:20]:
        print(f"{name:70s} {sharpe:>+7.3f} {wr:>4.1f}% {avg:>+6.3f}% {trades:>5.0f} {n:>3d}")

    # Top 20 SHORT
    short_ranked = [r for r in ranked if r[0].startswith("SHORT")]
    print(f"\n--- TOP 20 SHORT ---")
    for name, sharpe, wr, avg, trades, n in short_ranked[:20]:
        print(f"{name:70s} {sharpe:>+7.3f} {wr:>4.1f}% {avg:>+6.3f}% {trades:>5.0f} {n:>3d}")

    # Top 20 OVERALL
    print(f"\n--- TOP 20 OVERALL ---")
    for name, sharpe, wr, avg, trades, n in ranked[:20]:
        print(f"{name:70s} {sharpe:>+7.3f} {wr:>4.1f}% {avg:>+6.3f}% {trades:>5.0f} {n:>3d}")

    # Save
    out = Path("data") / f"golden_angles_{args.market}_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"ranked": [(k, s, w, a, t, n) for k, s, w, a, t, n in ranked[:200]], "total_combos": total_combos, "symbols": len(all_results), "elapsed": elapsed}, indent=2))
    print(f"\nSaved: {out}")

if __name__ == "__main__":
    main()
