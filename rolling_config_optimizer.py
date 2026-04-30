#!/usr/bin/env python3
"""
Rolling Config Optimizer — per-symbol adaptive parameter tuning.

For each tradeable_key, runs V8 backtests on a rolling 7-day window with
~1,700 gate configs (Phase A) + sizing sweep on winner (Phase B).
Scores with time-weighted Sharpe (last hour = 24x weight).
Stores ALL results in SQLite. Publishes winner to Redis for live pickup.

Usage:
    python3 rolling_config_optimizer.py --account trb --workers 8
    python3 rolling_config_optimizer.py --account trb --workers 4 --symbols NVDA,META
    python3 rolling_config_optimizer.py --report  # show standardization analysis
"""
# metrics_guard retrofit (audited 2026-04-30): this script writes a Sharpe
# number to a print/log surface. Per CLAUDE.md NO-LIES MANDATE, any future
# user-facing Sharpe MUST be routed through metrics_guard.validate_and_format_sharpe()
# with explicit label, n_syms, years, trades, mode. Bare 'Sharpe X.XX' output is forbidden.
from metrics_guard import validate_and_format_sharpe  # noqa: F401  (forward-prevention import)
import argparse
import itertools
import json
import logging
import os
import platform
import re
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("rolling_optimizer")

IS_SERVER = platform.system() == "Linux"
if IS_SERVER:
    BASE_PATH = Path("/home/niels/binance-sandbox")
    PYTHON = str(next(
        (p for p in [
            Path("/home/niels/.conda/envs/binance_env/bin/python3"),
            Path("/home/niels/miniconda3/envs/binance_env/bin/python"),
        ] if p.exists()),
        "python3"
    ))
    SCRIPTS_DIR = Path("/home/niels/binance-sandbox")
else:
    BASE_PATH = Path("/Users/niels/Documents/binance")
    PYTHON = "/opt/anaconda3/envs/binance_env/bin/python"
    SCRIPTS_DIR = BASE_PATH

ENGINE = SCRIPTS_DIR / "backtest_v8_engine.py"
TIMEOUT = 30  # 30s HARD LIMIT — if no result, config is broken

MACHINE_ID = "server204" if IS_SERVER else "macbook"

# ═══════════════════════════════════════════════════════════════
# TRADIER PHASE A: GATE SWEEP — entry/exit parameters (sizing locked)
# ═══════════════════════════════════════════════════════════════
PHASE_A_GRID = {
    # ALWAYS: disable BV gates + $70k account sizing
    "TRADIER_BACKTEST_VALIDATED_GATES_TRADIER": [False],
    "TRADIER_START_POSITION_SIZE": [3500],
    "TRADIER_MAX_ORDER_VALUE": [8000],
    "TRADIER_MAX_POSITION_SIZE": [15000],
    "TRADIER_SWING_LONG_BUDGET": [35000],
    "TRADIER_SWING_SHORT_BUDGET": [35000],
    "TRADIER_SWING_MAX_POSITION_SIZE": [12000],
    "TRADIER_SCALP_LONG_BUDGET": [15000],
    "TRADIER_SCALP_SHORT_BUDGET": [15000],
    # ENTRY TUNING
    "TRADIER_WT_DC_ENTRY_THRESHOLD": [25, 40],              # 2
    "TRADIER_ENTRY_MIN_ALIGNMENT": [4, 6],                  # 2
    # EXIT TUNING
    "TRADIER_MIN_EXIT_TF_AGAINST_TRADIER": [2, 3],         # 2
    # STRATEGY TOGGLES
    "TRADIER_DC_DAYTRADE_ENABLED": [True, False],           # 2
    "TRADIER_FH_MOMENTUM_ENABLED": [True, False],           # 2
    "TRADIER_GAP_FILL_ENABLED": [True, False],              # 2
    "TRADIER_RSI2_ENABLED": [True, False],                  # 2
}
# 128 configs (fixed sizing × 2^7 gate combos)
# 3 x 2 x 3 x 3 x 2 x 2 x 2 x 2 x 2 = 1,728 configs

# K_ZONE_SHORT pairing
_KZONE_PAIRS = {35: 65, 50: 50, 80: 20}

