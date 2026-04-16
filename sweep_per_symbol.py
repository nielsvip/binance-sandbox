#!/usr/bin/env python3
"""Per-symbol Sharpe analysis — find if individual symbols have much higher Sharpe.

Takes the best config from quality_sniper and runs it PER SYMBOL to expose
high-Sharpe individual symbols that get averaged down in fast-symbol sweeps.

If some symbols hit Sharpe 2.0+, we can build symbol-specific configs.
"""
import csv
import json
import os
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
SWEEP_DIR = BASE / "data" / "sweep_results"
NPZ_DIR = BASE / "backtest_v8" / "indicators"


def best_config_from_sniper(mode: str) -> dict:
    files = sorted(SWEEP_DIR.glob(f"quality_sniper_{mode}_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return {}
    rows = list(csv.DictReader(open(files[0])))
    rows.sort(key=lambda r: -float(r.get("sharpe", 0) or 0))
    best = rows[0]
    cfg = {}
    for k, v in best.items():
        if k.startswith("REENTRY_B") or k.startswith("CT_") or k.startswith("DELTA_") or k in (
            "ENTRY_SCORE_THRESHOLD", "K3M_FLOOR", "SATOSHIT_ENABLED", "RZ_EXIT_ENABLED",
            "STRUCTURAL_RANGE_SHIFT_EXIT"
        ):
            if v in ("True", "true"): cfg[k] = True
            elif v in ("False", "false"): cfg[k] = False
            else:
                try: cfg[k] = float(v)
                except: cfg[k] = v
    return cfg


def all_symbols(mode: str) -> list:
    syms = []
    for p in sorted(NPZ_DIR.glob("*.npz")):
        s = p.stem
        is_crypto = "USDT" in s or "USDC" in s
        if mode == "crypto" and is_crypto:
            syms.append(s)
        elif mode == "tradier" and not is_crypto:
            syms.append(s)
    return syms


def run_symbol(sym, cfg, mode, start, py_bin, engine_path):
    override = SWEEP_DIR / f"override_sym_{sym}.json"
    with open(override, "w") as f:
        json.dump(cfg, f)
    env = os.environ.copy()
    env["V8_OVERRIDE_FILE"] = str(override)
    env["V8_SWEEP_MODE"] = "1"
    cmd = [py_bin, str(engine_path), "--mode", mode, "--symbols", sym, "--start", start]
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600, env=env, cwd=str(BASE))
        elapsed = time.time() - t0
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
                    "symbol": sym, "sharpe": float(toks.get("sharpe", 0) or 0),
                    "trades": int(toks.get("trades", 0) or 0),
                    "wr": float(toks.get("wr", 0) or 0),
                    "pnl": float(toks.get("pnl", 0) or 0),
                    "avg_pnl": float(toks.get("avg_pnl", 0) or 0),
                    "elapsed": round(elapsed, 1),
                }
    except Exception:
        pass
    override.unlink(missing_ok=True)
    return {"symbol": sym, "sharpe": 0, "trades": 0, "elapsed": round(time.time() - t0, 1)}


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["crypto", "tradier"], default="tradier")
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--limit", type=int, default=0, help="Test first N symbols")
    args = ap.parse_args()

    if args.mode == "crypto" and args.start == "2024-01-01":
        args.start = "2022-01-01"

    py_bin = sys.executable
    engine = BASE / "v8_quick_engine.py"
    cfg = best_config_from_sniper(args.mode)
    if not cfg:
        print(f"No sniper results for {args.mode}; using defaults")

    syms = all_symbols(args.mode)
    if args.limit: syms = syms[: args.limit]
    print(f"PER-SYMBOL: {len(syms)} {args.mode} symbols, workers={args.workers}")
    print(f"Config (from quality_sniper best):")
    for k, v in sorted(cfg.items()): print(f"  {k}={v}")

    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    csv_path = SWEEP_DIR / f"per_symbol_{args.mode}_{ts}.csv"

    results = []
    best = 0.0
    from concurrent.futures import ProcessPoolExecutor, as_completed
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(run_symbol, s, dict(cfg), args.mode, args.start, py_bin, str(engine)): s for s in syms}
        for f in as_completed(futures):
            r = f.result()
            results.append(r)
            s = r.get("sharpe", 0)
            if s > best: best = s
            print(f"  [{len(results)}/{len(syms)}] {r['symbol']:15s} sh={s:+.3f} tr={r.get('trades',0):5d} WR={r.get('wr',0):.1f}% best={best:.3f} ({r['elapsed']:.0f}s)")

    results.sort(key=lambda x: -x.get("sharpe", 0))
    if results:
        with open(csv_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(results[0].keys()))
            w.writeheader()
            w.writerows(results)

    print(f"\n{'='*80}")
    print(f"Top 20 per-symbol:")
    for r in results[:20]:
        print(f"  {r['symbol']:15s} sh={r.get('sharpe',0):+.3f} tr={r.get('trades',0):5d} WR={r.get('wr',0):.1f}%")
    print(f"\nBest per-symbol Sharpe: {best:.3f}")
    print(f"Saved to {csv_path}")


if __name__ == "__main__":
    main()
