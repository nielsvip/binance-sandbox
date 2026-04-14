#!/usr/bin/env python3
"""Analyze tournament v2 results — compare each parameter's effect."""
import sys
from pathlib import Path
from collections import defaultdict

report = Path("/home/niels/binance-sandbox/data/tournament_v2/tournament_20260325_0829.md") if Path("/home/niels/binance-sandbox").exists() else None
if not report or not report.exists():
    # Try local
    for p in Path("/Users/niels/Documents/binance/data/tournament_v2").glob("tournament_*.md"):
        report = p
if not report:
    print("No tournament report found")
    sys.exit(1)

lines = report.read_text().splitlines()
configs = []
for line in lines:
    line = line.strip()
    if not line or line.startswith("#") or line.startswith("=") or line.startswith("-"): continue
    parts = line.split()
    if len(parts) < 7: continue
    try:
        name = parts[0]
        s_all = float(parts[1])
        s_train = float(parts[2])
        s_test = float(parts[3])
        wr = float(parts[4].replace("%",""))
        trades = int(parts[5])
        valid = "YES" in line
        configs.append((name, s_all, s_train, s_test, wr, trades, valid))
    except: continue

print(f"Parsed {len(configs)} configs from {report.name}")
baseline = [c for c in configs if c[0] == "BASELINE"]
if baseline:
    bl = baseline[0]
    print(f"BASELINE: ALL={bl[1]:.3f} TRAIN={bl[2]:.3f} TEST={bl[3]:.3f} WR={bl[4]:.1f}%")

# Per-parameter analysis
param_effects = defaultdict(lambda: defaultdict(lambda: {"all": [], "train": [], "test": [], "valid": 0, "n": 0}))
for name, s_all, s_train, s_test, wr, trades, valid in configs:
    if name == "BASELINE": continue
    for p in name.split("|"):
        if "=" in p:
            k, v = p.split("=", 1)
            d = param_effects[k][v]
            d["all"].append(s_all); d["train"].append(s_train); d["test"].append(s_test)
            d["n"] += 1
            if valid: d["valid"] += 1

# Also track baseline values (configs that DON'T mention a param use the baseline)
bl_test = baseline[0][3] if baseline else 0

print(f"\n{'='*90}")
print(f"PARAMETER-BY-PARAMETER ANALYSIS (vs baseline TEST Sharpe {bl_test:.3f})")
print(f"{'='*90}")

for param in sorted(param_effects.keys()):
    values = param_effects[param]
    print(f"\n--- {param} ---")
    ranked = sorted(values.items(), key=lambda x: sum(x[1]["test"])/max(len(x[1]["test"]),1), reverse=True)
    for val, data in ranked:
        avg_all = sum(data["all"]) / max(len(data["all"]),1)
        avg_test = sum(data["test"]) / max(len(data["test"]),1)
        avg_train = sum(data["train"]) / max(len(data["train"]),1)
        n = data["n"]; vc = data["valid"]
        delta = avg_test - bl_test
        tag = " <<<WINNER" if avg_test > bl_test and vc > 100 else (" GOOD" if avg_test > bl_test else " bad" if avg_test < bl_test - 5 else "")
        print(f"  {val:<12s} TEST={avg_test:>+8.2f}  TRAIN={avg_train:>+8.2f}  delta={delta:>+7.2f}  valid={vc:>5d}/{n:<5d}{tag}")

# Summary: best value per parameter
print(f"\n{'='*90}")
print(f"OPTIMAL VALUES (highest avg TEST Sharpe per parameter)")
print(f"{'='*90}")
print(f"{'Parameter':<25s} {'Best Value':<12s} {'TEST Sharpe':>12s} {'vs Baseline':>12s} {'Valid#':>8s}")
print("-" * 75)
for param in sorted(param_effects.keys()):
    values = param_effects[param]
    best_val = max(values.items(), key=lambda x: sum(x[1]["test"])/max(len(x[1]["test"]),1))
    avg_test = sum(best_val[1]["test"]) / max(len(best_val[1]["test"]),1)
    delta = avg_test - bl_test
    vc = best_val[1]["valid"]
    print(f"{param:<25s} {best_val[0]:<12s} {avg_test:>+12.2f} {delta:>+12.2f} {vc:>8d}")

# Compare with old 100.xlsx values
print(f"\n{'='*90}")
print(f"OLD 100.xlsx vs NEW TOURNAMENT — Parameter Comparison")
print(f"{'='*90}")
old_values = {
    "TP_PCT": ("0.015", "Current: 1.5%"),
    "MFI_LONG_MAX": ("50", "Current: MFI<50 for longs"),
    "TRAIL_EROSION": ("0.3", "Current: 30% trail"),
    "MAX_HOLD_BARS": ("80", "Current: 80 bars"),
    "ENTRY_VOL_MIN": ("1.3", "Current: vol>1.3x"),
    "TF_ALIGNMENT_MIN": ("3", "Current: 3 TFs aligned — CHANGED TO 2"),
    "K_ZONE_LONG_TH": ("90", "Current: K<90"),
    "ENTRY_ATR_PCT_MIN": ("1.5", "Current: ATR>1.5%"),
}
for param, (old_val, desc) in old_values.items():
    if param in param_effects:
        values = param_effects[param]
        best_val = max(values.items(), key=lambda x: sum(x[1]["test"])/max(len(x[1]["test"]),1))
        old_data = values.get(old_val)
        old_test = sum(old_data["test"]) / max(len(old_data["test"]),1) if old_data else 0
        new_test = sum(best_val[1]["test"]) / max(len(best_val[1]["test"]),1)
        change = "KEEP" if best_val[0] == old_val else f"CHANGE {old_val}→{best_val[0]}"
        print(f"  {param:<25s} {desc:<35s} → Best: {best_val[0]:<8s} (Δ={new_test-old_test:>+.2f}) {change}")