# ═══════════════════════════════════════════════════════════════
# PHASE B: SIZING SWEEP — position sizing (gates locked to Phase A winner)
# ═══════════════════════════════════════════════════════════════
PHASE_B_SIZING = {
    "TRADIER_START_POSITION_SIZE": [2000, 3500, 5000, 7000],
    "TRADIER_MAX_ORDER_VALUE": [5000, 8000, 12000],
    # Always include budgets so sizing doesn't get blocked
    "TRADIER_SWING_LONG_BUDGET": [35000],
    "TRADIER_SWING_SHORT_BUDGET": [35000],
    "TRADIER_SWING_MAX_POSITION_SIZE": [15000],
    "TRADIER_SCALP_LONG_BUDGET": [15000],
    "TRADIER_SCALP_SHORT_BUDGET": [15000],
    "TRADIER_MAX_POSITION_SIZE": [15000],
}
# 4 x 3 = 12 configs (sizing dimensions only, budgets locked)


def build_phase_a_configs() -> List[Dict]:
    """Build Phase A gate configs."""
    keys = list(PHASE_A_GRID.keys())
    values = [PHASE_A_GRID[k] for k in keys]
    return [dict(zip(keys, combo)) for combo in itertools.product(*values)]


def build_phase_b_configs(phase_a_winner: Dict) -> List[Dict]:
    """Build Phase B sizing configs, locked to Phase A winner gates."""
    keys = list(PHASE_B_SIZING.keys())
    values = [PHASE_B_SIZING[k] for k in keys]
    configs = []
    for combo in itertools.product(*values):
        cfg = dict(phase_a_winner)
        cfg.update(dict(zip(keys, combo)))
        configs.append(cfg)
    return configs


# ═══════════════════════════════════════════════════════════════
# CRYPTO PHASE A: GATE SWEEP — crypto entry/exit parameters
# Already wired through config.get_symbol_setting() in ez_manage/ez_positions_quick
# ═══════════════════════════════════════════════════════════════
CRYPTO_PHASE_A_GRID = {
    "K3M_CAP": [0, 50, 80],                        # 3
    "HTF_STRICT": [True, False],                     # 2
    "MTS_GATE_ENABLED": [True, False],               # 2
    "ENTRY_ATR_PCT_MIN": [0.5, 1.0, 1.5],           # 3
    "NOLOSS_MIN_PROFIT_PCT": [-999.0, 0.15, 0.30],  # 3
    "FAST_CUT_LOSS_THRESHOLD": [-1.0, -1.5, -2.5],  # 3
    "LONG_STOCH_CHASE_BLOCK": [True, False],         # 2
    "ENTRY_SCORE_MIN": [16, 18, 22],                 # 3
    "WT_EXIT_VEL_THRESHOLD": [-4.0, -8.0, -12.0],   # 3
}
# 3×2×2×3×3×3×2×3×3 = 5,832 — too many. Split like tradier:
# Phase A focused: K3M × HTF × MTS × ATR × NOLOSS × VEL_THRESH = 3×2×2×3×3×3 = 324
CRYPTO_PHASE_A_FOCUSED = {
    "K3M_CAP": [0, 50, 80],
    "HTF_STRICT": [True, False],
    "MTS_GATE_ENABLED": [True, False],
    "ENTRY_ATR_PCT_MIN": [0.5, 1.0, 1.5],
    "NOLOSS_MIN_PROFIT_PCT": [-999.0, 0.15, 0.30],
    "WT_EXIT_VEL_THRESHOLD": [-4.0, -8.0, -12.0],
}
# 3×2×2×3×3×3 = 324 configs

CRYPTO_PHASE_B_SIZING = {
    "START_POSITION_SIZE": [10, 18, 30, 50],    # crypto uses smaller $
    "MAX_POSITION_SIZE": [15, 20, 40],
}
# 4×3 = 12 configs


def build_crypto_phase_a_configs() -> List[Dict]:
    keys = list(CRYPTO_PHASE_A_FOCUSED.keys())
    values = [CRYPTO_PHASE_A_FOCUSED[k] for k in keys]
    return [dict(zip(keys, combo)) for combo in itertools.product(*values)]


def build_crypto_phase_b_configs(phase_a_winner: Dict) -> List[Dict]:
    keys = list(CRYPTO_PHASE_B_SIZING.keys())
    values = [CRYPTO_PHASE_B_SIZING[k] for k in keys]
    configs = []
    for combo in itertools.product(*values):
        cfg = dict(phase_a_winner)
        cfg.update(dict(zip(keys, combo)))
        configs.append(cfg)
    return configs


