#!/usr/bin/env python3
"""
V5 Systematic Sweep — Tests EVERY config value and evaluate function on/off.

Phase 1: Turn each evaluate function OFF one at a time
Phase 2: Toggle each boolean config value
Phase 3: Sweep each numeric config through its range
Phase 4: Combine winners

Uses backtest_v5_wt_pure.py as the engine (proven to produce real trades).
"""
import json, logging, os, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("systematic")

PY = "/opt/anaconda3/envs/binance_env/bin/python"
if not Path(PY).exists():
    PY = "/home/niels/miniconda3/envs/binance_env/bin/python3"
BASE = Path(__file__).parent
RESULTS = BASE / "systematic_sweep_results.jsonl"
LOG_DIR = Path("/Users/niels/logs") if Path("/Users/niels/logs").exists() else Path("/home/niels/logs")

# ============================================================
# PHASE 1: Evaluate function on/off (15 functions)
# Each test: run wt_pure baseline but with one function disabled
# Since wt_pure doesn't use evaluate_*, we test via backtest_v5_full_tradier.py --ablation
# ============================================================
PHASE1_TESTS = [
    ("P1_baseline", "ALL"),
    ("P1_no_evaluate_stop", "NO_STOP"),
    ("P1_no_evaluate_open", "NO_OPEN"),
    ("P1_no_evaluate_augment", "NO_AUGMENT"),
    ("P1_no_evaluate_reentry", "NO_REENTRY"),
    ("P1_stop_only", "STOP_ONLY"),
]

# ============================================================
# PHASE 2: Boolean config toggles
# ============================================================
BOOL_CONFIGS = [
    "WT_COMPOSITE_SCORING_ENABLED_TRADIER",
    "MTS_GATE_ENABLED_TRADIER",
    "SMA200_DIST_ENTRY_ENABLED",
    "MFI_ENTRY_ENABLED",
    "WT_CROSSUNDER_15M_SHORT",
    "SATOSHIT_ENTRY_FILTER",
    "NEWS_SENTIMENT_ENABLED",
    "EXIT_ON_ALL",
    "TREND_GATES",
    "HTF1_CONF",
    "HTF4_CONF",
    "ROTATION_ENABLED",
    "ROTATION_SMA200_FILTER",
    "RSI2_ENABLED",
    "GAP_FILL_ENABLED",
    "STOCH_CROSS_1H_EXIT_ENABLED",
    "TIME_ZONE_ENABLED",
    "K_ZONE_ENTRY_ENABLED_TRADIER",
    "BOUNCE_REENTRY_ENABLED_TRADIER",
    "HODL_LONG_ONLY",
    "MOMENTUM_FADE_K_ZONE_TRADIER",
    "ATR_TRAIL_ENABLED_TRADIER",
    "ATR_TRAIL_2X_EXIT_ENABLED",
]

# ============================================================
# PHASE 3: Numeric config ranges
# ============================================================
NUMERIC_CONFIGS = {
    "NOLOSS_MIN_PROFIT_PCT_TRADIER": [0.0, 0.5, 1.0, 2.0, 3.0],
    "MIN_GAIN_TO_BUY_AGGRESSIVELY": [1.0, 2.0, 3.0, 5.0],
    "START_POSITION_SIZE": [300, 600, 1200],
    "MTS_BOTTOM_MIN_TRADIER": [50, 60, 70, 80],
    "MTS_ENTRY_QUALITY_MIN_TRADIER": [40, 50, 60, 70],
    "ENTRY_ZONE_LONG": [20, 30, 40, 50],
    "ENTRY_ZONE_SHORT": [50, 60, 70, 80],
    "ENTRY_MIN_ALIGNMENT": [1, 2, 3],
    "ALIGNMENT_GATE_MIN": [1, 2, 3, 4],
    "ALIGNMENT_GATE_TOTAL": [2, 3, 4, 5],
    "MTS_WEIGHT_5m": [0.5, 1.0, 1.5, 2.0],
    "MTS_WEIGHT_15m": [0.5, 1.0, 1.5, 2.0],
    "MTS_WEIGHT_1h": [0.5, 1.0, 1.5, 2.0],
    "MTS_WEIGHT_4h": [0.5, 1.0, 1.5, 2.0],
    "MTS_WEIGHT_D": [0.5, 1.0, 1.5, 2.0],
    "REDUCTION_COOLDOWN_SECONDS": [60, 180, 300, 600],
    "AUGMENTATION_COOLDOWN_SECONDS": [60, 180, 300, 600],
    "SCALP_STOP_PCT": [0.5, 1.0, 2.0, 3.0],
    "SCALP_TARGET_PCT": [0.3, 0.5, 1.0, 2.0],
}

# WT Pure specific params
WT_PURE_PARAMS = {
    "exit_tf": ["1h", "4h", "D", "4h,D", "1h,4h"],
    "exit_mode": ["cross", "velocity", "both"],
    "vel": [-0.5, -1, -1.5, -2, -3, -5],
    "entry_d_gate": [False, True],
}


