#!/usr/bin/env python3
"""Phase 1b: Reentry block combination sweep (2^9 = 512 configs).
Tests every combination of the 9 ablation-proven reentry blocks.
Uses vectorized v8_quick_engine via V8_OVERRIDE_FILE.

Results: data/sweep_results/reentry_blocks_<mode>_<timestamp>.csv
"""
import csv
import itertools
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

BLOCKS = [
    "REENTRY_B15_STRONG_TREND_ENABLED",
    "REENTRY_B04_DC_RETEST_ENABLED",
    "REENTRY_B11_DC_BREAK_ENABLED",
    "REENTRY_B02_BC156_BOTTOM_ENABLED",
    "REENTRY_B12_WT_MOM_ENABLED",
    "REENTRY_B14_HA_TREND_ENABLED",
    "REENTRY_B10_STOCH_REV_ENABLED",
    "REENTRY_B01_WT_2of3_ENABLED",
    "REENTRY_B09_SNAPBACK_ENABLED",
]

SWEEP_DIR = BASE / "data" / "sweep_results"
SWEEP_DIR.mkdir(parents=True, exist_ok=True)


def build_configs():
    configs = []
    for combo in itertools.product([True, False], repeat=len(BLOCKS)):
        cfg = dict(zip(BLOCKS, combo))
        n_on = sum(1 for v in combo if v)
        cfg["_label"] = f"B{''.join('1' if v else '0' for v in combo)}_n{n_on}"
        configs.append(cfg)
    return configs


def run_one(cfg, mode, symbols, start, py_bin, engine_path):
    label = cfg.pop("_label", "unknown")
    override = SWEEP_DIR / f"override_rb_{label}.json"
    with open(override, "w") as f:
        json.dump(cfg, f)
    env = os.environ.copy()
    env["V8_OVERRIDE_FILE"] = str(override)
    env["V8_SWEEP_MODE"] = "1"
    cmd = [py_bin, str(engine_path), "--mode", mode, "--symbols", symbols, "--start", start]
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800, env=env, cwd=str(BASE))
        elapsed = time.time() - t0
        for line in reversed(proc.stdout.splitlines()):
            if "V8_QUICK_RESULT:" in line or "V8_RESULT:" in line:
                toks = {}
                line_body = line.split(":", 1)[1] if ":" in line else line
                for t in line_body.split():
                    if "=" in t:
                        k, v = t.split("=", 1)
                        toks[k] = v.rstrip("%").rstrip("s")
                override.unlink(missing_ok=True)
                return {
                    "label": label, "sharpe": float(toks.get("sharpe", toks.get("sharpe_w", 0)) or 0),
                    "pnl": float(toks.get("pnl", toks.get("gain_pct", 0)) or 0),
                    "trades": int(toks.get("trades", toks.get("closes", 0)) or 0),
                    "wins": int(toks.get("wins", 0) or 0), "losses": int(toks.get("losses", 0) or 0),
                    "wr": float(toks.get("wr", 0) or 0),
                    "elapsed": round(elapsed, 1), "status": "ok",
                    **{k: v for k, v in cfg.items()}
                }
    except subprocess.TimeoutExpired:
        elapsed = time.time() - t0
    except Exception as e:
        elapsed = time.time() - t0
    override.unlink(missing_ok=True)
    return {"label": label, "sharpe": 0, "pnl": 0, "trades": 0, "elapsed": round(elapsed, 1), "status": "timeout_or_error", **cfg}


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["crypto", "tradier"], default="crypto")
    ap.add_argument("--symbols", default="fast")
    ap.add_argument("--start", default="2022-01-01")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()

    py_bin = sys.executable
    engine = BASE / "v8_quick_engine.py"
    if not engine.exists():
        engine = BASE / "backtest_v8_engine.py"

    configs = build_configs()
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    csv_path = SWEEP_DIR / f"reentry_blocks_{args.mode}_{ts}.csv"
    progress_path = SWEEP_DIR / f"reentry_blocks_{args.mode}_progress.json"

    done_labels = set()
    results = []
    if args.resume and progress_path.exists():
        with open(progress_path) as f:
            results = json.load(f)
        done_labels = {r["label"] for r in results}
        print(f"Resuming: {len(done_labels)} already done")

    configs = [c for c in configs if c.get("_label", "") not in done_labels]
    total = len(configs) + len(done_labels)
    best_sharpe = max((r.get("sharpe", 0) for r in results), default=0)

    print(f"Reentry block sweep: {len(configs)} remaining of {total} | mode={args.mode} | symbols={args.symbols}")
    print(f"{'='*100}")

    from concurrent.futures import ProcessPoolExecutor, as_completed
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {}
        for cfg in configs:
            f = pool.submit(run_one, dict(cfg), args.mode, args.symbols, args.start, py_bin, str(engine))
            futures[f] = cfg.get("_label", "?")

        for f in as_completed(futures):
            r = f.result()
            results.append(r)
            s = r.get("sharpe", 0)
            if s > best_sharpe:
                best_sharpe = s
            n_done = len(results)
            n_on = sum(1 for k, v in r.items() if k.startswith("REENTRY_B") and v is True)
            print(f"  [{n_done}/{total}] {r['label']} blocks={n_on} sharpe={s:.3f} trades={r.get('trades',0)} best={best_sharpe:.3f} ({r.get('elapsed',0):.0f}s)")

            # Save progress after every result
            with open(progress_path, "w") as pf:
                json.dump(results, pf)

    # Write final CSV
    results.sort(key=lambda x: -x.get("sharpe", 0))
    if results:
        fieldnames = list(results[0].keys())
        with open(csv_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            w.writeheader()
            w.writerows(results)

    print(f"\n{'='*100}")
    print(f"{'Label':30s} {'Blocks':>6s} {'Sharpe':>8s} {'Trades':>7s} {'PnL%':>8s}")
    print(f"{'='*100}")
    for r in results[:20]:
        n_on = sum(1 for k, v in r.items() if k.startswith("REENTRY_B") and v is True)
        print(f"{r['label']:30s} {n_on:6d} {r.get('sharpe',0):8.3f} {r.get('trades',0):7d} {r.get('pnl',0):+7.2f}%")
    print(f"\nSaved {len(results)} results to {csv_path}")


if __name__ == "__main__":
    main()
