#!/usr/bin/env python3
"""
v8_tradier_rediscovery.py — Find tradier-native baseline from scratch.

Crypto winner config gives NEGATIVE Sharpe on tradier because:
  - crypto reentry blocks use dc/wt thresholds calibrated for crypto volatility
  - strength filter weights (B02=2, B04=3, B11=3, B15=4) are crypto-tuned

This sweep tests tradier-ONLY entry paths + various strength/no-strength combinations.
"""
import sys, time, json, itertools
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
sys.path.insert(0, str(Path(__file__).resolve().parent))
from v8_quick_engine import QuickConfig, load_npz, simulate, FAST_SYMBOLS_TRADIER

BASE = Path(__file__).resolve().parent
ALL12 = FAST_SYMBOLS_TRADIER.split(",")


def base_tradier_cfg():
    c = QuickConfig()
    c.apply_tradier_defaults()  # all tradier gates on
    c.STRENGTH_FILTER_ENABLED = False  # DISABLE — crypto-tuned weights don't fit
    c.CONFLUENCE_MODE_ENABLED = False
    c.PROFIT_TARGET_ENABLED = False
    c.STOP_LOSS_ENABLED = False
    c.HTF_MIN_ALIGNED = 1
    c.D_TREND_REQUIRED = True
    c.MIN_HOLD_BARS = 5
    c.WT_EXIT_MIN_TFS = 2
    c.COOLDOWN_BARS = 3
    return c


def run_exp(args):
    label, cfg_overrides, symbols = args
    stores = load_npz('tradier', symbols, '2024-01-01')
    if not stores: return {"label": label, "sharpe": 0, "trades": 0}
    cfg = base_tradier_cfg()
    for k, v in cfg_overrides.items():
        if hasattr(cfg, k):
            cur = getattr(cfg, k)
            if isinstance(cur, bool): setattr(cfg, k, bool(v))
            elif isinstance(cur, int): setattr(cfg, k, int(v))
            elif isinstance(cur, float): setattr(cfg, k, float(v))
            else: setattr(cfg, k, v)
    t0 = time.time()
    r = simulate(stores, cfg, 10000.0)
    r["label"] = label
    r["cfg"] = cfg_overrides
    r["n_symbols"] = len(symbols)
    r["elapsed"] = round(time.time() - t0, 1)
    return r


