#!/usr/bin/env python3
"""vec_vs_real_compare.py — compare vec_engine_v1 output against real-engine CSV
post-hoc. Loads the real-engine sweep CSV, runs vec_engine on the same configs,
and reports per-config delta on pool_sharpe / trades / max_dd / years.

Usage:
    vec_vs_real_compare.py <real_csv_path> --mode crypto --symbols BTCUSDC,ETHUSDC,SOLUSDC --start 2026-04-01

The vec_engine_v1.simulate() must be importable from this script's directory.
"""
import argparse
import csv
import json
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import vec_engine_v1


def load_real_rows(csv_path):
    rows = []
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        for r in reader:
            if r.get("status") != "ok":
                continue
            rows.append({
                "label": r["label"],
                "overrides": json.loads(r["overrides_json"]) if r.get("overrides_json") else {},
                "pool_sharpe": float(r["pool_sharpe"]),
                "sym_sharpe": float(r["sym_sharpe"]),
                "trades": int(r["trades"]),
                "max_dd_pct": float(r["max_dd_pct"]),
                "gain_pct": float(r["gain_pct"]),
                "years": float(r["years"]),
                "win_rate": float(r["win_rate"]) if r["win_rate"] else 0.0,
            })
    return rows


def run_vec(overrides, mode, symbols, start_date):
    t0 = time.time()
    cfg = vec_engine_v1.VecConfig.from_overrides(overrides, mode=mode)
    result = vec_engine_v1.simulate(
        mode=mode,
        symbols=symbols,
        start_date=start_date,
        cfg=cfg,
    )
    result["elapsed_s"] = time.time() - t0
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("real_csv", help="Path to real-engine sweep CSV")
    ap.add_argument("--mode", choices=["crypto", "tradier"], required=True)
    ap.add_argument("--symbols", required=True, help="Comma-separated symbol list")
    ap.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    ap.add_argument("--sharpe-tol", type=float, default=0.10)
    ap.add_argument("--trades-tol", type=float, default=0.30)
    ap.add_argument("--dd-tol", type=float, default=5.0)
    args = ap.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",")]
    rows = load_real_rows(args.real_csv)
    print(f"loaded {len(rows)} real-engine rows from {args.real_csv}")

    print(f"\n{'config':<25} {'real_ps':>8} {'vec_ps':>8} {'dps':>7}  {'real_tr':>7} {'vec_tr':>6} {'dtr%':>6}  {'real_dd':>7} {'vec_dd':>7} {'pass':>5}")
    pass_n = 0
    fail_n = 0
    results = []
    for r in rows:
        try:
            v = run_vec(r["overrides"], args.mode, symbols, args.start)
        except Exception as e:
            print(f"{r['label']:<25} VEC_FAILED: {e}")
            fail_n += 1
            continue
        d_ps = abs(v["pool_sharpe"] - r["pool_sharpe"])
        d_tr = abs(v["trades"] - r["trades"]) / max(r["trades"], 1)
        d_dd = abs(v["max_dd_pct"] - r["max_dd_pct"])
        ok = (d_ps <= args.sharpe_tol and d_tr <= args.trades_tol and d_dd <= args.dd_tol)
        if ok:
            pass_n += 1
        else:
            fail_n += 1
        print(f"{r['label']:<25} {r['pool_sharpe']:>8.4f} {v['pool_sharpe']:>8.4f} {d_ps:>7.4f}  {r['trades']:>7} {v['trades']:>6} {d_tr*100:>5.1f}%  {r['max_dd_pct']:>7.2f} {v['max_dd_pct']:>7.2f} {'PASS' if ok else 'FAIL':>5}")
        results.append({"label": r["label"], "real": r, "vec": v, "delta_ps": d_ps, "delta_tr_pct": d_tr, "delta_dd": d_dd, "pass": ok})

    print(f"\nSUMMARY: {pass_n} PASS / {fail_n} FAIL out of {len(rows)} configs")
    out = BASE / "data" / "vec_validator" / f"compare_{int(time.time())}.json"
    out.parent.mkdir(exist_ok=True, parents=True)
    with open(out, "w") as f:
        json.dump({"args": vars(args), "results": results, "summary": {"pass": pass_n, "fail": fail_n}}, f, indent=2, default=str)
    print(f"results -> {out}")
    sys.exit(0 if fail_n == 0 else 1)


if __name__ == "__main__":
    main()
