#!/usr/bin/env python3
"""
V5 Fast Sweep — Quick smoke tests on subset of symbols.

Tests each config on 10 stocks (6mo) or 6 crypto (6mo) in ~2 min each.
Produces a ranked leaderboard in ~1 hour for 30+ configs.
Top 5 winners get queued for full validation.

Usage:
    python3 v5_fast_sweep.py --mode tradier
    python3 v5_fast_sweep.py --mode crypto
    python3 v5_fast_sweep.py --mode both
"""

import argparse
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
logger = logging.getLogger("v5_fast")

if platform.system() == "Darwin":
    BASE_PATH = Path("/Users/niels/Documents/binance")
    PYTHON = "/opt/anaconda3/envs/binance_env/bin/python"
else:
    BASE_PATH = Path("/home/niels/binance")
    PYTHON = "/home/niels/.conda/envs/binance_env/bin/python"

RESULTS_DIR = (BASE_PATH.parent / "binance-sandbox" / "backtest_v5" / "fast_sweep") if platform.system() != "Darwin" else (BASE_PATH / "backtest_v5" / "fast_sweep")
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# Representative subsets — pick liquid, diverse symbols
# 30 stocks = enough for all functions to fire, still ~3-4 min per test
FAST_TRADIER = ["AAPL", "NVDA", "MSFT", "TSLA", "AMD", "JPM", "XOM", "PFE", "KO", "HD", "AMZN", "GOOGL", "META", "BA", "CVX", "MU", "COST", "WMT", "V", "MA", "LLY", "OXY", "UPS", "NKE", "T", "PEP", "CRWD", "ORCL", "FDX", "GM"]
FAST_CRYPTO = ["TRXUSDT", "XLMUSDT", "DOTUSDT", "ETCUSDT", "ATOMUSDT", "VETUSDT", "XMRUSDT", "DASHUSDT", "SUSHIUSDT", "COMPUSDT", "YFIUSDT", "SNXUSDT"]
FAST_START_TRADIER = "2024-06-01"  # 21 months — enough for all functions to fire
FAST_START_CRYPTO = "2024-06-01"

