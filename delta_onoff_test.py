#!/usr/bin/env python3
"""
Delta ON/OFF comparison test.
Runs V2 backtest (real exits) vs V1 baseline (fixed forward returns) on full symbol set.
Usage:
    python3 delta_onoff_test.py --crypto    # 48 symbols, 4yr
    python3 delta_onoff_test.py --stocks    # 121 symbols, 2yr
"""
import argparse
import json
import logging
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sweep_wt_dc_delta import backtest_symbol, backtest_symbol_v2, CRYPTO_NPZ, STOCK_NPZ
from wt_dc_delta import DEFAULT_CFG

logging.basicConfig(level=logging.INFO, format="%(asctime)s [TEST] %(message)s")
logger = logging.getLogger("onoff")

# V2 sweep winners
CRYPTO_LT_ON = {
    **DEFAULT_CFG,
    "tf_weights": {"3m": 1.0, "15m": 1.0, "1h": 3.0, "4h": 2.0, "D": 1.0},
    "entry_min_tf": 2,
    "entry_z_threshold": 1.5,
    "entry_accel_threshold": 0.0,
    "speed_smooth": 5,
    "tf_z_threshold": 1.5,
    "htf_gate": "4h_D",
    "cooldown_bars": 120,
    "exit_type": "combined_wt_stoch",
    "exit_tf": "4h",
    "exit_speed_pct": 50,
    "max_hold_bars": 480,
    "atr_entry_filter": 0,
    "giveback_pct": 50,
    "atr_trail_mult": 2.0,
}

CRYPTO_ST_ON = {
    **DEFAULT_CFG,
    "tf_weights": {"3m": 3.0, "15m": 2.0, "1h": 1.0, "4h": 1.0, "D": 0.5},
    "entry_min_tf": 2,
    "entry_z_threshold": 1.5,
    "entry_accel_threshold": 0.0,
    "speed_smooth": 5,
    "tf_z_threshold": 1.5,
    "htf_gate": "4h_D",
    "cooldown_bars": 20,
    "exit_type": "speed_decay",
    "exit_tf": "3m",
    "exit_speed_pct": 20,
    "max_hold_bars": 30,
    "atr_entry_filter": 0,
    "giveback_pct": 50,
    "atr_trail_mult": 2.0,
}

STOCKS_ST_ON = {
    **DEFAULT_CFG,
    "tf_weights": {"5m": 2.0, "15m": 3.0, "1h": 2.0, "4h": 1.0, "D": 0.5},
    "entry_min_tf": 3,
    "entry_z_threshold": 2.5,
    "entry_accel_threshold": 0.3,
    "speed_smooth": 5,
    "tf_z_threshold": 1.5,
    "htf_gate": "4h",
    "cooldown_bars": 60,
    "exit_type": "speed_decay",
    "exit_tf": "15m",
    "exit_speed_pct": 70,
    "max_hold_bars": 30,
    "atr_entry_filter": 1,
    "giveback_pct": 50,
    "atr_trail_mult": 2.0,
}

STOCKS_LT_ON = {
    **DEFAULT_CFG,
    "tf_weights": {"5m": 0.5, "15m": 1.0, "1h": 2.0, "4h": 3.0, "D": 2.0},
    "entry_min_tf": 2,
    "entry_z_threshold": 2.0,
    "entry_accel_threshold": 0.0,
    "speed_smooth": 5,
    "tf_z_threshold": 1.5,
    "htf_gate": "4h_D",
    "cooldown_bars": 240,
    "exit_type": "combined_wt_speed",
    "exit_tf": "4h",
    "exit_speed_pct": 50,
    "max_hold_bars": 240,
    "atr_entry_filter": 1,
    "giveback_pct": 50,
    "atr_trail_mult": 2.0,
}

# OFF = old defaults (no HTF gate, no cooldown, no exit logic — just forward returns)
OLD_DEFAULTS_CRYPTO = {
    **DEFAULT_CFG,
    "tf_weights": {"3m": 3.0, "15m": 2.0, "1h": 1.0, "4h": 1.0, "D": 0.5},
    "entry_min_tf": 4,
    "entry_z_threshold": 1.5,
    "entry_accel_threshold": 0.2,
    "speed_smooth": 3,
    "tf_z_threshold": 1.0,
    "htf_gate": "none",
    "cooldown_bars": 0,
}

OLD_DEFAULTS_STOCKS = {
    **DEFAULT_CFG,
    "tf_weights": {"5m": 0.30, "15m": 0.25, "1h": 0.20, "4h": 0.15, "D": 0.10},
    "entry_min_tf": 4,
    "entry_z_threshold": 1.5,
    "entry_accel_threshold": 0.2,
    "speed_smooth": 3,
    "tf_z_threshold": 1.0,
    "htf_gate": "none",
    "cooldown_bars": 0,
}


