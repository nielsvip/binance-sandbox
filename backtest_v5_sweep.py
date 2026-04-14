#!/usr/bin/env python3
"""
V5 Sweep — Run the ACTUAL engine with different config combinations.

Tests each BACKTEST_CHANGE (BC_100-BC_153) ON vs OFF and measures real PnL.
Runs on crypto (MacBook) or tradier (server).

Usage:
    python3 backtest_v5_sweep.py --mode crypto --start 2025-01-01
    python3 backtest_v5_sweep.py --mode tradier --start 2024-06-01
"""

import argparse
import asyncio
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
    BASE_PATH = Path("/home/niels/binance-sandbox")
    PYTHON = "/home/niels/.conda/envs/binance_env/bin/python3"

V5_DIR = BASE_PATH / "backtest_v5"
SWEEP_DIR = V5_DIR / "sweeps"

# Config variations to test — each is a dict of config overrides
SWEEP_CONFIGS = {
    "baseline": {},
    "BC100_off_entry_vol": {"ENTRY_VOL_MIN_RATIO": 1.0},
    "BC100_on_entry_vol_1.3": {"ENTRY_VOL_MIN_RATIO": 1.3},
    "BC103_off_atr_min": {"ENTRY_ATR_PCT_MIN": 0.0},
    "BC103_on_atr_min_1.5": {"ENTRY_ATR_PCT_MIN": 0.015},
    "BC111_off_rsi_gate": {"RSI_ENTRY_GATE_ENABLED": False},
    "BC111_on_rsi_gate": {"RSI_ENTRY_GATE_ENABLED": True},
    "BC113_off_stop_major": {"STOP_MAJOR_LOSS_BLOCK": False},
    "BC113_on_stop_major": {"STOP_MAJOR_LOSS_BLOCK": True},
    "BC150_off_compression": {"COMPRESSION_BREAKOUT_ENABLED": False},
    "BC150_on_compression": {"COMPRESSION_BREAKOUT_ENABLED": True},
    "BC151_augment_3pct": {"MIN_GAIN_TO_BUY_AGGRESSIVELY": 3.0},
    "BC151_augment_5pct": {"MIN_GAIN_TO_BUY_AGGRESSIVELY": 5.0},
    "BC152_off_fast_reentry": {"FAST_REENTRY_ENABLED": False},
    "BC152_on_fast_reentry": {"FAST_REENTRY_ENABLED": True},
    "WT_vel_-4": {"WT_EXIT_VEL_THRESHOLD": -4.0},
    "WT_vel_-6": {"WT_EXIT_VEL_THRESHOLD": -6.0},
    "WT_vel_-8": {"WT_EXIT_VEL_THRESHOLD": -8.0},
    "NOLOSS_0.30": {"NOLOSS_MIN_PROFIT_PCT": 0.30},
    "NOLOSS_0.50": {"NOLOSS_MIN_PROFIT_PCT": 0.50},
    "NOLOSS_1.00": {"NOLOSS_MIN_PROFIT_PCT": 1.00},
    "MTS_bottom_10": {"MTS_BOTTOM_MIN": 10.0},
    "MTS_bottom_15": {"MTS_BOTTOM_MIN": 15.0},
    "MTS_bottom_20": {"MTS_BOTTOM_MIN": 20.0},
    "HOLD_BARS_15": {"OPTIMAL_HOLD_BARS_3M": 15},
    "HOLD_BARS_21": {"OPTIMAL_HOLD_BARS_3M": 21},
    "HOLD_BARS_30": {"OPTIMAL_HOLD_BARS_3M": 30},
    "ACCOUNT_TP_1.5": {"ACCOUNT_TP_PCT": {"ang": 1.5}},
    "ACCOUNT_TP_3.0": {"ACCOUNT_TP_PCT": {"ang": 3.0}},
    "ACCOUNT_TP_5.0": {"ACCOUNT_TP_PCT": {"ang": 5.0}},
    # BC_155: Trader Research HARD GATES (2026-03-30 — OOS validated)
    "BC155a_off_adx4h": {"TR_ADX4H_GATE_ENABLED": False},
    "BC155a_adx4h_max16": {"TR_ADX4H_GATE_ENABLED": True, "TR_ADX4H_MAX": 16.0, "TR_ADX4H_BOYCOTT_SCORE": -40},
    "BC155a_adx4h_max20": {"TR_ADX4H_GATE_ENABLED": True, "TR_ADX4H_MAX": 20.0, "TR_ADX4H_BOYCOTT_SCORE": -40},
    "BC155a_adx4h_max25": {"TR_ADX4H_GATE_ENABLED": True, "TR_ADX4H_MAX": 25.0, "TR_ADX4H_BOYCOTT_SCORE": -30},
    "BC155b_off_bbw4h": {"TR_BBWIDTH4H_GATE_ENABLED": False},
    "BC155b_bbw4h_max8": {"TR_BBWIDTH4H_GATE_ENABLED": True, "TR_BBWIDTH4H_MAX": 8.0, "TR_BBWIDTH4H_BOYCOTT_SCORE": -35},
    "BC155b_bbw4h_max10": {"TR_BBWIDTH4H_GATE_ENABLED": True, "TR_BBWIDTH4H_MAX": 10.0, "TR_BBWIDTH4H_BOYCOTT_SCORE": -35},
    "BC155b_bbw4h_max12": {"TR_BBWIDTH4H_GATE_ENABLED": True, "TR_BBWIDTH4H_MAX": 12.0, "TR_BBWIDTH4H_BOYCOTT_SCORE": -25},
    "BC155c_off_chop4h": {"TR_CHOP4H_GATE_ENABLED": False},
    "BC155c_chop4h_min45": {"TR_CHOP4H_GATE_ENABLED": True, "TR_CHOP4H_MIN": 45.0},
    "BC155c_chop4h_min50": {"TR_CHOP4H_GATE_ENABLED": True, "TR_CHOP4H_MIN": 50.0},
    "BC155c_chop4h_min55": {"TR_CHOP4H_GATE_ENABLED": True, "TR_CHOP4H_MIN": 55.0},
    "BC155d_off_mfi4h_long": {"TR_MFI4H_LONG_ENABLED": False},
    "BC155d_mfi4h_long35": {"TR_MFI4H_LONG_ENABLED": True, "TR_MFI4H_LONG_MIN": 35.0},
    "BC155d_mfi4h_long40": {"TR_MFI4H_LONG_ENABLED": True, "TR_MFI4H_LONG_MIN": 40.0},
    "BC155d_mfi4h_long45": {"TR_MFI4H_LONG_ENABLED": True, "TR_MFI4H_LONG_MIN": 45.0},
    "BC155e_off_dcw4h_short": {"TR_DCWIDTH4H_SHORT_ENABLED": False},
    "BC155e_dcw4h_short12": {"TR_DCWIDTH4H_SHORT_ENABLED": True, "TR_DCWIDTH4H_SHORT_MAX": 12.0},
    "BC155e_dcw4h_short15": {"TR_DCWIDTH4H_SHORT_ENABLED": True, "TR_DCWIDTH4H_SHORT_MAX": 15.0},
    "BC155_stacked_tight": {"TR_ADX4H_GATE_ENABLED": True, "TR_ADX4H_MAX": 16.0, "TR_BBWIDTH4H_GATE_ENABLED": True, "TR_BBWIDTH4H_MAX": 8.0},
    "BC155_stacked_conservative": {"TR_ADX4H_GATE_ENABLED": True, "TR_ADX4H_MAX": 20.0, "TR_BBWIDTH4H_GATE_ENABLED": True, "TR_BBWIDTH4H_MAX": 10.0},
    "BC155_all_off": {"TR_ADX4H_GATE_ENABLED": False, "TR_BBWIDTH4H_GATE_ENABLED": False, "TR_CHOP4H_GATE_ENABLED": False, "TR_MFI4H_LONG_ENABLED": False, "TR_DCWIDTH4H_SHORT_ENABLED": False},
    "BC155_all_on": {"TR_ADX4H_GATE_ENABLED": True, "TR_BBWIDTH4H_GATE_ENABLED": True, "TR_CHOP4H_GATE_ENABLED": True, "TR_MFI4H_LONG_ENABLED": True, "TR_DCWIDTH4H_SHORT_ENABLED": True},
    # YouTube strategies (2026-03-27) — MUST validate with real evaluate functions before re-enabling on trb
    "YT_clenow_off": {"CLENOW_ENABLED": False},
    "YT_clenow_on": {"CLENOW_ENABLED": True},
    "YT_clenow_score8": {"CLENOW_ENABLED": True, "CLENOW_MIN_SCORE": 8.0},
    "YT_clenow_top10": {"CLENOW_ENABLED": True, "CLENOW_TOP_N": 10},
    "YT_smfi_off": {"SMFI_ENABLED": False},
    "YT_smfi_on": {"SMFI_ENABLED": True},
    "YT_smfi_hold5": {"SMFI_ENABLED": True, "SMFI_MAX_HOLD_DAYS": 5},
    "YT_smfi_hold15": {"SMFI_ENABLED": True, "SMFI_MAX_HOLD_DAYS": 15},
    "YT_minervini_off": {"MINERVINI_ENABLED": False},
    "YT_minervini_on": {"MINERVINI_ENABLED": True},
    "YT_minervini_sepa4": {"MINERVINI_ENABLED": True, "MINERVINI_MIN_SEPA_SCORE": 4},
    "YT_minervini_sepa6": {"MINERVINI_ENABLED": True, "MINERVINI_MIN_SEPA_SCORE": 6},
    "YT_connors_off": {"CONNORS_RSI_ENABLED": False},
    "YT_connors_on": {"CONNORS_RSI_ENABLED": True},
    "YT_connors_entry5": {"CONNORS_RSI_ENABLED": True, "CONNORS_RSI_ENTRY_THRESHOLD": 5.0},
    "YT_connors_entry15": {"CONNORS_RSI_ENABLED": True, "CONNORS_RSI_ENTRY_THRESHOLD": 15.0},
    "YT_orb_off": {"ORB_ENABLED": False},
    "YT_orb_on": {"ORB_ENABLED": True},
    "YT_episodic_off": {"EPISODIC_PIVOT_ENABLED": False},
    "YT_episodic_on": {"EPISODIC_PIVOT_ENABLED": True},
    "YT_all_off": {"CLENOW_ENABLED": False, "SMFI_ENABLED": False, "MINERVINI_ENABLED": False, "CONNORS_RSI_ENABLED": False, "ORB_ENABLED": False, "EPISODIC_PIVOT_ENABLED": False},
    "YT_all_on": {"CLENOW_ENABLED": True, "SMFI_ENABLED": True, "MINERVINI_ENABLED": True, "CONNORS_RSI_ENABLED": True, "ORB_ENABLED": True, "EPISODIC_PIVOT_ENABLED": True},
    # ===== PART 15: NOLOSS DOGMA SWEEP (2026-03-31) =====
    # Test: abandon STRICT_NO_LOSS, pure WT exits, WT augmentation
    "P15_BASELINE": {},
    "P15_NOLOSS_OFF": {"NOLOSS_MIN_PROFIT_PCT": 0.0, "STRICT_NO_LOSS_ACCOUNTS": []},
    "P15_NOLOSS_OFF_WT_AUG": {"NOLOSS_MIN_PROFIT_PCT": 0.0, "STRICT_NO_LOSS_ACCOUNTS": [], "AUGMENT_ONLY_WHEN_PROFITABLE": False, "AUGMENT_WT_GATE": True},
    "P15_PURE_WT": {"NOLOSS_MIN_PROFIT_PCT": -999.0, "STRICT_NO_LOSS_ACCOUNTS": [], "AUGMENT_ONLY_WHEN_PROFITABLE": False, "AUGMENT_WT_GATE": True, "EXIT_GAIN_GATE_ENABLED": False},
    "P15_PURE_WT_TP05": {"NOLOSS_MIN_PROFIT_PCT": -999.0, "STRICT_NO_LOSS_ACCOUNTS": [], "AUGMENT_ONLY_WHEN_PROFITABLE": False, "AUGMENT_WT_GATE": True, "EXIT_GAIN_GATE_ENABLED": False, "ACCOUNT_TP_PCT": {"ang": 0.5, "inf": 0.5, "men": 0.5, "flz": 0.5, "fin": 0.5}},
    "P15_PURE_WT_TP10": {"NOLOSS_MIN_PROFIT_PCT": -999.0, "STRICT_NO_LOSS_ACCOUNTS": [], "AUGMENT_ONLY_WHEN_PROFITABLE": False, "AUGMENT_WT_GATE": True, "EXIT_GAIN_GATE_ENABLED": False, "ACCOUNT_TP_PCT": {"ang": 1.0, "inf": 1.0, "men": 1.0, "flz": 1.0, "fin": 1.0}},
    "P15_TP_ONLY_05": {"NOLOSS_MIN_PROFIT_PCT": -999.0, "STRICT_NO_LOSS_ACCOUNTS": [], "WT_EXIT_ENABLED": False, "ACCOUNT_TP_PCT": {"ang": 0.5, "inf": 0.5, "men": 0.5, "flz": 0.5, "fin": 0.5}},
    "P15_TP_ONLY_10": {"NOLOSS_MIN_PROFIT_PCT": -999.0, "STRICT_NO_LOSS_ACCOUNTS": [], "WT_EXIT_ENABLED": False, "ACCOUNT_TP_PCT": {"ang": 1.0, "inf": 1.0, "men": 1.0, "flz": 1.0, "fin": 1.0}},
    "P15_WT_IBS": {"NOLOSS_MIN_PROFIT_PCT": -999.0, "STRICT_NO_LOSS_ACCOUNTS": [], "AUGMENT_ONLY_WHEN_PROFITABLE": False, "AUGMENT_WT_GATE": True, "EXIT_GAIN_GATE_ENABLED": False, "IBS_EXIT_ENABLED": True},
    "P15_HYBRID": {"NOLOSS_MIN_PROFIT_PCT": 0.1, "STRICT_NO_LOSS_ACCOUNTS": [], "AUGMENT_ONLY_WHEN_PROFITABLE": False, "AUGMENT_WT_GATE": True, "EXIT_GAIN_GATE_ENABLED": False, "ACCOUNT_TP_PCT": {"ang": 0.5, "inf": 0.5, "men": 0.5, "flz": 0.5, "fin": 0.5}},
    # ===== BC_162: SPIKE FADE SWEEP — P&D contrarian fading =====
    "SF_OFF": {"CRYPTO_SPIKE_FADE_ENABLED": False},
    "SF_3pct_10bar": {"CRYPTO_SPIKE_FADE_ENABLED": True, "CRYPTO_SPIKE_FADE_THRESHOLD_PCT": 3.0, "CRYPTO_SPIKE_FADE_LOOKBACK_BARS": 10},
    "SF_3pct_20bar": {"CRYPTO_SPIKE_FADE_ENABLED": True, "CRYPTO_SPIKE_FADE_THRESHOLD_PCT": 3.0, "CRYPTO_SPIKE_FADE_LOOKBACK_BARS": 20},
    "SF_5pct_10bar": {"CRYPTO_SPIKE_FADE_ENABLED": True, "CRYPTO_SPIKE_FADE_THRESHOLD_PCT": 5.0, "CRYPTO_SPIKE_FADE_LOOKBACK_BARS": 10},
    "SF_5pct_20bar": {"CRYPTO_SPIKE_FADE_ENABLED": True, "CRYPTO_SPIKE_FADE_THRESHOLD_PCT": 5.0, "CRYPTO_SPIKE_FADE_LOOKBACK_BARS": 20},
    "SF_2pct_10bar": {"CRYPTO_SPIKE_FADE_ENABLED": True, "CRYPTO_SPIKE_FADE_THRESHOLD_PCT": 2.0, "CRYPTO_SPIKE_FADE_LOOKBACK_BARS": 10},
    "SF_2pct_6bar": {"CRYPTO_SPIKE_FADE_ENABLED": True, "CRYPTO_SPIKE_FADE_THRESHOLD_PCT": 2.0, "CRYPTO_SPIKE_FADE_LOOKBACK_BARS": 6},
    "SF_3pct_10bar_max12": {"CRYPTO_SPIKE_FADE_ENABLED": True, "CRYPTO_SPIKE_FADE_THRESHOLD_PCT": 3.0, "CRYPTO_SPIKE_FADE_LOOKBACK_BARS": 10, "CRYPTO_SPIKE_FADE_MAX_POSITIONS": 12},
    "SF_5pct_10bar_noloss_off": {"CRYPTO_SPIKE_FADE_ENABLED": True, "CRYPTO_SPIKE_FADE_THRESHOLD_PCT": 5.0, "CRYPTO_SPIKE_FADE_LOOKBACK_BARS": 10, "NOLOSS_MIN_PROFIT_PCT": -999.0, "STRICT_NO_LOSS_ACCOUNTS": []},
}