# ═══════════════════════════════════════════════════════════════
# V8 RUNNER — subprocess per config
# ═══════════════════════════════════════════════════════════════
def run_one_config(args_tuple) -> Dict:
    """Run V8 engine with one config override. Returns result dict."""
    cfg, mode, account, start_date, capital, run_id, npz_dir, symbol = args_tuple
    override_dir = BASE_PATH / "backtest_v8" / "optimizer_overrides"
    override_dir.mkdir(parents=True, exist_ok=True)
    override_path = override_dir / f"override_{run_id}.json"
    with open(override_path, "w") as f:
        json.dump(cfg, f)
    env = os.environ.copy()
    env["V8_OVERRIDE_FILE"] = str(override_path)
    cmd = [PYTHON, str(ENGINE), "--mode", mode, "--account", account,
           "--start", start_date, "--symbols", symbol, "--capital", str(capital)]
    if npz_dir:
        cmd += ["--npz-dir", npz_dir]
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT,
                              env=env, cwd=str(SCRIPTS_DIR))
        elapsed = time.time() - t0
        output = proc.stdout + proc.stderr
        # Parse V8_RESULT (extended format)
        m = re.search(
            r"V8_RESULT:\s+sharpe=([-\d.]+)\s+pnl=([-\d.]+)\s+trades=(\d+)\s+wins=(\d+)\s+losses=(\d+)"
            r"(?:\s+total_pnl_dollars=([-\d.]+))?"
            r"(?:\s+avg_pnl=([-\d.]+))?"
            r"(?:\s+avg_pos_value=([-\d.]+))?",
            output
        )
        # Parse V8_LOG path
        log_m = re.search(r"V8_LOG:\s+(.+\.jsonl)", output)
        log_path = log_m.group(1).strip() if log_m else ""
        if m:
            result = {
                "run_id": run_id, "config": cfg,
                "raw_sharpe": float(m.group(1)),
                "raw_pnl_pct": float(m.group(2)),
                "trades": int(m.group(3)),
                "wins": int(m.group(4)),
                "losses": int(m.group(5)),
                "raw_pnl_dollars": float(m.group(6) or 0),
                "avg_pnl_per_trade": float(m.group(7) or 0),
                "avg_position_value": float(m.group(8) or 0),
                "elapsed": round(elapsed, 1),
                "status": "ok",
                "log_path": log_path,
            }
        else:
            result = {
                "run_id": run_id, "config": cfg,
                "raw_sharpe": 0.0, "raw_pnl_pct": 0.0,
                "trades": 0, "wins": 0, "losses": 0,
                "raw_pnl_dollars": 0.0, "avg_pnl_per_trade": 0.0,
                "avg_position_value": 0.0,
                "elapsed": round(elapsed, 1),
                "status": "no_result",
                "log_path": log_path,
                "error": output[-500:],
            }
    except subprocess.TimeoutExpired:
        elapsed = time.time() - t0
        result = {
            "run_id": run_id, "config": cfg,
            "raw_sharpe": 0.0, "raw_pnl_pct": 0.0,
            "trades": 0, "wins": 0, "losses": 0,
            "raw_pnl_dollars": 0.0, "avg_pnl_per_trade": 0.0,
            "avg_position_value": 0.0,
            "elapsed": round(elapsed, 1),
            "status": "timeout",
            "log_path": "",
        }
    except Exception as e:
        elapsed = time.time() - t0
        result = {
            "run_id": run_id, "config": cfg,
            "raw_sharpe": 0.0, "raw_pnl_pct": 0.0,
            "trades": 0, "wins": 0, "losses": 0,
            "raw_pnl_dollars": 0.0, "avg_pnl_per_trade": 0.0,
            "avg_position_value": 0.0,
            "elapsed": round(elapsed, 1),
            "status": "error",
            "log_path": "",
            "error": str(e),
        }
    override_path.unlink(missing_ok=True)
    return result


