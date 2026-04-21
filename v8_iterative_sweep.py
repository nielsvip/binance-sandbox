#!/usr/bin/env python3
"""
v8_iterative_sweep.py — Phased iterative sweep, anchored on V8Q v3 winner (Sharpe 1.93).

Phase 1 (full, 4 orthogonal sweeps):
  S1_P1A: PT × SL × WT_EXIT precision on 12 crypto symbols
  S1_P1B: Symbol subset rotation (find best 4/5/6-sym combo from 12)
  S2_P1C: Score × HTF × Hold entry-stringency grid
  S2_P1D: Block weight / block-enable combinations

Phase 2 (smaller, seeded by top-N of Phase 1):
  Focused micro-experiments around each top winner

Phase 3 (validation, broadest):
  Best from Phase 2 on 24-symbol expanded universe
"""
import sys, time, itertools, json, argparse
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
sys.path.insert(0, str(Path(__file__).resolve().parent))
from v8_quick_engine import QuickConfig, load_npz, simulate, FAST_SYMBOLS_CRYPTO

BASE = Path(__file__).resolve().parent

FAST12 = FAST_SYMBOLS_CRYPTO.split(",")  # 11 actually — BTC/ETH/SOL/BNB/XRP/ADA/AVAX/DOT/LINK/LTC/UNI
# Add SKYUSDT to get exactly 12 if available (optional)


def winner_v3():
    c = QuickConfig()
    c.STRENGTH_FILTER_ENABLED = True
    c.STRENGTH_MIN_SCORE = 5.0
    c.HTF_MIN_ALIGNED = 1
    c.D_TREND_REQUIRED = True
    c.MIN_HOLD_BARS = 10
    c.WT_EXIT_MIN_TFS = 2
    c.COOLDOWN_BARS = 3
    return c


def run_one(args):
    label, cfg_overrides, symbols = args
    stores = load_npz('crypto', symbols, '2022-01-01')
    if not stores: return {"label": label, "sharpe": 0, "trades": 0}
    cfg = winner_v3()
    for k, v in cfg_overrides.items():
        if hasattr(cfg, k):
            cur = getattr(cfg, k)
            if isinstance(cur, bool): setattr(cfg, k, bool(v))
            elif isinstance(cur, int): setattr(cfg, k, int(v))
            elif isinstance(cur, float): setattr(cfg, k, float(v))
            else: setattr(cfg, k, v)
    t0 = time.time()
    r = simulate(stores, cfg, 10000.0)
    r["label"] = label; r["cfg"] = cfg_overrides; r["n_symbols"] = len(symbols)
    r["symbols_list"] = ",".join(symbols)
    r["elapsed"] = round(time.time() - t0, 1)
    return r


def sweep_P1A_pt_sl_wtexit():
    """PT × SL × WT_EXIT precision grid on full 11-symbol set."""
    exps = []
    for pt in [1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9]:
        for sl_enable, sl_pct in [(False, 0), (True, 0.3), (True, 0.5), (True, 0.7), (True, 1.0), (True, 1.5)]:
            for wt_exit in [2, 3, 4]:  # MEMORY RULE: valid range only. 1 too loose, 5 banned (Sharpe 0.001).
                cfg = {"WT_EXIT_MIN_TFS": wt_exit}
                if sl_enable:
                    cfg["STOP_LOSS_ENABLED"] = True
                    cfg["STOP_LOSS_PCT"] = sl_pct
                exps.append((f"P1A_PT{pt}_SL{sl_pct if sl_enable else 'off'}_WT{wt_exit}", cfg, FAST12))
    return exps


def sweep_P1B_symbol_subsets():
    """Subset rotation — find best N-symbol combo from 11."""
    exps = []
    all_syms = FAST12
    # All 4-sym and 5-sym combinations from top-7
    TOP7 = ["LINKUSDT","ETHUSDT","DOTUSDT","BTCUSDT","UNIUSDT","ADAUSDT","BNBUSDT"]
    for size in [3, 4, 5, 6]:
        for combo in itertools.combinations(TOP7, size):
            exps.append((f"P1B_N{size}_" + "+".join(s[:3] for s in combo), {}, list(combo)))
    # Full 11-sym baseline
    exps.append(("P1B_ALL11", {}, all_syms))
    return exps


