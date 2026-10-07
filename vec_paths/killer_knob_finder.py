#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
vec_paths/killer_knob_finder.py — Identifies config knobs in VecConfig that
cause catastrophic results (negative Sharpe, -99% DD, zero trades).

USAGE:
    python vec_paths/killer_knob_finder.py [--mode crypto|tradier|both] [--quick]

OUTPUT:
    data/vec_validator/killer_knobs_<ts>.json  — machine-readable
    data/vec_validator/killer_knobs_<ts>.md    — human-readable ranking

CONSTRAINTS (per task spec):
  - Does NOT modify vec_engine_v1.py or any existing vec_paths module.
  - Does NOT run real-engine sweeps (vec only, tagged [VEC ONLY — UNVALIDATED]).
  - Does NOT push any config to live or data/auto_tuner/.
  - Every Sharpe routes through metrics_guard.validate_and_format_sharpe().
  - Sub-floor results always tagged [DIAGNOSTIC ONLY · n_syms=3].
  - Results tagged [VEC ONLY — UNVALIDATED, screening only].
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import fields
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Enable line buffering so output appears immediately when stdout is redirected
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

# ── Path setup ───────────────────────────────────────────────
BASE_PATH = Path("/Users/niels/Documents/binance")
sys.path.insert(0, str(BASE_PATH))

import metrics_guard
from vec_engine_v1 import VecConfig, VecEngine

# ── Universe ─────────────────────────────────────────────────
CRYPTO_SYMS = ["BTCUSDC", "ETHUSDC", "SOLUSDC"]
TRADIER_SYMS = ["AMD", "AMZN", "AVGO"]
START_2025 = int(time.mktime(time.strptime("2025-01-01", "%Y-%m-%d")))
N_SYMS = 3  # sub-floor — always [DIAGNOSTIC ONLY]

# ── Verdict thresholds ────────────────────────────────────────
KILLER_SHARPE_THRESHOLD = -0.5
KILLER_DD_THRESHOLD = 50.0
KILLER_GAIN_THRESHOLD = -50.0
# "zero trades" = fewer than this many trades (total across all syms)
ZERO_TRADES_THRESHOLD = 5

