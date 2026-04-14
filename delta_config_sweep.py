#!/usr/bin/env python3
"""
Delta config switch sweep — tests every on/off combination.
Uses V8 engine (backtest_v8_engine.py) with real trading logic.
Phase 1: 10 symbols × 2 months (fast validation)
Phase 2: 48 symbols × 4 years (full validation of winners)

Usage:
    python3 delta_config_sweep.py --phase1              # Quick 10-sym test
    python3 delta_config_sweep.py --phase2              # Full 48-sym validation
    python3 delta_config_sweep.py --phase1 --server     # Run on server
"""
import argparse
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from itertools import product

logging.basicConfig(level=logging.INFO, format="%(asctime)s [SWEEP] %(message)s")
logger = logging.getLogger("config_sweep")

BASE = Path(__file__).resolve().parent
RESULTS_DIR = BASE / "data" / "delta_config_sweep"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# Detect environment
IS_SERVER_S1 = os.path.exists("/home/niels/.conda/envs/binance_env/bin/python")
IS_SERVER_S2 = os.path.exists("/home/niels/miniconda3/envs/binance_env/bin/python")
if IS_SERVER_S1:
    PYTHON = "/home/niels/.conda/envs/binance_env/bin/python"
elif IS_SERVER_S2:
    PYTHON = "/home/niels/miniconda3/envs/binance_env/bin/python"
else:
    PYTHON = "/opt/anaconda3/envs/binance_env/bin/python"

# Phase 1 symbols: diverse, liquid, 4yr data available
PHASE1_SYMBOLS = "DOTUSDT,TRXUSDT,XLMUSDT,ATOMUSDT,SANDUSDT,GRTUSDT,ETCUSDT,SUSHIUSDT,SNXUSDT,VETUSDT"
PHASE1_START = "2026-02-01"  # 2 months

# Phase 2: all 48
PHASE2_START = "2022-04-01"  # 4 years

# Config switches to test — each has (name, values_to_test)
SWITCHES = [
    # Core delta gates
    ("DELTA_ENGINE_ENABLED", [True, False]),
    ("DELTA_GATE_OPEN", [True, False]),
    ("DELTA_GATE_AUGMENT", [True, False]),
    ("DELTA_GATE_REENTRY", [True, False]),
    ("DELTA_EXIT_SPEED_DECAY", [True, False]),
    ("DELTA_EXIT_WT_CROSS", [True, False]),
    ("DELTA_EXIT_OVERRIDE_NOLOSS", [True, False]),
    # Legacy paths
    ("LEGACY_GUARANTEED_REENTRY", [True, False]),
    ("LEGACY_DIRECTION_FAVORABLE", [True, False]),
    ("LEGACY_DC_BREAKOUT_REENTRY", [True, False]),
    ("LEGACY_PROC_SINGLE_REENTRY", [True, False]),
    # Service gates
    ("DELTA_SERVICE_REDUCE_GATE", [True, False]),
    ("DELTA_SERVICE_TRAILING_STOP", [True, False]),
    # Scoring
    ("DELTA_ENTRY_SCORE_BONUS", [0, 10, 15, 25]),
    ("DELTA_ENTRY_SCORE_PENALTY", [0, -15, -25, -40]),
    ("DELTA_MIN_TF_FOR_ACTION", [1, 2, 3]),
    # HTF gate
    ("DELTA_HTF_GATE", ["none", "4h", "4h_D"]),
    # Entry params
    ("DELTA_ENTRY_MIN_TF", [2, 3, 4]),
    ("DELTA_ENTRY_Z_THRESHOLD", [1.0, 1.5, 2.0, 2.5]),
    ("DELTA_TF_Z_THRESHOLD", [1.0, 1.5, 2.0]),
]


def build_ablation_configs():
    """Build ablation configs: baseline + toggle each switch one at a time."""
    # Baseline: all delta ON, legacy OFF (the recommended config)
    baseline = {
        "DELTA_ENGINE_ENABLED": True,
        "DELTA_GATE_OPEN": True,
        "DELTA_GATE_AUGMENT": True,
        "DELTA_GATE_REENTRY": True,
        "DELTA_EXIT_SPEED_DECAY": True,
        "DELTA_EXIT_WT_CROSS": True,
        "DELTA_EXIT_OVERRIDE_NOLOSS": True,
        "LEGACY_GUARANTEED_REENTRY": False,
        "LEGACY_DIRECTION_FAVORABLE": False,
        "LEGACY_DC_BREAKOUT_REENTRY": False,
        "LEGACY_PROC_SINGLE_REENTRY": False,
        "DELTA_SERVICE_REDUCE_GATE": True,
        "DELTA_SERVICE_TRAILING_STOP": True,
        "DELTA_ENTRY_SCORE_BONUS": 15,
        "DELTA_ENTRY_SCORE_PENALTY": -25,
        "DELTA_MIN_TF_FOR_ACTION": 2,
        "DELTA_HTF_GATE": "4h_D",
        "DELTA_ENTRY_MIN_TF": 3,
        "DELTA_ENTRY_Z_THRESHOLD": 2.5,
        "DELTA_TF_Z_THRESHOLD": 1.5,
    }
    configs = [("BASELINE_ALL_DELTA_ON", baseline)]
    # ALL OFF (pure legacy — the worst case)
    all_off = {k: False if isinstance(v, bool) else (0 if isinstance(v, int) else ("none" if isinstance(v, str) else v)) for k, v in baseline.items()}
    all_off["LEGACY_GUARANTEED_REENTRY"] = True
    all_off["LEGACY_DIRECTION_FAVORABLE"] = True
    all_off["LEGACY_DC_BREAKOUT_REENTRY"] = True
    all_off["LEGACY_PROC_SINGLE_REENTRY"] = True
    all_off["DELTA_ENTRY_SCORE_BONUS"] = 0
    all_off["DELTA_ENTRY_SCORE_PENALTY"] = 0
    all_off["DELTA_MIN_TF_FOR_ACTION"] = 1
    all_off["DELTA_ENTRY_MIN_TF"] = 4
    all_off["DELTA_ENTRY_Z_THRESHOLD"] = 1.5
    all_off["DELTA_TF_Z_THRESHOLD"] = 1.0
    configs.append(("ALL_LEGACY_NO_DELTA", all_off))
    # Ablation: toggle each switch one at a time from baseline
    for name, values in SWITCHES:
        for val in values:
            if val == baseline.get(name):
                continue  # Skip baseline value
            cfg = dict(baseline)
            cfg[name] = val
            label = f"ABLATE_{name}={val}"
            configs.append((label, cfg))
    return configs


