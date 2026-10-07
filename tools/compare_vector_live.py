#!/usr/bin/env python3
"""Compare vector (v12_quick) vs live (backtest_v12_engine) for entire book baseline.
Second run is live backtest via real scripts (ez_manage.process_position etc).
Makes table of results and differences.
"""
from pathlib import Path
import sys, csv, json
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.opt.evaluate_v12 import evaluate as vec_eval
from backtest_v12_engine import run_one as live_run

SYMSIDES = ["SNDK_LONG","ZECUSDC_LONG","BTCUSDC_LONG","NVDA_LONG","MU_LONG","GOOGL_LONG","AGCO_LONG","GLD_LONG"]
WINDOW = 30

def run_both(symside, window=30):
    vec = vec_eval(symside, {}, window_days=window)
    # live: run_one expects overrides dict, window_days
    try:
        live = live_run(symside, overrides={}, window_days=window)
        # live_run returns dict with gain_pct etc or maybe different keys
        # Normalize
        if isinstance(live, dict):
            lg = float(live.get("gain_pct") or live.get("gain_pct_2000norm") or 0.0)
            lt = int(live.get("trades") or live.get("n_trades") or 0)
            lvalid = live.get("valid", True)
            lbh = live.get("bh_pct")
        else:
            lg, lt, lvalid, lbh = 0.0, 0, False, None
    except Exception as e:
        lg, lt, lvalid, lbh = 0.0, 0, False, str(e)
        live = {"error": str(e)}
    vg = float(vec.get("gain_pct") or 0.0)
    vt = int(vec.get("trades") or 0)
    vvalid = vec.get("valid")
    vbh = vec.get("bh_pct")
    return {
        "symside": symside,
        "window": window,
        "vec_gain": vg,
        "vec_trades": vt,
        "vec_valid": vvalid,
        "vec_bh": vbh,
        "vec_sharpe": vec.get("pool_sharpe"),
        "vec_dd": vec.get("max_dd_pct"),
        "vec_tim": vec.get("tim_pct"),
        "live_gain": lg,
        "live_trades": lt,
        "live_valid": lvalid,
        "live_bh": lbh,
        "live_sharpe": live.get("pool_sharpe") if isinstance(live, dict) else None,
        "live_dd": live.get("max_dd_pct") if isinstance(live, dict) else None,
        "live_tim": live.get("tim_pct") if isinstance(live, dict) else None,
        "gain_diff": vg - lg,
        "trades_diff": vt - lt,
        "bh_diff": (vbh - lbh) if (vbh is not None and lbh is not None) else None,
    }

def main():
    out = ROOT / "data/reports/vector_vs_live_30d.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    print(f"Running vector vs live for {len(SYMSIDES)} symsides window {WINDOW}d via real scripts")
    for sym in SYMSIDES:
        print(f"  {sym} ...")
        r = run_both(sym, WINDOW)
        rows.append(r)
        print(f"    vec {r['vec_gain']:.4f} trades {r['vec_trades']} valid {r['vec_valid']} | live {r['live_gain']:.4f} trades {r['live_trades']} valid {r['live_valid']} | diff gain {r['gain_diff']:.4f} trades {r['trades_diff']}")
    # Write CSV
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"Wrote {out}")
    # Print markdown table
    print("\n| symside | vec_gain | live_gain | gain_diff | vec_trades | live_trades | trades_diff | vec_sharpe | live_sharpe | vec_dd | live_dd | vec_tim | live_tim |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        print(f"| {r['symside']} | {r['vec_gain']:.2f} | {r['live_gain']:.2f} | {r['gain_diff']:.2f} | {r['vec_trades']} | {r['live_trades']} | {r['trades_diff']} | {r['vec_sharpe']:.3f} | {str(r['live_sharpe'])[:5]} | {r['vec_dd']:.1f} | {str(r['live_dd'])[:5]} | {r['vec_tim']:.1f} | {str(r['live_tim'])[:5]} |")

if __name__ == "__main__":
    main()