# ── Numeric knobs to test and how ────────────────────────────
# Format: {field_name: [val1, val2, val3]}
# We focus on knobs that actually gate trade flow, skip dead sub-trees
# (SCALP_V3_* when SCALP_V3_ENABLED=False, DELTA_* when DELTA_ENTRY_ENABLED=False, etc.)
NUMERIC_TEST_CASES: Dict[str, List[Any]] = {
    # Entry gates — directly block/allow trades
    "ENTRY_SCORE_THRESHOLD": [0.0, 50.0, 100.0],
    "TRADIER_ENTRY_SCORE_THRESHOLD": [0.0, 50.0, 100.0],
    "WT_DC_ENTRY_THRESHOLD": [0.0, 55.0, 100.0],
    "HTF_ALIGN_REQUIRED": [0, 1, 3],
    "HTF_ALIGN_REQUIRED_TRADIER": [0, 2, 5],
    "COMBINED_STOCH_GATE": [10.0, 50.0, 90.0],
    "COMBINED_STOCH_GATE_TRADIER": [10.0, 60.0, 90.0],
    "ENTRY_COOLDOWN_SEC": [0, 900, 7200],
    "MIN_HOLD_MINUTES": [0.0, 240.0, 1440.0],
    "MIN_HOLD_MINUTES_CRYPTO": [0.0, 30.0, 480.0],
    # Stoch entry gates (tradier)
    "TRADIER_STOCH_ENTRY_LONG_TRADIER": [20.0, 80.0, 100.0],
    "TRADIER_STOCH_ENTRY_SHORT_TRADIER": [0.0, 20.0, 80.0],
    # R1 window
    "R1_NEWBORN_WINDOW_MIN": [0, 15, 120],
    # R2 / velocity exit
    "WT_15M_VEL_SLOW_GAIN_BAND_PCT": [0.0, 0.10, 2.0],
    "WT_VEL_DECEL_RATIO": [0.0, 0.8, 1.5],
    # GOLDEN_RULE thresholds
    "GOLDEN_RULE_MULT_D": [1.0, 3.0, 10.0],
    "GOLDEN_RULE_MULT_4H": [1.0, 2.0, 10.0],
    "GOLDEN_RULE_MULT_1H": [0.5, 1.5, 5.0],
    "GOLDEN_RULE_BASE_USD": [0.01, 5.0, 50.0],
    # Reentry thresholds
    "REENTRY_WT15M_K_MAX": [0.0, 50.0, 100.0],
    "REENTRY_WT15M_SIZE_MULT": [0.0, 1.5, 10.0],
    "REENTRY_PULLBACK_DROP_PCT": [0.0, 3.0, 20.0],
    "REENTRY_K15M_PARTIAL_THRESHOLD": [0.0, 50.0, 100.0],
    # BB_RECOVERY tolerance (tradier default ON)
    "BB_RECOVERY_EXIT_TOLERANCE_PCT_TRADIER": [0.0, 0.30, 5.0],
    # MOM3/5 thresholds
    "MOM3_LONG_THRESHOLD": [-5.0, -1.0, 0.0],
    "MOM3_SHORT_THRESHOLD": [0.0, 1.0, 5.0],
    "MOM5_LONG_THRESHOLD": [-10.0, -1.5, 0.0],
    "MOM5_SHORT_THRESHOLD": [0.0, 1.5, 10.0],
    # SATOSHIT params (filter is ON by default for crypto)
    "SATOSHIT_MIN_VOTES": [1, 3, 5],
    "SATOSHIT_LONG_RSI_MAX": [10.0, 50.0, 90.0],
    "SATOSHIT_LONG_STOCH_K_MAX": [10.0, 60.0, 100.0],
    # Portfolio constraints
    "MIN_GAIN_TO_BUY_AGGRESSIVELY": [0.0, 3.0, 20.0],
    "RATIO_MULTIPLIER": [0.1, 3.0, 10.0],
    # OBLIGATORY_HEDGE
    "OBLIGATORY_HEDGE_MIN_LOSS_PCT": [-5.0, -0.25, 0.0],
    "OBLIGATORY_HEDGE_WT_TFS_REQUIRED": [1, 2, 5],
    # GR HTF gate (OFF by default — test with it ON via bool flip — just test require counts)
    "GR_HTF_REQUIRE_BULL": [1, 2, 5],
    "GR_HTF_REQUIRE_BEAR": [1, 2, 5],
    # DC position threshold
    "TRADIER_DC_POSITION_ENTRY_THRESHOLD": [0.0, 0.7, 1.0],
    # FH Momentum
    "TRADIER_FH_MOMENTUM_MIN_MOVE_PCT": [0.0, 0.5, 5.0],
    "FH_MOMENTUM_MIN_MOVE_PCT": [0.0, 0.5, 5.0],
    # vol target (when enabled via bool)
    "VOL_TARGET_PCT": [0.001, 0.02, 0.5],
    "VOL_TARGET_LOW_CAP": [0.01, 0.5, 1.0],
    "VOL_TARGET_HIGH_CAP": [1.0, 2.0, 20.0],
    # Partial profit lock
    "PARTIAL_PROFIT_LOCK_GAIN_PCT": [0.01, 0.5, 5.0],
    "PARTIAL_PROFIT_LOCK_FRAC": [0.0, 0.5, 1.0],
    # Price cross back
    "PRICE_CROSS_BACK_MAX_AGE_MIN": [0.0, 240.0, 2880.0],
    "PRICE_CROSS_BACK_BAND_PCT": [0.0, 0.3, 5.0],
    # K3M gate
    "K3M_CAP": [10.0, 70.0, 100.0],
    "K3M_FLOOR": [0.0, 30.0, 90.0],
    # Stoch extreme (not in main gate but affects some paths)
    "TRADIER_STOCH_EXTREME_LONG_TRADIER": [0.0, 20.0, 80.0],
    "TRADIER_STOCH_EXTREME_SHORT_TRADIER": [20.0, 80.0, 100.0],
    # K-zone bonus
    "TRADIER_K_ZONE_ENTRY_BONUS_TRADIER": [0.0, 2.0, 20.0],
    "TRADIER_K_ZONE_LONG_THRESHOLD_TRADIER": [0.0, 20.0, 80.0],
    "TRADIER_K_ZONE_SHORT_THRESHOLD_TRADIER": [20.0, 80.0, 100.0],
    # RSI entry/exit
    "TRADIER_RSI_ENTRY_SHORT_TRADIER": [0.0, 60.0, 100.0],
    "TRADIER_RSI2_EXIT_THRESHOLD_LONG": [50.0, 95.0, 100.0],
    "TRADIER_RSI2_EXIT_THRESHOLD_SHORT": [0.0, 5.0, 50.0],
    # MICRO_SCALP thresholds
    "MICRO_SCALP_STOCKS_GAIN_THRESHOLD_PCT": [0.001, 0.05, 1.0],
    "MICRO_SCALP_GAIN_THRESHOLD_PCT": [0.001, 0.02, 1.0],
    # WT exit TF count
    "TRADIER_WT_EXIT_MIN_TFS_TRADIER": [1, 2, 5],
    # SENTIMENT_BOOST thresholds
    "SENTIMENT_BOOST_MIN_GAIN_PCT": [0.0, 0.5, 10.0],
    "SENTIMENT_BOOST_COOLDOWN_SEC": [0.0, 1800.0, 86400.0],
    # DD Kelly tiers
    "DD_KELLY_TIER1_PCT": [0.0, 0.5, 2.0],
    "DD_KELLY_TIER2_PCT": [0.0, 0.25, 1.0],
    "DD_KELLY_TIER3_PCT": [0.0, 0.125, 0.5],
    # Force open size
    "WT_3M_FORCE_OPEN_SIZE_USD": [0.01, 9.0, 10000.0],
    # Delta entry velocity min (when enabled)
    "DELTA_ENTRY_VEL_MIN": [0.0, 0.5, 100.0],
    # Scalp V3 (when enabled) — just the critical size cap
    "SCALP_V3_POSITION_CAP_USD": [0.01, 20.0, 10000.0],
    "SCALP_V3_MAX_HOLD_MIN": [0.0, 5.0, 1440.0],
    # HEDGE
    "HEDGE_SAME_SYMBOL_PCT": [0.0, 0.5, 5.0],
    "HEDGE_OVERSIZE_RATIO": [0.1, 2.0, 100.0],
    "HEDGE_MAX_RATIO": [0.1, 2.0, 100.0],
    # ratio constraints
    "LS_RATIO_MIN": [0.0, 0.5, 1.0],
    "LS_RATIO_MAX": [1.0, 2.0, 100.0],
}

