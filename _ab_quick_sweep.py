#!/usr/bin/env python3
"""Thin A/B driver for the 5 switches wired into v8_quick_engine.py.
Loads NPZ once, runs baseline + each variant inside the same process.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from v8_quick_engine import QuickConfig, load_npz, simulate, FAST_SYMBOLS_CRYPTO, FAST_SYMBOLS_TRADIER

MODE = sys.argv[1] if len(sys.argv) > 1 else "crypto"
START = sys.argv[2] if len(sys.argv) > 2 else "2024-01-01"

symbols = (FAST_SYMBOLS_TRADIER if MODE == "tradier" else FAST_SYMBOLS_CRYPTO).split(",")
base = Path(__file__).resolve().parent
npz_dir = ""
for prefix in ["backtest_v8", "backtest_v7"]:
    d = base / prefix / "indicators"
    if d.exists() and any(d.glob("*.npz")):
        npz_dir = str(d)
        break

print(f"A/B sweep: mode={MODE} start={START} symbols={len(symbols)} npz={npz_dir}")
t0 = time.time()
stores = load_npz(MODE, symbols, START, npz_dir)
print(f"NPZ loaded in {time.time() - t0:.1f}s — {len(stores)} symbols")

def run(label, overrides):
    cfg = QuickConfig()
    cfg.MODE = MODE
    if MODE == "tradier":
        cfg.apply_tradier_defaults()
    for k, v in overrides.items():
        setattr(cfg, k, v)
    t = time.time()
    r = simulate(stores, cfg, 10000.0)
    r["elapsed"] = round(time.time() - t, 1)
    r["label"] = label
    print(f"  {label:36s}  sharpe={r['sharpe']:+.4f}  trades={r['trades']:<5d}  wr={r['wr']:.1f}%  avg={r['avg_pnl_pct']:+.3f}%  {r['elapsed']}s")
    return r

print("\nBASELINE (all switches at default/off):")
baseline_overrides = {
    "ENTRY_SYMGATE_ENABLED": False,
    "REENTRY_SYMGATE_ENABLED": False,
    "REENTRY_MIN_GAP_BARS": 0,
    "ENTRY_ZONE_LONG": 0.0,
    "ENTRY_ZONE_SHORT": 100.0,
    "REENTRY_RALLY_K15M_MAX": 100.0,
}
B = run("baseline", baseline_overrides)

print("\nA/B VARIANTS:")
results = [B]

variants = [
    ("SYMGATE_entry_on",        {**baseline_overrides, "ENTRY_SYMGATE_ENABLED": True}),
    ("SYMGATE_reentry_on",      {**baseline_overrides, "REENTRY_SYMGATE_ENABLED": True}),
    ("MIN_GAP_5bars",           {**baseline_overrides, "REENTRY_MIN_GAP_BARS": 5}),
    ("MIN_GAP_20bars",          {**baseline_overrides, "REENTRY_MIN_GAP_BARS": 20}),
    ("ENTRY_ZONE_SHORT_65",     {**baseline_overrides, "ENTRY_ZONE_SHORT": 65.0}),
    ("ENTRY_ZONE_SHORT_40",     {**baseline_overrides, "ENTRY_ZONE_SHORT": 40.0}),
    ("ENTRY_ZONE_LONG_35",      {**baseline_overrides, "ENTRY_ZONE_LONG": 35.0}),
    ("RALLY_K15M_60",           {**baseline_overrides, "REENTRY_RALLY_K15M_MAX": 60.0}),
    ("RALLY_K15M_40",           {**baseline_overrides, "REENTRY_RALLY_K15M_MAX": 40.0}),
]
for label, ov in variants:
    results.append(run(label, ov))

print("\nDELTA SUMMARY (vs baseline):")
print(f"  baseline sharpe={B['sharpe']:+.4f} trades={B['trades']}")
for r in results[1:]:
    ds = r["sharpe"] - B["sharpe"]
    dt = r["trades"] - B["trades"]
    flag = " *" if abs(ds) >= 0.05 else ""
    print(f"  {r['label']:28s}  Δsharpe={ds:+.4f}  Δtrades={dt:+d}{flag}")

out = base / "data" / "sweep_results" / f"ab_quick_{MODE}_{int(time.time())}.json"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(results, indent=2))
print(f"\nSaved: {out}")
