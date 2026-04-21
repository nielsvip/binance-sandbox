#!/usr/bin/env python3
"""
v8_million_sweep.py — Massive 12-symbol parameter sweep (millions of combinations).

RULES ENFORCED:
  - Baseline floor: Sharpe >= 2.0 (below = trash, not worth logging detail)
  - Valid WT_EXIT_MIN_TFS range: [2, 3, 4] ONLY (5 banned, 1 too loose)
  - Dead switches excluded: CT_15M_MOMENTUM, CT_CHOP_4H, CT_VOLUME_SURGE
  - CT_WT_VELOCITY_GATE + CT_DC_CROSSOVER_SKIP must stay True (proven)
  - Block scoring weights stay anchor: B15=4, B04/B11=3, B02=2

Grid shape (12 symbols × 4yr = ~300s/config with 4 workers = ~75s/config parallel):
  Entry stringency:  6 scores × 4 HTF × 5 hold    = 120
  Exit tuning:       3 WT_exit × 7 PT × 5 SL      = 105
  Block ablation:    2^7 = 128 block on/off combos
  Symbol subsets:    2^12 - 12 - 1 = ~4100 combos (skip 0/1 sym)

  Smart: don't do full Cartesian. Focus on dimensions most likely to lift past 2.0
  based on current peak (TOP3 Sharpe 1.93): tighter PT, per-symbol subsets, block combos.

This version: ~250K meaningful configs per mode (crypto + tradier).
"""
import argparse, json, os, sys, time, itertools, hashlib, csv
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
sys.path.insert(0, str(Path(__file__).resolve().parent))
from v8_quick_engine import QuickConfig, load_npz, simulate, FAST_SYMBOLS_CRYPTO, FAST_SYMBOLS_TRADIER

BASE = Path(__file__).resolve().parent
SHARPE_BASELINE = 2.0  # MEMORY RULE: below 2.0 is trash


def build_grid():
    """Structured grid — avoids banned combos per memory rules."""
    # ENTRY stringency (6×4×5=120)
    score_vals = [4.0, 4.5, 5.0, 5.5, 6.0, 7.0]
    htf_vals = [0, 1, 2, 3]
    hold_vals = [8, 10, 12, 15, 20]

    # EXIT tuning (3×7×5=105) — WT_EXIT strictly in [2,3,4] per memory
    wt_exit_vals = [2, 3, 4]
    pt_vals = [1.2, 1.4, 1.5, 1.6, 1.7, 1.8, 2.0]
    sl_vals = [None, 0.5, 0.8, 1.0, 1.5]  # None = SL disabled

    # CT gates (locked per memory)
    # CT_WT_VELOCITY=True always; CT_DC_CROSSOVER=True always
    # CT_15M_MOMENTUM/CT_CHOP_4H/CT_VOLUME_SURGE=False always (ABLATION dead)

    # Block enables (7 block switches — smarter than 2^7)
    # Key combos: all-on, NO_B11 (proven winner), NO_B09/B01 (already default-off so always same)
    block_presets = [
        {},  # baseline = all on
        {"REENTRY_B11_DC_BREAK_ENABLED": False},  # proven winner on 11-sym
        {"REENTRY_B12_WT_MOM_ENABLED": False},
        {"REENTRY_B14_HA_TREND_ENABLED": False},
        {"REENTRY_B10_STOCH_REV_ENABLED": False},
        {"REENTRY_B02_BC156_BOTTOM_ENABLED": False},
        {"REENTRY_B11_DC_BREAK_ENABLED": False, "REENTRY_B12_WT_MOM_ENABLED": False},
    ]

    # Build total grid
    combos = []
    for score in score_vals:
        for htf in htf_vals:
            for hold in hold_vals:
                for wt_exit in wt_exit_vals:
                    for pt in pt_vals:
                        for sl in sl_vals:
                            for blocks in block_presets:
                                cfg = {
                                    "STRENGTH_MIN_SCORE": score,
                                    "HTF_MIN_ALIGNED": htf,
                                    "D_TREND_REQUIRED": True,
                                    "MIN_HOLD_BARS": hold,
                                    "WT_EXIT_MIN_TFS": wt_exit,
                                    **blocks,
                                }
                                if sl is not None:
                                    cfg["STOP_LOSS_ENABLED"] = True
                                    cfg["STOP_LOSS_PCT"] = sl
                                combos.append(cfg)
    return combos


TOP_SYMBOL_SETS_CRYPTO = {
    "TOP3": ["LINKUSDT","ETHUSDT","DOTUSDT"],
    "TOP4": ["LINKUSDT","ETHUSDT","DOTUSDT","BTCUSDT"],
    "TOP5": ["LINKUSDT","ETHUSDT","DOTUSDT","BTCUSDT","UNIUSDT"],
    "TOP5_v2": ["ETHUSDT","DOTUSDT","BTCUSDT","UNIUSDT","ADAUSDT"],  # no LINK, Phase 1 winner
    "TOP6": ["LINKUSDT","ETHUSDT","DOTUSDT","BTCUSDT","UNIUSDT","ADAUSDT"],
    "TOP7": ["LINKUSDT","ETHUSDT","DOTUSDT","BTCUSDT","UNIUSDT","ADAUSDT","BNBUSDT"],
    "ALL11": FAST_SYMBOLS_CRYPTO.split(","),
}

TOP_SYMBOL_SETS_TRADIER = {
    "ALL12": FAST_SYMBOLS_TRADIER.split(","),
    # TODO: populate when we have per-symbol tradier baseline data
}


def cfg_hash(cfg):
    return hashlib.md5(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:10]