# =============================================================================
# ALL CONFIGS — ordered by expected impact
# =============================================================================
TRADIER_CONFIGS = [
    # =====================================================================
    # PHASE 1: FUNCTION-LEVEL ABLATION — exact PnL of each evaluate_* function
    # =====================================================================
    ("T01_baseline", {}),
    # --- evaluate_stop sub-functions (exit ablation) ---
    ("T02_no_WT_exit", {"WT_EXIT_ENABLED": False}),
    ("T03_no_IBS_exit", {"IBS_EXIT_ENABLED": False}),
    ("T04_no_STRUCT_exit", {"STRUCT_EXIT_ENABLED": False}),
    ("T05_no_ALGO_exit", {"ALGO_EXIT_ENABLED": False}),
    ("T06_no_DC_STOP_exit", {"DC_STOP_ENABLED": False}),
    ("T07_no_BREAKEVEN_exit", {"BREAK_EVEN_GUARD_ENABLED": False}),
    ("T08_IBS_only_exits", {"WT_EXIT_ENABLED": False, "STRUCT_EXIT_ENABLED": False, "ALGO_EXIT_ENABLED": False, "DC_STOP_ENABLED": False, "BREAK_EVEN_GUARD_ENABLED": False}),
    # --- evaluate_reentry ablation ---
    ("T09_no_reentry", {"REENTRY_ENABLED": False}),
    # --- evaluate_augment ablation ---
    ("T10_no_augment", {"AUGMENT_ENABLED": False}),
    # --- evaluate_open sub-functions (entry ablation) ---
    ("T11_no_DC_BREAK_entry", {"DC_BREAK_ENTRY_ENABLED": False}),
    ("T12_no_OVERSOLD_entry", {"OVERSOLD_TURN_ENABLED": False}),
    ("T13_no_OVERBOUGHT_entry", {"OVERBOUGHT_TURN_ENABLED": False}),
    ("T14_DC_BREAK_only_entry", {"OVERSOLD_TURN_ENABLED": False, "OVERBOUGHT_TURN_ENABLED": False}),
    # =====================================================================
    # PHASE 2: THRESHOLD SWEEP — find optimal values for winning functions
    # =====================================================================
    # WT exit (if it fires): what gain gate makes it profitable?
    ("T15_wt_gain_1pct", {"WT_EXIT_MIN_GAIN": 1.0}),
    ("T16_wt_gain_2pct", {"WT_EXIT_MIN_GAIN": 2.0}),
    ("T17_wt_gain_3pct", {"WT_EXIT_MIN_GAIN": 3.0}),
    ("T18_wt_3of3_only", {"WT_EXIT_MIN_TFS": 3}),
    # NOLOSS threshold (controls when ANY exit can fire)
    ("T19_noloss_05", {"NOLOSS_MIN_PROFIT_PCT_TRADIER": 0.5}),
    ("T20_noloss_2", {"NOLOSS_MIN_PROFIT_PCT_TRADIER": 2.0}),
    ("T21_noloss_3", {"NOLOSS_MIN_PROFIT_PCT_TRADIER": 3.0}),
    # Position sizing
    ("T22_size_400", {"START_POSITION_SIZE": 400}),
    ("T23_size_800", {"START_POSITION_SIZE": 800}),
    ("T24_size_1200", {"START_POSITION_SIZE": 1200}),
    ("T25_size_2000", {"START_POSITION_SIZE": 2000}),
    # Hold time before exits fire
    ("T26_hold_5m", {"MIN_HOLD_MINUTES": 5}),
    ("T27_hold_20m", {"MIN_HOLD_MINUTES": 20}),
    # Max open positions
    ("T28_max_pos_8", {"MAX_POSITIONS": 8}),
    ("T29_max_pos_24", {"MAX_POSITIONS": 24}),
    # =====================================================================
    # PHASE 3: COMBOS — combine winners from phase 1+2
    # =====================================================================
    ("T30_no_wt+size1200", {"WT_EXIT_ENABLED": False, "START_POSITION_SIZE": 1200}),
    ("T31_no_wt+noloss05", {"WT_EXIT_ENABLED": False, "NOLOSS_MIN_PROFIT_PCT_TRADIER": 0.5}),
    ("T32_no_wt+no_aug", {"WT_EXIT_ENABLED": False, "AUGMENT_ENABLED": False}),
    ("T33_no_wt+no_reentry", {"WT_EXIT_ENABLED": False, "REENTRY_ENABLED": False}),
    ("T34_no_wt+size1200+noloss05", {"WT_EXIT_ENABLED": False, "START_POSITION_SIZE": 1200, "NOLOSS_MIN_PROFIT_PCT_TRADIER": 0.5}),
    ("T35_ibs_only+size1200", {"WT_EXIT_ENABLED": False, "STRUCT_EXIT_ENABLED": False, "ALGO_EXIT_ENABLED": False, "DC_STOP_ENABLED": False, "BREAK_EVEN_GUARD_ENABLED": False, "START_POSITION_SIZE": 1200}),
]

CRYPTO_CONFIGS = [
    # =====================================================================
    # PHASE 1: EXIT ABLATION
    # =====================================================================
    ("C01_baseline", {}),
    ("C02_no_WT_exit", {"WT_EXIT_ENABLED": False}),
    ("C03_no_STRUCT_exit", {"STRUCT_EXIT_ENABLED": False}),
    ("C04_no_HEDGE_exit", {"HEDGE_CLEANUP_ENABLED": False}),
    ("C05_no_BREAKEVEN", {"BREAK_EVEN_GUARD_ENABLED": False}),
    # =====================================================================
    # PHASE 2: ENTRY ABLATION
    # =====================================================================
    ("C06_no_DC_BREAK", {"DC_BREAK_ENTRY_ENABLED": False}),
    ("C07_no_LONG", {"LONG_ENTRY_ENABLED": False}),
    ("C08_no_SHORT", {"SHORT_ENTRY_ENABLED": False}),
    # =====================================================================
    # PHASE 3: EXIT TUNING
    # =====================================================================
    ("C09_wt_3of3", {"WT_EXIT_MIN_TFS": 3}),
    ("C10_wt_gain_05", {"WT_EXIT_MIN_GAIN": 0.5}),
    ("C11_wt_gain_1", {"WT_EXIT_MIN_GAIN": 1.0}),
    # =====================================================================
    # PHASE 4: ENTRY TUNING
    # =====================================================================
    ("C12_entry_12", {"ENTRY_SCORE_MIN": 12}),
    ("C13_entry_15", {"ENTRY_SCORE_MIN": 15}),
    ("C14_entry_20", {"ENTRY_SCORE_MIN": 20}),
    # =====================================================================
    # PHASE 5: SIZING & RATIO
    # =====================================================================
    ("C15_ratio_3", {"RATIO_MULTIPLIER": 3.0}),
    ("C16_ratio_5", {"RATIO_MULTIPLIER": 5.0}),
    ("C17_ratio_6", {"RATIO_MULTIPLIER": 6.0}),
    ("C18_size_20", {"START_POSITION_SIZE": 20}),
    ("C19_size_50", {"START_POSITION_SIZE": 50}),
    # =====================================================================
    # PHASE 6: COMBOS
    # =====================================================================
    ("C20_no_wt+ratio5", {"WT_EXIT_ENABLED": False, "RATIO_MULTIPLIER": 5.0}),
    ("C21_no_wt+entry15+ratio5", {"WT_EXIT_ENABLED": False, "ENTRY_SCORE_MIN": 15, "RATIO_MULTIPLIER": 5.0}),
    ("C22_kitchen_sink", {"WT_EXIT_ENABLED": False, "ENTRY_SCORE_MIN": 15, "RATIO_MULTIPLIER": 5.0, "START_POSITION_SIZE": 50}),
]