def run_single_config(config_name: str, overrides: dict, mode: str, symbols: str, start: str, capital: float, seed_file: str = ""):
    """Run one engine instance with specific config overrides."""
    # Write config overrides to temp file
    override_path = SWEEP_DIR / f"override_{config_name}.json"
    with open(override_path, "w") as f:
        json.dump(overrides, f)
    # Build command
    cmd = [PYTHON, str(BASE_PATH / "backtest_v5_engine.py"), "--mode", mode, "--symbols", symbols, "--start", start, "--capital", str(capital)]
    if seed_file:
        cmd.extend(["--seed-positions", seed_file])
    # Set env var for config overrides
    env = os.environ.copy()
    env["V5_CONFIG_OVERRIDES"] = str(override_path)
    log_path = SWEEP_DIR / f"sweep_{mode}_{config_name}.log"
    logger.info(f"Running {config_name}: {overrides}")
    t0 = time.time()
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600, env=env)  # 1 hour timeout
        elapsed = time.time() - t0
        # Parse results from output
        output = result.stdout + result.stderr
        with open(log_path, "w") as f:
            f.write(output)
        # Find the JSONL output file
        trades_file = None
        for line in output.split("\n"):
            if "Wrote" in line and ".jsonl" in line:
                parts = line.split()
                for p in parts:
                    if p.endswith(".jsonl"):
                        trades_file = p
        # Parse trades JSONL for real stats
        n_trades = 0; n_opens = 0; n_closes = 0; n_wins = 0; n_losses = 0; total_pnl = 0.0
        n_sf_trades = 0; sf_pnl = 0.0; n_sf_wins = 0
        if trades_file and os.path.exists(trades_file):
            with open(trades_file) as f:
                for line in f:
                    try:
                        t = json.loads(line)
                        n_trades += 1
                        action = t.get('action', '')
                        reason = t.get('reason', '')
                        pnl = float(t.get('pnl', 0) or t.get('realized_pnl', 0) or 0)
                        is_sf = 'SPIKE_FADE' in reason or 'SF_EXHAUST' in reason
                        if action in ('OPEN', 'REENTRY'): n_opens += 1
                        if action in ('CLOSE', 'REDUCE', 'QUICK_CLOSE'):
                            n_closes += 1; total_pnl += pnl
                            if pnl > 0: n_wins += 1
                            elif pnl < 0: n_losses += 1
                            if is_sf: n_sf_trades += 1; sf_pnl += pnl; n_sf_wins += (1 if pnl > 0 else 0)
                    except: pass
        # Parse equity/Sharpe from engine output — also try summary JSON
        final_equity = 0.0; sharpe = 0.0; max_dd = 0.0
        for line in output.split("\n"):
            if "Final equity:" in line:
                try: final_equity = float(line.split("$")[-1].strip().split()[0].replace(",",""))
                except: pass
            if "Sharpe:" in line:
                try: sharpe = float(line.split(":")[-1].strip())
                except: pass
            if "Max Drawdown:" in line:
                try: max_dd = float(line.split(":")[-1].strip().replace("%",""))
                except: pass
            if "Total actions" in line:
                logger.info(f"  {config_name}: {line.strip()}")
        # Fallback: parse summary JSON if engine wrote one
        import glob as _glob
        for sj in sorted(_glob.glob(str(V5_DIR / "logs" / f"summary_*.json")), reverse=True)[:1]:
            try:
                sd = json.loads(open(sj).read())
                if not final_equity: final_equity = sd.get('final_equity', 0)
                if not sharpe: sharpe = sd.get('sharpe', 0)
                if not max_dd: max_dd = sd.get('max_drawdown_pct', 0)
                if not n_wins: n_wins = sd.get('winning_closes', 0); n_losses = sd.get('losing_closes', 0); n_closes = n_wins + n_losses; wr = (n_wins / n_closes * 100) if n_closes else 0
            except: pass
        wr = (n_wins / n_closes * 100) if n_closes > 0 else 0
        sf_wr = (n_sf_wins / n_sf_trades * 100) if n_sf_trades > 0 else 0
        r = {"config": config_name, "overrides": overrides, "trades": n_trades, "opens": n_opens, "closes": n_closes, "wins": n_wins, "losses": n_losses, "win_rate": round(wr, 1), "total_pnl": round(total_pnl, 2), "sharpe": round(sharpe, 3), "max_dd": round(max_dd, 2), "final_equity": round(final_equity, 2), "sf_trades": n_sf_trades, "sf_pnl": round(sf_pnl, 2), "sf_wr": round(sf_wr, 1), "elapsed": round(elapsed, 1), "trades_file": trades_file, "log_file": str(log_path)}
        logger.info(f"  {config_name}: {n_closes} closes, WR={wr:.1f}%, PnL=${total_pnl:+.2f}, SF={n_sf_trades} trades/${sf_pnl:+.2f} ({elapsed:.0f}s)")
        return r
    except subprocess.TimeoutExpired:
        logger.error(f"  {config_name}: TIMEOUT")
        return {"config": config_name, "overrides": overrides, "trades": 0, "elapsed": 600, "error": "TIMEOUT"}
    except Exception as e:
        logger.error(f"  {config_name}: ERROR {e}")
        return {"config": config_name, "overrides": overrides, "trades": 0, "elapsed": 0, "error": str(e)}


