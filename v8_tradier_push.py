#!/usr/bin/env python3
"""
v8_tradier_push.py — Push tradier baseline (Sharpe 0.39) higher via targeted experiments.

Baseline: CT_VEL + CT_DC only on 12 stocks → Sharpe 0.39, 2,392 trades, 68.7% WR.
All tradier-specific gates (K_ZONE/MFI/VWAP/FH_MOMENTUM/DC_DAYTRADE) turned out to HURT.
Strength filter also hurts (wrong weights for stocks).

Strategy: probe from this baseline in the directions that worked on crypto.
"""
import sys, time, json
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
sys.path.insert(0, str(Path(__file__).resolve().parent))
from v8_quick_engine import QuickConfig, load_npz, simulate, FAST_SYMBOLS_TRADIER

BASE = Path(__file__).resolve().parent
ALL12 = FAST_SYMBOLS_TRADIER.split(",")


def tradier_baseline():
    """Confirmed winning tradier baseline — do NOT enable tradier-specific gates!"""
    c = QuickConfig()
    c.MODE = "tradier"
    c.STRUCTURAL_RANGE_SHIFT_TF = "bb_1h"
    c.STRENGTH_FILTER_ENABLED = False  # hurts stocks
    c.PROFIT_TARGET_ENABLED = False  # default off, probe later
    c.STOP_LOSS_ENABLED = False
    # Keep core proven crypto filters (BC_170, BC_172 work on stocks too)
    c.CT_WT_VELOCITY_GATE_ENABLED = True
    c.CT_DC_CROSSOVER_SKIP_ENABLED = True
    # Reentry blocks all on (let them all fire)
    # Tradier-specific gates all OFF
    c.K_ZONE_ENTRY_ENABLED = False
    c.MFI_ENTRY_ENABLED = False
    c.VWAP_FILTER_ENABLED = False
    c.FH_MOMENTUM_ENABLED = False
    c.DC_DAYTRADE_ENABLED = False
    # Exit gates
    c.STOCH_CROSS_1H_EXIT_ENABLED = False
    c.MFI_FLIP_EXIT_ENABLED = False
    c.WT_CROSSUNDER_FINAL_ENABLED = False
    c.MI_EXIT_ENABLED = False
    c.SATOSHIT_ENABLED = True
    c.RZ_EXIT_ENABLED = True
    c.DELTA_ENGINE_ENABLED = True
    c.DELTA_ENTRY_ENABLED = True
    c.STRUCTURAL_RANGE_SHIFT_EXIT = True
    # HTF
    c.HTF_ALIGNMENT_ENABLED = True
    c.HTF_MIN_ALIGNED = 1
    c.D_TREND_REQUIRED = True
    c.MIN_HOLD_BARS = 10
    c.WT_EXIT_MIN_TFS = 2
    c.COOLDOWN_BARS = 3
    c.K3M_FLOOR = 30
    return c


def run_exp(args):
    label, overrides, symbols = args
    stores = load_npz('tradier', symbols, '2024-01-01')
    if not stores: return {"label": label, "sharpe": 0, "trades": 0}
    cfg = tradier_baseline()
    for k, v in overrides.items():
        if hasattr(cfg, k):
            cur = getattr(cfg, k)
            if isinstance(cur, bool): setattr(cfg, k, bool(v))
            elif isinstance(cur, int): setattr(cfg, k, int(v))
            elif isinstance(cur, float): setattr(cfg, k, float(v))
            else: setattr(cfg, k, v)
    t0 = time.time()
    r = simulate(stores, cfg, 10000.0)
    r["label"] = label
    r["cfg"] = overrides
    r["n_symbols"] = len(symbols)
    r["elapsed"] = round(time.time() - t0, 1)
    return r