def run_v8_backtest(config_overrides, symbols, start_date, label, workers=2):
    """Run V8 backtest with config overrides, return results dict."""
    # Write config overrides to a temp JSON file that V8 can read
    override_file = RESULTS_DIR / f"override_{label}_{int(time.time())}.json"
    with open(override_file, "w") as f:
        json.dump(config_overrides, f)
    env = os.environ.copy()
    env["V8_OVERRIDE_FILE"] = str(override_file)
    cmd = [
        PYTHON, "-u", str(BASE / "backtest_v8_engine.py"),
        "--mode", "crypto",
        "--account", "ang",
        "--start", start_date,
        "--capital", "1000",
        "--workers", str(workers),
        "--symbols", symbols,
    ]
    logger.info(f"Running: {label} ({symbols.count(',') + 1} sym, start={start_date})")
    t0 = time.time()
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600, cwd=str(BASE), env=env)
        elapsed = time.time() - t0
        # Parse V8_RESULT line from stdout
        output = result.stdout + (result.stderr or "")
        for line in reversed(output.split("\n")):
            line = line.strip()
            if line.startswith("V8_RESULT:"):
                parts = line.replace("V8_RESULT:", "").strip().split()
                data = {"label": label, "elapsed": elapsed, "config": config_overrides}
                for p in parts:
                    if "=" in p:
                        k, v = p.split("=", 1)
                        try:
                            data[k] = float(v)
                        except ValueError:
                            data[k] = v
                return data
        return {"label": label, "elapsed": elapsed, "status": "no_v8_result", "stdout_tail": output[-500:], "config": config_overrides}
    except subprocess.TimeoutExpired:
        return {"label": label, "elapsed": 3600, "status": "timeout", "config": config_overrides}
    except Exception as e:
        return {"label": label, "elapsed": time.time() - t0, "status": f"error: {e}", "config": config_overrides}
    finally:
        override_file.unlink(missing_ok=True)


def run_sweep(symbols, start_date, phase_name, workers=2):
    """Run all ablation configs."""
    configs = build_ablation_configs()
    logger.info(f"{phase_name}: {len(configs)} configs × {symbols.count(',') + 1} symbols")
    results = []
    for i, (label, cfg) in enumerate(configs):
        logger.info(f"[{i+1}/{len(configs)}] {label}")
        result = run_v8_backtest(cfg, symbols, start_date, label, workers)
        results.append(result)
        sharpe = result.get("sharpe", result.get("avg_sharpe", "?"))
        trades = result.get("trades", result.get("total_trades", "?"))
        wr = result.get("wr", result.get("win_rate", "?"))
        logger.info(f"  → Sharpe={sharpe} WR={wr} trades={trades} ({result.get('elapsed', 0):.0f}s)")
    # Sort by sharpe
    results.sort(key=lambda x: -float(x.get("sharpe", x.get("avg_sharpe", 0)) or 0))
    # Save
    out = RESULTS_DIR / f"{phase_name}_{int(time.time())}.json"
    with open(out, "w") as f:
        json.dump({"phase": phase_name, "n_configs": len(configs), "results": results}, f, indent=2, default=str)
    logger.info(f"Saved to {out}")
    # Print top 10
    logger.info(f"\n{'='*100}")
    logger.info(f"TOP 10 — {phase_name}")
    for i, r in enumerate(results[:10], 1):
        s = r.get("sharpe", r.get("avg_sharpe", "?"))
        w = r.get("wr", r.get("win_rate", "?"))
        t = r.get("trades", r.get("total_trades", "?"))
        logger.info(f"  {i:>2}. Sharpe={s} WR={w} trades={t} — {r['label']}")
    logger.info(f"{'='*100}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase1", action="store_true", help="Quick 10-sym 2-month test")
    parser.add_argument("--phase2", action="store_true", help="Full 48-sym 4-year validation")
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    if args.phase1:
        run_sweep(PHASE1_SYMBOLS, PHASE1_START, "phase1_10sym_2mo", args.workers)
    elif args.phase2:
        run_sweep("ALL", PHASE2_START, "phase2_48sym_4yr", args.workers)
    else:
        print("Usage: --phase1 | --phase2")
