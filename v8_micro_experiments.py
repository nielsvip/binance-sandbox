#!/usr/bin/env python3
"""
v8_micro_experiments.py — Run 6-10 small targeted experiments in parallel.

Each experiment probes ONE dimension from the V8Q winning config (Sharpe 0.94).
Runs 12-symbol 4yr crypto each, ~10s per config, parallelized.

Hypotheses:
  H1: Higher STRENGTH_MIN_SCORE (6-8) trades off volume for per-symbol consistency
  H2: Profit-target at 1.0% captures consistent wins (vs technical-only exit)
  H3: Stop-loss at 0.5% caps variance
  H4: Longer MIN_HOLD (20-40 bars) lets winners run to their structural exit
  H5: Filtering to top-5 symbols raises aggregate Sharpe by cutting weak drag
  H6: Weighted block bias — emphasize high-quality blocks (B15 weight=5 vs =4)
  H7: Per-symbol min-trades gate (skip symbols with <20 entries)
"""
import sys, time, json
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
sys.path.insert(0, str(Path(__file__).resolve().parent))
from v8_quick_engine import QuickConfig, load_npz, simulate, FAST_SYMBOLS_CRYPTO

BASE = Path(__file__).resolve().parent
FAST_CRYPTO_ALL = FAST_SYMBOLS_CRYPTO.split(",")
TOP5_CRYPTO = ["BTCUSDT","ETHUSDT","DOTUSDT","LINKUSDT","UNIUSDT"]  # Sharpe > 1.0 per-symbol
MID5_CRYPTO = ["ADAUSDT","BNBUSDT","AVAXUSDT","LTCUSDT","XRPUSDT"]
BOTTOM3_CRYPTO = ["SOLUSDT","XRPUSDT","AVAXUSDT"]


def winner_cfg():
    c = QuickConfig()
    c.STRENGTH_FILTER_ENABLED = True
    c.STRENGTH_MIN_SCORE = 5.0
    c.HTF_MIN_ALIGNED = 1
    c.D_TREND_REQUIRED = True
    c.MIN_HOLD_BARS = 10
    c.WT_EXIT_MIN_TFS = 2
    c.COOLDOWN_BARS = 3
    return c


def run_exp(args):
    """One experiment run — returns dict with label + result."""
    label, cfg_overrides, symbols = args
    stores = load_npz('crypto', symbols, '2022-01-01')
    if not stores:
        return {"label": label, "sharpe": 0, "trades": 0, "error": "no_data"}
    cfg = winner_cfg()
    for k, v in cfg_overrides.items():
        if hasattr(cfg, k):
            cur = getattr(cfg, k)
            if isinstance(cur, bool): setattr(cfg, k, bool(v))
            elif isinstance(cur, int): setattr(cfg, k, int(v))
            elif isinstance(cur, float): setattr(cfg, k, float(v))
            else: setattr(cfg, k, v)
    t0 = time.time()
    r = simulate(stores, cfg, 10000.0)
    r["label"] = label
    r["cfg"] = cfg_overrides
    r["symbols"] = len(symbols)
    r["elapsed"] = round(time.time() - t0, 1)
    return r


def main():
    # 28 micro-experiments across 7 hypotheses
    experiments = []

    # H1: Score variation
    for score in [3, 4, 5, 6, 7, 8]:
        experiments.append((f"H1_SCORE_{score}", {"STRENGTH_MIN_SCORE": float(score)}, FAST_CRYPTO_ALL))

    # H2: Profit target variation
    for pt in [0.5, 1.0, 1.5, 2.0, 3.0]:
        experiments.append((f"H2_PT_{pt}", {"PROFIT_TARGET_ENABLED": True, "PROFIT_TARGET_PCT": float(pt)}, FAST_CRYPTO_ALL))

    # H3: Stop loss variation (with PT=2.0)
    for sl in [0.3, 0.5, 0.8, 1.2]:
        experiments.append((f"H3_SL_{sl}", {"PROFIT_TARGET_ENABLED": True, "PROFIT_TARGET_PCT": 2.0, "STOP_LOSS_ENABLED": True, "STOP_LOSS_PCT": float(sl)}, FAST_CRYPTO_ALL))

    # H4: Min hold variation
    for hold in [3, 10, 20, 40, 80]:
        experiments.append((f"H4_HOLD_{hold}", {"MIN_HOLD_BARS": hold}, FAST_CRYPTO_ALL))

    # H5: Symbol subset filtering
    experiments.append(("H5_TOP5", {}, TOP5_CRYPTO))
    experiments.append(("H5_MID5", {}, MID5_CRYPTO))
    experiments.append(("H5_BOTTOM3", {}, BOTTOM3_CRYPTO))
    experiments.append(("H5_ALL11", {}, FAST_CRYPTO_ALL))

    # H6: HTF strictness — if top5 wins are weak, relax; if strong, tighten
    experiments.append(("H6_HTF_STRICT", {"HTF_MIN_ALIGNED": 3, "D_TREND_REQUIRED": True}, TOP5_CRYPTO))
    experiments.append(("H6_HTF_LOOSE", {"HTF_MIN_ALIGNED": 0, "D_TREND_REQUIRED": False}, TOP5_CRYPTO))

    # H7: Confluence mode (agree N blocks)
    for n in [2, 3, 4]:
        experiments.append((f"H7_CONFLUENCE_{n}", {"CONFLUENCE_MODE_ENABLED": True, "CONFLUENCE_MIN_BLOCKS": n, "STRENGTH_FILTER_ENABLED": False}, FAST_CRYPTO_ALL))

    # Baseline (winner config as-is)
    experiments.append(("BASELINE_WINNER", {}, FAST_CRYPTO_ALL))

    print(f"Running {len(experiments)} micro-experiments in parallel (4 workers)...")
    results = []
    with ProcessPoolExecutor(max_workers=4) as ex:
        futures = {ex.submit(run_exp, e): e for e in experiments}
        for fut in as_completed(futures):
            try:
                r = fut.result()
                results.append(r)
                print(f"  [{len(results):2d}/{len(experiments)}] {r['label']:<20} Sharpe={r['sharpe']:.3f} trades={r['trades']} wr={r.get('wr', 0):.1f}% avg={r.get('avg_pnl_pct', 0):.3f}% syms={r['symbols']} ({r['elapsed']}s)")
            except Exception as e:
                print(f"  ERROR: {e}")

    # Sort by Sharpe (trades>=30)
    valid = [r for r in results if r.get('trades', 0) >= 30]
    valid.sort(key=lambda r: r['sharpe'], reverse=True)
    print(f"\n{'='*100}")
    print(f"  TOP 10 by Sharpe (trades>=30):")
    print(f"{'='*100}")
    for r in valid[:10]:
        print(f"  {r['label']:<20} Sharpe={r['sharpe']:.4f} trades={r['trades']:>5} wr={r.get('wr', 0):.1f}% avg={r.get('avg_pnl_pct', 0):.3f}%  cfg={r['cfg']}")

    out_path = BASE / "data" / "sweep_results" / f"v8_micro_experiments_{int(time.time())}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nSaved: {out_path}")


if __name__ == "__main__":
    main()