def run_fast_test(mode: str, name: str, overrides: dict) -> dict:
    """Run a fast V5 backtest on subset of symbols."""
    script = "backtest_v5_run_tradier.py" if mode == "tradier" else "backtest_v5_run.py"
    symbols = FAST_TRADIER if mode == "tradier" else FAST_CRYPTO
    start = FAST_START_TRADIER if mode == "tradier" else FAST_START_CRYPTO
    cmd = [PYTHON, "-u", str(BASE_PATH / script)]
    if mode == "crypto":
        cmd += ["--mode", "crypto"]
    cmd += ["--symbols", ",".join(symbols), "--start", start]
    env = os.environ.copy()
    env["V5_SWEEP_OVERRIDES"] = json.dumps(overrides)
    env["V5_SWEEP_NAME"] = name
    log_path = RESULTS_DIR / f"{name}.log"
    t0 = time.time()
    try:
        with open(log_path, "w") as logf:
            proc = subprocess.run(cmd, env=env, stdout=logf, stderr=subprocess.STDOUT, timeout=600, cwd=str(BASE_PATH))
        elapsed = time.time() - t0
        result = parse_log(log_path, mode, name, overrides, elapsed)
        return result
    except subprocess.TimeoutExpired:
        return {"mode": mode, "name": name, "error": "timeout", "elapsed": 600}
    except Exception as e:
        return {"mode": mode, "name": name, "error": str(e), "elapsed": time.time() - t0}


def parse_log(log_path, mode, name, overrides, elapsed):
    """Extract key metrics from V5 log."""
    r = {"mode": mode, "name": name, "overrides": overrides, "elapsed": round(elapsed, 1)}
    try:
        text = log_path.read_text()
        for line in text.split("\n"):
            if "Realized:" in line and "trades" in line and "PnL:" in line:
                for seg in line.split("|"):
                    seg = seg.strip()
                    if seg.startswith("Realized:"):
                        try: r["trades"] = int(seg.split("trades")[0].split(":")[1].strip())
                        except: pass
                    if "PnL: $" in seg:
                        try: r["pnl"] = float(seg.split("$")[1].strip())
                        except: pass
            if "Sharpe" in line and "annualized" in line:
                try: r["sharpe"] = float(line.split(":")[1].strip())
                except: pass
            if "Win rate:" in line:
                try: r["wr"] = float(line.split("Win rate:")[1].split("%")[0].strip())
                except: pass
            if "TOTAL:" in line and "$" in line:
                try: r["total_pnl"] = float(line.split("TOTAL: $")[1].strip())
                except: pass
        # Parse exit stats
        in_exits = False
        exits = {}
        for line in text.split("\n"):
            if "EXIT REASONS" in line or "EXIT FUNCTIONS" in line:
                in_exits = True; continue
            if in_exits and "===" in line:
                in_exits = False; continue
            if in_exits and "n=" in line and "PnL:" in line:
                parts = line.strip().split()
                try:
                    ename = parts[0]
                    n = int(parts[1].replace("n=", ""))
                    pnl = float(parts[3].replace("$", "").replace(",", ""))
                    exits[ename] = {"n": n, "pnl": pnl}
                except: pass
        r["exits"] = exits
    except Exception as e:
        r["parse_error"] = str(e)
    return r


