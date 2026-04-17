#!/usr/bin/env python3
"""6-chapter head-to-head sweep on the 48-symbol crypto NPZ set.

Chapters are thematic bundles. Matrix tests singletons + promising pairs vs F baseline.
"""
import json
import os
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
from v8_quick_engine import QuickConfig, load_npz, simulate

# Load the 48-symbol crypto list — same the precompute used
SYMS_48 = json.load(open(BASE / "backtest_48_symbols.json"))
START = "2022-01-01"
NPZ_DIR = str(BASE / "backtest_v8" / "indicators")

# ═══════════════════════════════════════════════════════════════════════
# Chapter definitions — bundles of switches flipped ON together
# ═══════════════════════════════════════════════════════════════════════
CHAPTERS = {
    "F_baseline": {
        # everything at crypto defaults — no overrides
    },
    "A_quality_sniper": {
        # "Top4 reentry + high selectivity"
        "REENTRY_B15_STRONG_TREND_ENABLED": True,
        "REENTRY_B04_DC_RETEST_ENABLED":    True,
        "REENTRY_B11_DC_BREAK_ENABLED":     True,
        "REENTRY_B02_BC156_BOTTOM_ENABLED": True,
        "REENTRY_B10_STOCH_REV_ENABLED":    False,
        "REENTRY_B12_WT_MOM_ENABLED":       False,
        "REENTRY_B14_HA_TREND_ENABLED":     False,   # -0.052 Sharpe loser
        "ENTRY_SCORE_THRESHOLD":            24.0,
        "K3M_FLOOR":                        30.0,
        "HTF_MIN_ALIGNED":                  3,
    },
    "B_velocity_hunter": {
        "CT_WT_VELOCITY_GATE_ENABLED":      True,
        "CT_WT_VELOCITY_1H_MIN":            2.0,
        "STRUCTURAL_RANGE_SHIFT_EXIT":      True,
        "STRUCTURAL_RANGE_SHIFT_TF":        "dc_4h",   # crypto SRS
        "DELTA_ENGINE_ENABLED":             True,
    },
    "C_race_defense": {
        # Tonight's auto-revert said these HURT crypto slightly. Re-test at 48-sym scale.
        "ENTRY_SYMGATE_ENABLED":            True,
        "REENTRY_SYMGATE_ENABLED":          True,
        "REENTRY_MIN_GAP_BARS":             5,         # ~15min on 3m
        "REENTRY_RALLY_K15M_MAX":           60.0,
    },
    "D_loss_defense": {
        # Today's live deployments — currently no backtest proof.
        # Note: HTF_EXIT_VETO/NOLOSS gates are LIVE-only (not in v8_quick_engine),
        # so this chapter proves only what the vectorized engine can see.
        # What's observable here: REENTRY_FAVORABLE_MOVE_PCT, REENTRY_SYMGATE,
        # REENTRY_MIN_GAP. The NOLOSS / HTF_EXIT_VETO changes need live/full backtest.
        "REENTRY_FAVORABLE_MOVE_PCT":       1.0,
        "REENTRY_SYMGATE_ENABLED":          True,
        "REENTRY_MIN_GAP_BARS":             5,
    },
    "E_conviction_scoring": {
        # Also live-only in ez_positions_quick — most of these aren't hooked in v8_quick_engine.
        # Setting them as a marker; agent noted they may come back inert.
        "RANK_CONVICTION_ENABLED":          True,
        "DC_MOMENT_ENABLED":                True,
        "WINNER_PROTECT_ENABLED":           True,
    },
}


def run(label, overrides, stores):
    cfg = QuickConfig()
    cfg.MODE = "crypto"
    for k, v in overrides.items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)
    t0 = time.time()
    r = simulate(stores, cfg, 10000.0)
    r["label"] = label
    r["elapsed"] = round(time.time() - t0, 1)
    r["config"] = overrides
    return r