def main():
    exps = []

    # ROUND 1: Add each tradier gate INDIVIDUALLY (proven baseline OFF → ON)
    # to confirm my finding that they hurt
    for gate in ["K_ZONE_ENTRY_ENABLED", "MFI_ENTRY_ENABLED", "VWAP_FILTER_ENABLED", "FH_MOMENTUM_ENABLED", "DC_DAYTRADE_ENABLED"]:
        exps.append((f"R1_ADD_{gate[:-8]}", {gate: True}, ALL12))
    for gate in ["STOCH_CROSS_1H_EXIT_ENABLED", "MFI_FLIP_EXIT_ENABLED", "WT_CROSSUNDER_FINAL_ENABLED", "MI_EXIT_ENABLED"]:
        exps.append((f"R1_ADD_{gate[:-8]}", {gate: True}, ALL12))

    # ROUND 2: PT overlay (critical on crypto, test on tradier)
    for pt in [0.5, 0.8, 1.0, 1.2, 1.5, 2.0, 3.0]:
        exps.append((f"R2_PT{pt}", {"PROFIT_TARGET_ENABLED": True, "PROFIT_TARGET_PCT": pt}, ALL12))

    # ROUND 3: SL overlay
    for sl in [0.3, 0.5, 0.8, 1.2]:
        exps.append((f"R3_SL{sl}", {"STOP_LOSS_ENABLED": True, "STOP_LOSS_PCT": sl}, ALL12))

    # ROUND 4: PT + SL combos
    for pt in [0.8, 1.0, 1.5]:
        for sl in [0.3, 0.5, 1.0]:
            exps.append((f"R4_PT{pt}_SL{sl}", {"PROFIT_TARGET_ENABLED": True, "PROFIT_TARGET_PCT": pt, "STOP_LOSS_ENABLED": True, "STOP_LOSS_PCT": sl}, ALL12))

    # ROUND 5: HTF strictness + hold
    for htf in [0, 1, 2, 3]:
        for hold in [5, 10, 15, 20]:
            exps.append((f"R5_H{htf}_HD{hold}", {"HTF_MIN_ALIGNED": htf, "MIN_HOLD_BARS": hold}, ALL12))

    # ROUND 6: Disable individual core crypto blocks
    for block in ["B02", "B04", "B10", "B11", "B12", "B14", "B15"]:
        key_map = {"B02": "REENTRY_B02_BC156_BOTTOM_ENABLED", "B04": "REENTRY_B04_DC_RETEST_ENABLED", "B10": "REENTRY_B10_STOCH_REV_ENABLED", "B11": "REENTRY_B11_DC_BREAK_ENABLED", "B12": "REENTRY_B12_WT_MOM_ENABLED", "B14": "REENTRY_B14_HA_TREND_ENABLED", "B15": "REENTRY_B15_STRONG_TREND_ENABLED"}
        exps.append((f"R6_NO_{block}", {key_map[block]: False}, ALL12))

    # ROUND 7: Remove CT gates (verify they help)
    exps.append(("R7_NO_CT_VEL", {"CT_WT_VELOCITY_GATE_ENABLED": False}, ALL12))
    exps.append(("R7_NO_CT_DC", {"CT_DC_CROSSOVER_SKIP_ENABLED": False}, ALL12))
    exps.append(("R7_NO_BOTH_CT", {"CT_WT_VELOCITY_GATE_ENABLED": False, "CT_DC_CROSSOVER_SKIP_ENABLED": False}, ALL12))

    # ROUND 8: Symbol subsets — identify top tradier stocks
    # Start with small subsets to see per-symbol performance
    for single in ALL12:
        exps.append((f"R8_SOLO_{single[:4]}", {}, [single]))

    # ROUND 9: Score threshold WITH tradier-specific weighting needed?
    # For now, test with strength filter AT LOWER scores (might not hurt as much as score=5)
    for score in [2.0, 2.5, 3.0]:
        exps.append((f"R9_STRENGTH_{score}", {"STRENGTH_FILTER_ENABLED": True, "STRENGTH_MIN_SCORE": score}, ALL12))

    # BASELINE
    exps.append(("R0_BASELINE", {}, ALL12))

    print(f"Running {len(exps)} tradier push experiments (6 workers)...")
    results = []
    with ProcessPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(run_exp, e): e for e in exps}
        for fut in as_completed(futs):
            try:
                r = fut.result()
                results.append(r)
                if r.get("sharpe", 0) > 0.45:
                    print(f"  🎯 {r['label']:<32} Sharpe={r['sharpe']:.4f} trades={r['trades']:>5} wr={r.get('wr',0):.1f}% avg={r.get('avg_pnl_pct',0):.3f}%")
            except Exception as e:
                print(f"  ERROR: {e}")
    valid = sorted([r for r in results if r.get("trades", 0) >= 20], key=lambda r: r["sharpe"], reverse=True)
    print(f"\n{'='*110}")
    print(f"  TOP 30 TRADIER (trades>=20):")
    print(f"{'='*110}")
    for r in valid[:30]:
        print(f"  {r['label']:<32} Sharpe={r['sharpe']:.4f} trades={r['trades']:>5} wr={r.get('wr',0):.1f}% avg={r.get('avg_pnl_pct',0):.3f}% pnl=${r.get('pnl',0):.0f}")
    out = BASE / "data" / "sweep_results" / f"v8_tradier_push_{int(time.time())}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nSaved: {out}")


if __name__ == "__main__":
    main()
