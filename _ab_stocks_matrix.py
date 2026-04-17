#!/usr/bin/env python3
"""Stocks matrix sweep — designed for S2 (stocks backtest machine).
Applies stock-appropriate defaults (bb_1h SRS, 15m entry-zone, HTF=2+) then tests
chapter-style bundles with HIGHER timeframes bias (stocks benefit from HTFs).

Chapters adapted from crypto winner (Sharpe 2.25 on 48-sym crypto):
  S_F = tradier baseline (apply_tradier_defaults)
  S_A = Quality sniper — tight k3m + HTF=3 + ENTRY_SCORE=24
  S_B = HTF-velocity hunter — CT_WT_VELOCITY on 1h/4h, not 3m
  S_C = Race defense — SYMGATE + 15m MIN_GAP + tight RALLY
  S_D = HTF-only entries — strict HTF_MIN_ALIGNED=3, D must align, require 4h WT
  S_E = Conviction scoring — RANK_CONVICTION + DC_MOMENT + WINNER_PROTECT
  S_H = High-TF only — entry gates on 1h/4h/D, no 3m/5m gates (stocks trend slowly)
  S_V = Ultra-velocity — CT_WT_VELOCITY_1H_MIN tuned 4, 6, 8 (stocks trend longer)
"""
import itertools
import json
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
from v8_quick_engine import QuickConfig, load_npz, simulate, FAST_SYMBOLS_TRADIER

# Expect this to run on S2 with full tradier NPZ (~128 symbols). On MacBook, falls back to FAST list.
TRADIER_ALL_JSON = BASE / "backtest_tradier_symbols.json"
if TRADIER_ALL_JSON.exists():
    SYMS = json.load(open(TRADIER_ALL_JSON))
else:
    SYMS = FAST_SYMBOLS_TRADIER.split(",")
START = "2024-01-01"
NPZ_DIR = str(BASE / "backtest_v8" / "indicators")

# ─────────────────────────────────────────────────────────────
# Chapter bundles — stocks-tuned
# ─────────────────────────────────────────────────────────────
S_A_SNIPER = {
    "REENTRY_B15_STRONG_TREND_ENABLED": True,
    "REENTRY_B04_DC_RETEST_ENABLED":    True,
    "REENTRY_B11_DC_BREAK_ENABLED":     True,
    "REENTRY_B02_BC156_BOTTOM_ENABLED": True,
    "REENTRY_B10_STOCH_REV_ENABLED":    False,
    "REENTRY_B12_WT_MOM_ENABLED":       False,
    "REENTRY_B14_HA_TREND_ENABLED":     False,
    "ENTRY_SCORE_THRESHOLD":            24.0,
    "K3M_FLOOR":                        30.0,
    "HTF_MIN_ALIGNED":                  3,
}
S_B_HTF_VELOCITY = {
    "CT_WT_VELOCITY_GATE_ENABLED":      True,
    "CT_WT_VELOCITY_1H_MIN":            4.0,   # conservative start for stocks
    "STRUCTURAL_RANGE_SHIFT_EXIT":      True,
    "STRUCTURAL_RANGE_SHIFT_TF":        "bb_1h",  # stocks SRS — NEVER swap to dc_4h
    "DELTA_ENGINE_ENABLED":             True,
}
S_C_RACE = {
    "ENTRY_SYMGATE_ENABLED":            True,
    "REENTRY_SYMGATE_ENABLED":          True,
    "REENTRY_MIN_GAP_BARS":             15,     # ~15min on 1m bars for stocks
    "REENTRY_RALLY_K15M_MAX":           50.0,
}
S_D_HTF_STRICT = {
    "HTF_MIN_ALIGNED":                  3,
    "D_TREND_REQUIRED":                 True,
    "HTF_ALIGNMENT_ENABLED":            True,
}
S_E_CONVICTION = {
    "RANK_CONVICTION_ENABLED":          True,
    "RANK_CONVICTION_MIN":              3,
    "DC_MOMENT_ENABLED":                True,
    "DC_MOMENT_OPPOSE_THRESHOLD":       40.0,
    "WINNER_PROTECT_ENABLED":           True,
    "WINNER_PROTECT_GAIN_PCT":          1.5,   # stocks gain slower → 1.5 vs crypto's 1.0
}
S_H_HIGHER_TF_ENTRY = {
    # Make ENTRY_ZONE_K_TF use 1h instead of 15m — stocks entry on deeper oversold HTF
    "ENTRY_ZONE_K_TF":                  "1h",
    "ENTRY_ZONE_LONG":                  30.0,   # 1h K<30 for LONG entry
    "ENTRY_ZONE_SHORT":                 70.0,   # 1h K>70 for SHORT entry
    "HTF_MIN_ALIGNED":                  2,
}
S_V_ULTRA_VELOCITY = {
    # Stocks trend longer — push velocity threshold higher
    "CT_WT_VELOCITY_GATE_ENABLED":      True,
    "CT_WT_VELOCITY_1H_MIN":            8.0,   # aggressive
    "DELTA_ENGINE_ENABLED":             True,
}

CHAPTERS = {
    "S_F":   {},
    "S_A":   S_A_SNIPER,
    "S_B":   S_B_HTF_VELOCITY,
    "S_C":   S_C_RACE,
    "S_D":   S_D_HTF_STRICT,
    "S_E":   S_E_CONVICTION,
    "S_H":   S_H_HIGHER_TF_ENTRY,
    "S_V":   S_V_ULTRA_VELOCITY,
}