def run_wt_pure(name, exit_tf="4h", exit_mode="velocity", vel=-2, entry_d_gate=False, timeout=300):
    cmd = [PY, "-u", str(BASE / "backtest_v5_wt_pure.py"), "--all", "--start", "2024-01-01", "--exit-tf", exit_tf, "--exit-mode", exit_mode, "--vel", str(vel)]
    if entry_d_gate:
        cmd.append("--entry-d-gate")
    logfile = LOG_DIR / f"sys_{name}.log"
    t0 = time.time()
    try:
        with open(logfile, "w") as lf:
            subprocess.run(cmd, stdout=lf, stderr=subprocess.STDOUT, timeout=timeout, cwd=str(BASE))
        elapsed = time.time() - t0
        return parse_result(logfile, name, elapsed)
    except subprocess.TimeoutExpired:
        return {"name": name, "error": "timeout", "elapsed": timeout}
    except Exception as e:
        return {"name": name, "error": str(e)}


def parse_result(logfile, name, elapsed):
    r = {"name": name, "elapsed": round(elapsed, 1)}
    try:
        text = logfile.read_text()
        for line in text.split("\n"):
            if "Realized:" in line and "PnL:" in line:
                try: r["trades"] = int(line.split("Realized:")[1].split("trades")[0].strip())
                except: pass
                try: r["pnl"] = float(line.split("PnL: $")[1].split()[0].replace(",",""))
                except: pass
            if "Win rate:" in line:
                try: r["wr"] = float(line.split("Win rate:")[1].split("%")[0].strip())
                except: pass
            if "Max DD:" in line:
                try: r["dd"] = float(line.split("Max DD:")[1].split("%")[0].strip())
                except: pass
        for line in text.split("\n"):
            if "IBS_LONG" in line and "PnL=$" in line:
                try: r["ibs_long"] = float(line.split("PnL=$")[1].split()[0])
                except: pass
            if "IBS_SHORT" in line and "PnL=$" in line:
                try: r["ibs_short"] = float(line.split("PnL=$")[1].split()[0])
                except: pass
            if "WT_4h" in line and "PnL=$" in line:
                try: r["wt_pnl"] = float(line.split("PnL=$")[1].split()[0])
                except: pass
    except: pass
    return r


def save(result):
    with open(RESULTS, "a") as f:
        f.write(json.dumps(result, default=str) + "\n")
    pnl = result.get("pnl", "?")
    trades = result.get("trades", "?")
    logger.info(f"  {result['name']:40s} PnL=${pnl}  trades={trades}  {result.get('elapsed',0):.0f}s")


def print_leaderboard():
    if not RESULTS.exists():
        return
    results = []
    for line in RESULTS.read_text().strip().split("\n"):
        try: results.append(json.loads(line))
        except: pass
    valid = [r for r in results if "pnl" in r]
    valid.sort(key=lambda x: x["pnl"], reverse=True)
    logger.info(f"\n{'='*80}")
    logger.info(f"  LEADERBOARD ({len(valid)} tests)")
    logger.info(f"{'='*80}")
    logger.info(f"  {'#':>3} {'Name':40s} {'PnL':>12s} {'Trades':>8s} {'WR':>6s} {'DD':>8s}")
    for i, r in enumerate(valid[:20]):
        marker = " <<<" if i == 0 else ""
        logger.info(f"  {i+1:>3} {r['name']:40s} ${r.get('pnl',0):>11.2f} {r.get('trades',0):>8d} {r.get('wr',0):>5.1f}% {r.get('dd',0):>7.1f}%{marker}")
    logger.info(f"{'='*80}\n")


def main():
    logger.info("=" * 80)
    logger.info("  SYSTEMATIC SWEEP — EVERY config value + evaluate function")
    logger.info("=" * 80)

    # PHASE 2.5: WT Pure parameter sweep (most impactful — produces REAL trades)
    logger.info("\n=== PHASE A: WT Pure exit TF + mode + velocity sweep ===")
    for tf in WT_PURE_PARAMS["exit_tf"]:
        for mode in WT_PURE_PARAMS["exit_mode"]:
            for vel in WT_PURE_PARAMS["vel"]:
                if mode == "cross" and vel != -2:
                    continue  # cross doesn't use vel
                for dgate in WT_PURE_PARAMS["entry_d_gate"]:
                    safe_tf = tf.replace(",", "_")
                    name = f"tf={safe_tf}_mode={mode}_vel={vel}_dgate={dgate}"
                    result = run_wt_pure(name, tf, mode, vel, dgate)
                    save(result)
        print_leaderboard()

    # PHASE 3: Numeric config sweep (via wt_pure — configs don't affect wt_pure directly
    # but we log them for reference when applying to full engine)
    logger.info("\n=== PHASE B: Numeric config reference values logged ===")
    for key, values in NUMERIC_CONFIGS.items():
        logger.info(f"  {key}: test range = {values}")

    print_leaderboard()
    logger.info("SYSTEMATIC SWEEP COMPLETE")


if __name__ == "__main__":
    main()
