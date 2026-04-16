#!/usr/bin/env python3
"""Per-symbol config optimizer — sweep quality_sniper configs per symbol.

For each top symbol, runs all sniper configs individually. The "average" sweep
finds one config that works OK for all symbols. This finds each symbol's OPTIMAL
config, which should beat the averaged ceiling.

Output: best-per-symbol config + its Sharpe.
"""
import csv
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
SWEEP_DIR = BASE / "data" / "sweep_results"
from sweep_quality_sniper import build_configs as sniper_configs


def run_cfg(sym, cfg, mode, start, py_bin, engine_path):
    label = cfg.pop("_label", "?")
    override = SWEEP_DIR / f"override_psc_{sym}_{label[:30]}.json".replace("/", "_")
    with open(override, "w") as f:
        json.dump(cfg, f)
    env = os.environ.copy()
    env["V8_OVERRIDE_FILE"] = str(override)
    env["V8_SWEEP_MODE"] = "1"
    cmd = [py_bin, str(engine_path), "--mode", mode, "--symbols", sym, "--start", start]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300, env=env, cwd=str(BASE))
        for line in reversed(proc.stdout.splitlines()):
            if "V8_QUICK_RESULT:" in line or "V8_RESULT:" in line:
                toks = {}
                body = line.split(":", 1)[1]
                for t in body.split():
                    if "=" in t:
                        k, v = t.split("=", 1)
                        toks[k] = v.rstrip("%").rstrip("s")
                override.unlink(missing_ok=True)
                return {
                    "symbol": sym, "label": label,
                    "sharpe": float(toks.get("sharpe", 0) or 0),
                    "trades": int(toks.get("trades", 0) or 0),
                    "wr": float(toks.get("wr", 0) or 0),
                    **cfg
                }
    except Exception:
        pass
    override.unlink(missing_ok=True)
    return {"symbol": sym, "label": label, "sharpe": 0, "trades": 0}


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["crypto", "tradier"], default="tradier")
    ap.add_argument("--symbols", required=True, help="Comma-separated symbol list")
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--workers", type=int, default=3)
    args = ap.parse_args()

    if args.mode == "crypto" and args.start == "2024-01-01":
        args.start = "2022-01-01"

    py_bin = sys.executable
    engine = BASE / "v8_quick_engine.py"
    syms = args.symbols.split(",")
    configs = sniper_configs()
    print(f"PER-SYMBOL-CONFIG: {len(syms)} symbols × {len(configs)} configs = {len(syms)*len(configs)} runs | workers={args.workers}")

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    csv_path = SWEEP_DIR / f"per_symbol_config_{args.mode}_{ts}.csv"
    all_results = []
    best_per_sym = {}

    from concurrent.futures import ProcessPoolExecutor, as_completed
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {}
        for sym in syms:
            for cfg in configs:
                f = pool.submit(run_cfg, sym, dict(cfg), args.mode, args.start, py_bin, str(engine))
                futures[f] = (sym, cfg.get("_label"))

        n_done = 0
        for f in as_completed(futures):
            r = f.result()
            all_results.append(r)
            n_done += 1
            sym = r["symbol"]
            s = r.get("sharpe", 0)
            if sym not in best_per_sym or s > best_per_sym[sym]["sharpe"]:
                best_per_sym[sym] = r
                print(f"  [{n_done}/{len(futures)}] {sym:10s} NEW BEST sh={s:.3f} tr={r.get('trades',0)} WR={r.get('wr',0):.1f}% label={r.get('label','?')[:40]}")

    all_results.sort(key=lambda r: (r["symbol"], -r.get("sharpe", 0)))
    if all_results:
        with open(csv_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(all_results[0].keys()), extrasaction="ignore")
            w.writeheader()
            w.writerows(all_results)

    print(f"\n{'='*80}\nBEST CONFIG PER SYMBOL:")
    for sym, r in sorted(best_per_sym.items(), key=lambda kv: -kv[1]["sharpe"]):
        print(f"  {sym:10s} sharpe={r['sharpe']:+.3f} trades={r['trades']} WR={r.get('wr',0):.1f}% label={r.get('label','?')}")
    print(f"\nSaved {len(all_results)} rows to {csv_path}")
    best_json = SWEEP_DIR / f"per_symbol_best_{args.mode}_{ts}.json"
    with open(best_json, "w") as f:
        json.dump(best_per_sym, f, indent=2, default=str)
    print(f"Best-per-symbol saved to {best_json}")


if __name__ == "__main__":
    main()
