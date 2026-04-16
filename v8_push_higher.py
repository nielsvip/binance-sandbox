#!/usr/bin/env python3
"""
v8_push_higher.py — Fine-grain sweep around Sharpe 1.39/1.90 winner to find absolute peak.

Probes:
  1. PT precision: 1.2/1.3/1.4/1.5/1.6/1.7/1.8/2.0
  2. SL precision: 0.3/0.5/0.7/0.9/1.2/1.5
  3. Hold precision: 5/8/10/12/15/20
  4. Score precision: 4/4.5/5/5.5/6/7
  5. Per-symbol subset: all combos of TOP7 (BTC/ETH/DOT/LINK/UNI/ADA/BNB)
  6. Multi-overlay: PT × SL × HTF
"""
import sys, time, itertools, json
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
sys.path.insert(0, str(Path(__file__).resolve().parent))
from v8_quick_engine import QuickConfig, load_npz, simulate, FAST_SYMBOLS_CRYPTO

BASE = Path(__file__).resolve().parent

TOP3 = ["LINKUSDT","ETHUSDT","DOTUSDT"]
TOP5 = ["LINKUSDT","ETHUSDT","DOTUSDT","BTCUSDT","UNIUSDT"]
TOP7 = ["LINKUSDT","ETHUSDT","DOTUSDT","BTCUSDT","UNIUSDT","ADAUSDT","BNBUSDT"]
ALL11 = FAST_SYMBOLS_CRYPTO.split(",")


def winner_cfg():
    c = QuickConfig()
    c.STRENGTH_FILTER_ENABLED = True
    c.STRENGTH_MIN_SCORE = 5.0
    c.HTF_MIN_ALIGNED = 1
    c.D_TREND_REQUIRED = True
    c.MIN_HOLD_BARS = 10
    c.WT_EXIT_MIN_TFS = 2
    c.COOLDOWN_BARS = 3
    c.PROFIT_TARGET_ENABLED = True
    c.PROFIT_TARGET_PCT = 1.5
    return c


def run_exp(args):
    label, cfg_overrides, symbols = args
    stores = load_npz('crypto', symbols, '2022-01-01')
    if not stores: return {"label": label, "sharpe": 0, "trades": 0, "error": "no_data"}
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
    r["label"] = label; r["cfg"] = cfg_overrides; r["symbols"] = len(symbols)
    r["elapsed"] = round(time.time() - t0, 1)
    return r


def main():
    experiments = []

    # 1. PT precision on both ALL11 and TOP5
    for pt in [1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 2.0]:
        experiments.append((f"PT_{pt}_ALL11", {"PROFIT_TARGET_PCT": float(pt)}, ALL11))
        experiments.append((f"PT_{pt}_TOP5", {"PROFIT_TARGET_PCT": float(pt)}, TOP5))
        experiments.append((f"PT_{pt}_TOP3", {"PROFIT_TARGET_PCT": float(pt)}, TOP3))

    # 2. SL overlay on winner (PT=1.5)
    for sl in [0.3, 0.5, 0.7, 0.9, 1.2, 1.5]:
        experiments.append((f"PT1.5_SL{sl}_ALL11", {"STOP_LOSS_ENABLED": True, "STOP_LOSS_PCT": float(sl)}, ALL11))
        experiments.append((f"PT1.5_SL{sl}_TOP5", {"STOP_LOSS_ENABLED": True, "STOP_LOSS_PCT": float(sl)}, TOP5))

    # 3. Hold precision on TOP5 + PT=1.5
    for hold in [5, 8, 10, 12, 15, 20, 30]:
        experiments.append((f"HOLD_{hold}_TOP5", {"MIN_HOLD_BARS": hold}, TOP5))

    # 4. Score precision on TOP5 + PT=1.5
    for score in [3.0, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0, 7.0, 8.0]:
        experiments.append((f"SCORE_{score}_TOP5", {"STRENGTH_MIN_SCORE": float(score)}, TOP5))

    # 5. TOP-N symbol subsets
    experiments.append(("TOP7", {}, TOP7))
    for extra in ["ADAUSDT", "BNBUSDT", "AVAXUSDT", "SOLUSDT"]:
        experiments.append((f"TOP5+{extra}", {}, TOP5 + [extra]))
    for removed in ["LINKUSDT", "ETHUSDT", "DOTUSDT", "BTCUSDT", "UNIUSDT"]:
        subset = [s for s in TOP5 if s != removed]
        experiments.append((f"TOP5-{removed}", {}, subset))

    # 6. Multi-overlay: PT + SL combo on TOP5
    for pt in [1.3, 1.5, 1.7]:
        for sl in [0.5, 0.8, 1.2]:
            experiments.append((f"PT{pt}_SL{sl}_TOP5", {"PROFIT_TARGET_PCT": float(pt), "STOP_LOSS_ENABLED": True, "STOP_LOSS_PCT": float(sl)}, TOP5))

    # 7. WT exit strictness
    for wt_tfs in [2, 3, 4]:  # MEMORY RULE: 5-of-5 banned (Sharpe 0.001). 1 too loose.
        experiments.append((f"WT_EXIT_{wt_tfs}_TOP5", {"WT_EXIT_MIN_TFS": wt_tfs}, TOP5))

    # 8. Trailing stop (exit at partial profit decay)
    for pt in [0.8, 1.0, 1.2]:
        for sl in [0.4, 0.6]:
            experiments.append((f"TIGHT_PT{pt}_SL{sl}_TOP5", {"PROFIT_TARGET_PCT": float(pt), "STOP_LOSS_ENABLED": True, "STOP_LOSS_PCT": float(sl)}, TOP5))

    print(f"Running {len(experiments)} precision experiments in parallel (6 workers)...")
    results = []
    with ProcessPoolExecutor(max_workers=6) as ex:
        futures = {ex.submit(run_exp, e): e for e in experiments}
        for fut in as_completed(futures):
            try:
                r = fut.result()
                results.append(r)
                if len(results) % 10 == 0 or r.get('sharpe', 0) > 1.5:
                    print(f"  [{len(results):3d}/{len(experiments)}] {r['label']:<28} Sharpe={r['sharpe']:.4f} trades={r['trades']:>4} wr={r.get('wr',0):.1f}% avg={r.get('avg_pnl_pct',0):.3f}%")
            except Exception as e:
                print(f"  ERROR: {e}")

    # Top 20 valid
    valid = [r for r in results if r.get('trades', 0) >= 20]
    valid.sort(key=lambda r: r['sharpe'], reverse=True)
    print(f"\n{'='*110}")
    print(f"  TOP 20 by Sharpe (trades>=20):")
    print(f"{'='*110}")
    for r in valid[:20]:
        print(f"  {r['label']:<28} Sharpe={r['sharpe']:.4f} trades={r['trades']:>4} wr={r.get('wr',0):.1f}% avg={r.get('avg_pnl_pct',0):.3f}% pnl=${r.get('pnl',0):.0f}")

    out_path = BASE / "data" / "sweep_results" / f"v8_push_higher_{int(time.time())}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nSaved: {out_path}")


if __name__ == "__main__":
    main()
