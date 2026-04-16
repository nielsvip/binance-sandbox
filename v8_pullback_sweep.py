#!/usr/bin/env python3
"""
v8_pullback_sweep.py — Targeted sweep for pullback-reversal strategy.

Goal: find configs that maximize Sharpe using the NEW pullback blocks (B_PULL1-4)
+ wt_velocity_decay exit. Test on 12 crypto + 12 tradier.

Key dimensions:
  - STRENGTH_MIN_SCORE: 5, 8, 10, 12, 15 (pullback weights are higher)
  - MIN_HOLD_BARS: 10, 20, 30, 40 (let pullback-to-trend play out)
  - WT_EXIT_MIN_TFS: 2, 3, 4 (valid only per memory)
  - PT: 1.4, 1.6, 1.8, 2.0, 2.5
  - WT_VEL_DECAY_THRESHOLD: 0.5, 1.0, 1.5, 2.0
  - Symbol subsets (crypto only)

Baseline floor: Sharpe 1.5 (report all above; dump top-100 to CSV)
"""
import argparse, json, os, sys, time, itertools
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
sys.path.insert(0, str(Path(__file__).resolve().parent))
from v8_quick_engine import QuickConfig, load_npz, simulate, FAST_SYMBOLS_CRYPTO, FAST_SYMBOLS_TRADIER

BASE = Path(__file__).resolve().parent

TOP3 = ["LINKUSDT","ETHUSDT","DOTUSDT"]
TOP5 = ["LINKUSDT","ETHUSDT","DOTUSDT","BTCUSDT","UNIUSDT"]
ALL11 = FAST_SYMBOLS_CRYPTO.split(",")
ALL_TRADIER = FAST_SYMBOLS_TRADIER.split(",")


def build_grid():
    combos = []
    score_vals = [5, 8, 10, 12, 15]
    hold_vals = [10, 20, 30, 40]
    wt_exit_vals = [2, 3, 4]
    pt_vals = [1.4, 1.6, 1.8, 2.0, 2.5]
    decay_vals = [0.5, 1.0, 1.5, 2.0]
    # Block toggles: try with/without breakout blocks
    block_presets = [
        {},  # all on
        {"REENTRY_B11_DC_BREAK_ENABLED": False, "REENTRY_B15_STRONG_TREND_ENABLED": False},  # pullback only
        {"REENTRY_B11_DC_BREAK_ENABLED": False},  # no DC break
    ]
    for score in score_vals:
        for hold in hold_vals:
            for wt_exit in wt_exit_vals:
                for pt in pt_vals:
                    for decay in decay_vals:
                        for blocks in block_presets:
                            cfg = {
                                "STRENGTH_MIN_SCORE": float(score),
                                "MIN_HOLD_BARS": hold,
                                "WT_EXIT_MIN_TFS": wt_exit,
                                "PROFIT_TARGET_ENABLED": True,
                                "PROFIT_TARGET_PCT": pt,
                                "WT_VEL_DECAY_EXIT_ENABLED": True,
                                "WT_VEL_DECAY_THRESHOLD": decay,
                                "HTF_MIN_ALIGNED": 1,
                                "D_TREND_REQUIRED": True,
                                "COOLDOWN_BARS": 3,
                                **blocks,
                            }
                            combos.append(cfg)
    return combos


def run_one(args):
    mode, symbols, cfg_dict, npz_dir, start = args
    stores = load_npz(mode, symbols, start, npz_dir)
    if not stores: return {"sharpe": 0, "trades": 0, "cfg": cfg_dict, "n_syms": 0}
    cfg = QuickConfig()
    if mode == "tradier": cfg.apply_tradier_defaults()
    cfg.STRENGTH_FILTER_ENABLED = True
    cfg.CT_WT_VELOCITY_GATE_ENABLED = True
    cfg.CT_DC_CROSSOVER_SKIP_ENABLED = True
    cfg.CT_15M_MOMENTUM_GATE_ENABLED = False
    cfg.CT_CHOP_4H_GATE_ENABLED = False
    cfg.CT_VOLUME_SURGE_GATE_ENABLED = False
    for k, v in cfg_dict.items():
        if hasattr(cfg, k):
            cur = getattr(cfg, k)
            if isinstance(cur, bool): setattr(cfg, k, bool(v))
            elif isinstance(cur, int): setattr(cfg, k, int(v))
            elif isinstance(cur, float): setattr(cfg, k, float(v))
            else: setattr(cfg, k, v)
    t0 = time.time()
    r = simulate(stores, cfg, 10000.0)
    r["cfg"] = cfg_dict
    r["n_syms"] = len(symbols)
    r["elapsed"] = round(time.time() - t0, 1)
    return r


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["crypto","tradier"], default="crypto")
    p.add_argument("--symbols", default="fast")
    p.add_argument("--start", default="")
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--npz-dir", default="")
    args = p.parse_args()
    start = args.start or ("2022-01-01" if args.mode == "crypto" else "2024-01-01")
    if args.symbols == "TOP3": syms = TOP3
    elif args.symbols == "TOP5": syms = TOP5
    elif args.symbols == "fast": syms = ALL_TRADIER if args.mode == "tradier" else ALL11
    else: syms = [s.strip() for s in args.symbols.split(",")]

    grid = build_grid()
    print(f"Pullback sweep: mode={args.mode} | {len(syms)} syms | {len(grid)} configs | {args.workers} workers")
    todo = [(args.mode, syms, cfg, args.npz_dir, start) for cfg in grid]
    t0 = time.time()
    best = 0
    results = []
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(run_one, t): i for i, t in enumerate(todo)}
        done = 0
        for fut in as_completed(futs):
            try: r = fut.result()
            except Exception as e: continue
            done += 1
            results.append(r)
            s = r.get("sharpe", 0)
            if s > best and r.get("trades", 0) >= 30:
                best = s
                print(f"  🎯 [{done}/{len(todo)}] NEW BEST Sharpe={s:.4f} trades={r['trades']} wr={r.get('wr',0):.1f}% avg={r.get('avg_pnl_pct',0):.3f}% cfg={r['cfg']}")
            if done % 50 == 0:
                elapsed = time.time() - t0
                rate = done / elapsed if elapsed > 0 else 0
                eta = (len(todo) - done) / rate / 60 if rate > 0 else 0
                print(f"  [{done}/{len(todo)}] elapsed={elapsed:.0f}s rate={rate:.2f}/s ETA={eta:.0f}min best={best:.4f}")

    valid = sorted([r for r in results if r.get("trades", 0) >= 30], key=lambda r: r["sharpe"], reverse=True)
    print(f"\n{'='*110}")
    print(f"  TOP 30 (trades>=30):")
    print(f"{'='*110}")
    for r in valid[:30]:
        print(f"  Sharpe={r['sharpe']:.4f} trades={r['trades']:>4} wr={r.get('wr',0):.1f}% avg={r.get('avg_pnl_pct',0):.3f}% pnl=${r.get('pnl',0):.0f} cfg={r['cfg']}")
    out = BASE / "data" / "sweep_results" / f"v8_pullback_{args.mode}_{int(time.time())}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(valid[:200], indent=2, default=str))
    print(f"\nSaved top-200: {out}")


if __name__ == "__main__":
    main()
