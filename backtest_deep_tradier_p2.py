#!/usr/bin/env python3
# pylint: disable=W,C,R,I
"""
TRADIER PHASE 2 — Fine grid around OOS-validated winners from Phase 1.

Top OOS winners:
  RSI7_E3_X75  → OOS Sharpe 2.844, WR 69.7%, PF 5.036
  RSI3_E3_X80  → OOS Sharpe 1.568, WR 65.8%, PF 2.519
  RSI3_E5_X80  → OOS Sharpe 0.993, WR 65.4%, PF 2.605
  RSI7_E3_X80  → OOS Sharpe 0.840, WR 66.6%, PF 1.987
  RSI5_E5_X80  → OOS Sharpe 0.808, WR 65.0%, PF 3.727

Fine-tune: RSI period [5-10], entry [2-8], exit [70-90], SMA [100-300], MFI on/off, BB on/off
"""
import sys, json, signal, time, math
import numpy as np
from pathlib import Path
from multiprocessing import Pool, cpu_count
from dataclasses import asdict
# Import everything from the Phase 1 script
sys.path.insert(0, str(Path(__file__).parent))
from backtest_deep_tradier import DeepConfig, Position, load_klines, run_backtest, get_symbols, aggregate, run_config, RESULTS_DIR, KLINES_DIR, ENTRY_TF, N_WORKERS, logger, shutdown_flag


def _pool_init():
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)


def _worker(args):
    symbol, cfg_dict, oos = args
    cfg = DeepConfig(**cfg_dict)
    data = load_klines(symbol, ENTRY_TF)
    if data is None: return None
    try: return run_backtest(symbol, cfg, data, oos)
    except Exception: return None

