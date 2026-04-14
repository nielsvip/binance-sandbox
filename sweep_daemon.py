#!/usr/bin/env python3
"""
Sweep Daemon — Self-improving tournament engine for V8 backtests.

Replaces: sweep_orchestrator.py, v5_continuous_sweep.py, manual SERVER_LOCKS.md

Features:
  1. DEDUP: Hash-based — never re-runs a config
  2. TOURNAMENT: Winners breed winners (crossover + mutation + midpoints)
  3. AUTO-RESTART: systemd-friendly loop, picks up where it left off
  4. AUTO-APPLY: Twice daily, writes winning params to config.py / config_tradier.py
  5. COORDINATION: Claim-based per-machine, no manual locks

Usage:
    # On any machine (local, server1, server2):
    python3 sweep_daemon.py --machine local --mode tradier --workers 4
    python3 sweep_daemon.py --machine server1 --mode tradier --workers 6
    python3 sweep_daemon.py --machine server1 --mode crypto --workers 6

    # Status check:
    python3 sweep_daemon.py --status

    # Import existing CSV results:
    python3 sweep_daemon.py --import-csv

    # Force breed next generation:
    python3 sweep_daemon.py --breed --mode tradier

    # Force apply best config now:
    python3 sweep_daemon.py --apply-now --mode tradier
"""

import argparse
import copy
import csv
import hashlib
import itertools
import json
import logging
import os
import platform
import random
import re
import signal
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("sweep_daemon")
logs_dir = Path.home() / "logs"
logs_dir.mkdir(parents=True, exist_ok=True)
fh = RotatingFileHandler(str(logs_dir / "sweep_daemon.log"), maxBytes=50 * 1024 * 1024, backupCount=3)
fh.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(levelname)s %(message)s"))
logger.addHandler(fh)

IS_SERVER = platform.system() == "Linux"
if IS_SERVER:
    BASE_PATH = Path("/home/niels/binance-sandbox")
    SCRIPTS_DIR = Path("/home/niels/binance")
    PYTHON = str(next(
        (p for p in [
            Path("/home/niels/.conda/envs/binance_env/bin/python3"),
            Path("/home/niels/miniconda3/envs/binance_env/bin/python"),
        ] if p.exists()),
        "python3"
    ))
else:
    BASE_PATH = Path("/Users/niels/Documents/binance")
    SCRIPTS_DIR = BASE_PATH
    PYTHON = "/opt/anaconda3/envs/binance_env/bin/python"

ENGINE = SCRIPTS_DIR / "backtest_v8_engine.py"
SWEEP_DIR = BASE_PATH / "backtest_v8" / "sweeps"
SWEEP_DIR.mkdir(parents=True, exist_ok=True)

# Config files (only written on local MacBook, never on servers)
CONFIG_CRYPTO = SCRIPTS_DIR / "config.py"
CONFIG_TRADIER = SCRIPTS_DIR / "config_tradier.py"
BACKUPS_DIR = SCRIPTS_DIR / "backups"
BACKUPS_DIR.mkdir(parents=True, exist_ok=True)

TIMEOUT_PER_CONFIG = 7200  # 2h max per config

# ═══════════════════════════════════════════════════════════════
# FAST SYMBOL SETS (12 representative per market)
# ═══════════════════════════════════════════════════════════════
FAST_SYMBOLS = {
    "tradier": "AAPL,MSFT,NVDA,AMZN,JPM,XOM,ABBV,TSLA,SPY,META,BA,GLD",
    "crypto": "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,ADAUSDT,AVAXUSDT,DOTUSDT,LINKUSDT,MATICUSDT,LTCUSDT,UNIUSDT",
}

# ═══════════════════════════════════════════════════════════════
# AUTO-APPLY WINDOWS (UTC hours) — twice daily
# ═══════════════════════════════════════════════════════════════
APPLY_WINDOWS_UTC = [9, 21]  # 09:00 UTC (5am ET) and 21:00 UTC (5pm ET)
APPLY_MIN_IMPROVEMENT_PCT = 10.0  # Must beat current baseline by 10%
APPLY_MIN_TRADES = 50
APPLY_MIN_SHARPE = 1.0

# ═══════════════════════════════════════════════════════════════
# SAFE-TO-CHANGE PARAMS (whitelist for auto-apply)
# Sacred params (augment gate, ratio, etc.) are NEVER touched
# ═══════════════════════════════════════════════════════════════
SAFE_PARAMS_CRYPTO = {
    "DC_BREAKOUT_ENTRY_ENABLED", "DC_BREAKOUT_SCORE", "DC_BREAKOUT_TF",
    "DC_WIDTH_SIZING_ENABLED", "DC_EDGE_SIZING_ENABLED",
    "WT_EXIT_VEL_THRESHOLD", "WT_REDUCE_FRAC_LOW", "WT_REDUCE_FRAC_MED", "WT_REDUCE_FRAC_HIGH",
    "ENTRY_SCORE_MIN",
    "MI_EXIT_ENABLED", "MI_ENTRY_ENABLED", "MI_TF_AGREE_MIN", "MI_MIN_GAIN_EXIT",
    "MI_STRUCT_EXIT_ENABLED", "MI_EXHAUST_EXIT_ENABLED", "MI_DIV_EXIT_ENABLED",
    "MI_VELOCITY_EXIT_ENABLED", "MI_WAVE_EXIT_ENABLED",
    "MI_ENTRY_STRUCT_BONUS", "MI_ENTRY_EXHAUST_BONUS",
}