# ═══════════════════════════════════════════════════════════════
# TIME-WEIGHTED SCORING
# ═══════════════════════════════════════════════════════════════
def compute_weighted_metrics(log_path: str, window_start_ts: float,
                             window_end_ts: float) -> Dict[str, float]:
    """Read trade JSONL, compute time-weighted % Sharpe and $ PnL."""
    defaults = {"weighted_sharpe": 0, "weighted_pnl_pct": 0, "weighted_pnl_dollars": 0,
                "avg_position_value": 0, "avg_pnl_per_trade": 0}
    if not log_path or not Path(log_path).exists():
        return defaults
    trades = []
    try:
        with open(log_path) as f:
            for line in f:
                t = json.loads(line)
                if t.get("pnl_dollars") is None:
                    continue
                ts = t.get("timestamp", 0)
                if isinstance(ts, str):
                    try:
                        ts = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
                    except Exception:
                        ts = 0
                trades.append({
                    "timestamp": float(ts),
                    "pnl_pct": float(t.get("pnl_pct", 0)),
                    "pnl_dollars": float(t.get("pnl_dollars", 0)),
                    "position_value": float(t.get("position_value", 0)),
                })
    except Exception:
        return defaults
    if len(trades) < 2:
        return defaults
    window_hours = max(1, (window_end_ts - window_start_ts) / 3600)
    pct_r, dol_r, weights, pos_vals = [], [], [], []
    for t in trades:
        hour = (t["timestamp"] - window_start_ts) / 3600
        weight = 1.0 + 23.0 * max(0, min(hour, window_hours)) / window_hours
        pct_r.append(t["pnl_pct"])
        dol_r.append(t["pnl_dollars"])
        weights.append(weight)
        pos_vals.append(t["position_value"])
    w = np.array(weights)
    w_norm = w / w.sum()
    r_pct = np.array(pct_r)
    wmean = np.dot(w_norm, r_pct)
    wstd = np.sqrt(np.dot(w_norm, (r_pct - wmean) ** 2))
    r_dol = np.array(dol_r)
    return {
        "weighted_sharpe": float(wmean / wstd) if wstd > 0 else 0.0,
        "weighted_pnl_pct": float(np.sum(r_pct * w) / np.sum(w) * len(trades)),
        "weighted_pnl_dollars": float(np.dot(w_norm, r_dol) * len(trades)),
        "avg_position_value": float(np.mean(pos_vals)) if pos_vals else 0,
        "avg_pnl_per_trade": float(np.mean(dol_r)) if dol_r else 0,
    }


# ═══════════════════════════════════════════════════════════════
# REDIS PUBLISHER
# ═══════════════════════════════════════════════════════════════
def publish_winner(symbol: str, side: str, account: str, best: Dict):
    """Publish winning config to Redis for live trading pickup."""
    try:
        import redis
        r = redis.Redis(host="localhost", port=6379, db=0, socket_connect_timeout=2)
        key = f"regime_cfg:{account}:{symbol}_{side}"
        payload = {
            **best["config"],
            "_source": "rolling_optimizer",
            "_ts": time.time(),
            "_composite_score": best.get("composite_score", 0),
            "_weighted_sharpe": best.get("weighted_sharpe", 0),
            "_weighted_pnl_dollars": best.get("weighted_pnl_dollars", 0),
            "_trades": best.get("trades", 0),
        }
        r.set(key, json.dumps(payload, default=str), ex=86400 * 2)  # 2-day TTL
        logger.info(f"[PUBLISH] {key}: composite={best.get('composite_score', 0):.3f} sharpe={best.get('weighted_sharpe', 0):.3f} ${best.get('weighted_pnl_dollars', 0):.2f}")
    except Exception as e:
        logger.warning(f"[PUBLISH] Redis error: {e}")


# ═══════════════════════════════════════════════════════════════
# REDIS CLAIM — multi-machine coordination
# ═══════════════════════════════════════════════════════════════
def try_claim(tradeable_key: str) -> bool:
    """Try to claim a symbol for optimization. Returns True if claimed."""
    try:
        import redis
        r = redis.Redis(host="localhost", port=6379, db=0, socket_connect_timeout=2)
        claim_key = f"optimizer_claim:{tradeable_key}"
        return bool(r.set(claim_key, MACHINE_ID, nx=True, ex=7200))
    except Exception:
        return True  # If Redis down, proceed anyway (single machine)


