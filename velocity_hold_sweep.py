"""
Targeted parameter sweep: CT_WT_VELOCITY_1H_MIN × HOLD_BARS_OPEN × WRONG_SIDE_ABS_KILL
Applied on top of the 0.9466 baseline (item10 config).

Goal: find combination that pushes pool Sharpe above 0.95 on 12 syms → validate on 48.

Usage:
  python3 velocity_hold_sweep.py --npz-dir /path/to/npz \
    --baseline-json /path/to/crypto_0p9466_item10.json \
    --start 2022-01-01 --validate-48 --symbols-48-json /path/to/backtest_48_symbols.json
"""
import argparse
import csv
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from v8_quick_engine import QuickConfig, simulate, iter_npz

CRYPTO_12 = [
    "BTCUSDC", "ETHUSDC", "SOLUSDC", "BNBUSDC", "XRPUSDC", "AVAXUSDC",
    "LINKUSDC", "ADAUSDC", "DOTUSDT", "LTCUSDC", "TRXUSDT", "ATOMUSDT",
]

SWEEP_VARIANTS = [
    ("baseline_item10", {}),
    # CT_WT_VELOCITY_1H_MIN sweep (base is 18.0 in item10)
    ("vel1h_12", {"CT_WT_VELOCITY_1H_MIN": 12.0}),
    ("vel1h_15", {"CT_WT_VELOCITY_1H_MIN": 15.0}),
    ("vel1h_18", {"CT_WT_VELOCITY_1H_MIN": 18.0}),
    ("vel1h_21", {"CT_WT_VELOCITY_1H_MIN": 21.0}),
    ("vel1h_24", {"CT_WT_VELOCITY_1H_MIN": 24.0}),
    ("vel1h_27", {"CT_WT_VELOCITY_1H_MIN": 27.0}),
    # HOLD_BARS_OPEN sweep (base is 250 in item10)
    ("hold150", {"HOLD_BARS_OPEN": 150}),
    ("hold200", {"HOLD_BARS_OPEN": 200}),
    ("hold300", {"HOLD_BARS_OPEN": 300}),
    ("hold400", {"HOLD_BARS_OPEN": 400}),
    ("hold500", {"HOLD_BARS_OPEN": 500}),
    # MIN_HOLD_BARS sweep (how long before exit allowed)
    ("min_hold_300", {"MIN_HOLD_BARS": 300}),
    ("min_hold_400", {"MIN_HOLD_BARS": 400}),
    ("min_hold_500", {"MIN_HOLD_BARS": 500}),
    ("min_hold_750", {"MIN_HOLD_BARS": 750}),
    # STRENGTH_MIN_SCORE sweep (default in item10: not set → default 5.0)
    ("strength_2", {"STRENGTH_MIN_SCORE": 2.0}),
    ("strength_3", {"STRENGTH_MIN_SCORE": 3.0}),
    ("strength_3p75", {"STRENGTH_MIN_SCORE": 3.75}),
    ("strength_4p5", {"STRENGTH_MIN_SCORE": 4.5}),
    # Combined: high velocity + long hold
    ("vel21_hold300", {"CT_WT_VELOCITY_1H_MIN": 21.0, "HOLD_BARS_OPEN": 300}),
    ("vel21_hold400", {"CT_WT_VELOCITY_1H_MIN": 21.0, "HOLD_BARS_OPEN": 400}),
    ("vel24_hold300", {"CT_WT_VELOCITY_1H_MIN": 24.0, "HOLD_BARS_OPEN": 300}),
    ("vel24_hold400", {"CT_WT_VELOCITY_1H_MIN": 24.0, "HOLD_BARS_OPEN": 400}),
    ("vel21_minhold500", {"CT_WT_VELOCITY_1H_MIN": 21.0, "MIN_HOLD_BARS": 500}),
    ("vel24_minhold500", {"CT_WT_VELOCITY_1H_MIN": 24.0, "MIN_HOLD_BARS": 500}),
    # NOLOSS interaction
    ("noloss_off", {"NOLOSS_ENABLED": False}),
    ("noloss_off_vel21", {"NOLOSS_ENABLED": False, "CT_WT_VELOCITY_1H_MIN": 21.0}),
    # TF_FOCUS hard gate
    ("tf_focus_hard", {"TF_FOCUS_ENTRY_HARD_GATE": True}),
    ("tf_focus_hard_vel21", {"TF_FOCUS_ENTRY_HARD_GATE": True, "CT_WT_VELOCITY_1H_MIN": 21.0}),
    # K3M_FLOOR (base=22.5): higher = more selective
    ("k3m_30", {"K3M_FLOOR": 30.0}),
    ("k3m_40", {"K3M_FLOOR": 40.0}),
]


