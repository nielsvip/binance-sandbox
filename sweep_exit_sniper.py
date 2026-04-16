#!/usr/bin/env python3
"""Exit sniper — for the top-5 entry configs from quality_sniper,
sweep exit parameters (SRS TF, RZ thresholds, rally gates, reentry sizing).

Cascades from quality_sniper results. Target: Sharpe > 1.8.
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

SWEEP_DIR = BASE / "data" / "sweep_results"

# Exit parameter grid — tested on top of winning entry configs
EXIT_GRID = {
    "STRUCTURAL_RANGE_SHIFT_TF": ["bb_1h", "dc_1h", "dc_4h", "bb_4h"],
    "RZ_TOP_BB_THRESHOLD": [0.80, 0.85, 0.92, 0.97],
    "RZ_BOT_BB_THRESHOLD": [0.03, 0.08, 0.15, 0.20],
    "REENTRY_RALLY_K15M_MAX": [60.0, 75.0, 90.0],
    "REENTRY_RALLY_HTF_MIN": [1, 2, 3],
}


def load_top_entries(mode: str, n: int = 5) -> list:
    """Load top-N entry configs from quality_sniper CSV."""
    pattern = f"quality_sniper_{mode}_*.csv"
    files = sorted(SWEEP_DIR.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return []
    rows = list(csv.DictReader(open(files[0])))
    rows.sort(key=lambda r: -float(r.get("sharpe", 0) or 0))
    tops = []
    for r in rows[:n]:
        base = {}
        for k in r:
            if k.startswith("REENTRY_B") or k.startswith("CT_") or k.startswith("DELTA_") or k in (
                "ENTRY_SCORE_THRESHOLD", "K3M_FLOOR", "SATOSHIT_ENABLED", "RZ_EXIT_ENABLED",
                "STRUCTURAL_RANGE_SHIFT_EXIT"
            ):
                v = r[k]
                if v in ("True", "true"): base[k] = True
                elif v in ("False", "false"): base[k] = False
                else:
                    try: base[k] = float(v)
                    except: base[k] = v
        base["_base_label"] = r.get("label", "top")
        base["_base_sharpe"] = float(r.get("sharpe", 0) or 0)
        tops.append(base)
    return tops


def build_configs(mode):
    tops = load_top_entries(mode, n=5)
    if not tops:
        print(f"[WARN] No quality_sniper_{mode} results found — using baseline defaults")
        tops = [{"_base_label": "baseline", "_base_sharpe": 0.0}]
    configs = []
    ex_keys = list(EXIT_GRID.keys())
    ex_vals = [EXIT_GRID[k] for k in ex_keys]
    for top in tops:
        base_label = top.pop("_base_label")
        base_sharpe = top.pop("_base_sharpe")
        for combo in itertools.product(*ex_vals):
            cfg = dict(top)
            cfg.update(dict(zip(ex_keys, combo)))
            ex_label = f"srs={combo[0][-3:]}_rztop={combo[1]}_rzbot={combo[2]}_k15={int(combo[3])}_htf={combo[4]}"
            cfg["_label"] = f"{base_label[:20]}__{ex_label}"
            configs.append(cfg)
    return configs


def run_one(cfg, mode, symbols, start, py_bin, engine_path):
    label = cfg.pop("_label", "?")
    override = SWEEP_DIR / f"override_exit_{label.replace('/', '_')[:80]}.json"
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
                body = line.split(":", 1)[1]
                for t in body.split():
                    if "=" in t:
                        k, v = t.split("=", 1)
                        toks[k] = v.rstrip("%").rstrip("s")
                override.unlink(missing_ok=True)
                return {
                    "label": label,
                    "sharpe": float(toks.get("sharpe", 0) or 0),
                    "pnl": float(toks.get("pnl", 0) or 0),
                    "trades": int(toks.get("trades", 0) or 0),
                    "wr": float(toks.get("wr", 0) or 0),
                    "avg_pnl": float(toks.get("avg_pnl", 0) or 0),
                    "elapsed": round(elapsed, 1), "status": "ok",
                    **{k: v for k, v in cfg.items()}
                }
    except Exception:
        pass
    override.unlink(missing_ok=True)
    return {"label": label, "sharpe": 0, "trades": 0, "status": "err", "elapsed": round(time.time() - t0, 1), **cfg}


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["crypto", "tradier"], default="tradier")
    ap.add_argument("--symbols", default="fast")
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--workers", type=int, default=3)
    args = ap.parse_args()

    py_bin = sys.executable
    engine = BASE / "v8_quick_engine.py"
    configs = build_configs(args.mode)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    csv_path = SWEEP_DIR / f"exit_sniper_{args.mode}_{ts}.csv"

    print(f"EXIT SNIPER: {len(configs)} configs | mode={args.mode}")
    best = 0.0
    results = []

    from concurrent.futures import ProcessPoolExecutor, as_completed
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(run_one, dict(c), args.mode, args.symbols, args.start, py_bin, str(engine)): c.get("_label") for c in configs}
        for f in as_completed(futures):
            r = f.result()
            results.append(r)
            s = r.get("sharpe", 0)
            if s > best: best = s
            n = len(results)
            print(f"  [{n}/{len(configs)}] {r['label'][:55]:55s} sh={s:.3f} tr={r.get('trades',0)} WR={r.get('wr',0):.1f}% best={best:.3f}")

    results.sort(key=lambda x: -x.get("sharpe", 0))
    if results:
        with open(csv_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(results[0].keys()), extrasaction="ignore")
            w.writeheader()
            w.writerows(results)
    print(f"\nFINAL BEST: {best:.3f}  {'🎯 TARGET HIT (1.8)' if best > 1.8 else ''}")
    print(f"Saved {len(results)} to {csv_path}")


if __name__ == "__main__":
    main()