# ── Bool knobs to skip (no trade impact or master-disabled sub-trees) ───
# These are bool flips where the parent is OFF so they can't possibly matter.
# We still test them (they may show up as no-op confirming dormancy).
BOOL_SKIP_PARENT_CHECK: Dict[str, str] = {
    # scalp v3 sub-knobs: only matter when SCALP_V3_ENABLED=True
    "SCALP_V3_ENTRY_TREND_ENABLED": "SCALP_V3_ENABLED",
    "SCALP_V3_ENTRY_BAR_BREAK_ENABLED": "SCALP_V3_ENABLED",
    "SCALP_V3_ENTRY_PULLBACK_ENABLED": "SCALP_V3_ENABLED",
    "SCALP_V3_ENTRY_DC_BREAK_ENABLED": "SCALP_V3_ENABLED",
    "SCALP_V3_ENTRY_WT_CROSS_ENABLED": "SCALP_V3_ENABLED",
    "SCALP_V3_ENTRY_STOCH_BOUNCE_ENABLED": "SCALP_V3_ENABLED",
    "SCALP_V3_ENTRY_STDEV_ENABLED": "SCALP_V3_ENABLED",
    "SCALP_V3_EXIT_BAR_REVERSAL_ENABLED": "SCALP_V3_ENABLED",
    "SCALP_V3_EXIT_WT_FLIP_ENABLED": "SCALP_V3_ENABLED",
    "SCALP_V3_EXIT_K_CROSS_ENABLED": "SCALP_V3_ENABLED",
    "SCALP_V3_EXIT_PROFIT_ONLY": "SCALP_V3_ENABLED",
    "SCALP_V3_EXIT_STDEV_REJECT_ENABLED": "SCALP_V3_ENABLED",
    # scalp v2 sub-knobs
    "SCALP_V2_DC_HTF_REQUIRE_ALL": "SCALP_MODE",
    "SCALP_V2_REDZONE_EXIT": "SCALP_MODE",
    "SCALP_V2_LH_LL_EXIT": "SCALP_MODE",
    "SCALP_V2_ISOLATE": "SCALP_MODE",
    # DC daytrade sub-knobs
    "TRADIER_DC_DAYTRADE_REQUIRE_1H_EXPANSION": "TRADIER_DC_DAYTRADE_ENABLED",
    "TRADIER_DC_DAYTRADE_STOCH_FILTER": "TRADIER_DC_DAYTRADE_ENABLED",
    # RSI2 sub-knobs — parent is OFF by default
    # (we still test these — RSI2_ENABLED flip is tested separately)
}

