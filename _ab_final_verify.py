#!/usr/bin/env python3
"""Final verification: extend VEL ceiling + combined-winner test.
1. Combined winner base (VEL=3.5, RALLY=40, RANK=3, WP=1.0, others at best)
2. Extend VEL axis to find ceiling (4.0, 4.5, 5.0)
3. RALLY tradeoff in combined context (30, 50)
"""
import json
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
from v8_quick_engine import QuickConfig, load_npz, simulate

SYMS_48 = json.load(open(BASE / "backtest_48_symbols.json"))
START = "2022-01-01"
NPZ_DIR = str(BASE / "backtest_v8" / "indicators")

# Combined-winner base derived from coord descent
COMBINED_BASE = {
    "ENTRY_SCORE_THRESHOLD":    0.0,
    "HTF_MIN_ALIGNED":          1,
    "CT_WT_VELOCITY_1H_MIN":    3.5,
    "REENTRY_RALLY_K15M_MAX":   40.0,
    "RANK_CONVICTION_MIN":      3,
    "WINNER_PROTECT_GAIN_PCT":  1.0,
}


def run(overrides, stores):
    cfg = QuickConfig()
    cfg.MODE = "crypto"
    for k, v in overrides.items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)
    return simulate(stores, cfg, 10000.0)


def main():
    print(f"Loading 48 NPZ …")
    t0 = time.time()
    stores = load_npz("crypto", SYMS_48, START, NPZ_DIR)
    print(f"  {len(stores)} symbols in {time.time()-t0:.1f}s\n")

    tests = [
        ("combined_base",          dict(COMBINED_BASE)),
        # VEL ceiling search
        ("vel=4.0",                {**COMBINED_BASE, "CT_WT_VELOCITY_1H_MIN": 4.0}),
        ("vel=4.5",                {**COMBINED_BASE, "CT_WT_VELOCITY_1H_MIN": 4.5}),
        ("vel=5.0",                {**COMBINED_BASE, "CT_WT_VELOCITY_1H_MIN": 5.0}),
        ("vel=6.0",                {**COMBINED_BASE, "CT_WT_VELOCITY_1H_MIN": 6.0}),
        # RALLY tradeoff
        ("rally=30_vol-quality",   {**COMBINED_BASE, "REENTRY_RALLY_K15M_MAX": 30.0}),
        ("rally=50_vol+",          {**COMBINED_BASE, "REENTRY_RALLY_K15M_MAX": 50.0}),
        # WP ceiling (WP=0 = always protect winners below +1%)
        ("wp=0.5",                 {**COMBINED_BASE, "WINNER_PROTECT_GAIN_PCT": 0.5}),
        # Best-of-best: whatever VEL ceiling ends up, combined with RALLY=30
        ("rally=30 vel=4.5",       {**COMBINED_BASE, "CT_WT_VELOCITY_1H_MIN": 4.5, "REENTRY_RALLY_K15M_MAX": 30.0}),
    ]

    print(f"{'label':26s}  {'sharpe':>8s}  {'trades':>6s}  {'WR':>5s}  {'avg%':>7s}")
    print("-" * 70)
    results = []
    best = {"sharpe": -999, "label": "?", "cfg": None}
    for label, cfg in tests:
        r = run(cfg, stores)
        r["label"] = label
        r["cfg"] = cfg
        results.append(r)
        flag = ""
        if r["sharpe"] > best["sharpe"] and r["trades"] >= 10:
            best = {"sharpe": r["sharpe"], "label": label, "cfg": cfg,
                    "trades": r["trades"], "wr": r["wr"], "avg": r["avg_pnl_pct"]}
            flag = " ★"
        print(f"{label:26s}  {r['sharpe']:+.4f}  {r['trades']:>5d}  {r['wr']:4.1f}%  {r['avg_pnl_pct']:+.3f}%{flag}")

    print("\n" + "=" * 70)
    print(f"WINNER: {best['label']}")
    print(f"  sharpe={best['sharpe']:+.4f}  trades={best['trades']}  WR={best['wr']:.1f}%  avg={best['avg']:+.3f}%")
    print(f"  cfg: {best['cfg']}")

    out = BASE / "data" / "sweep_results" / f"final_verify_{int(time.time())}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"tests": results, "best": best, "combined_base": COMBINED_BASE}, indent=2, default=str))
    print(f"\nSaved: {out}")


if __name__ == "__main__":
    main()
