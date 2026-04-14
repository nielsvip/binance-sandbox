#!/usr/bin/env python3
"""
V5 Continuous Sweep — Tests config variations 24/7, logs results, reports winners.

Runs on server. Alternates between tradier and crypto.
Each config variation runs backtest_v5_run_tradier.py or backtest_v5_run.py with
environment-variable overrides, then compares PnL/Sharpe to baseline.

Results written to backtest_v5/sweep_continuous/results.jsonl
"""

import json
import logging
import os
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("v5_sweep")

if platform.system() == "Darwin":
    BASE_PATH = Path("/Users/niels/Documents/binance")
    PYTHON = "/opt/anaconda3/envs/binance_env/bin/python"
else:
    BASE_PATH = Path("/home/niels/binance")
    PYTHON = "/home/niels/.conda/envs/binance_env/bin/python"

RESULTS_DIR = BASE_PATH.parent / "binance-sandbox" / "backtest_v5" / "sweep_continuous" if platform.system() != "Darwin" else BASE_PATH / "backtest_v5" / "sweep_continuous"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
RESULTS_FILE = RESULTS_DIR / "results.jsonl"

# =============================================================================
# CONFIG VARIATIONS TO TEST
# =============================================================================
# Each variation is: (name, mode, overrides_dict)
# overrides_dict keys match config_tradier.py or config.py field names

TRADIER_VARIATIONS = [
    ("baseline", {}),
    # WT exit threshold variations
    ("wt_exit_3of3_only", {"WT_EXIT_MIN_TFS": 3}),  # require ALL 3 TFs against
    ("wt_exit_gain_2pct", {"WT_EXIT_MIN_GAIN": 2.0}),  # require 2% gain before WT exit
    ("wt_exit_gain_3pct", {"WT_EXIT_MIN_GAIN": 3.0}),  # require 3% gain
    ("wt_exit_disabled", {"WT_EXIT_ENABLED": False}),  # no WT exit at all — IBS only
    # IBS variations
    ("ibs_threshold_085", {"IBS_LONG_THRESHOLD": 0.85, "IBS_SHORT_THRESHOLD": 0.15}),
    ("ibs_threshold_095", {"IBS_LONG_THRESHOLD": 0.95, "IBS_SHORT_THRESHOLD": 0.05}),
    ("ibs_min_gain_05", {"IBS_MIN_GAIN": 0.5}),
    ("ibs_min_gain_2", {"IBS_MIN_GAIN": 2.0}),
    # Entry threshold
    ("entry_score_15", {"ENTRY_SCORE_MIN": 15}),
    ("entry_score_20", {"ENTRY_SCORE_MIN": 20}),
    ("entry_score_24", {"ENTRY_SCORE_MIN": 24}),
    ("entry_score_25", {"ENTRY_SCORE_MIN": 25}),
    # BC_154 stock-specific params
    ("htf_align_2", {"HTF_ALIGNMENT_MIN": 2}),
    ("stoch_gate_60", {"COMBINED_STOCH_GATE": 60}),
    ("stoch_gate_70", {"COMBINED_STOCH_GATE": 70}),
    ("reentry_stoch_80", {"REENTRY_STOCH_K_MAX": 80}),
    ("mfi_only_sizing", {"MFI_ONLY_SIZING": True}),
    ("wt_cross_align_3", {"WT_CROSS_ALIGNMENT_MIN": 3}),
    # Position sizing
    ("pos_size_800", {"START_POSITION_SIZE": 800}),
    ("pos_size_1200", {"START_POSITION_SIZE": 1200}),
    # Augment variations
    ("augment_off", {"AUGMENT_ENABLED": False}),
    ("augment_min_gain_5", {"AUGMENT_MIN_GAIN": 5.0}),
    # Hold time
    ("min_hold_20min", {"MIN_HOLD_MINUTES": 20}),
    ("min_hold_5min", {"MIN_HOLD_MINUTES": 5}),
    # Combined winners (will be populated after Tier 1-4 results)
    ("best_combo_v1", {"WT_EXIT_ENABLED": False, "ENTRY_SCORE_MIN": 24, "COMBINED_STOCH_GATE": 60}),
]

CRYPTO_VARIATIONS = [
    ("baseline", {}),
    ("wt_exit_3of3_only", {"WT_EXIT_MIN_TFS": 3}),
    ("wt_exit_gain_05pct", {"WT_EXIT_MIN_GAIN": 0.5}),
    ("wt_exit_gain_1pct", {"WT_EXIT_MIN_GAIN": 1.0}),
    ("wt_exit_disabled", {"WT_EXIT_ENABLED": False}),
    ("ratio_mult_3", {"RATIO_MULTIPLIER": 3.0}),
    ("ratio_mult_5", {"RATIO_MULTIPLIER": 5.0}),
    ("entry_score_15", {"ENTRY_SCORE_MIN": 15}),
    ("entry_score_20", {"ENTRY_SCORE_MIN": 20}),
]