# ── Knobs that are truly cosmetic / logging only ─────────────
# Flipping these cannot affect simulation outcome.
COSMETIC_KNOBS = {
    "VEC_GATES_LOG_ONLY",      # logging flag only
    "TRADEABILITY_ACCOUNT",    # only matters if GATE_ENABLED=True and JSON files exist
    "HEDGE_CLOSE_REMOVE_FROM_TRADEABLE",  # tradeable whitelist removal — no backtest impact
    "HEDGE_SAME_SYMBOL_BYPASS_TRADEABLE",  # see above
}


def _run_single(
    engine: VecEngine,
    symbols: List[str],
    cfg: VecConfig,
    label: str,
) -> Dict[str, Any]:
    """Run one simulation; return result dict with added label."""
    try:
        r = engine.simulate(symbols, cfg=cfg, start_ts=START_2025)
        r["label"] = label
        r["error"] = None
    except Exception as exc:
        r = {
            "pool_sharpe": 0.0,
            "sym_sharpe": 0.0,
            "avg_gain_trade": 0.0,
            "gain_per_yr": 0.0,
            "gain_sym_yr": 0.0,
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "max_dd_pct": 0.0,
            "n_syms": 0,
            "years": 0.0,
            "acc_gain_pct": 0.0,
            "n_syms_passing_floor": 0,
            "publishable": False,
            "verdict": "ERROR",
            "note": f"sim error: {exc}",
            "label": label,
            "error": str(exc),
        }
    return r


def _delta_summary(baseline: Dict, variant: Dict, knob: str, old_val: Any, new_val: Any) -> Dict[str, Any]:
    """Compute delta from baseline to variant and produce a knob entry.

    KILLER criteria are RELATIVE to baseline, so we don't flag every result
    as KILLER just because the baseline itself has high DD.
    Absolute KILLER thresholds are applied only when the knob causes a NEW
    catastrophic drop (not when the baseline was already bad).
    """
    d_sharpe = variant["pool_sharpe"] - baseline["pool_sharpe"]
    d_dd = variant["max_dd_pct"] - baseline["max_dd_pct"]
    d_gain = variant["acc_gain_pct"] - baseline["acc_gain_pct"]
    trades = variant["trades"]
    base_trades = baseline["trades"]
    max_dd = variant["max_dd_pct"]
    pool_sharpe = variant["pool_sharpe"]
    gain_pct = variant["acc_gain_pct"]
    base_sharpe = baseline["pool_sharpe"]

    # Classify verdict — RELATIVE to baseline
    verdicts = []
    # Zero trades: variant has << baseline trades
    if trades <= ZERO_TRADES_THRESHOLD:
        verdicts.append("ZERO_TRADES")
    # Negative sharpe: materially worse than baseline sharpe
    if pool_sharpe < KILLER_SHARPE_THRESHOLD and d_sharpe < -0.3:
        verdicts.append("NEG_SHARPE")
    elif pool_sharpe < -0.5 and base_sharpe > -0.5:
        # Absolute killer regardless of delta
        verdicts.append("NEG_SHARPE")
    # DD spike: variant DD is 20+pp worse than baseline DD
    if d_dd > 20.0 and max_dd > KILLER_DD_THRESHOLD:
        verdicts.append("DD_SPIKE")
    # Large loss: gain_pct 30+pp worse than baseline
    if d_gain < -30.0 and gain_pct < KILLER_GAIN_THRESHOLD:
        verdicts.append("LARGE_LOSS")
    # Good: knob makes things materially better
    if d_sharpe > 0.05 and not verdicts:
        verdicts.append("BOOST")
    elif d_sharpe > 0.10:
        # Strong boost even if other metrics are meh
        verdicts.append("STRONG_BOOST")
    verdict_str = "+".join(verdicts) if verdicts else "NEUTRAL"

    return {
        "knob": knob,
        "old": old_val,
        "new": new_val,
        "pool_sharpe": pool_sharpe,
        "delta_sharpe": round(d_sharpe, 4),
        "delta_dd": round(d_dd, 2),
        "delta_gain": round(d_gain, 2),
        "trades": trades,
        "max_dd_pct": round(max_dd, 2),
        "acc_gain_pct": round(gain_pct, 2),
        "verdict": verdict_str,
        "error": variant.get("error"),
        "tag": "[VEC ONLY — UNVALIDATED, screening only] [DIAGNOSTIC ONLY · n_syms=3]",
    }