def print_results(results, mode):
    """Print ranked leaderboard."""
    valid = [r for r in results if "pnl" in r and r.get("mode") == mode]
    if not valid:
        logger.info(f"  No valid results for {mode}")
        return
    valid.sort(key=lambda x: x.get("pnl", -99999), reverse=True)
    baseline_pnl = next((r["pnl"] for r in valid if "baseline" in r["name"]), 0)
    logger.info(f"\n{'='*80}")
    logger.info(f"  FAST SWEEP RESULTS — {mode.upper()} ({len(valid)} configs)")
    logger.info(f"  Symbols: {len(FAST_TRADIER if mode=='tradier' else FAST_CRYPTO)} | Start: {FAST_START_TRADIER if mode=='tradier' else FAST_START_CRYPTO}")
    logger.info(f"{'='*80}")
    logger.info(f"  {'#':>2} {'Name':30s} {'PnL':>10s} {'vs Base':>10s} {'Sharpe':>7s} {'WR':>6s} {'Trades':>7s} {'Time':>6s}")
    logger.info(f"  {'-'*76}")
    for i, r in enumerate(valid):
        delta = r["pnl"] - baseline_pnl
        delta_str = f"+${delta:.0f}" if delta >= 0 else f"-${abs(delta):.0f}"
        marker = " <<<" if i == 0 and delta > 0 else (" ***" if delta > baseline_pnl * 0.2 else "")
        logger.info(f"  {i+1:>2} {r['name']:30s} ${r.get('pnl',0):>9.2f} {delta_str:>10s} {r.get('sharpe',0):>7.2f} {r.get('wr',0):>5.1f}% {r.get('trades',0):>7d} {r.get('elapsed',0):>5.0f}s{marker}")
    logger.info(f"{'='*80}")
    # Show exit breakdown for top 3
    for r in valid[:3]:
        exits = r.get("exits", {})
        if exits:
            logger.info(f"\n  {r['name']} exits:")
            for ename, edata in sorted(exits.items(), key=lambda x: x[1].get("pnl", 0), reverse=True):
                logger.info(f"    {ename:20s} n={edata['n']:>5d}  PnL=${edata['pnl']:>10.2f}")
    logger.info("")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["tradier", "crypto", "both"], default="both")
    parser.add_argument("--loop", action="store_true", help="Loop continuously")
    args = parser.parse_args()
    cycle = 0
    while True:
        cycle += 1
        logger.info(f"\n{'#'*80}")
        logger.info(f"  FAST SWEEP CYCLE {cycle} — {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")
        logger.info(f"{'#'*80}")
        all_results = []
        if args.mode in ("tradier", "both"):
            logger.info(f"\n[TRADIER] {len(TRADIER_CONFIGS)} configs × {len(FAST_TRADIER)} symbols...")
            for name, overrides in TRADIER_CONFIGS:
                r = run_fast_test("tradier", name, overrides)
                all_results.append(r)
                pnl_str = f"${r.get('pnl', 0):.2f}" if "pnl" in r else r.get("error", "?")
                logger.info(f"  {name:30s} → {pnl_str:>12s}  ({r.get('elapsed',0):.0f}s)")
            print_results(all_results, "tradier")
        if args.mode in ("crypto", "both"):
            logger.info(f"\n[CRYPTO] {len(CRYPTO_CONFIGS)} configs × {len(FAST_CRYPTO)} symbols...")
            for name, overrides in CRYPTO_CONFIGS:
                r = run_fast_test("crypto", name, overrides)
                all_results.append(r)
                pnl_str = f"${r.get('pnl', 0):.2f}" if "pnl" in r else r.get("error", "?")
                logger.info(f"  {name:30s} → {pnl_str:>12s}  ({r.get('elapsed',0):.0f}s)")
            print_results(all_results, "crypto")
        # Save results
        results_file = RESULTS_DIR / f"fast_results_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
        with open(results_file, "w") as f:
            json.dump(all_results, f, indent=2, default=str)
        logger.info(f"Results saved: {results_file}")
        if not args.loop:
            break
        logger.info(f"Cycle {cycle} done. Starting next cycle...")


if __name__ == "__main__":
    main()