def sweep_P1C_entry_stringency():
    """Score × HTF × Hold grid on full 11-sym."""
    exps = []
    for score in [3.0, 4.0, 4.5, 5.0, 5.5, 6.0, 7.0]:
        for htf in [0, 1, 2, 3]:
            for hold in [5, 8, 10, 12, 15, 20]:
                cfg = {"STRENGTH_MIN_SCORE": score, "HTF_MIN_ALIGNED": htf, "MIN_HOLD_BARS": hold}
                exps.append((f"P1C_S{score}_H{htf}_HD{hold}", cfg, FAST12))
    return exps


def sweep_P1D_blocks():
    """Block-enable combinations × with/without each CT gate."""
    exps = []
    # Baseline: all 7 blocks on
    exps.append(("P1D_ALL7_BLOCKS", {}, FAST12))
    # Each block individually disabled (7 one-out experiments)
    for block in ["B02","B04","B10","B11","B12","B14","B15"]:
        key = f"REENTRY_{block}_{'BC156_BOTTOM' if block=='B02' else 'DC_RETEST' if block=='B04' else 'STOCH_REV' if block=='B10' else 'DC_BREAK' if block=='B11' else 'WT_MOM' if block=='B12' else 'HA_TREND' if block=='B14' else 'STRONG_TREND'}_ENABLED"
        exps.append((f"P1D_NO_{block}", {key: False}, FAST12))
    # CT gate variations
    exps.append(("P1D_NO_CT_VEL", {"CT_WT_VELOCITY_GATE_ENABLED": False}, FAST12))
    exps.append(("P1D_NO_CT_DC", {"CT_DC_CROSSOVER_SKIP_ENABLED": False}, FAST12))
    exps.append(("P1D_NO_BOTH_CT", {"CT_WT_VELOCITY_GATE_ENABLED": False, "CT_DC_CROSSOVER_SKIP_ENABLED": False}, FAST12))
    # Satoshit on/off
    exps.append(("P1D_NO_SATOSHIT", {"SATOSHIT_ENABLED": False}, FAST12))
    return exps


PHASES = {
    "P1A": sweep_P1A_pt_sl_wtexit,
    "P1B": sweep_P1B_symbol_subsets,
    "P1C": sweep_P1C_entry_stringency,
    "P1D": sweep_P1D_blocks,
}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--phase", choices=list(PHASES.keys()) + ["ALL"], default="ALL")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--top-n", type=int, default=30, help="Print top N results")
    args = p.parse_args()

    phase_keys = [args.phase] if args.phase != "ALL" else list(PHASES.keys())
    all_results = {}

    for pk in phase_keys:
        exps = PHASES[pk]()
        print(f"\n{'='*70}\n  PHASE {pk} — {len(exps)} configs, {args.workers} workers\n{'='*70}")
        t_phase = time.time()
        results = []
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(run_one, e): e for e in exps}
            for fut in as_completed(futs):
                try:
                    r = fut.result()
                    results.append(r)
                    if r.get("sharpe", 0) > 1.5:
                        print(f"  🎯 {r['label']:<40} Sharpe={r['sharpe']:.4f} trades={r['trades']:>4} wr={r.get('wr',0):.1f}% avg={r.get('avg_pnl_pct',0):.3f}%")
                except Exception as e:
                    print(f"  ERROR: {e}")
        elapsed = time.time() - t_phase
        print(f"\n  Phase {pk} done in {elapsed:.0f}s ({len(results)} results)")
        all_results[pk] = results
        valid = sorted([r for r in results if r.get("trades", 0) >= 20], key=lambda r: r["sharpe"], reverse=True)
        print(f"\n  TOP {args.top_n} (trades>=20):")
        for r in valid[:args.top_n]:
            print(f"    {r['label']:<40} Sharpe={r['sharpe']:.4f} trades={r['trades']:>4} wr={r.get('wr',0):.1f}% avg={r.get('avg_pnl_pct',0):.3f}% pnl=${r.get('pnl',0):.0f}")

    out_path = BASE / "data" / "sweep_results" / f"v8_iterative_{args.phase}_{int(time.time())}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(all_results, indent=2, default=str))
    print(f"\nSaved: {out_path}")


if __name__ == "__main__":
    main()