def main():
    print(f"Loading 48-symbol NPZ from {NPZ_DIR} …")
    t0 = time.time()
    stores = load_npz("crypto", SYMS_48, START, NPZ_DIR)
    print(f"  {len(stores)} symbols loaded in {time.time()-t0:.1f}s\n")

    # MATRIX ORDER per agent recommendation:
    # F → A → D → A+D → B → A+B → E → A+E → A+D+B → C → remaining pairs
    runs = [
        ("F",     CHAPTERS["F_baseline"]),
        ("A",     CHAPTERS["A_quality_sniper"]),
        ("D",     CHAPTERS["D_loss_defense"]),
        ("A+D",   {**CHAPTERS["A_quality_sniper"], **CHAPTERS["D_loss_defense"]}),
        ("B",     CHAPTERS["B_velocity_hunter"]),
        ("A+B",   {**CHAPTERS["A_quality_sniper"], **CHAPTERS["B_velocity_hunter"]}),
        ("E",     CHAPTERS["E_conviction_scoring"]),
        ("A+E",   {**CHAPTERS["A_quality_sniper"], **CHAPTERS["E_conviction_scoring"]}),
        ("A+D+B", {**CHAPTERS["A_quality_sniper"], **CHAPTERS["D_loss_defense"], **CHAPTERS["B_velocity_hunter"]}),
        ("C",     CHAPTERS["C_race_defense"]),
        ("B+D",   {**CHAPTERS["B_velocity_hunter"], **CHAPTERS["D_loss_defense"]}),
        ("A+B+D", {**CHAPTERS["A_quality_sniper"], **CHAPTERS["B_velocity_hunter"], **CHAPTERS["D_loss_defense"]}),
        ("A+C",   {**CHAPTERS["A_quality_sniper"], **CHAPTERS["C_race_defense"]}),
        ("B+E",   {**CHAPTERS["B_velocity_hunter"], **CHAPTERS["E_conviction_scoring"]}),
        ("ALL",   {**CHAPTERS["A_quality_sniper"], **CHAPTERS["B_velocity_hunter"], **CHAPTERS["C_race_defense"], **CHAPTERS["D_loss_defense"], **CHAPTERS["E_conviction_scoring"]}),
    ]

    results = []
    best_sharpe = -999
    best_label = "?"

    print(f"{'LABEL':10s}  {'SHARPE':>9s}  {'TRADES':>7s}  {'WR':>6s}  {'AVG%':>8s}  {'PNL$':>9s}  {'SEC':>5s}")
    print("-" * 72)

    for label, overrides in runs:
        r = run(label, overrides, stores)
        results.append(r)
        if r["sharpe"] > best_sharpe:
            best_sharpe = r["sharpe"]; best_label = label
        print(f"{label:10s}  {r['sharpe']:+.4f}    {r['trades']:>6d}  {r['wr']:>5.1f}%  {r['avg_pnl_pct']:+.4f}%  {r['pnl']:>+9.2f}  {r['elapsed']:>4.1f}s")

        # Early-abort rule: Sharpe < 0.1 below best-so-far AND we've run ≥4
        if len(results) >= 4 and r["sharpe"] < (best_sharpe - 0.1):
            print(f"  ⚠ {label} below best - 0.1 gap — continuing anyway (full matrix requested)")

    print("\n" + "=" * 72)
    print(f"BEST: {best_label}  sharpe={best_sharpe:+.4f}")

    # Rank by sharpe
    ranked = sorted(results, key=lambda r: -r["sharpe"])
    print("\nRANKED (best → worst):")
    for r in ranked:
        ds = r["sharpe"] - results[0]["sharpe"]  # vs F
        print(f"  {r['label']:10s}  sharpe={r['sharpe']:+.4f}  ({'+' if ds>=0 else ''}{ds:.4f} vs F)  trades={r['trades']}")

    out = BASE / "data" / "sweep_results" / f"chapters_crypto_48sym_{int(time.time())}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nSaved: {out}")


if __name__ == "__main__":
    main()