def release_claim(tradeable_key: str):
    try:
        import redis
        r = redis.Redis(host="localhost", port=6379, db=0, socket_connect_timeout=2)
        r.delete(f"optimizer_claim:{tradeable_key}")
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════
# OPTIMIZE ONE SYMBOL
# ═══════════════════════════════════════════════════════════════
def optimize_symbol(symbol: str, side: str, account: str, workers: int,
                    capital: float, npz_dir: str, db_conn, mode: str = "tradier") -> Optional[Dict]:
    """Run Phase A + Phase B for one symbol/side. Returns best config dict."""
    from rolling_optimizer_db import (insert_result, expand_config_params,
                                      compute_composite_scores, get_best_config)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    # Use last 7 days of NPZ data (NPZ end date, not calendar today)
    # NPZ ends ~2026-03-25 for tradier, ~2026-03-30 for crypto
    # start_date = 7 days before NPZ end ≈ 2026-03-18
    start_date = "2026-03-18"  # TODO: detect from NPZ timestamps
    window_start_ts = datetime(2026, 3, 18, tzinfo=timezone.utc).timestamp()
    window_end_ts = datetime(2026, 3, 25, tzinfo=timezone.utc).timestamp()

    # ── Phase A: gate sweep ──
    configs_a = build_phase_a_configs() if mode == "tradier" else build_crypto_phase_a_configs()
    logger.info(f"[{symbol}_{side}] Phase A ({mode}): {len(configs_a)} gate configs, {workers} workers")
    run_args = []
    for i, cfg in enumerate(configs_a):
        run_id = f"opt_{symbol}_{side}_{today}_a{i:05d}"
        run_args.append((cfg, mode, account, start_date, capital, run_id, npz_dir, symbol))
    results_a = _run_pool(run_args, workers)
    # Score and store Phase A results
    for r in results_a:
        wm = compute_weighted_metrics(r.get("log_path", ""), window_start_ts, window_end_ts)
        r.update(wm)
        r["symbol"] = symbol
        r["side"] = side
        r["account"] = account
        r["run_date"] = today
        r["window_start"] = start_date
        r["window_end"] = today
        rid = insert_result(db_conn, r)
        expand_config_params(db_conn, rid, r.get("config", {}),
                             r.get("weighted_sharpe", 0), 0, symbol, side, today)
    compute_composite_scores(db_conn, symbol, side, today)
    # Find Phase A winner
    best_a = get_best_config(db_conn, symbol, side, today)
    if not best_a or best_a["trades"] < 2:
        logger.warning(f"[{symbol}_{side}] Phase A: no winner with trades >= 2")
        return None
    logger.info(f"[{symbol}_{side}] Phase A winner: composite={best_a['composite_score']:.3f} sharpe={best_a['weighted_sharpe']:.3f} ${best_a['weighted_pnl_dollars']:.2f} ({best_a['trades']} trades)")

    # ── Phase B: sizing sweep on Phase A winner ──
    configs_b = build_phase_b_configs(best_a["config"]) if mode == "tradier" else build_crypto_phase_b_configs(best_a["config"])
    logger.info(f"[{symbol}_{side}] Phase B: {len(configs_b)} sizing configs")
    run_args_b = []
    for i, cfg in enumerate(configs_b):
        run_id = f"opt_{symbol}_{side}_{today}_b{i:05d}"
        run_args_b.append((cfg, mode, account, start_date, capital, run_id, npz_dir, symbol))
    results_b = _run_pool(run_args_b, workers)
    for r in results_b:
        wm = compute_weighted_metrics(r.get("log_path", ""), window_start_ts, window_end_ts)
        r.update(wm)
        r["symbol"] = symbol
        r["side"] = side
        r["account"] = account
        r["run_date"] = today
        r["window_start"] = start_date
        r["window_end"] = today
        rid = insert_result(db_conn, r)
        expand_config_params(db_conn, rid, r.get("config", {}),
                             r.get("weighted_sharpe", 0), 0, symbol, side, today)
    compute_composite_scores(db_conn, symbol, side, today)
    # Final winner (Phase A + Phase B combined)
    best_final = get_best_config(db_conn, symbol, side, today)
    if best_final:
        logger.info(f"[{symbol}_{side}] FINAL: composite={best_final['composite_score']:.3f} sharpe={best_final['weighted_sharpe']:.3f} ${best_final['weighted_pnl_dollars']:.2f} avg_trade=${best_final['avg_pnl_per_trade']:.2f} pos_val=${best_final['avg_position_value']:.0f}")
    return best_final