def main():
    exps = []

    # ROUND 1: Establish tradier baseline — what combination of tradier gates works?
    # Only enable ONE tradier-specific entry gate at a time
    # (disable crypto blocks by turning all REENTRY_B*_ENABLED off)
    NO_CRYPTO_BLOCKS = {
        "REENTRY_B02_BC156_BOTTOM_ENABLED": False,
        "REENTRY_B04_DC_RETEST_ENABLED": False,
        "REENTRY_B10_STOCH_REV_ENABLED": False,
        "REENTRY_B11_DC_BREAK_ENABLED": False,
        "REENTRY_B12_WT_MOM_ENABLED": False,
        "REENTRY_B14_HA_TREND_ENABLED": False,
        "REENTRY_B15_STRONG_TREND_ENABLED": False,
        "SATOSHIT_ENABLED": False,
        "DELTA_ENTRY_ENABLED": False,
    }
    # Test each tradier gate alone
    tradier_gates = ["K_ZONE_ENTRY_ENABLED", "MFI_ENTRY_ENABLED", "FH_MOMENTUM_ENABLED", "DC_DAYTRADE_ENABLED"]
    for kept in tradier_gates:
        cfg = dict(NO_CRYPTO_BLOCKS)
        # Disable all tradier gates except one
        for g in tradier_gates:
            cfg[g] = (g == kept)
        exps.append((f"R1_ONLY_{kept.replace('_ENABLED','')}", cfg, ALL12))

    # Test ALL tradier gates on (with no crypto blocks)
    cfg = dict(NO_CRYPTO_BLOCKS)
    for g in tradier_gates: cfg[g] = True
    exps.append(("R1_ALL_TRADIER_GATES", cfg, ALL12))

    # Test crypto-native DC breakout alone (without STRENGTH_FILTER)
    cfg = dict(NO_CRYPTO_BLOCKS)
    for g in tradier_gates: cfg[g] = False  # disable tradier too
    # Let core DC_break + WT_2of3 fire
    cfg["REENTRY_B11_DC_BREAK_ENABLED"] = True
    cfg["REENTRY_B12_WT_MOM_ENABLED"] = True
    cfg["REENTRY_B15_STRONG_TREND_ENABLED"] = True
    exps.append(("R1_CORE_CRYPTO_BLOCKS", cfg, ALL12))

    # ROUND 2: With best approach — vary PT
    # (we'll determine best from Round 1, but run these in same batch for speed)
    # Assume ALL_TRADIER_GATES wins — test PT overlays
    for pt in [0.5, 1.0, 1.5, 2.0, 3.0]:
        cfg = dict(NO_CRYPTO_BLOCKS)
        for g in tradier_gates: cfg[g] = True
        cfg["PROFIT_TARGET_ENABLED"] = True
        cfg["PROFIT_TARGET_PCT"] = pt
        exps.append((f"R2_TRADIER_GATES_PT{pt}", cfg, ALL12))

    # ROUND 3: SL overlay + Score threshold tests
    for sl in [0.3, 0.5, 0.8, 1.2]:
        cfg = dict(NO_CRYPTO_BLOCKS)
        for g in tradier_gates: cfg[g] = True
        cfg["PROFIT_TARGET_ENABLED"] = True
        cfg["PROFIT_TARGET_PCT"] = 1.5
        cfg["STOP_LOSS_ENABLED"] = True
        cfg["STOP_LOSS_PCT"] = sl
        exps.append((f"R3_TRADIER_PT1.5_SL{sl}", cfg, ALL12))

    # ROUND 4: Hold + WT_exit variations (with best from R2 likely)
    for hold in [5, 10, 20, 40]:
        for wt in [1, 2, 3]:
            cfg = dict(NO_CRYPTO_BLOCKS)
            for g in tradier_gates: cfg[g] = True
            cfg["MIN_HOLD_BARS"] = hold
            cfg["WT_EXIT_MIN_TFS"] = wt
            cfg["PROFIT_TARGET_ENABLED"] = True
            cfg["PROFIT_TARGET_PCT"] = 1.5
            exps.append((f"R4_HOLD{hold}_WT{wt}", cfg, ALL12))

    # ROUND 5: Single-gate isolation + PT (best gate × PT)
    for gate in tradier_gates:
        for pt in [1.0, 1.5]:
            cfg = dict(NO_CRYPTO_BLOCKS)
            for g in tradier_gates: cfg[g] = (g == gate)
            cfg["PROFIT_TARGET_ENABLED"] = True
            cfg["PROFIT_TARGET_PCT"] = pt
            exps.append((f"R5_{gate.replace('_ENABLED','')}_PT{pt}", cfg, ALL12))

    # ROUND 6: Disable D_TREND_REQUIRED (may be too strict for stocks)
    cfg = dict(NO_CRYPTO_BLOCKS)
    for g in tradier_gates: cfg[g] = True
    cfg["D_TREND_REQUIRED"] = False
    cfg["HTF_MIN_ALIGNED"] = 0
    cfg["PROFIT_TARGET_ENABLED"] = True
    cfg["PROFIT_TARGET_PCT"] = 1.5
    exps.append(("R6_NO_HTF_LOOSE_PT1.5", cfg, ALL12))
    # HTF=2 strict
    cfg = dict(cfg)
    cfg["HTF_MIN_ALIGNED"] = 2
    cfg["D_TREND_REQUIRED"] = True
    exps.append(("R6_HTF2_PT1.5", cfg, ALL12))

    print(f"Running {len(exps)} tradier rediscovery experiments in parallel (6 workers)...")
    results = []
    with ProcessPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(run_exp, e): e for e in exps}
        for fut in as_completed(futs):
            try:
                r = fut.result()
                results.append(r)
                if r.get("sharpe", 0) > 0.3 or r.get("trades", 0) > 1000:
                    print(f"  [{len(results):3d}/{len(exps)}] {r['label']:<30} Sharpe={r['sharpe']:.4f} trades={r['trades']:>5} wr={r.get('wr',0):.1f}% avg={r.get('avg_pnl_pct',0):.3f}%")
            except Exception as e:
                print(f"  ERROR: {e}")

    valid = [r for r in results if r.get('trades', 0) >= 20]
    valid.sort(key=lambda r: r['sharpe'], reverse=True)
    print(f"\n{'='*110}")
    print(f"  TOP 25 TRADIER (trades>=20, sorted by Sharpe):")
    print(f"{'='*110}")
    for r in valid[:25]:
        print(f"  {r['label']:<30} Sharpe={r['sharpe']:.4f} trades={r['trades']:>5} wr={r.get('wr',0):.1f}% avg={r.get('avg_pnl_pct',0):.3f}%")

    out_path = BASE / "data" / "sweep_results" / f"v8_tradier_rediscovery_{int(time.time())}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nSaved: {out_path}")


if __name__ == "__main__":
    main()