def run_test(npz_dir, configs, labels):
    """Run all configs on all symbols, report comparison."""
    npz_files = sorted(npz_dir.glob("*.npz"))
    logger.info(f"Testing {len(npz_files)} symbols × {len(configs)} configs")
    results = {}
    for label, cfg, use_v2 in configs:
        t0 = time.time()
        all_stats = []
        for npz_path in npz_files:
            if use_v2:
                stat = backtest_symbol_v2(npz_path, cfg)
            else:
                stat = backtest_symbol(npz_path, cfg, forward_bars=20)
            if stat and stat["trades"] > 0:
                all_stats.append((npz_path.stem, stat))
        elapsed = time.time() - t0
        if not all_stats:
            logger.warning(f"  {label}: NO TRADES")
            continue
        valid = [(name, s) for name, s in all_stats if s["trades"] >= 3]
        total_trades = sum(s["trades"] for _, s in all_stats)
        avg_sharpe = float(np.mean([s["sharpe"] for _, s in valid])) if valid else 0
        avg_wr = float(np.mean([s["wr"] for _, s in valid])) if valid else 0
        avg_ret = float(np.mean([s["avg_ret"] for _, s in valid])) if valid else 0
        n_profitable = sum(1 for _, s in all_stats if s["sharpe"] > 0)
        n_symbols = len(all_stats)
        avg_hold = float(np.mean([s.get("avg_hold", 20) for _, s in valid])) if valid else 20
        avg_giveback = float(np.mean([s.get("avg_giveback", 0) for _, s in valid])) if valid else 0
        results[label] = {
            "sharpe": avg_sharpe,
            "wr": avg_wr,
            "avg_ret": avg_ret,
            "trades": total_trades,
            "symbols": n_symbols,
            "profitable": n_profitable,
            "pct_profitable": round(n_profitable / max(n_symbols, 1) * 100, 1),
            "avg_hold": avg_hold,
            "avg_giveback": avg_giveback,
            "elapsed": elapsed,
        }
        logger.info(f"  {label:25s}: Sharpe={avg_sharpe:+.3f} WR={avg_wr:.1f}% ret={avg_ret:+.3f}% trades={total_trades:>6} syms={n_symbols} prof={n_profitable} ({n_profitable/max(n_symbols,1)*100:.0f}%) hold={avg_hold:.0f} gb={avg_giveback:.2f} ({elapsed:.1f}s)")
    # Summary
    logger.info(f"\n{'='*100}")
    logger.info(f"{'Config':25s} {'Sharpe':>8} {'WR':>6} {'AvgRet':>8} {'Trades':>7} {'Syms':>5} {'Prof%':>6} {'Hold':>5} {'GvBk':>5}")
    logger.info(f"{'-'*100}")
    for label, r in results.items():
        marker = " ★" if r["sharpe"] == max(x["sharpe"] for x in results.values()) else ""
        logger.info(f"{label:25s} {r['sharpe']:>+7.3f} {r['wr']:>5.1f}% {r['avg_ret']:>+7.3f}% {r['trades']:>7} {r['symbols']:>5} {r['pct_profitable']:>5.1f}% {r['avg_hold']:>5.0f} {r['avg_giveback']:>5.2f}{marker}")
    logger.info(f"{'='*100}")
    # Save
    out = Path("data/delta_sweep") / f"onoff_test_{int(time.time())}.json"
    with open(out, "w") as f:
        json.dump(results, f, indent=2)
    logger.info(f"Saved to {out}")
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--crypto", action="store_true")
    parser.add_argument("--stocks", action="store_true")
    args = parser.parse_args()
    if args.crypto:
        configs = [
            ("OLD_defaults_fwd20", OLD_DEFAULTS_CRYPTO, False),
            ("V2_crypto_ST", CRYPTO_ST_ON, True),
            ("V2_crypto_LT", CRYPTO_LT_ON, True),
        ]
        run_test(CRYPTO_NPZ, configs, ["OFF", "ST", "LT"])
    elif args.stocks:
        configs = [
            ("OLD_defaults_fwd20", OLD_DEFAULTS_STOCKS, False),
            ("V2_stocks_ST", STOCKS_ST_ON, True),
            ("V2_stocks_LT", STOCKS_LT_ON, True),
        ]
        run_test(STOCK_NPZ, configs, ["OFF", "ST", "LT"])
    else:
        print("Usage: --crypto | --stocks")