def run_backtest(mode: str, name: str, overrides: dict, timeout: int = 7200) -> dict:
    """Run a single V5 backtest with config overrides."""
    script = "backtest_v5_run_tradier.py" if mode == "tradier" else "backtest_v5_run.py"
    start_date = "2024-01-01" if mode == "tradier" else "2024-01-01"
    cmd = [PYTHON, "-u", str(BASE_PATH / script)]
    if mode == "crypto":
        cmd += ["--mode", "crypto"]
    cmd += ["--all", "--start", start_date]
    env = os.environ.copy()
    env["V5_SWEEP_OVERRIDES"] = json.dumps(overrides)
    env["V5_SWEEP_NAME"] = name
    log_path = RESULTS_DIR / f"log_{mode}_{name}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.log"
    logger.info(f"[{mode}] Starting: {name} (overrides: {overrides})")
    t0 = time.time()
    try:
        with open(log_path, "w") as logf:
            proc = subprocess.run(cmd, env=env, stdout=logf, stderr=subprocess.STDOUT, timeout=timeout, cwd=str(BASE_PATH))
        elapsed = time.time() - t0
        # Parse results from log
        result = parse_results(log_path, mode, name, overrides, elapsed)
        logger.info(f"[{mode}] Done: {name} → PnL=${result.get('pnl', 0):.2f}, Sharpe={result.get('sharpe', 0):.2f}, trades={result.get('trades', 0)}, {elapsed:.0f}s")
        return result
    except subprocess.TimeoutExpired:
        logger.error(f"[{mode}] TIMEOUT: {name} after {timeout}s")
        return {"mode": mode, "name": name, "error": "timeout", "overrides": overrides}
    except Exception as e:
        logger.error(f"[{mode}] ERROR: {name}: {e}")
        return {"mode": mode, "name": name, "error": str(e), "overrides": overrides}


def parse_results(log_path: Path, mode: str, name: str, overrides: dict, elapsed: float) -> dict:
    """Parse PnL, Sharpe, trades from V5 backtest log."""
    result = {"mode": mode, "name": name, "overrides": overrides, "elapsed": elapsed, "timestamp": datetime.utcnow().isoformat()}
    try:
        text = log_path.read_text()
        for line in text.split("\n"):
            if "Realized:" in line and "PnL:" in line:
                parts = line.split("|")
                for p in parts:
                    p = p.strip()
                    if p.startswith("Realized:"):
                        result["trades"] = int(p.split("trades")[0].split(":")[1].strip())
                    if "PnL: $" in p:
                        result["pnl"] = float(p.split("$")[1].strip())
            if "Sharpe" in line and "annualized" in line:
                try:
                    result["sharpe"] = float(line.split(":")[1].strip())
                except (ValueError, IndexError):
                    pass
            if "Win rate:" in line:
                try:
                    result["win_rate"] = float(line.split("Win rate:")[1].split("%")[0].strip())
                except (ValueError, IndexError):
                    pass
            if "TOTAL:" in line and "$" in line:
                try:
                    result["total_pnl"] = float(line.split("TOTAL: $")[1].strip())
                except (ValueError, IndexError):
                    pass
        # Parse exit function stats
        exit_stats = {}
        in_exits = False
        for line in text.split("\n"):
            if "EXIT REASONS" in line or "EXIT FUNCTIONS" in line:
                in_exits = True
                continue
            if in_exits and "===" in line:
                in_exits = False
                continue
            if in_exits and "n=" in line and "PnL:" in line:
                parts = line.strip().split()
                if len(parts) >= 6:
                    exit_name = parts[0]
                    try:
                        n = int(parts[1].replace("n=", ""))
                        pnl = float(parts[3].replace("$", "").replace(",", ""))
                        wr = float(parts[5].replace("%", "")) if len(parts) > 5 else 0
                        exit_stats[exit_name] = {"n": n, "pnl": pnl, "wr": wr}
                    except (ValueError, IndexError):
                        pass
        result["exit_stats"] = exit_stats
    except Exception as e:
        result["parse_error"] = str(e)
    return result


def save_result(result: dict):
    """Append result to JSONL file."""
    with open(RESULTS_FILE, "a") as f:
        f.write(json.dumps(result, default=str) + "\n")


def print_leaderboard():
    """Print current leaderboard from results file."""
    if not RESULTS_FILE.exists():
        return
    results = []
    for line in RESULTS_FILE.read_text().strip().split("\n"):
        if line:
            try:
                results.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    if not results:
        return
    for mode in ["tradier", "crypto"]:
        mode_results = [r for r in results if r.get("mode") == mode and "pnl" in r]
        if not mode_results:
            continue
        mode_results.sort(key=lambda x: x.get("pnl", 0), reverse=True)
        logger.info(f"\n{'='*60}")
        logger.info(f"  LEADERBOARD — {mode.upper()}")
        logger.info(f"{'='*60}")
        for i, r in enumerate(mode_results[:10]):
            logger.info(f"  #{i+1} {r['name']:30s} PnL=${r.get('pnl',0):>10.2f}  Sharpe={r.get('sharpe',0):>6.2f}  WR={r.get('win_rate',0):>5.1f}%  trades={r.get('trades',0):>5d}")
        logger.info(f"{'='*60}\n")


def main():
    logger.info("=" * 60)
    logger.info("  V5 CONTINUOUS SWEEP — 24/7 Config Optimization")
    logger.info("=" * 60)
    cycle = 0
    while True:
        cycle += 1
        logger.info(f"\n--- CYCLE {cycle} started at {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC ---")
        # Tradier sweep
        logger.info(f"[TRADIER] Testing {len(TRADIER_VARIATIONS)} configs...")
        for name, overrides in TRADIER_VARIATIONS:
            result = run_backtest("tradier", name, overrides)
            save_result(result)
        print_leaderboard()
        # Crypto sweep
        logger.info(f"[CRYPTO] Testing {len(CRYPTO_VARIATIONS)} configs...")
        for name, overrides in CRYPTO_VARIATIONS:
            result = run_backtest("crypto", name, overrides)
            save_result(result)
        print_leaderboard()
        logger.info(f"--- CYCLE {cycle} complete. Restarting... ---")


if __name__ == "__main__":
    main()