def load_baseline(path):
    with open(path) as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith("_")}


def build_cfg(base_overrides, extra_overrides, mode="crypto"):
    cfg = QuickConfig()
    cfg.MODE = mode
    cfg.LTF = "3m"
    for k, v in base_overrides.items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)
    for k, v in extra_overrides.items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)
    return cfg


def run_test(cfg, symbols, npz_dir, start_date, mode):
    stream = iter_npz(mode=mode, symbols=symbols, start_date=start_date, npz_dir=npz_dir)
    t0 = time.time()
    res = simulate(stream, cfg)
    return res, time.time() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="crypto")
    ap.add_argument("--npz-dir", required=True)
    ap.add_argument("--baseline-json", required=True)
    ap.add_argument("--start", default="2022-01-01")
    ap.add_argument("--out", default="velocity_hold_sweep_results.csv")
    ap.add_argument("--validate-48", action="store_true")
    ap.add_argument("--symbols-48-json", default="")
    args = ap.parse_args()

    base_overrides = load_baseline(args.baseline_json)
    print(f"Baseline loaded: {len(base_overrides)} keys")

    syms_48 = []
    if args.validate_48 and args.symbols_48_json:
        import json as _j
        syms_48 = _j.load(open(args.symbols_48_json))

    results = []
    print(f"\nRunning {len(SWEEP_VARIANTS)} variants on {len(CRYPTO_12)} symbols...")
    print(f"{'Variant':<35} {'Sharpe':>7} {'Trades':>7} {'Gain%':>8} {'DD%':>6} {'Time':>6}")
    print("-" * 75)

    for name, overrides in SWEEP_VARIANTS:
        cfg = build_cfg(base_overrides, overrides, args.mode)
        try:
            res, elapsed = run_test(cfg, CRYPTO_12, args.npz_dir, args.start, args.mode)
            ps = res.get("pool_sharpe", 0.0)
            tr = res.get("trades", 0)
            gain = res.get("accumulated_gain_pct", 0.0)
            dd = res.get("max_dd_pct", 0.0)
            print(f"{name:<35} {ps:>7.4f} {tr:>7d} {gain:>8.1f} {dd:>6.2f} {elapsed:>5.0f}s")
            results.append({
                "variant": name, "pool_sharpe": ps, "trades": tr,
                "gain_pct": round(gain, 1), "dd_pct": round(dd, 2),
                "elapsed_s": round(elapsed, 1),
            })
        except Exception as e:
            print(f"{name:<35} ERROR: {e}")

    results.sort(key=lambda r: r["pool_sharpe"], reverse=True)
    print(f"\nTop 5 by Sharpe:")
    for r in results[:5]:
        print(f"  {r['variant']}: sharpe={r['pool_sharpe']} trades={r['trades']} gain={r['gain_pct']}%")

    if args.validate_48 and syms_48:
        print(f"\nValidating top 5 on 48 symbols...")
        for r in results[:5]:
            name = r["variant"]
            overrides = dict(next(v for n, v in SWEEP_VARIANTS if n == name))
            cfg = build_cfg(base_overrides, overrides, args.mode)
            try:
                res48, el = run_test(cfg, syms_48, args.npz_dir, args.start, args.mode)
                ps48 = res48.get("pool_sharpe", 0.0)
                tr48 = res48.get("trades", 0)
                gain48 = res48.get("accumulated_gain_pct", 0.0)
                dd48 = res48.get("max_dd_pct", 0.0)
                print(f"  {name}: 12sym={r['pool_sharpe']:.4f} → 48sym={ps48:.4f} "
                      f"trades={tr48} gain={gain48:.0f}% dd={dd48:.1f}% {el:.0f}s")
                r["sharpe_48"] = ps48
                r["trades_48"] = tr48
                r["gain_48"] = round(gain48, 1)
            except Exception as e:
                print(f"  {name}: ERROR {e}")

    with open(args.out, "w", newline="") as f:
        fieldnames = ["variant", "pool_sharpe", "trades", "gain_pct", "dd_pct", "elapsed_s"]
        if args.validate_48:
            fieldnames += ["sharpe_48", "trades_48", "gain_48"]
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(results)

    print(f"\nResults saved to {args.out}")


if __name__ == "__main__":
    main()