def _run_pool(run_args: list, workers: int) -> List[Dict]:
    """Run configs through ThreadPoolExecutor (subprocess per config), return results."""
    from concurrent.futures import ThreadPoolExecutor
    results = []
    t0 = time.time()
    total = len(run_args)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(run_one_config, a): a for a in run_args}
        done_count = 0
        for fut in as_completed(futures):
            try:
                r = fut.result()
                results.append(r)
                done_count += 1
                if done_count % 10 == 0 or done_count == total:
                    ok = sum(1 for r in results if r.get("trades", 0) > 0)
                    elapsed = time.time() - t0
                    eta = (elapsed / done_count * (total - done_count)) / 60 if done_count > 0 else 0
                    logger.info(f"  {done_count}/{total} ({ok} trades) {elapsed:.0f}s ETA={eta:.1f}min")
            except Exception as e:
                logger.error(f"Worker error: {e}")
    elapsed = time.time() - t0
    ok = sum(1 for r in results if r.get("trades", 0) > 0)
    logger.info(f"  Done: {len(results)} configs, {ok} with trades, {elapsed:.0f}s")
    return results


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(description="Rolling Config Optimizer — per-symbol adaptive tuning")
    parser.add_argument("--mode", choices=["crypto", "tradier"], default="tradier")
    parser.add_argument("--account", type=str, default="")
    parser.add_argument("--workers", type=int, default=4 if not IS_SERVER else 8)
    parser.add_argument("--capital", type=float, default=70000.0)
    parser.add_argument("--symbols", type=str, default="", help="Comma-separated symbols (empty=tradeable_keys)")
    parser.add_argument("--npz-dir", type=str, default="", help="Explicit NPZ directory")
    parser.add_argument("--report", action="store_true", help="Show standardization report and exit")
    parser.add_argument("--continuous", action="store_true", help="Loop forever (daemon mode)")
    parser.add_argument("--db-path", type=str, default="", help="SQLite DB path override")
    args = parser.parse_args()

    account = args.account or ("ang" if args.mode == "crypto" else "trb")
    from rolling_optimizer_db import get_db, standardization_report, DB_PATH
    db_path = Path(args.db_path) if args.db_path else DB_PATH
    conn = get_db(db_path)

    if args.report:
        report = standardization_report(conn)
        if report:
            print(f"\n{'='*70}")
            print(f"  STANDARDIZATION REPORT ({len(report)} params analyzed)")
            print(f"  {'Param':<45} {'Winner':<12} {'Keys':>5} {'Margin':>8}")
            for r in report:
                print(f"  {r['param_name']:<45} {r['winning_value']:<12} {r['n_keys']:>5} {r['margin_pct']:>7.1f}%")
            print(f"{'='*70}")
        else:
            print("No data yet.")
        conn.close()
        return

    # Load tradeable keys
    if args.symbols:
        symbol_sides = []
        for s in args.symbols.split(","):
            s = s.strip().upper()
            if "_" in s:
                sym, side = s.rsplit("_", 1)
                symbol_sides.append((sym, side))
            else:
                symbol_sides.append((s, "LONG"))
                symbol_sides.append((s, "SHORT"))
    else:
        symbol_sides = _load_tradeable_keys(account, args.mode)
    if not symbol_sides:
        logger.error("No symbols to optimize")
        conn.close()
        return

    logger.info(f"Rolling Optimizer ({args.mode}): {len(symbol_sides)} keys, account={account}, workers={args.workers}")
    while True:
        t_pass = time.time()
        for symbol, side in symbol_sides:
            tk = f"{account}:{symbol}_{side}"
            if not try_claim(tk):
                logger.info(f"[{symbol}_{side}] Claimed by another machine, skipping")
                continue
            try:
                best = optimize_symbol(symbol, side, account, args.workers,
                                       args.capital, args.npz_dir, conn, args.mode)
                if best:
                    publish_winner(symbol, side, account, best)
            except Exception as e:
                logger.error(f"[{symbol}_{side}] ERROR: {e}")
            finally:
                release_claim(tk)
        elapsed = time.time() - t_pass
        logger.info(f"Full pass done: {len(symbol_sides)} keys in {elapsed/3600:.1f}h")
        if not args.continuous:
            break
        logger.info("Sleeping 1h before next pass...")
        time.sleep(3600)
    conn.close()


def _load_tradeable_keys(account: str, mode: str = "tradier") -> List[Tuple[str, str]]:
    """Load tradeable keys from tradeable_keys.json, filter by account."""
    tk_path = BASE_PATH / "tradeable_keys.json"
    if not tk_path.exists():
        if mode == "crypto":
            return [(s, sd) for s in ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT"]
                    for sd in ["LONG", "SHORT"]]
        return [(s, sd) for s in ["NVDA", "AAPL", "META", "MSFT", "XOM", "BA"]
                for sd in ["LONG", "SHORT"]]
    with open(tk_path) as f:
        keys = json.load(f)
    result = []
    for k in keys:
        k = str(k).strip()
        if not k.startswith(f"{account}:"):
            continue
        rest = k.split(":", 1)[1]
        if "_LONG" in rest:
            sym = rest.replace("_LONG", "")
            result.append((sym, "LONG"))
        elif "_SHORT" in rest:
            sym = rest.replace("_SHORT", "")
            result.append((sym, "SHORT"))
    return result


if __name__ == "__main__":
    main()