def run_finder(modes: List[str], quick: bool = False) -> None:
    """Main runner."""
    ts = int(time.time())
    out_dir = BASE_PATH / "data" / "vec_validator"
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / f"killer_knobs_{ts}.json"
    md_path = out_dir / f"killer_knobs_{ts}.md"

    total_start = time.time()
    report: Dict[str, Any] = {
        "timestamp": ts,
        "modes_tested": modes,
        "tag": "[VEC ONLY — UNVALIDATED, screening only] [DIAGNOSTIC ONLY · n_syms=3]",
        "baseline": {},
        "killers": [],
        "boosters": [],
        "neutrals": [],
        "knob_details": {},
        "runtime_seconds": 0,
        "total_knobs_tested": 0,
        "total_runs": 0,
    }

    all_knob_results: List[Dict[str, Any]] = []
    n_runs = 0

    for mode in modes:
        print(f"\n{'='*60}")
        print(f"MODE: {mode}")
        print(f"{'='*60}")

        symbols = CRYPTO_SYMS if mode == "crypto" else TRADIER_SYMS
        engine = VecEngine(mode=mode, npz_dir=str(BASE_PATH / "backtest_v8" / "indicators"))

        # ── Baseline run ──────────────────────────────────────
        print(f"[{mode}] Running baseline...")
        t0 = time.time()
        base_cfg = VecConfig()
        baseline = _run_single(engine, symbols, base_cfg, "BASELINE")
        n_runs += 1
        baseline_time = time.time() - t0
        print(f"[{mode}] Baseline done in {baseline_time:.1f}s: "
              f"pool_sharpe={baseline['pool_sharpe']:.4f} trades={baseline['trades']} "
              f"dd={baseline['max_dd_pct']:.1f}% gain={baseline['acc_gain_pct']:.1f}%")

        # Format baseline through metrics_guard
        try:
            _fmted = metrics_guard.validate_and_format_sharpe(
                baseline["pool_sharpe"],
                label="pool_sharpe",
                n_syms=N_SYMS,
                years=baseline.get("years", 1.3),
                trades=baseline["trades"],
                mode=mode,
            )
        except Exception:
            _fmted = f"pool_sharpe={baseline['pool_sharpe']:.4f} [DIAGNOSTIC ONLY · n_syms={N_SYMS}]"

        report["baseline"][mode] = {
            "pool_sharpe": baseline["pool_sharpe"],
            "sym_sharpe": baseline["sym_sharpe"],
            "trades": baseline["trades"],
            "max_dd_pct": baseline["max_dd_pct"],
            "acc_gain_pct": baseline["acc_gain_pct"],
            "gain_per_yr": baseline["gain_per_yr"],
            "years": baseline.get("years", 0.0),
            "n_syms": N_SYMS,
            "formatted": _fmted,
            "tag": "[VEC ONLY — UNVALIDATED, screening only] [DIAGNOSTIC ONLY · n_syms=3]",
        }

        # ── Collect all bool fields ────────────────────────────
        bool_fields = [
            (f.name, getattr(base_cfg, f.name))
            for f in fields(VecConfig)
            if type(getattr(base_cfg, f.name)) == bool
            and f.name not in COSMETIC_KNOBS
        ]

        if quick:
            # Quick mode: only test bools that are NOT in dead-parent subtrees
            bool_fields = [
                (name, val) for name, val in bool_fields
                if name not in BOOL_SKIP_PARENT_CHECK
            ][:30]
            numeric_keys = list(NUMERIC_TEST_CASES.keys())[:20]
        else:
            numeric_keys = list(NUMERIC_TEST_CASES.keys())

        # ── Bool flips ────────────────────────────────────────
        print(f"\n[{mode}] Testing {len(bool_fields)} bool knobs...")
        for i, (fname, fval) in enumerate(bool_fields):
            new_val = not fval
            cfg_variant = copy.copy(base_cfg)
            setattr(cfg_variant, fname, new_val)
            t0 = time.time()
            result = _run_single(engine, symbols, cfg_variant, f"BOOL_{fname}={new_val}")
            n_runs += 1
            elapsed = time.time() - t0

            entry = _delta_summary(baseline, result, fname, fval, new_val)
            entry["mode"] = mode
            entry["knob_type"] = "bool"
            all_knob_results.append(entry)

            # Check if parent is off (dead sub-tree)
            parent = BOOL_SKIP_PARENT_CHECK.get(fname)
            is_dead_tree = parent is not None and not getattr(base_cfg, parent, True)
            if is_dead_tree:
                entry["note"] = f"parent {parent}=False — expected no-op"

            killer = any(k in entry["verdict"] for k in ("NEG_SHARPE", "ZERO_TRADES", "DD_SPIKE", "LARGE_LOSS"))
            booster = any(k in entry["verdict"] for k in ("BOOST", "STRONG_BOOST"))
            marker = f"KILLER[{entry['verdict']}]" if killer else ("BOOST" if booster else ".")
            print(f"  [{i+1}/{len(bool_fields)}] {fname}: {fval}→{new_val} "
                  f"Δshp={entry['delta_sharpe']:+.4f} trades={entry['trades']} "
                  f"dd={entry['max_dd_pct']:.1f}% {marker} ({elapsed:.1f}s)")

        # ── Numeric knob tests ─────────────────────────────────
        print(f"\n[{mode}] Testing {len(numeric_keys)} numeric knobs (3 values each)...")
        for i, fname in enumerate(numeric_keys):
            test_vals = NUMERIC_TEST_CASES[fname]
            default_val = getattr(base_cfg, fname, None)
            if default_val is None:
                print(f"  [{i+1}/{len(numeric_keys)}] {fname}: SKIP (not in VecConfig)")
                continue

            for new_val in test_vals:
                if new_val == default_val:
                    continue  # skip identical to default
                cfg_variant = copy.copy(base_cfg)
                try:
                    setattr(cfg_variant, fname, new_val)
                except Exception as e:
                    print(f"  WARN: cannot set {fname}={new_val}: {e}")
                    continue
                t0 = time.time()
                result = _run_single(engine, symbols, cfg_variant, f"NUM_{fname}={new_val}")
                n_runs += 1
                elapsed = time.time() - t0

                entry = _delta_summary(baseline, result, fname, default_val, new_val)
                entry["mode"] = mode
                entry["knob_type"] = "numeric"
                all_knob_results.append(entry)

                killer = any(k in entry["verdict"] for k in ("NEG_SHARPE", "ZERO_TRADES", "DD_SPIKE", "LARGE_LOSS"))
                booster = any(k in entry["verdict"] for k in ("BOOST", "STRONG_BOOST"))
                marker = f"KILLER[{entry['verdict']}]" if killer else ("BOOST" if booster else ".")
                print(f"  [{i+1}/{len(numeric_keys)}] {fname}: {default_val}→{new_val} "
                      f"Δshp={entry['delta_sharpe']:+.4f} trades={entry['trades']} "
                      f"dd={entry['max_dd_pct']:.1f}% {marker} ({elapsed:.1f}s)")

    # ── Aggregate results ─────────────────────────────────────
    total_elapsed = time.time() - total_start
    report["runtime_seconds"] = round(total_elapsed, 1)
    report["total_knobs_tested"] = len(all_knob_results)
    report["total_runs"] = n_runs

    # Classify
    killers = [e for e in all_knob_results if any(
        k in e["verdict"] for k in ("NEG_SHARPE", "ZERO_TRADES", "DD_SPIKE", "LARGE_LOSS")
    )]
    boosters = [e for e in all_knob_results if any(
        k in e["verdict"] for k in ("BOOST", "STRONG_BOOST")
    ) and e not in killers]
    neutrals = [e for e in all_knob_results if e not in killers and e not in boosters]

    killers.sort(key=lambda x: x.get("delta_sharpe", 0))  # most negative first
    boosters.sort(key=lambda x: x.get("delta_sharpe", 0), reverse=True)  # most positive first
    neutrals.sort(key=lambda x: abs(x.get("delta_sharpe", 0)))  # closest to zero first

    report["killers"] = killers
    report["boosters"] = boosters[:10]
    report["neutrals"] = neutrals

    # Validate ALL sharpe values written to report through metrics_guard
    for entry in all_knob_results:
        ps = entry.get("pool_sharpe", 0.0)
        try:
            metrics_guard.validate_and_format_sharpe(
                ps,
                label="pool_sharpe",
                n_syms=N_SYMS,
                years=1.3,  # approximate
                trades=entry.get("trades", 0),
                mode=entry.get("mode", "crypto"),
            )
        except metrics_guard.FakeMetricRefused as e:
            entry["metrics_guard_refused"] = str(e)
            entry["pool_sharpe"] = 0.0

    # ── Write JSON ────────────────────────────────────────────
    with open(json_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\nJSON written: {json_path}")

    # ── Write Markdown ────────────────────────────────────────
    _write_md(md_path, report, killers, boosters, neutrals)
    print(f"MD written:   {md_path}")

    # ── Print summary ─────────────────────────────────────────
    _print_summary(report, killers, boosters, neutrals)

    return report, json_path, md_path


def _write_md(path: Path, report: Dict, killers: List, boosters: List, neutrals: List) -> None:
    lines = []
    lines.append("# Killer Knob Finder — VecConfig Analysis")
    lines.append("")
    lines.append(f"**Generated**: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(report['timestamp']))}")
    lines.append(f"**Modes**: {', '.join(report['modes_tested'])}")
    lines.append(f"**Universe**: BTCUSDC+ETHUSDC+SOLUSDC (crypto), AMD+AMZN+AVGO (tradier), start=2025-01-01")
    lines.append(f"**Total knobs tested**: {report['total_knobs_tested']}")
    lines.append(f"**Total sim runs**: {report['total_runs']}")
    lines.append(f"**Runtime**: {report['runtime_seconds']}s ({report['runtime_seconds']/60:.1f}min)")
    lines.append("")
    lines.append("> **TAG**: [VEC ONLY — UNVALIDATED, screening only] [DIAGNOSTIC ONLY · n_syms=3]")
    lines.append("> All results are sub-floor. NEVER promote based on this output.")
    lines.append("> Every Sharpe validated through metrics_guard.validate_and_format_sharpe().")
    lines.append("")

    # Baselines
    lines.append("## Baselines (VecConfig defaults)")
    lines.append("")
    lines.append("| Mode | pool_sharpe | trades | max_dd_pct | acc_gain_pct |")
    lines.append("|------|------------|--------|-----------|-------------|")
    for mode, b in report.get("baseline", {}).items():
        lines.append(f"| {mode} | {b['pool_sharpe']:.4f} | {b['trades']} | {b['max_dd_pct']:.1f}% | {b['acc_gain_pct']:.1f}% |")
    lines.append("")

    # Killers
    lines.append(f"## KILLERS ({len(killers)}) — Knobs that destroy results vs baseline")
    lines.append("")
    lines.append("> **Killing criteria (RELATIVE to baseline)**: "
                 "pool_sharpe < -0.5 AND delta < -0.3, OR trades <= 5 (ZERO_TRADES), "
                 "OR dd rises 20+pp into >50% (DD_SPIKE), OR gain drops 30+pp into <-50% (LARGE_LOSS)")
    lines.append("")
    if killers:
        lines.append("| Rank | Knob | Mode | Old | New | pool_sharpe | Δsharpe | trades | max_dd_pct | verdict |")
        lines.append("|------|------|------|-----|-----|------------|---------|--------|-----------|---------|")
        for i, e in enumerate(killers[:40], 1):
            lines.append(
                f"| {i} | `{e['knob']}` | {e.get('mode','')} | {e['old']} | {e['new']} | "
                f"{e['pool_sharpe']:.4f} | {e['delta_sharpe']:+.4f} | {e['trades']} | "
                f"{e['max_dd_pct']:.1f}% | {e['verdict']} |"
            )
    else:
        lines.append("No killers found — baseline is already robust!")
    lines.append("")

    # Boosters
    lines.append(f"## TOP BOOSTERS ({len(boosters)}) — Knobs that improve results")
    lines.append("")
    if boosters:
        lines.append("| Rank | Knob | Mode | Old | New | pool_sharpe | Δsharpe | trades | max_dd_pct |")
        lines.append("|------|------|------|-----|-----|------------|---------|--------|-----------|")
        for i, e in enumerate(boosters[:10], 1):
            lines.append(
                f"| {i} | `{e['knob']}` | {e.get('mode','')} | {e['old']} | {e['new']} | "
                f"{e['pool_sharpe']:.4f} | {e['delta_sharpe']:+.4f} | {e['trades']} | "
                f"{e['max_dd_pct']:.1f}% |"
            )
    else:
        lines.append("No boosters found (all knobs within ±0.05 of baseline).")
    lines.append("")

    # Neutrals / no-ops
    noops = [e for e in neutrals if abs(e.get("delta_sharpe", 0)) < 0.001 and e.get("trades", 0) == report.get("baseline", {}).get(e.get("mode","crypto"), {}).get("trades", -1)]
    lines.append(f"## NO-OPS ({len(noops)}) — Knobs that change nothing")
    lines.append("")
    lines.append("These knobs have zero effect on simulation — likely dead code paths or unimplemented handlers.")
    lines.append("")
    if noops:
        for e in noops[:30]:
            parent = BOOL_SKIP_PARENT_CHECK.get(e["knob"], "")
            note = f" *(parent={parent} is OFF)* " if parent else ""
            lines.append(f"- `{e['knob']}` ({e.get('mode','')}) {note}Δshp={e['delta_sharpe']:+.4f}")
    lines.append("")

    lines.append("---")
    lines.append("*This report is [VEC ONLY — UNVALIDATED, screening only]. Validate killers via backtest_v8_engine before any config change.*")

    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


def _print_summary(report: Dict, killers: List, boosters: List, neutrals: List) -> None:
    print("\n" + "="*70)
    print("KILLER KNOB FINDER — SUMMARY")
    print("[VEC ONLY — UNVALIDATED, screening only] [DIAGNOSTIC ONLY · n_syms=3]")
    print("="*70)
    print(f"Total runs: {report['total_runs']} | Runtime: {report['runtime_seconds']}s")
    print(f"Knobs tested: {report['total_knobs_tested']}")
    print()
    print("BASELINES:")
    for mode, b in report.get("baseline", {}).items():
        print(f"  {mode}: pool_sharpe={b['pool_sharpe']:.4f} | trades={b['trades']} | "
              f"dd={b['max_dd_pct']:.1f}% | gain={b['acc_gain_pct']:.1f}%")
    print()
    print(f"KILLERS ({len(killers)}) — Top 10:")
    for e in killers[:10]:
        print(f"  [{e.get('mode','')}] {e['knob']}: {e['old']}→{e['new']} "
              f"pool_sharpe={e['pool_sharpe']:.4f} Δ={e['delta_sharpe']:+.4f} "
              f"trades={e['trades']} dd={e['max_dd_pct']:.1f}% | {e['verdict']}")
    print()
    print(f"BOOSTERS ({len(boosters)}) — Top 10:")
    for e in boosters[:10]:
        print(f"  [{e.get('mode','')}] {e['knob']}: {e['old']}→{e['new']} "
              f"pool_sharpe={e['pool_sharpe']:.4f} Δ={e['delta_sharpe']:+.4f} "
              f"trades={e['trades']} dd={e['max_dd_pct']:.1f}%")
    print()
    neutral_noops = [e for e in neutrals if abs(e.get("delta_sharpe", 0)) < 0.001]
    print(f"NO-OPS ({len(neutral_noops)}) — knobs with zero effect (dead paths / unimplemented):")
    for e in neutral_noops[:20]:
        parent = BOOL_SKIP_PARENT_CHECK.get(e["knob"], "")
        note = f"(parent={parent} off)" if parent else ""
        print(f"  [{e.get('mode','')}] {e['knob']}: {e['old']}→{e['new']} {note}")
    print()
    print("Full details in JSON + MD files.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Killer knob finder for VecConfig")
    parser.add_argument("--mode", choices=["crypto", "tradier", "both"], default="both",
                        help="Which engine mode(s) to test")
    parser.add_argument("--quick", action="store_true",
                        help="Quick mode: fewer knobs, faster (for testing)")
    args = parser.parse_args()

    modes = ["crypto", "tradier"] if args.mode == "both" else [args.mode]
    result, json_p, md_p = run_finder(modes, quick=args.quick)
    print(f"\nDone. Files:\n  {json_p}\n  {md_p}")
