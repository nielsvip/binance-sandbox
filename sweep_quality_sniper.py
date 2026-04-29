#!/usr/bin/env python3
"""Quality Sniper sweep — extreme selectivity targeting Sharpe > 1.8.

Hypothesis: current entry_gates sweep saturates at Sharpe 0.30 because
parameters don't differentiate. Sharpe >1.8 requires:
  1. ONLY highest-quality reentry blocks enabled (B15+B11+B04 sniper stack)
  2. Extreme entry score threshold (28, 32, 36 — vs normal 15-24)
  3. Tightest K3M cap (10-20 — ultra-oversold only)
  4. DELTA + CT velocity gates mandatory
  5. HTF alignment min 3 (vs normal 1-2)
  6. Smallest sizing (test if position size inflates noise)

This sweep deliberately trades volume for quality. Expected: few trades, very high WR,
very high risk-adjusted return. If this doesn't break Sharpe 0.8+, the ceiling is real.
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
SWEEP_DIR.mkdir(parents=True, exist_ok=True)

# SNIPER STACK — ALL 7 keepers always on (B15 alone too rare on fast-sym subset).
# Instead, we vary the EXIT and SIZING aggressiveness — that's where quality lives.
# Each combo keeps 7 keepers ON; toggles B12 (volume king) + B10 (stoch rev, 69% WR)
# since those are the two most likely to be "cuttable" for quality-over-volume tradeoff.
SNIPER_BLOCK_COMBOS = [
    # Full stack (baseline for quality sniper)
    {"REENTRY_B15_STRONG_TREND_ENABLED": True, "REENTRY_B04_DC_RETEST_ENABLED": True,
     "REENTRY_B11_DC_BREAK_ENABLED": True, "REENTRY_B02_BC156_BOTTOM_ENABLED": True,
     "REENTRY_B12_WT_MOM_ENABLED": True, "REENTRY_B14_HA_TREND_ENABLED": True,
     "REENTRY_B10_STOCH_REV_ENABLED": True, "_label": "all7"},
    # Drop B12 (volume king but lowest quality of keepers)
    {"REENTRY_B15_STRONG_TREND_ENABLED": True, "REENTRY_B04_DC_RETEST_ENABLED": True,
     "REENTRY_B11_DC_BREAK_ENABLED": True, "REENTRY_B02_BC156_BOTTOM_ENABLED": True,
     "REENTRY_B12_WT_MOM_ENABLED": False, "REENTRY_B14_HA_TREND_ENABLED": True,
     "REENTRY_B10_STOCH_REV_ENABLED": True, "_label": "no_B12"},
    # Drop B14 (moderate Sharpe 0.11)
    {"REENTRY_B15_STRONG_TREND_ENABLED": True, "REENTRY_B04_DC_RETEST_ENABLED": True,
     "REENTRY_B11_DC_BREAK_ENABLED": True, "REENTRY_B02_BC156_BOTTOM_ENABLED": True,
     "REENTRY_B12_WT_MOM_ENABLED": True, "REENTRY_B14_HA_TREND_ENABLED": False,
     "REENTRY_B10_STOCH_REV_ENABLED": True, "_label": "no_B14"},
    # Drop B12 AND B14 (keep 5 strongest)
    {"REENTRY_B15_STRONG_TREND_ENABLED": True, "REENTRY_B04_DC_RETEST_ENABLED": True,
     "REENTRY_B11_DC_BREAK_ENABLED": True, "REENTRY_B02_BC156_BOTTOM_ENABLED": True,
     "REENTRY_B12_WT_MOM_ENABLED": False, "REENTRY_B14_HA_TREND_ENABLED": False,
     "REENTRY_B10_STOCH_REV_ENABLED": True, "_label": "top5_B15_B04_B11_B02_B10"},
    # Top 4 only (drop B10+B12+B14)
    {"REENTRY_B15_STRONG_TREND_ENABLED": True, "REENTRY_B04_DC_RETEST_ENABLED": True,
     "REENTRY_B11_DC_BREAK_ENABLED": True, "REENTRY_B02_BC156_BOTTOM_ENABLED": True,
     "REENTRY_B12_WT_MOM_ENABLED": False, "REENTRY_B14_HA_TREND_ENABLED": False,
     "REENTRY_B10_STOCH_REV_ENABLED": False, "_label": "top4_B15_B04_B11_B02"},
    # High-WR focus (B15 94%, B11 97%, B10 69-75%)
    {"REENTRY_B15_STRONG_TREND_ENABLED": True, "REENTRY_B04_DC_RETEST_ENABLED": False,
     "REENTRY_B11_DC_BREAK_ENABLED": True, "REENTRY_B02_BC156_BOTTOM_ENABLED": True,
     "REENTRY_B12_WT_MOM_ENABLED": False, "REENTRY_B14_HA_TREND_ENABLED": False,
     "REENTRY_B10_STOCH_REV_ENABLED": True, "_label": "highWR_B15_B11_B02_B10"},
]

# Common base — ALL sniper configs get these
SNIPER_BASE = {
    "REENTRY_B01_WT_2of3_ENABLED": False,  # confirmed cut
    "REENTRY_B09_SNAPBACK_ENABLED": False,  # confirmed cut
    "CT_WT_VELOCITY_GATE_ENABLED": True,
    "CT_DC_CROSSOVER_SKIP_ENABLED": True,
    "DELTA_ENGINE_ENABLED": True,
    "DELTA_ENTRY_ENABLED": True,
    "STRUCTURAL_RANGE_SHIFT_EXIT": True,
    "RZ_EXIT_ENABLED": True,
    "SATOSHIT_ENABLED": True,  # try both ways
}

# Selectivity grid — less extreme than before so trades actually happen
SELECTIVITY_GRID = {
    "ENTRY_SCORE_THRESHOLD": [15.0, 20.0, 24.0, 28.0],
    "K3M_FLOOR": [20.0, 25.0, 30.0, 35.0],
    "CT_WT_VELOCITY_1H_MIN": [0.5, 1.0, 1.5, 2.0],
}


def build_configs():
    configs = []
    sel_keys = list(SELECTIVITY_GRID.keys())
    sel_values = [SELECTIVITY_GRID[k] for k in sel_keys]
    for block_cfg in SNIPER_BLOCK_COMBOS:
        block_label = block_cfg.pop("_label", "?")
        for combo in itertools.product(*sel_values):
            cfg = dict(SNIPER_BASE)
            cfg.update({k: v for k, v in block_cfg.items() if not k.startswith("_")})
            cfg.update(dict(zip(sel_keys, combo)))
            sel_label = "_".join(f"{k[:6]}{v}" for k, v in zip(sel_keys, combo))
            cfg["_label"] = f"{block_label}__{sel_label}"
            configs.append(cfg)
        block_cfg["_label"] = block_label  # restore
    return configs


def run_one(cfg, mode, symbols, start, py_bin, engine_path):
    label = cfg.pop("_label", "unknown")
    override = SWEEP_DIR / f"override_snipe_{label.replace('/', '_')[:80]}.json"
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
                # Strip the prefix tag
                line_body = line.split(":", 1)[1] if ":" in line else line
                for t in line_body.split():
                    if "=" in t:
                        k, v = t.split("=", 1)
                        toks[k] = v.rstrip("%").rstrip("s")
                override.unlink(missing_ok=True)
                # 2026-04-29: pool_sharpe + sym_sharpe canonical (CLAUDE.md rule 4). sharpe_w/_ann banned.
                pool = float(toks.get("pool_sharpe", toks.get("sharpe_pt", toks.get("sharpe", toks.get("sharpe_w", 0)))) or 0)
                sym = float(toks.get("sym_sharpe", 0) or 0)
                sh = pool  # canonical Sharpe = pool_sharpe
                return {
                    "label": label,
                    "sharpe": sh, "pool_sharpe": pool, "sym_sharpe": sym,
                    # legacy slots aliased to pool_sharpe so ranking-by-sharpe_w/_ann keeps working
                    "sharpe_pt": pool, "sharpe_w": pool, "sharpe_ann": pool,
                    "pnl": float(toks.get("pnl", toks.get("gain_pct", 0)) or 0),
                    "trades": int(toks.get("trades", toks.get("closes", 0)) or 0),
                    "wins": int(toks.get("wins", 0) or 0), "losses": int(toks.get("losses", 0) or 0),
                    "wr": float(toks.get("wr", 0) or 0),
                    "avg_pnl": float(toks.get("avg_pnl", 0) or 0),
                    "elapsed": round(elapsed, 1), "status": "ok",
                    **{k: v for k, v in cfg.items()}
                }
    except subprocess.TimeoutExpired:
        elapsed = time.time() - t0
    except Exception as e:
        elapsed = time.time() - t0
    override.unlink(missing_ok=True)
    return {"label": label, "sharpe": 0, "pool_sharpe": 0, "sym_sharpe": 0,
            "sharpe_pt": 0, "sharpe_w": 0, "sharpe_ann": 0,
            "pnl": 0, "trades": 0, "elapsed": round(elapsed, 1), "status": "timeout_or_error", **cfg}


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["crypto", "tradier"], default="tradier")
    ap.add_argument("--symbols", default="fast")
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()

    py_bin = sys.executable
    engine = BASE / "v8_quick_engine.py"
    if not engine.exists():
        engine = BASE / "backtest_v8_engine.py"

    configs = build_configs()
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    csv_path = SWEEP_DIR / f"quality_sniper_{args.mode}_{ts}.csv"
    progress_path = SWEEP_DIR / f"quality_sniper_{args.mode}_progress.json"

    done_labels = set()
    results = []
    if args.resume and progress_path.exists():
        with open(progress_path) as f:
            results = json.load(f)
        done_labels = {r["label"] for r in results}

    configs = [c for c in configs if c.get("_label", "") not in done_labels]
    total = len(configs) + len(done_labels)
    best_sharpe = max((r.get("sharpe", 0) for r in results), default=0)
    best_sharpe_pt = max((r.get("sharpe_pt", 0) for r in results), default=0)

    print(f"QUALITY SNIPER: {len(configs)} remaining of {total} | mode={args.mode} | symbols={args.symbols}")
    print(f"TARGET: Sharpe > 1.8 (current: {best_sharpe:.3f} annualized / {best_sharpe_pt:.3f} per-trade)")
    print("=" * 110)

    from concurrent.futures import ProcessPoolExecutor, as_completed
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(run_one, dict(c), args.mode, args.symbols, args.start, py_bin, str(engine)): c.get("_label") for c in configs}

        for f in as_completed(futures):
            r = f.result()
            results.append(r)
            s = r.get("sharpe", 0)
            spt = r.get("sharpe_pt", 0)
            if s > best_sharpe: best_sharpe = s
            if spt > best_sharpe_pt: best_sharpe_pt = spt
            n_done = len(results)
            print(f"  [{n_done}/{total}] {r['label'][:50]:50s} sh={s:.3f} sh_pt={spt:.3f} trades={r.get('trades',0)} best={best_sharpe:.3f}/{best_sharpe_pt:.3f} ({r.get('elapsed',0):.0f}s)")
            with open(progress_path, "w") as pf:
                json.dump(results, pf)

    results.sort(key=lambda x: -max(x.get("sharpe", 0), x.get("sharpe_pt", 0)))
    if results:
        with open(csv_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(results[0].keys()), extrasaction="ignore")
            w.writeheader()
            w.writerows(results)

    print(f"\n{'='*110}")
    print(f"{'Label':50s} {'Sharpe':>8s} {'Sharpe_pt':>10s} {'Trades':>7s} {'WR%':>5s} {'PnL%':>8s}")
    print(f"{'='*110}")
    for r in results[:20]:
        t = r.get("trades", 0)
        wr = (r.get("wins", 0) / max(1, t)) * 100
        print(f"{r['label'][:50]:50s} {r.get('sharpe',0):8.3f} {r.get('sharpe_pt',0):10.3f} {t:7d} {wr:5.1f} {r.get('pnl',0):+7.2f}%")
    print(f"\nSaved {len(results)} results to {csv_path}")
    print(f"FINAL BEST: Sharpe={best_sharpe:.3f}  Sharpe_pt={best_sharpe_pt:.3f}  {'🎯 TARGET HIT' if best_sharpe > 1.8 else '⏳ below target'}")


if __name__ == "__main__":
    main()