SAFE_PARAMS_TRADIER = {
    "TRADIER_DC_DAYTRADE_ENABLED", "TRADIER_DC_DAYTRADE_TARGET_PCT",
    "TRADIER_DC_DAYTRADE_STOP_PCT", "TRADIER_DC_DAYTRADE_MAX_HOLD_MINUTES",
    "TRADIER_DC_DAYTRADE_BUFFER",
    "TRADIER_WT_EXIT_TFS_TRADIER", "TRADIER_WT_EXIT_MIN_TFS_TRADIER",
    "TRADIER_WT_COMPOSITE_SCORING_ENABLED_TRADIER",
    "TRADIER_MI_EXIT_ENABLED_TRADIER", "TRADIER_MI_ENTRY_ENABLED_TRADIER",
    "TRADIER_MI_TF_AGREE_MIN_TRADIER", "TRADIER_MI_MIN_GAIN_EXIT_TRADIER",
    "TRADIER_MI_STRUCT_EXIT_ENABLED_TRADIER", "TRADIER_MI_EXHAUST_EXIT_ENABLED_TRADIER",
    "TRADIER_MI_DIV_EXIT_ENABLED_TRADIER", "TRADIER_MI_VELOCITY_EXIT_ENABLED_TRADIER",
    "TRADIER_MI_WAVE_EXIT_ENABLED_TRADIER",
    "TRADIER_K_ZONE_LONG_THRESHOLD_TRADIER", "TRADIER_K_ZONE_SHORT_THRESHOLD_TRADIER",
    "TRADIER_K_ZONE_ENTRY_BONUS_TRADIER",
    "TRADIER_K3M_EXHAUSTION_LOW", "TRADIER_K3M_EXHAUSTION_HIGH",
    "TRADIER_LTF_ALIGNMENT_MIN", "TRADIER_DAILY_ALIGNMENT_MANDATORY",
    "TRADIER_STOCH_ENTRY_LONG_TRADIER", "TRADIER_STOCH_ENTRY_SHORT_TRADIER",
    "TRADIER_STOCH_EXTREME_LONG_TRADIER", "TRADIER_STOCH_EXTREME_SHORT_TRADIER",
    "TRADIER_ENTRY_SCORE_THRESHOLD",
    "TRADIER_RSI_ENTRY_LONG_TRADIER", "TRADIER_RSI_ENTRY_SHORT_TRADIER",
    "TRADIER_RSI2_ENTRY_THRESHOLD", "TRADIER_GAP_FILL_MIN_GAP_PCT",
    "TRADIER_DC_POSITION_ENTRY_THRESHOLD",
    "TRADIER_CONGRESS_CONVICTION_SIZING_BOOST",
    "TRADIER_FH_MOMENTUM_ENABLED", "TRADIER_FH_MOMENTUM_MIN_MOVE_PCT",
    "TRADIER_FH_MOMENTUM_MFI_CONFIRM", "TRADIER_FH_MOMENTUM_DC_CONFIRM",
}

# ═══════════════════════════════════════════════════════════════
# FUNCTION-LEVEL ABLATION CONFIGS
# One config per function: disable it, measure impact vs baseline.
# ═══════════════════════════════════════════════════════════════

ABLATION_FLAGS = [
    # Original 13 (from eval_funcs + main loops)
    "ABLATION_DISABLE_ENTRY_TECHNICAL",
    "ABLATION_DISABLE_ENTRY_LEADERBOARD",
    "ABLATION_DISABLE_ENTRY_RANKING",
    "ABLATION_DISABLE_ENTRY_REVERSAL",
    "ABLATION_DISABLE_REENTRY",
    "ABLATION_DISABLE_AUGMENTATION",
    "ABLATION_DISABLE_FAST_RISER",
    "ABLATION_DISABLE_CHECK_NOLOSS",
    "ABLATION_DISABLE_HEDGE",
    "ABLATION_DISABLE_RATIO_REBALANCE",
    "ABLATION_DISABLE_QUICK_EXIT",
    "ABLATION_DISABLE_QUICK_ENTRY",
    "ABLATION_DISABLE_REENTRY_ENFORCE",
    # 6 newly-added loops (previously missing from V8)
    "ABLATION_DISABLE_HIGH_GAIN_AUGMENT",
    "ABLATION_DISABLE_SPIKE_FADE_EXIT",
    "ABLATION_DISABLE_SCALP_GUARD",
    "ABLATION_DISABLE_DC_BREACH_REDUCE",
    "ABLATION_DISABLE_AGGRESSIVE_HEDGE",
    "ABLATION_DISABLE_PERIODIC_REENTRY",
]

def build_ablation_configs() -> List[Dict]:
    """Build ablation configs: 1 baseline (all enabled) + 1 per function (that one disabled)."""
    configs = []
    # Baseline: everything enabled (all flags False)
    baseline = {flag: False for flag in ABLATION_FLAGS}
    baseline["_ABLATION_NAME"] = "BASELINE"
    configs.append(baseline)
    # One config per function: disable just that one
    for flag in ABLATION_FLAGS:
        cfg = {f: False for f in ABLATION_FLAGS}
        cfg[flag] = True
        cfg["_ABLATION_NAME"] = flag.replace("ABLATION_DISABLE_", "OFF_")
        configs.append(cfg)
    return configs

# ═══════════════════════════════════════════════════════════════
# TIER DEFINITIONS (imported from backtest_v8_sweep.py grids)
# Gen 0 = all tier grids. Gen 1+ = bred from winners.
# ═══════════════════════════════════════════════════════════════