def merge(*dicts):
    out = {}
    for d in dicts:
        out.update(d)
    return out


# Priority-ordered matrix: singletons first, then compounds informed by crypto winners.
MATRIX = [
    ("S_F",              {}),
    ("S_A",              CHAPTERS["S_A"]),
    ("S_B",              CHAPTERS["S_B"]),
    ("S_C",              CHAPTERS["S_C"]),
    ("S_D",              CHAPTERS["S_D"]),
    ("S_E",              CHAPTERS["S_E"]),
    ("S_H",              CHAPTERS["S_H"]),
    ("S_V",              CHAPTERS["S_V"]),
    # Two-chapter compounds — grounded on crypto findings (velocity + race defense won)
    ("S_B+S_C",          merge(CHAPTERS["S_B"], CHAPTERS["S_C"])),
    ("S_B+S_D",          merge(CHAPTERS["S_B"], CHAPTERS["S_D"])),
    ("S_B+S_E",          merge(CHAPTERS["S_B"], CHAPTERS["S_E"])),
    ("S_V+S_C",          merge(CHAPTERS["S_V"], CHAPTERS["S_C"])),
    ("S_V+S_E",          merge(CHAPTERS["S_V"], CHAPTERS["S_E"])),
    ("S_H+S_E",          merge(CHAPTERS["S_H"], CHAPTERS["S_E"])),
    ("S_H+S_D",          merge(CHAPTERS["S_H"], CHAPTERS["S_D"])),
    # Three-chapter stacks
    ("S_B+S_C+S_E",      merge(CHAPTERS["S_B"], CHAPTERS["S_C"], CHAPTERS["S_E"])),
    ("S_V+S_C+S_E",      merge(CHAPTERS["S_V"], CHAPTERS["S_C"], CHAPTERS["S_E"])),
    ("S_H+S_D+S_E",      merge(CHAPTERS["S_H"], CHAPTERS["S_D"], CHAPTERS["S_E"])),
    ("S_B+S_D+S_E",      merge(CHAPTERS["S_B"], CHAPTERS["S_D"], CHAPTERS["S_E"])),
    # Full stack
    ("ALL_B+C+D+E",      merge(CHAPTERS["S_B"], CHAPTERS["S_C"], CHAPTERS["S_D"], CHAPTERS["S_E"])),
    ("ALL_V+C+D+E",      merge(CHAPTERS["S_V"], CHAPTERS["S_C"], CHAPTERS["S_D"], CHAPTERS["S_E"])),
    ("ALL_H+C+D+E",      merge(CHAPTERS["S_H"], CHAPTERS["S_C"], CHAPTERS["S_D"], CHAPTERS["S_E"])),
]


def run(overrides, stores):
    cfg = QuickConfig()
    cfg.apply_tradier_defaults()   # stocks-specific defaults first
    for k, v in overrides.items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)
    return simulate(stores, cfg, 10000.0)


def main():
    print(f"STOCKS MATRIX  mode=tradier  start={START}  symbols={len(SYMS)}")
    print(f"  NPZ dir: {NPZ_DIR}")
    t0 = time.time()
    stores = load_npz("tradier", SYMS, START, NPZ_DIR)
    print(f"  {len(stores)} symbols loaded in {time.time()-t0:.1f}s\n")
    if not stores:
        print("NO STORES — aborting")
        return

    results = []
    best = {"sharpe": -999, "label": "?", "cfg": None}
    print(f"{'LABEL':20s}  {'SHARPE':>8s}  {'TRADES':>6s}  {'WR':>5s}  {'AVG%':>7s}  {'PNL$':>9s}")
    print("-" * 72)

    for label, overrides in MATRIX:
        r = run(overrides, stores)
        r["label"] = label
        r["cfg"] = overrides
        results.append(r)
        flag = ""
        if r["sharpe"] > best["sharpe"] and r["trades"] >= 10:
            best = {"sharpe": r["sharpe"], "label": label, "cfg": overrides,
                    "trades": r["trades"], "wr": r["wr"], "avg": r["avg_pnl_pct"]}
            flag = " ★"
        print(f"{label:20s}  {r['sharpe']:+.4f}  {r['trades']:>5d}  {r['wr']:4.1f}%  {r['avg_pnl_pct']:+.3f}%  {r.get('pnl', 0):>+9.2f}{flag}")

    # Ranked
    print("\n" + "=" * 72)
    if best["cfg"] is not None:
        print(f"WINNER: {best['label']}  sharpe={best['sharpe']:+.4f}  trades={best['trades']}  WR={best['wr']}%")
        print(f"  cfg: {best['cfg']}\n")
    ranked = sorted(results, key=lambda r: -r["sharpe"])
    print("RANKED (best → worst):")
    for r in ranked:
        ds = r["sharpe"] - results[0]["sharpe"]
        print(f"  {r['label']:20s}  sh={r['sharpe']:+.4f}  ({'+' if ds>=0 else ''}{ds:.4f} vs S_F)  tr={r['trades']}  WR={r['wr']:.1f}%")

    out = BASE / "data" / "sweep_results" / f"stocks_matrix_{int(time.time())}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"results": results, "best": best, "chapters": {k: list(v.items()) for k, v in CHAPTERS.items()}}, indent=2, default=str))
    print(f"\nSaved: {out}")


if __name__ == "__main__":
    main()