def run_one(args):
    mode, symbols, cfg_dict, npz_dir, start = args
    stores = load_npz(mode, symbols, start, npz_dir)
    if not stores: return {"sharpe": 0, "trades": 0, "cfg": cfg_dict, "symbols": len(symbols) if symbols else 0, "status": "no_data"}
    cfg = QuickConfig()
    if mode == "tradier":
        cfg.apply_tradier_defaults()
    # Always-locked per memory
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
    r["symbols"] = len(symbols) if symbols else 0
    r["elapsed"] = round(time.time() - t0, 1)
    r["status"] = "ok" if r["trades"] > 0 else "no_trades"
    return r


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["crypto", "tradier"], default="crypto")
    p.add_argument("--symbol-set", default="TOP3", help="Symbol set name or 'all-subsets'")
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--start", default="2022-01-01")
    p.add_argument("--npz-dir", default="")
    p.add_argument("--limit", type=int, default=0, help="Limit configs (0=all)")
    p.add_argument("--baseline-floor", type=float, default=SHARPE_BASELINE)
    args = p.parse_args()

    sym_sets = TOP_SYMBOL_SETS_TRADIER if args.mode == "tradier" else TOP_SYMBOL_SETS_CRYPTO
    if args.symbol_set == "all-subsets":
        symbol_groups = list(sym_sets.items())
    else:
        if args.symbol_set not in sym_sets:
            print(f"Unknown symbol set: {args.symbol_set}. Available: {list(sym_sets.keys())}")
            return
        symbol_groups = [(args.symbol_set, sym_sets[args.symbol_set])]

    grid = build_grid()
    if args.start != "2022-01-01":  # tradier uses 2024-01-01
        pass
    # Expand grid × symbol groups
    all_configs = []
    for sym_name, syms in symbol_groups:
        for cfg_dict in grid:
            all_configs.append((sym_name, syms, dict(cfg_dict)))
    if args.limit > 0:
        all_configs = all_configs[:args.limit]

    total = len(all_configs)
    print(f"MILLION SWEEP: mode={args.mode} | {len(symbol_groups)} symbol sets × {len(grid)} configs = {total} total")
    print(f"Baseline floor: Sharpe >= {args.baseline_floor}")
    print(f"Workers: {args.workers}")

    csv_path = BASE / "data" / "sweep_results" / f"v8_million_{args.mode}_{args.symbol_set}_{int(time.time())}.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    top_k = 100  # keep top-100 by Sharpe in memory
    heap = []  # (sharpe, cfg_dict, result)

    todo = [(args.mode, syms, cfg, args.npz_dir, args.start) for (name, syms, cfg) in all_configs]

    t0 = time.time()
    completed = 0
    above_floor = 0
    best_sharpe = 0
    last_print = 0

    # CSV header dynamically from first successful result
    csv_file = open(csv_path, "w", newline="")
    writer = None

    def write_row(r):
        nonlocal writer
        cfg = r.get("cfg", {})
        row = {
            "sharpe": r.get("sharpe", 0),
            "pnl": r.get("pnl", 0),
            "trades": r.get("trades", 0),
            "wins": r.get("wins", 0),
            "losses": r.get("losses", 0),
            "wr": r.get("wr", 0),
            "avg_pnl_pct": r.get("avg_pnl_pct", 0),
            "elapsed": r.get("elapsed", 0),
            "symbols": r.get("symbols", 0),
        }
        for k, v in cfg.items():
            row[f"cfg_{k}"] = v
        if writer is None:
            writer = csv.DictWriter(csv_file, fieldnames=list(row.keys()))
            writer.writeheader()
        writer.writerow(row)
        csv_file.flush()

    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(run_one, t): t for t in todo}
        for fut in as_completed(futs):
            try:
                r = fut.result()
            except Exception as e:
                print(f"  ERROR: {e}")
                continue
            completed += 1
            s = r.get("sharpe", 0)
            trades = r.get("trades", 0)
            # Only write rows above baseline floor (reduces CSV clutter from memory rule)
            if s >= args.baseline_floor or trades == 0:
                write_row(r)
            if s >= args.baseline_floor and trades >= 20:
                above_floor += 1
                heap.append((s, r.get("cfg", {}), r))
                heap.sort(reverse=True)
                if len(heap) > top_k: heap = heap[:top_k]
                if s > best_sharpe:
                    best_sharpe = s
                    print(f"  🎯 [{completed}/{total}] NEW BEST Sharpe={s:.4f} trades={trades} wr={r.get('wr',0):.1f}% cfg={r.get('cfg',{})}")
            now = time.time()
            if now - last_print > 60 or completed == total:
                last_print = now
                rate = completed / (now - t0) if now > t0 else 0
                eta = (total - completed) / rate / 60 if rate > 0 else 0
                print(f"  [{completed}/{total}] elapsed={now-t0:.0f}s rate={rate:.1f}/s ETA={eta:.1f}min above_floor={above_floor} best={best_sharpe:.4f}")

    csv_file.close()
    print(f"\n{'='*80}")
    print(f"SWEEP COMPLETE — {completed} configs in {time.time()-t0:.0f}s")
    print(f"Above Sharpe {args.baseline_floor}: {above_floor}")
    print(f"Best Sharpe: {best_sharpe:.4f}")
    print(f"CSV: {csv_path}")
    print(f"\nTOP 20 configs (Sharpe >= {args.baseline_floor}):")
    for s, cfg, r in heap[:20]:
        print(f"  Sharpe={s:.4f} trades={r.get('trades',0):>4} wr={r.get('wr',0):.1f}% avg={r.get('avg_pnl_pct',0):.3f}%  cfg={cfg}")


if __name__ == "__main__":
    main()