# We import the tier definitions dynamically to avoid duplication
def _load_tier_grids():
    """Load tier grid dicts from backtest_v8_sweep.py by importing the module."""
    try:
        sweep_module_path = SCRIPTS_DIR / "backtest_v8_sweep.py"
        if not sweep_module_path.exists():
            logger.warning(f"backtest_v8_sweep.py not found at {sweep_module_path}")
            return {}, {}
        import importlib.util
        spec = importlib.util.spec_from_file_location("v8_sweep", str(sweep_module_path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        crypto_tiers = {}
        tradier_tiers = {}
        for name in dir(mod):
            obj = getattr(mod, name)
            if name.startswith("CRYPTO_TIER") and isinstance(obj, dict):
                crypto_tiers[name] = obj
            elif name.startswith("TRADIER_TIER") and isinstance(obj, dict) and obj is not None:
                tradier_tiers[name] = obj
        # Fixed-list tiers
        if hasattr(mod, "TRADIER_TIER5_CONFIGS"):
            tradier_tiers["TRADIER_TIER5_CONFIGS"] = mod.TRADIER_TIER5_CONFIGS
        if hasattr(mod, "TRADIER_TIER7_CONFIGS"):
            tradier_tiers["TRADIER_TIER7_CONFIGS"] = mod.TRADIER_TIER7_CONFIGS
        return crypto_tiers, tradier_tiers
    except Exception as e:
        logger.error(f"Failed to load tier grids: {e}")
        return {}, {}


def _build_grid(param_grid: Dict) -> List[Dict]:
    """Cartesian product of a parameter grid dict."""
    keys = list(param_grid.keys())
    values = [param_grid[k] for k in keys]
    configs = []
    for combo in itertools.product(*values):
        configs.append(dict(zip(keys, combo)))
    return configs


# ═══════════════════════════════════════════════════════════════
# TOURNAMENT BREEDER
# ═══════════════════════════════════════════════════════════════

class TournamentBreeder:
    """Breeds new configs from top performers."""

    # Numeric params that can be mutated by ±10-20%
    NUMERIC_PARAMS = {
        "DC_BREAKOUT_SCORE", "WT_EXIT_VEL_THRESHOLD", "WT_REDUCE_FRAC_LOW",
        "WT_REDUCE_FRAC_MED", "WT_REDUCE_FRAC_HIGH", "ENTRY_SCORE_MIN",
        "MI_TF_AGREE_MIN", "MI_MIN_GAIN_EXIT", "MI_ENTRY_STRUCT_BONUS",
        "MI_ENTRY_EXHAUST_BONUS",
        "TRADIER_DC_DAYTRADE_TARGET_PCT", "TRADIER_DC_DAYTRADE_STOP_PCT",
        "TRADIER_DC_DAYTRADE_MAX_HOLD_MINUTES", "TRADIER_DC_DAYTRADE_BUFFER",
        "TRADIER_WT_EXIT_MIN_TFS_TRADIER",
        "TRADIER_K_ZONE_LONG_THRESHOLD_TRADIER", "TRADIER_K_ZONE_SHORT_THRESHOLD_TRADIER",
        "TRADIER_K_ZONE_ENTRY_BONUS_TRADIER",
        "TRADIER_K3M_EXHAUSTION_LOW", "TRADIER_K3M_EXHAUSTION_HIGH",
        "TRADIER_LTF_ALIGNMENT_MIN", "TRADIER_ENTRY_SCORE_THRESHOLD",
        "TRADIER_RSI_ENTRY_LONG_TRADIER", "TRADIER_RSI_ENTRY_SHORT_TRADIER",
        "TRADIER_RSI2_ENTRY_THRESHOLD", "TRADIER_GAP_FILL_MIN_GAP_PCT",
        "TRADIER_DC_POSITION_ENTRY_THRESHOLD",
        "TRADIER_CONGRESS_CONVICTION_SIZING_BOOST",
        "TRADIER_FH_MOMENTUM_MIN_MOVE_PCT",
        "TRADIER_MI_TF_AGREE_MIN_TRADIER", "TRADIER_MI_MIN_GAIN_EXIT_TRADIER",
    }

    # Boolean params that can be flipped
    BOOL_PARAMS = {
        "DC_BREAKOUT_ENTRY_ENABLED", "DC_WIDTH_SIZING_ENABLED", "DC_EDGE_SIZING_ENABLED",
        "MI_EXIT_ENABLED", "MI_ENTRY_ENABLED", "MI_STRUCT_EXIT_ENABLED",
        "MI_EXHAUST_EXIT_ENABLED", "MI_DIV_EXIT_ENABLED", "MI_WAVE_EXIT_ENABLED",
        "TRADIER_DC_DAYTRADE_ENABLED", "TRADIER_WT_COMPOSITE_SCORING_ENABLED_TRADIER",
        "TRADIER_MI_EXIT_ENABLED_TRADIER", "TRADIER_MI_ENTRY_ENABLED_TRADIER",
        "TRADIER_MI_STRUCT_EXIT_ENABLED_TRADIER", "TRADIER_MI_EXHAUST_EXIT_ENABLED_TRADIER",
        "TRADIER_MI_DIV_EXIT_ENABLED_TRADIER", "TRADIER_MI_WAVE_EXIT_ENABLED_TRADIER",
        "TRADIER_DAILY_ALIGNMENT_MANDATORY",
        "TRADIER_FH_MOMENTUM_ENABLED", "TRADIER_FH_MOMENTUM_MFI_CONFIRM",
        "TRADIER_FH_MOMENTUM_DC_CONFIRM",
    }

    # Categorical params (pick from parent A or B)
    CAT_PARAMS = {
        "DC_BREAKOUT_TF", "TRADIER_WT_EXIT_TFS_TRADIER",
    }

    @staticmethod
    def crossover(parent_a: Dict, parent_b: Dict) -> Dict:
        """Take entry params from A, exit params from B (or vice versa)."""
        child = {}
        entry_keywords = ("ENTRY", "SCORE", "ZONE", "STOCH", "RSI", "MFI", "ALIGNMENT", "BREAKOUT", "GAP", "MOMENTUM", "CONVICTION")
        exit_keywords = ("EXIT", "VEL", "REDUCE", "FRAC", "STOP", "HOLD")
        all_keys = set(parent_a.keys()) | set(parent_b.keys())
        for k in all_keys:
            is_entry = any(ew in k.upper() for ew in entry_keywords)
            is_exit = any(ew in k.upper() for ew in exit_keywords)
            if is_entry and not is_exit:
                child[k] = parent_a.get(k, parent_b.get(k))
            elif is_exit and not is_entry:
                child[k] = parent_b.get(k, parent_a.get(k))
            else:
                child[k] = random.choice([parent_a.get(k), parent_b.get(k)])
        return child

    @staticmethod
    def mutate(config: Dict, mutation_rate: float = 0.2) -> Dict:
        """Randomly mutate ~20% of params. Numeric: ±10-20%. Bool: flip. Cat: keep."""
        child = dict(config)
        for k, v in child.items():
            if random.random() > mutation_rate:
                continue
            base_k = k.replace("TRADIER_", "").replace("_TRADIER", "")
            if k in TournamentBreeder.BOOL_PARAMS or base_k in TournamentBreeder.BOOL_PARAMS:
                if isinstance(v, bool):
                    child[k] = not v
            elif k in TournamentBreeder.NUMERIC_PARAMS or base_k in TournamentBreeder.NUMERIC_PARAMS:
                if isinstance(v, (int, float)) and v != 0:
                    factor = random.uniform(0.8, 1.2)
                    new_val = v * factor
                    child[k] = type(v)(round(new_val, 4) if isinstance(v, float) else round(new_val))
            # Cat params: leave unchanged during mutation
        return child

    @staticmethod
    def midpoint(parent_a: Dict, parent_b: Dict) -> Dict:
        """Average numeric params between two parents."""
        child = {}
        all_keys = set(parent_a.keys()) | set(parent_b.keys())
        for k in all_keys:
            va = parent_a.get(k)
            vb = parent_b.get(k)
            if va is None:
                child[k] = vb
            elif vb is None:
                child[k] = va
            elif isinstance(va, (int, float)) and isinstance(vb, (int, float)):
                mid = (va + vb) / 2
                child[k] = type(va)(round(mid, 4) if isinstance(va, float) else round(mid))
            elif isinstance(va, bool) and isinstance(vb, bool):
                child[k] = random.choice([va, vb])
            else:
                child[k] = random.choice([va, vb])
        return child

    @classmethod
    def breed_generation(cls, winners: List[Dict], target_count: int = 60) -> Tuple[List[Dict], str]:
        """
        From N winners, breed target_count new configs using crossover, mutation, midpoints.
        Returns (new_configs, method_description).
        """
        if len(winners) < 2:
            logger.warning("Need >=2 winners to breed. Mutating the single winner.")
            new_configs = [cls.mutate(winners[0], mutation_rate=0.3) for _ in range(target_count)]
            return new_configs, "mutate_single"
        new_configs = []
        methods_used = []
        # 1. Crossover: all pairs of top winners (~40% of budget)
        crossover_budget = int(target_count * 0.4)
        pairs = list(itertools.combinations(range(len(winners)), 2))
        random.shuffle(pairs)
        for i, j in pairs[:crossover_budget]:
            child = cls.crossover(winners[i], winners[j])
            new_configs.append(child)
        if crossover_budget > 0:
            methods_used.append(f"crossover({min(crossover_budget, len(pairs))})")
        # 2. Midpoints between adjacent winners (~20% of budget)
        midpoint_budget = int(target_count * 0.2)
        for idx in range(min(midpoint_budget, len(winners) - 1)):
            child = cls.midpoint(winners[idx], winners[idx + 1])
            new_configs.append(child)
        if midpoint_budget > 0:
            methods_used.append(f"midpoint({min(midpoint_budget, len(winners) - 1)})")
        # 3. Mutations of top winners (~40% of budget)
        remaining = target_count - len(new_configs)
        for _ in range(remaining):
            parent = random.choice(winners[:min(5, len(winners))])
            child = cls.mutate(parent, mutation_rate=0.25)
            new_configs.append(child)
        methods_used.append(f"mutate({remaining})")
        return new_configs, " + ".join(methods_used)


# ═══════════════════════════════════════════════════════════════
# V8 ENGINE RUNNER
# ═══════════════════════════════════════════════════════════════

def run_one_config(args_tuple) -> Dict:
    """Run a single V8 backtest config. Same interface as backtest_v8_sweep.py."""
    config_hash_val, params, mode, account, start, capital, npz_dir, symbols_filter = args_tuple
    override_path = SWEEP_DIR / f"daemon_override_{config_hash_val}.json"
    try:
        with open(override_path, "w") as f:
            json.dump(params, f)
        env = os.environ.copy()
        env["V8_OVERRIDE_FILE"] = str(override_path)
        cmd = [PYTHON, str(ENGINE), "--mode", mode, "--account", account, "--start", start, "--capital", str(capital)]
        if npz_dir:
            cmd += ["--npz-dir", npz_dir]
        if symbols_filter:
            cmd += ["--symbols", symbols_filter]
        t0 = time.time()
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_PER_CONFIG, env=env, cwd=str(SCRIPTS_DIR))
        elapsed = time.time() - t0
        output = proc.stdout + proc.stderr
        m = re.search(r"V8_RESULT:\s+sharpe=([-\d.]+)\s+pnl=([-\d.]+)\s+trades=(\d+)\s+wins=(\d+)\s+losses=(\d+)", output)
        if m:
            return {"config_hash": config_hash_val, "config": params, "sharpe": float(m.group(1)), "pnl": float(m.group(2)), "trades": int(m.group(3)), "wins": int(m.group(4)), "losses": int(m.group(5)), "elapsed": round(elapsed, 1), "status": "ok"}
        else:
            return {"config_hash": config_hash_val, "config": params, "sharpe": 0.0, "pnl": 0.0, "trades": 0, "wins": 0, "losses": 0, "elapsed": round(elapsed, 1), "status": "no_result", "error": output[-500:]}
    except subprocess.TimeoutExpired:
        return {"config_hash": config_hash_val, "config": params, "sharpe": 0.0, "pnl": 0.0, "trades": 0, "wins": 0, "losses": 0, "elapsed": TIMEOUT_PER_CONFIG, "status": "timeout"}
    except Exception as e:
        return {"config_hash": config_hash_val, "config": params, "sharpe": 0.0, "pnl": 0.0, "trades": 0, "wins": 0, "losses": 0, "elapsed": 0, "status": "error", "error": str(e)}
    finally:
        override_path.unlink(missing_ok=True)


# ═══════════════════════════════════════════════════════════════
# CONFIG AUTO-APPLIER
# ═══════════════════════════════════════════════════════════════

def apply_best_config(db, mode: str, force: bool = False) -> bool:
    """Apply the best config to config.py / config_tradier.py. Only on local MacBook."""
    if IS_SERVER and not force:
        logger.info("Auto-apply only runs on local MacBook, not servers")
        return False
    safe_params = SAFE_PARAMS_TRADIER if mode == "tradier" else SAFE_PARAMS_CRYPTO
    config_file = CONFIG_TRADIER if mode == "tradier" else CONFIG_CRYPTO
    best_list = db.get_top_configs(mode, min_trades=APPLY_MIN_TRADES, min_sharpe=APPLY_MIN_SHARPE, limit=1)
    if not best_list:
        logger.info(f"[APPLY] No qualifying configs for {mode} (need trades>{APPLY_MIN_TRADES}, sharpe>{APPLY_MIN_SHARPE})")
        return False
    best = best_list[0]
    best_params = json.loads(best["params_json"])
    baseline = db.get_baseline_sharpe(mode)
    if baseline > 0 and not force:
        improvement = ((best["sharpe"] - baseline) / baseline) * 100
        if improvement < APPLY_MIN_IMPROVEMENT_PCT:
            logger.info(f"[APPLY] Best Sharpe {best['sharpe']:.3f} vs baseline {baseline:.3f} = {improvement:.1f}% improvement (need {APPLY_MIN_IMPROVEMENT_PCT}%)")
            return False
    # Filter to safe params only
    params_to_apply = {k: v for k, v in best_params.items() if k in safe_params}
    if not params_to_apply:
        logger.info("[APPLY] No safe params in winning config")
        return False
    # Backup
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    backup_path = BACKUPS_DIR / f"before_autoapply_{mode}_{ts}.py"
    if config_file.exists():
        import shutil
        shutil.copy2(config_file, backup_path)
        logger.info(f"[APPLY] Backup: {backup_path}")
    # Read current config and update values
    config_text = config_file.read_text()
    changes_made = []
    for param, value in params_to_apply.items():
        if isinstance(value, str):
            new_val_str = f'"{value}"'
        elif isinstance(value, bool):
            new_val_str = str(value)
        elif isinstance(value, list):
            new_val_str = json.dumps(value)
        else:
            new_val_str = str(value)
        pattern = rf'^({param}\s*=\s*)(.+)$'
        match = re.search(pattern, config_text, re.MULTILINE)
        if match:
            old_val = match.group(2).strip()
            if old_val != new_val_str:
                config_text = re.sub(pattern, rf'\g<1>{new_val_str}', config_text, flags=re.MULTILINE)
                changes_made.append(f"{param}: {old_val} -> {new_val_str}")
        else:
            logger.warning(f"[APPLY] Param {param} not found in {config_file.name}, skipping")
    if not changes_made:
        logger.info("[APPLY] No changes needed (config already matches best)")
        return False
    config_file.write_text(config_text)
    logger.info(f"[APPLY] Applied {len(changes_made)} changes to {config_file.name}:")
    for c in changes_made:
        logger.info(f"  {c}")
    db.record_apply(best["config_hash"], mode, params_to_apply, baseline, best["sharpe"], notes="; ".join(changes_made))
    return True


# ═══════════════════════════════════════════════════════════════
# CSV IMPORT (migrate existing results into DB)
# ═══════════════════════════════════════════════════════════════

def import_existing_results(db):
    """Import all existing V8 sweep CSVs and progress JSONs into the DB."""
    imported = 0
    # Import progress JSONs (richer data, has full config dicts)
    for json_path in sorted(SWEEP_DIR.glob("*_progress.json")):
        try:
            with open(json_path) as f:
                results = json.load(f)
            mode = "tradier" if "tradier" in json_path.name else "crypto"
            tier_match = re.search(r"_t(\d+[fF]?)", json_path.name)
            tier = f"T{tier_match.group(1)}" if tier_match else "T?"
            for r in results:
                if "config" not in r or not r["config"]:
                    continue
                h = db.add_config(r["config"], mode, tier=tier, generation=0)
                if not db.has_result(r["config"]):
                    db.save_result(
                        config_hash=h, mode=mode,
                        sharpe=r.get("sharpe", 0.0), pnl=r.get("pnl", 0.0),
                        trades=r.get("trades", 0), wins=r.get("wins", 0), losses=r.get("losses", 0),
                        elapsed=r.get("elapsed", 0.0), status=r.get("status", "ok"),
                        error=r.get("error", ""), machine="import",
                    )
                    imported += 1
            logger.info(f"Imported {json_path.name}: {len(results)} results")
        except Exception as e:
            logger.error(f"Failed to import {json_path.name}: {e}")
    # Import CSVs (has cfg_ columns)
    for csv_path in sorted(SWEEP_DIR.glob("v8_sweep_*.csv")):
        try:
            mode = "tradier" if "tradier" in csv_path.name else "crypto"
            tier_match = re.search(r"_t(\d+[fF]?)", csv_path.name)
            tier = f"T{tier_match.group(1)}" if tier_match else "T?"
            with open(csv_path) as f:
                reader = csv.DictReader(f)
                for row in reader:
                    cfg = {}
                    for k, v in row.items():
                        if k.startswith("cfg_"):
                            param_name = k[4:]
                            try:
                                cfg[param_name] = json.loads(v)
                            except (json.JSONDecodeError, TypeError):
                                if v.lower() == "true":
                                    cfg[param_name] = True
                                elif v.lower() == "false":
                                    cfg[param_name] = False
                                else:
                                    try:
                                        cfg[param_name] = float(v) if "." in str(v) else int(v)
                                    except (ValueError, TypeError):
                                        cfg[param_name] = v
                    if not cfg:
                        continue
                    if not db.has_result(cfg):
                        h = db.add_config(cfg, mode, tier=tier, generation=0)
                        db.save_result(
                            config_hash=h, mode=mode,
                            sharpe=float(row.get("sharpe", 0)), pnl=float(row.get("pnl", 0)),
                            trades=int(row.get("trades", 0)), wins=int(row.get("wins", 0)), losses=int(row.get("losses", 0)),
                            elapsed=float(row.get("elapsed", 0)), status=row.get("status", "ok"),
                            machine="import_csv",
                        )
                        imported += 1
        except Exception as e:
            logger.error(f"Failed to import {csv_path.name}: {e}")
    logger.info(f"Total imported: {imported} new results")
    return imported


# ═══════════════════════════════════════════════════════════════
# SEED GEN 0 (load all tier grids into DB)
# ═══════════════════════════════════════════════════════════════

def seed_generation_zero(db, mode: str) -> int:
    """Load all tier grids as generation 0 configs. Returns count of new configs added."""
    crypto_tiers, tradier_tiers = _load_tier_grids()
    tiers = tradier_tiers if mode == "tradier" else crypto_tiers
    total_added = 0
    for tier_name, tier_def in tiers.items():
        if isinstance(tier_def, list):
            # Fixed config list (Tier 5, Tier 7)
            configs = tier_def
        elif isinstance(tier_def, dict):
            configs = _build_grid(tier_def)
        else:
            continue
        before = db.get_pending_count(mode)
        db.add_configs_batch(configs, mode, tier=tier_name, generation=0)
        after = db.get_pending_count(mode)
        added = after - before
        total_added += added
        logger.info(f"Seeded {tier_name}: {len(configs)} configs ({added} new)")
    return total_added


# ═══════════════════════════════════════════════════════════════
# BREED NEXT GENERATION
# ═══════════════════════════════════════════════════════════════

def breed_next_generation(db, mode: str, top_n: int = 10, target_count: int = 60) -> int:
    """Breed new configs from top performers. Returns count of new configs."""
    current_gen = db.get_max_generation(mode)
    next_gen = current_gen + 1
    top = db.get_top_configs(mode, min_trades=20, min_sharpe=0.0, limit=top_n)
    if len(top) < 2:
        logger.warning(f"Not enough tested configs to breed (have {len(top)}, need >=2)")
        return 0
    winner_params = [json.loads(t["params_json"]) for t in top]
    winner_hashes = [t["config_hash"] for t in top]
    logger.info(f"Breeding gen {next_gen} from {len(winner_params)} winners (best Sharpe: {top[0]['sharpe']:.3f})")
    new_configs, method = TournamentBreeder.breed_generation(winner_params, target_count=target_count)
    # Dedup against existing configs
    truly_new = []
    for cfg in new_configs:
        if not db.config_exists(cfg):
            truly_new.append(cfg)
    if not truly_new:
        logger.info(f"All {len(new_configs)} bred configs already exist, increasing mutation rate")
        new_configs_v2, method = TournamentBreeder.breed_generation(winner_params, target_count=target_count * 2)
        for cfg in new_configs_v2:
            cfg_mutated = TournamentBreeder.mutate(cfg, mutation_rate=0.5)
            if not db.config_exists(cfg_mutated):
                truly_new.append(cfg_mutated)
            if len(truly_new) >= target_count:
                break
        method += " + high_mutation_retry"
    child_hashes = db.add_configs_batch(truly_new, mode, tier=f"bred_gen{next_gen}", generation=next_gen)
    db.record_generation(mode, next_gen, winner_hashes, child_hashes, method)
    logger.info(f"Gen {next_gen}: {len(truly_new)} new configs bred via {method}")
    return len(truly_new)


# ═══════════════════════════════════════════════════════════════
# MAIN DAEMON LOOP
# ═══════════════════════════════════════════════════════════════

def daemon_loop(machine: str, mode: str, workers: int, account: str, start_date: str, capital: float, symbols: str, npz_dir: str, batch_size: int = 20):
    """Main loop: claim → run → save → breed → apply → repeat forever."""
    from sweep_db import SweepDB
    db = SweepDB()
    logger.info(f"Sweep daemon starting: machine={machine} mode={mode} workers={workers}")
    logger.info(f"DB: {db.db_path}")
    # Step 1: Seed gen 0 if DB is empty for this mode
    if db.get_run_count(mode) == 0:
        logger.info("Empty DB — importing existing results first")
        import_existing_results(db)
    pending = db.get_pending_count(mode)
    if pending == 0:
        logger.info("No pending configs — seeding generation 0 from tier grids")
        seed_generation_zero(db, mode)
    last_apply_check = 0
    cycle = 0
    while True:
        cycle += 1
        logger.info(f"{'='*60}")
        logger.info(f"CYCLE {cycle} — {mode} on {machine}")
        # Check how many pending configs exist
        pending = db.get_pending_count(mode)
        logger.info(f"Pending configs: {pending}")
        if pending == 0:
            logger.info("No pending configs — breeding next generation")
            bred = breed_next_generation(db, mode)
            if bred == 0:
                logger.info("Could not breed new configs. Sleeping 5min then retrying with wider mutations.")
                time.sleep(300)
                continue
            pending = db.get_pending_count(mode)
        # Claim a batch
        claimed = db.claim_configs(machine, mode, limit=batch_size)
        if not claimed:
            logger.info("Nothing to claim (all pending claimed by other machines). Sleep 60s.")
            time.sleep(60)
            continue
        logger.info(f"Claimed {len(claimed)} configs for testing")
        # Build run args
        run_args = []
        for item in claimed:
            run_args.append((item["config_hash"], item["params"], mode, account, start_date, capital, npz_dir, symbols))
        # Run batch with ProcessPoolExecutor
        results = []
        t_batch_start = time.time()
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(run_one_config, a): a for a in run_args}
            for fut in as_completed(futures):
                try:
                    r = fut.result()
                except Exception as e:
                    a = futures[fut]
                    r = {"config_hash": a[0], "config": a[1], "sharpe": 0.0, "pnl": 0.0, "trades": 0, "wins": 0, "losses": 0, "elapsed": 0, "status": "error", "error": str(e)}
                results.append(r)
                # Save immediately
                db.save_result(
                    config_hash=r["config_hash"], mode=mode,
                    sharpe=r.get("sharpe", 0.0), pnl=r.get("pnl", 0.0),
                    trades=r.get("trades", 0), wins=r.get("wins", 0), losses=r.get("losses", 0),
                    elapsed=r.get("elapsed", 0.0), status=r.get("status", "ok"),
                    error=r.get("error", ""), machine=machine,
                    symbols=symbols, start_date=start_date, capital=capital,
                )
                s = r.get("status", "?")
                sh = r.get("sharpe", 0)
                tr = r.get("trades", 0)
                logger.info(f"  [{len(results)}/{len(claimed)}] hash={r['config_hash']} sharpe={sh:.3f} trades={tr} status={s}")
        batch_elapsed = time.time() - t_batch_start
        ok_results = [r for r in results if r.get("status") == "ok" and r.get("trades", 0) > 0]
        if ok_results:
            best_r = max(ok_results, key=lambda x: x["sharpe"])
            logger.info(f"Batch done: {len(ok_results)}/{len(results)} ok, best sharpe={best_r['sharpe']:.3f}, elapsed={batch_elapsed:.0f}s")
        else:
            logger.info(f"Batch done: 0/{len(results)} with results, elapsed={batch_elapsed:.0f}s")
        # Print generation stats
        gen_stats = db.get_generation_stats(mode)
        if gen_stats:
            logger.info("Generation stats:")
            for g in gen_stats:
                logger.info(f"  Gen {g['generation']}: {g['tested']}/{g['total']} tested, avg_sharpe={g['avg_sharpe'] or 0:.3f}, max_sharpe={g['max_sharpe'] or 0:.3f}")
        # Check auto-apply window (twice daily)
        now_utc = datetime.now(timezone.utc)
        if now_utc.hour in APPLY_WINDOWS_UTC and time.time() - last_apply_check > 3600:
            logger.info("Auto-apply window — checking for improvements")
            applied = apply_best_config(db, mode)
            if applied:
                logger.info("CONFIG UPDATED — new params applied to live config")
            last_apply_check = time.time()
        # Small pause between batches (let other processes breathe)
        time.sleep(5)


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Sweep Daemon — self-improving tournament engine")
    parser.add_argument("--machine", type=str, default="local", help="Machine identifier (local, server1, server2)")
    parser.add_argument("--mode", type=str, choices=["tradier", "crypto"], default="tradier")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--account", type=str, default="")
    parser.add_argument("--start", type=str, default="2024-06-01")
    parser.add_argument("--capital", type=float, default=10000.0)
    parser.add_argument("--symbols", type=str, default="fast", help="'fast' for 12 symbols, 'all' for full set, or comma-sep list")
    parser.add_argument("--npz-dir", type=str, default="")
    parser.add_argument("--batch-size", type=int, default=20, help="Configs per batch before checking breed/apply")
    parser.add_argument("--status", action="store_true", help="Print DB status and exit")
    parser.add_argument("--import-csv", action="store_true", help="Import existing CSV results and exit")
    parser.add_argument("--breed", action="store_true", help="Force breed next generation and exit")
    parser.add_argument("--apply-now", action="store_true", help="Force apply best config now and exit")
    parser.add_argument("--seed", action="store_true", help="Seed generation 0 from tier grids and exit")
    parser.add_argument("--top", type=int, default=0, help="Show top N configs and exit")
    parser.add_argument("--ablation", action="store_true", help="Seed ablation configs (1 baseline + 13 function knockouts) and exit")
    parser.add_argument("--ablation-results", action="store_true", help="Show ablation results ranked by importance")
    args = parser.parse_args()
    from sweep_db import SweepDB
    db = SweepDB()
    # One-shot commands
    if args.status:
        print(db.status_summary())
        return
    if args.import_csv:
        n = import_existing_results(db)
        print(f"Imported {n} results")
        print(db.status_summary())
        return
    if args.seed:
        n = seed_generation_zero(db, args.mode)
        print(f"Seeded {n} new configs for {args.mode}")
        print(db.status_summary())
        return
    if args.breed:
        n = breed_next_generation(db, args.mode)
        print(f"Bred {n} new configs for {args.mode}")
        print(db.status_summary())
        return
    if args.apply_now:
        applied = apply_best_config(db, args.mode, force=True)
        print(f"Applied: {applied}")
        return
    if args.ablation:
        ablation_configs = build_ablation_configs()
        hashes = db.add_configs_batch(ablation_configs, args.mode, tier="ABLATION", generation=0)
        print(f"Seeded {len(ablation_configs)} ablation configs for {args.mode}:")
        for cfg in ablation_configs:
            name = cfg.get("_ABLATION_NAME", "?")
            h = hashes[ablation_configs.index(cfg)] if ablation_configs.index(cfg) < len(hashes) else "?"
            already = db.has_result(cfg)
            status = "DONE" if already else "PENDING"
            print(f"  {name:<30} hash={h} {status}")
        print(db.status_summary())
        return
    if args.ablation_results:
        print(f"\nABLATION RESULTS — {args.mode.upper()}")
        print(f"{'Function':<35} {'Sharpe':>8} {'PnL%':>8} {'Trades':>7} {'WR%':>6}  {'Impact'}")
        print("-" * 90)
        ablation_configs = build_ablation_configs()
        baseline_sharpe = None
        results_list = []
        for cfg in ablation_configs:
            name = cfg.get("_ABLATION_NAME", "?")
            from sweep_db import config_hash as _ch
            h = _ch(cfg)
            with db._conn() as conn:
                row = conn.execute("SELECT sharpe, pnl, trades, win_rate FROM runs WHERE config_hash = ? AND status = 'ok' ORDER BY sharpe DESC LIMIT 1", (h,)).fetchone()
            if row:
                if name == "BASELINE":
                    baseline_sharpe = row["sharpe"]
                results_list.append((name, row["sharpe"], row["pnl"], row["trades"], row["win_rate"]))
            else:
                results_list.append((name, None, None, None, None))
        # Sort by impact (how much Sharpe drops when function is disabled)
        for name, sharpe, pnl, trades, wr in sorted(results_list, key=lambda x: -(x[1] or 0) if x[0] == "BASELINE" else (x[1] or 999)):
            if sharpe is None:
                print(f"  {name:<35} {'NOT RUN':>8}")
                continue
            if baseline_sharpe is not None and name != "BASELINE":
                delta = sharpe - baseline_sharpe
                impact = f"{'CRITICAL' if delta < -2 else 'HIGH' if delta < -0.5 else 'MEDIUM' if delta < -0.1 else 'LOW' if delta < 0 else 'NEGATIVE'} ({delta:+.3f})"
            else:
                impact = "BASELINE" if name == "BASELINE" else "?"
            print(f"  {name:<35} {sharpe:>8.3f} {pnl:>8.2f} {trades:>7} {wr:>5.1f}%  {impact}")
        return
    if args.top > 0:
        top = db.get_top_configs(args.mode, min_trades=20, limit=args.top)
        print(f"\nTOP {args.top} — {args.mode.upper()}")
        print(f"{'#':>3} {'Sharpe':>8} {'PnL%':>8} {'Trades':>7} {'WR%':>6} {'Gen':>4}  Config Hash")
        for i, t in enumerate(top):
            wr = t.get("win_rate", 0)
            print(f"{i+1:>3} {t['sharpe']:>8.3f} {t['pnl']:>8.2f} {t['trades']:>7} {wr:>5.1f}% {t['generation']:>4}  {t['config_hash']}")
            params = json.loads(t["params_json"])
            for k, v in sorted(params.items()):
                print(f"      {k} = {v}")
        return
    # Resolve symbols
    symbols = args.symbols
    if symbols == "fast":
        symbols = FAST_SYMBOLS.get(args.mode, "")
    elif symbols == "all":
        symbols = ""
    account = args.account or ("ang" if args.mode == "crypto" else "trb")
    # Run the daemon
    logger.info("=" * 60)
    logger.info("SWEEP DAEMON STARTING")
    logger.info(f"  Machine: {args.machine}")
    logger.info(f"  Mode: {args.mode}")
    logger.info(f"  Workers: {args.workers}")
    logger.info(f"  Account: {account}")
    logger.info(f"  Start: {args.start}")
    logger.info(f"  Symbols: {symbols or 'ALL'}")
    logger.info(f"  Batch size: {args.batch_size}")
    logger.info(f"  DB: {db.db_path}")
    logger.info("=" * 60)
    daemon_loop(
        machine=args.machine, mode=args.mode, workers=args.workers,
        account=account, start_date=args.start, capital=args.capital,
        symbols=symbols, npz_dir=args.npz_dir, batch_size=args.batch_size,
    )


if __name__ == "__main__":
    main()