def generate_phase2_configs():
    configs = []
    # ── Fine grid around RSI7 + exit 75-90 ──
    for rsi_p in [5, 6, 7, 8, 9, 10]:
        for entry in [2, 3, 4, 5, 6, 7, 8]:
            for exit_val in [70, 72, 75, 77, 80, 82, 85, 87, 90]:
                for sma in [100, 150, 200, 250, 300]:
                    configs.append(DeepConfig(name=f"P2_R{rsi_p}_E{entry}_X{exit_val}_S{sma}", rsi_period=rsi_p, rsi_entry_long=entry, rsi_entry_short=100-entry, rsi_exit_long=exit_val, rsi_exit_short=100-exit_val, sma_period=sma))
    # ── MFI threshold variations for best configs ──
    for mfi_t in [30, 40, 50, 60, 70]:
        for rsi_p in [5, 7]:
            for exit_val in [75, 80]:
                configs.append(DeepConfig(name=f"P2_R{rsi_p}_X{exit_val}_MFI{mfi_t}", rsi_period=rsi_p, rsi_entry_long=3, rsi_entry_short=97, rsi_exit_long=exit_val, rsi_exit_short=100-exit_val, mfi_threshold=mfi_t))
    # ── BB filter with best RSI configs ──
    for bb in [0.0, 0.05, 0.1, 0.15, 0.2, 0.25]:
        for rsi_p in [5, 7]:
            configs.append(DeepConfig(name=f"P2_R{rsi_p}_BB{bb}", rsi_period=rsi_p, rsi_entry_long=3, rsi_entry_short=97, rsi_exit_long=80, rsi_exit_short=20, use_bb_filter=True, bb_entry_pct=bb))
    # ── Profit target with best configs ──
    for pt in [2.0, 3.0, 4.0, 5.0, 7.0, 10.0, 15.0]:
        for rsi_p in [5, 7]:
            configs.append(DeepConfig(name=f"P2_R{rsi_p}_PT{pt}", rsi_period=rsi_p, rsi_entry_long=3, rsi_entry_short=97, rsi_exit_long=80, rsi_exit_short=20, use_profit_target=True, profit_target_pct=pt))
    # ── Time stop with best configs ──
    for days in [5, 7, 10, 15, 20, 30]:
        for rsi_p in [5, 7]:
            configs.append(DeepConfig(name=f"P2_R{rsi_p}_TS{days}d", rsi_period=rsi_p, rsi_entry_long=3, rsi_entry_short=97, rsi_exit_long=80, rsi_exit_short=20, use_time_stop=True, max_hold_days=days))
    # ── No DC breakout (pure RSI mean reversion) ──
    for rsi_p in [5, 6, 7, 8]:
        for entry in [2, 3, 5]:
            for exit_val in [75, 80, 85]:
                configs.append(DeepConfig(name=f"P2_PURE_R{rsi_p}_E{entry}_X{exit_val}", rsi_period=rsi_p, rsi_entry_long=entry, rsi_entry_short=100-entry, rsi_exit_long=exit_val, rsi_exit_short=100-exit_val, use_dc_breakout=False))
    # ── Stoch cross + RSI combo (re-test with optimal RSI) ──
    for rsi_p in [5, 7]:
        for exit_val in [75, 80, 85]:
            configs.append(DeepConfig(name=f"P2_STOCH_R{rsi_p}_X{exit_val}", rsi_period=rsi_p, rsi_entry_long=3, rsi_entry_short=97, rsi_exit_long=exit_val, rsi_exit_short=100-exit_val, use_stoch_entry=True))
    return configs

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--symbols", type=int, default=0)
    args = parser.parse_args()
    RESULTS_FILE_P2 = RESULTS_DIR / "deep_results_p2.json"
    BEST_FILE_P2 = RESULTS_DIR / "deep_best_p2.json"
    if args.report:
        if not RESULTS_FILE_P2.exists(): logger.error("No P2 results"); sys.exit(0)
        results = json.loads(RESULTS_FILE_P2.read_text())
        sorted_r = sorted(results.items(), key=lambda x: x[1].get("avg_sharpe", -999), reverse=True)
        print(f"\n{'═' * 110}")
        print(f"TRADIER PHASE 2 — Top 30 (fine grid around OOS winners)")
        print(f"{'═' * 110}")
        print(f"\n{'#':>3} {'Config':<45} {'Sharpe':>8} {'WR%':>7} {'PF':>7} {'DD%':>7} {'Trades':>7} {'AvgBars':>8} {'TotalPnL':>10}")
        print("-" * 105)
        for i, (n, m) in enumerate(sorted_r[:30]):
            print(f"{i+1:>3} {n:<45} {m.get('avg_sharpe', 0):>8.3f} {m.get('avg_wr', 0):>6.1f}% {m.get('avg_pf', 0):>7.3f} {m.get('avg_dd', 0):>6.2f}% {m.get('n_trades', 0):>7} {m.get('avg_bars', 0):>8.1f} {m.get('total_pnl', 0):>10.1f}")
        # Positive PnL only
        pos = [(n, m) for n, m in results.items() if m.get('total_pnl', 0) > 0 and m.get('n_trades', 0) >= 100]
        if pos:
            print(f"\nPOSITIVE PnL + 100+ trades:")
            pos.sort(key=lambda x: x[1]['avg_sharpe'], reverse=True)
            for i, (n, m) in enumerate(pos[:15]):
                print(f"  {n:<45} Sharpe={m['avg_sharpe']:>6.3f} WR={m['avg_wr']:.1f}% PF={m['avg_pf']:.3f} Trades={m['n_trades']} PnL={m['total_pnl']:.1f}")
        print(f"\nTotal P2 configs tested: {len(results)}")
        sys.exit(0)
    symbols = get_symbols(args.symbols)
    logger.info(f"Phase 2: {len(symbols)} symbols")
    configs = generate_phase2_configs()
    logger.info(f"Generated {len(configs)} Phase 2 configs")
    all_results = {}
    if RESULTS_FILE_P2.exists():
        try: all_results = json.loads(RESULTS_FILE_P2.read_text())
        except: pass
    remaining = [c for c in configs if c.name not in all_results]
    logger.info(f"Already tested: {len(all_results)}, remaining: {len(remaining)}")
    pool = Pool(N_WORKERS, initializer=_pool_init)
    best_sharpe = max((v.get("avg_sharpe", -999) for v in all_results.values()), default=-999)
    last_save = time.time()
    try:
        for ci, cfg in enumerate(remaining):
            if shutdown_flag: break
            t0 = time.time()
            tasks = [(s, asdict(cfg), False) for s in symbols]
            results = pool.map(_worker, tasks, chunksize=max(1, len(symbols) // N_WORKERS))
            agg = aggregate(results)
            if not agg: continue
            agg["config"] = asdict(cfg)
            all_results[cfg.name] = agg
            elapsed = time.time() - t0
            marker = ""
            if agg.get("avg_sharpe", -999) > best_sharpe:
                best_sharpe = agg["avg_sharpe"]
                marker = " ★ NEW BEST"
                oos_agg = run_config(pool, cfg, symbols, oos=True)
                if oos_agg:
                    agg["oos_sharpe"] = oos_agg.get("avg_sharpe")
                    agg["oos_wr"] = oos_agg.get("avg_wr")
                    agg["oos_pf"] = oos_agg.get("avg_pf")
                    marker += f" (OOS: {oos_agg.get('avg_sharpe', 0):.3f})"
            if (ci + 1) % 50 == 0 or marker:
                logger.info(f"  [{ci+1}/{len(remaining)}] {cfg.name}: Sharpe={agg.get('avg_sharpe')} WR={agg.get('avg_wr')}% PF={agg.get('avg_pf')} ({agg.get('n_trades', 0)} trades, {elapsed:.1f}s){marker}")
            if time.time() - last_save > 60:
                RESULTS_FILE_P2.write_text(json.dumps(all_results, indent=2, default=str)); last_save = time.time()
    finally:
        pool.close(); pool.join()
    RESULTS_FILE_P2.write_text(json.dumps(all_results, indent=2, default=str))
    sorted_best = sorted(all_results.items(), key=lambda x: x[1].get("avg_sharpe", -999), reverse=True)[:20]
    BEST_FILE_P2.write_text(json.dumps(dict(sorted_best), indent=2, default=str))
    logger.info(f"Phase 2 done. {len(all_results)} configs. Best: {best_sharpe:.3f}")
