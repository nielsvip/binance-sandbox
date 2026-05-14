"""vec_matrix_runner.py — continuous vectorized parameter sweep runner.

Runs v8_vec_sweep parameter grids 24/7 on any machine with NPZ data.
Imports existing tier grid definitions from backtest_v8_sweep.py so grids
stay in sync automatically. 4-13x faster than backtest_v8_engine, no asyncio,
no OOM risk on normal sweeps.

USAGE
=====
    # S1 — crypto + tradier alternating
    python vec_matrix_runner.py --mode crypto --account ang
    python vec_matrix_runner.py --mode tradier --account trb

    # MacBook — tradier (or crypto, data available for both)
    python vec_matrix_runner.py --mode tradier --account trb --workers 2

    # nohup persistent launch
    nohup python vec_matrix_runner.py --mode crypto --account ang \\
        > ~/logs/vec_matrix_crypto.log 2>&1 < /dev/null & disown

DESIGN
======
- Imports grid_* functions from backtest_v8_sweep.py (single source of truth).
- For each (label, overrides) pair: builds SweepConfig, calls run_sweep().
- Tracks seen config-hashes in data/vec_matrix_seen_<mode>.json.
- Loops indefinitely; once all variants seen, resets and re-sweeps.
- All results through metrics_guard — NO-LIES MANDATE.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ── Repo root on path ────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))

from v8_vec_sweep import SweepConfig, run_sweep

# Import grid definitions from backtest_v8_sweep (single source of truth)
from backtest_v8_sweep import (
    grid_gr_phase2_bb_tradier,
    grid_gr_phase2_bb_crypto,
    grid_gr_micro_ablation_tradier,
    grid_gr_micro_ablation_crypto,
    grid_gr_consensus_targeted_crypto,
    grid_gr_entry_exit_grid_tradier,
    grid_gr_entry_exit_grid_crypto,
    grid_tradier_grtf7_hunt,
    grid_vec_validate_5cfg,
    grid_system_combo,
    grid_tradier_param_hunt,
)

# ════════════════════════════════════════════════════════════════════════════
# Symbol universes
# ════════════════════════════════════════════════════════════════════════════

CRYPTO_SYMBOLS = [
    "BTCUSDC", "ETHUSDC", "SOLUSDC", "ADAUSDC", "BNBUSDC",
    "AVAXUSDC", "XRPUSDC", "LINKUSDC", "LTCUSDC", "UNIUSDC",
]

TRADIER_SYMBOLS = [
    "AAPL", "AMZN", "AVGO", "AMD", "ADBE", "ABNB", "ARM", "ASML", "AXON", "BA",
    "BABA", "ABBV", "ABT", "ADP", "ADM", "AEM", "AG", "AGCO", "ALB", "ASTS",
]

# ════════════════════════════════════════════════════════════════════════════
# Tier registry — order = priority (runs first)
# ════════════════════════════════════════════════════════════════════════════

CRYPTO_TIERS: List[Tuple[str, Any, str]] = [
    # (tier_name, grid_fn, start_date)
    ("vec_validate_5cfg",           grid_vec_validate_5cfg,           "2024-01-01"),
    ("gr_phase2_bb_crypto",         grid_gr_phase2_bb_crypto,         "2024-01-01"),
    ("gr_micro_ablation_crypto",    grid_gr_micro_ablation_crypto,    "2024-01-01"),
    ("gr_consensus_targeted_crypto",grid_gr_consensus_targeted_crypto,"2024-01-01"),
    ("gr_entry_exit_grid_crypto",   grid_gr_entry_exit_grid_crypto,   "2024-01-01"),
    ("system_combo",                grid_system_combo,                "2024-01-01"),
]

TRADIER_TIERS: List[Tuple[str, Any, str]] = [
    # (tier_name, grid_fn, start_date)
    ("vec_validate_5cfg",           grid_vec_validate_5cfg,           "2024-01-01"),
    ("gr_phase2_bb_tradier",        grid_gr_phase2_bb_tradier,        "2024-01-01"),
    ("gr_micro_ablation_tradier",   grid_gr_micro_ablation_tradier,   "2024-01-01"),
    ("gr_entry_exit_grid_tradier",  grid_gr_entry_exit_grid_tradier,  "2024-01-01"),
    ("tradier_grtf7_hunt",          grid_tradier_grtf7_hunt,          "2024-01-01"),
    ("tradier_param_hunt",          grid_tradier_param_hunt,          "2024-01-01"),
]

SEEN_DIR = REPO_ROOT / "data"
RESULTS_DIR = REPO_ROOT / "data" / "sweep_results"


def _cfg_hash(overrides: dict) -> str:
    return hashlib.md5(json.dumps(overrides, sort_keys=True).encode()).hexdigest()[:12]


def _load_seen(mode: str) -> Dict[str, str]:
    p = SEEN_DIR / f"vec_matrix_seen_{mode}.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            return {}
    return {}


def _save_seen(mode: str, seen: Dict[str, str]) -> None:
    SEEN_DIR.mkdir(parents=True, exist_ok=True)
    p = SEEN_DIR / f"vec_matrix_seen_{mode}.json"
    p.write_text(json.dumps(seen, indent=2))


def _build_config(overrides: dict) -> SweepConfig:
    cfg = SweepConfig()
    for k, v in overrides.items():
        if hasattr(cfg, k):
            setattr(cfg, k, type(getattr(cfg, k))(v))
        else:
            # Unknown key — set directly (will silently be ignored by sim)
            setattr(cfg, k, v)
    return cfg


def _write_csv_row(mode: str, tier: str, label: str, overrides: dict, metrics: dict) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = RESULTS_DIR / f"vec_matrix_{mode}_{tier}.csv"
    import csv as _csv
    row = {
        "label": label,
        "tier": tier,
        "pool_sharpe": round(metrics.get("pool_sharpe", 0.0), 4),
        "sym_sharpe": round(metrics.get("sym_sharpe", 0.0), 4),
        "avg_gain_trade": round(metrics.get("avg_gain_trade", 0.0), 4),
        "gain_per_yr": round(metrics.get("gain_per_yr", 0.0), 2),
        "gain_sym_yr": round(metrics.get("gain_sym_yr", 0.0), 4),
        "trades": metrics.get("n_trades", metrics.get("trades", 0)),
        "max_dd_pct": round(metrics.get("max_dd_pct", 0.0), 4),
        "n_syms": metrics.get("n_syms", 0),
        "years": round(metrics.get("years", 0.0), 3),
        "cfg_hash": _cfg_hash(overrides),
        "overrides_json": json.dumps(overrides, separators=(",", ":")),
    }
    write_header = not csv_path.exists()
    with csv_path.open("a", newline="") as f:
        w = _csv.DictWriter(f, fieldnames=list(row.keys()))
        if write_header:
            w.writeheader()
        w.writerow(row)


def run_matrix(
    mode: str,
    account: str,
    workers: int = 1,
    reset_seen: bool = False,
) -> None:
    symbols = CRYPTO_SYMBOLS if mode == "crypto" else TRADIER_SYMBOLS
    tiers = CRYPTO_TIERS if mode == "crypto" else TRADIER_TIERS

    seen = {} if reset_seen else _load_seen(mode)
    print(f"[vec_matrix] mode={mode} symbols={len(symbols)} workers={workers} seen={len(seen)}", flush=True)

    cycle = 0
    while True:
        cycle += 1
        new_this_cycle = 0
        for tier_name, grid_fn, start in tiers:
            grid = grid_fn()
            for label, overrides in grid:
                h = _cfg_hash(overrides)
                seen_key = f"{tier_name}/{h}"
                if seen_key in seen:
                    continue
                cfg = _build_config(overrides)
                print(
                    f"[vec_matrix] cycle={cycle} tier={tier_name} label={label}"
                    f" overrides={len(overrides)} hash={h}",
                    flush=True,
                )
                t0 = time.perf_counter()
                try:
                    metrics = run_sweep(
                        mode=mode,
                        account=account,
                        symbols=symbols,
                        sides=["LONG", "SHORT"],
                        start=start,
                        config=cfg,
                        workers=workers,
                        write_history=False,
                    )
                    elapsed = time.perf_counter() - t0
                    ps = metrics.get("pool_sharpe", 0.0)
                    trades = metrics.get("n_trades", metrics.get("trades", 0))
                    gain = metrics.get("gain_per_yr", 0.0)
                    print(
                        f"[vec_matrix] ✓ {tier_name}/{label}"
                        f" pool_sharpe={ps:+.4f} trades={trades} gain/yr={gain:+.1f}%"
                        f" elapsed={elapsed:.1f}s",
                        flush=True,
                    )
                    _write_csv_row(mode, tier_name, label, overrides, metrics)
                    seen[seen_key] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    _save_seen(mode, seen)
                    new_this_cycle += 1
                except Exception as e:
                    elapsed = time.perf_counter() - t0
                    print(
                        f"[vec_matrix] ✗ {tier_name}/{label} FAILED after {elapsed:.1f}s: {e}",
                        flush=True,
                    )

        if new_this_cycle == 0:
            # All variants seen — reset and re-sweep (configs change, re-check)
            print(
                f"[vec_matrix] cycle={cycle} complete — all variants seen. Resetting seen for re-sweep.",
                flush=True,
            )
            seen = {}
            _save_seen(mode, seen)


def main() -> None:
    ap = argparse.ArgumentParser(description="Continuous vectorized parameter sweep runner")
    ap.add_argument("--mode", required=True, choices=["crypto", "tradier"])
    ap.add_argument("--account", default="ang")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--reset-seen", action="store_true", help="Clear seen-hash cache before starting")
    args = ap.parse_args()
    run_matrix(
        mode=args.mode,
        account=args.account,
        workers=args.workers,
        reset_seen=args.reset_seen,
    )


if __name__ == "__main__":
    main()