def main():
    parser = argparse.ArgumentParser(description="V5 Sweep")
    parser.add_argument("--mode", choices=["crypto", "tradier"], required=True)
    parser.add_argument("--start", type=str, default=None)
    parser.add_argument("--symbols", type=str, default="")
    parser.add_argument("--capital", type=float, default=None)
    parser.add_argument("--seed-positions", type=str, default="")
    parser.add_argument("--configs", type=str, default="all", help="Comma-separated config names or 'all'")
    args = parser.parse_args()
    if args.start is None:
        args.start = "2025-01-01" if args.mode == "crypto" else "2024-06-01"
    if args.capital is None:
        args.capital = 10000.0 if args.mode == "crypto" else 70000.0
    if not args.symbols:
        npz_dir = BASE_PATH / "backtest_v4" / "indicators" if args.mode == "crypto" else BASE_PATH / "backtest_v4_tradier" / "indicators"
        args.symbols = ",".join(sorted([p.stem for p in npz_dir.glob("*.npz")]))
    SWEEP_DIR.mkdir(parents=True, exist_ok=True)
    if args.configs == "all":
        configs_to_run = SWEEP_CONFIGS
    else:
        names = [n.strip() for n in args.configs.split(",")]
        configs_to_run = {n: SWEEP_CONFIGS[n] for n in names if n in SWEEP_CONFIGS}
    results = []
    for name, overrides in configs_to_run.items():
        r = run_single_config(name, overrides, args.mode, args.symbols, args.start, args.capital, args.seed_positions)
        results.append(r)
    # Summary table
    print("\n" + "=" * 120)
    print(f"  V5 SWEEP RESULTS — {args.mode.upper()} — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 120)
    print(f"  {'Config':<28s} {'Closes':>7s} {'WR%':>6s} {'PnL($)':>10s} {'Sharpe':>7s} {'MaxDD%':>7s} {'SF_Trades':>9s} {'SF_PnL':>9s} {'SF_WR%':>6s} {'Time':>5s}")
    print("-" * 120)
    for r in sorted(results, key=lambda x: -x.get("total_pnl", 0)):
        c = r.get
        print(f"  {c('config','?'):<28s} {c('closes',0):>7d} {c('win_rate',0):>5.1f}% {c('total_pnl',0):>+9.2f} {c('sharpe',0):>7.3f} {c('max_dd',0):>6.2f}% {c('sf_trades',0):>9d} {c('sf_pnl',0):>+8.2f} {c('sf_wr',0):>5.1f}% {c('elapsed',0):>4.0f}s")
    print("=" * 120)
    # Save JSON
    results_path = SWEEP_DIR / f"sweep_results_{args.mode}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
    with open(results_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    # Save CSV to data/sweep_results/ (centralized per CLAUDE.md Part 15 rules)
    csv_dir = BASE_PATH / "data" / "sweep_results"
    csv_dir.mkdir(parents=True, exist_ok=True)
    csv_path = csv_dir / f"sweep_{args.mode}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
    csv_cols = ["config", "closes", "win_rate", "total_pnl", "sharpe", "max_dd", "final_equity", "sf_trades", "sf_pnl", "sf_wr", "opens", "wins", "losses", "elapsed"]
    with open(csv_path, "w") as f:
        f.write(",".join(csv_cols) + "\n")
        for r in sorted(results, key=lambda x: -x.get("total_pnl", 0)):
            f.write(",".join(str(r.get(c, "")) for c in csv_cols) + "\n")
    logger.info(f"JSON: {results_path}")
    logger.info(f"CSV:  {csv_path}")
    print(f"\n  Results: {csv_path}")


if __name__ == "__main__":
    main()
