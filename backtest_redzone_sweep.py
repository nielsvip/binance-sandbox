#!/usr/bin/env python3
"""
RED ZONE Sweep v3 — 9 focused configs, 10 symbols, 3 months.
Target: ~1 hour per config → 9 hours total.
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

SWEEP_DIR = Path("backtest_v8/sweeps/redzone")
SWEEP_DIR.mkdir(parents=True, exist_ok=True)

# 3 months = Jan 2026 to Apr 2026 (most recent data)
V8_CMD = [
    sys.executable, "backtest_v8_engine.py",
    "--mode", "crypto", "--account", "ang",
    "--start", "2026-01-01", "--capital", "1000",
    "--symbols", "ANKRUSDT,BANDUSDT,BATUSDT,CHRUSDT,COMPUSDT,COTIUSDT,DOTUSDT,ENJUSDT,GRTUSDT,SANDUSDT",
]

# ═══ 9 FOCUSED CONFIGS ═══
# Vary k_entry, k_exit, bb — fix mode to "both" which is the only useful one
configs = []
for ke in [40, 50, 60]:
    for kx in [70, 80, 90]:
        configs.append({
            "name": f"RZ_k{ke}_kx{kx}_bb85_both",
            "RZ_K_ENTRY_MAX": float(ke),
            "RZ_K_EXIT": float(kx),
            "RZ_TOP_BB_THRESHOLD": 0.85,
            "RZ_BOT_BB_THRESHOLD": 0.15,
            "RZ_ENTRY_ENABLED": True,
            "RZ_EXIT_ENABLED": True,
            "RZ_LEGS_MIN": 10.0,
            "RZ_REQUIRE_STRUCT": False,
            "RZ_MFI_EXIT": 85.0,
        })

print(f"Total configs: {len(configs)}")

CONFIG_FILE = Path("config.py")
PER_CONFIG_TIMEOUT = 7200  # 2 hours per config


def patch_config(cfg):
    text = CONFIG_FILE.read_text()
    for key in ["RZ_ENTRY_ENABLED", "RZ_EXIT_ENABLED", "RZ_TOP_BB_THRESHOLD", "RZ_BOT_BB_THRESHOLD",
                 "RZ_LEGS_MIN", "RZ_REQUIRE_STRUCT", "RZ_K_EXIT", "RZ_MFI_EXIT", "RZ_K_ENTRY_MAX"]:
        if key in cfg:
            pattern = rf"({key}:\s*\w+\s*=\s*)([^\s#]+)"
            text = re.sub(pattern, rf"\g<1>{cfg[key]}", text)
    CONFIG_FILE.write_text(text)


def parse_log(log_path):
    if not log_path.exists():
        return {}
    text = log_path.read_text()
    # RZ signal counts (raw signals fired in delta engine — many never reach V8_TRADE)
    sig_baseline_long = text.count("BASELINE_BOUNCE_LONG")
    sig_baseline_short = text.count("BASELINE_BOUNCE_SHORT")
    sig_breakdown_truck = text.count("BREAKDOWN_TRUCK")
    sig_bottom_bounce = text.count("BOTTOM_HUGE_BOUNCE")
    sig_rejection_load = text.count("REJECTION_OLD_REDZONE")
    sig_top_rejection = text.count("TOP_REJECTION_SHORT")
    sig_red_zone_entry = text.count("RED_ZONE_ENTRY")
    # WT_LTF_GATE blocks of RZ entries (should be 0 after the bypass fix)
    wt_ltf_blocks = text.count("WT_LTF_GATE]")
    wt_gate_bypass = text.count("WT_GATE_BYPASS_RZ")
    # V8_TRADE actual orders categorized by reason
    v8_open_quick = sum(1 for line in text.splitlines() if "V8_TRADE" in line and "QUICK_OPEN" in line)
    v8_open_rz = sum(1 for line in text.splitlines() if "V8_TRADE" in line and ("RZ_" in line or "BASELINE_BOUNCE" in line or "BREAKDOWN_TRUCK" in line or "BOTTOM_HUGE" in line or "REJECTION_OLD" in line or "DELTA_ENTRY" in line))
    v8_reduce = sum(1 for line in text.splitlines() if "V8_TRADE" in line and "REDUCE" in line)
    v8_close = sum(1 for line in text.splitlines() if "V8_TRADE" in line and "CLOSE" in line)
    return {
        "lines": text.count("\n"),
        # RZ signals (in delta engine)
        "sig_baseline_long": sig_baseline_long,
        "sig_baseline_short": sig_baseline_short,
        "sig_breakdown_truck": sig_breakdown_truck,
        "sig_bottom_bounce": sig_bottom_bounce,
        "sig_rejection_load": sig_rejection_load,
        "sig_top_rejection": sig_top_rejection,
        "sig_red_zone_entry": sig_red_zone_entry,
        "sig_total_rz_entries": sig_baseline_long + sig_baseline_short + sig_breakdown_truck + sig_bottom_bounce + sig_rejection_load + sig_top_rejection,
        # Gate stats
        "wt_ltf_blocks": wt_ltf_blocks,
        "wt_gate_bypass_rz": wt_gate_bypass,
        # Actual V8 trades by category
        "v8_open_quick": v8_open_quick,
        "v8_open_rz": v8_open_rz,
        "v8_reduce": v8_reduce,
        "v8_close": v8_close,
        "v8_trades": text.count("V8_TRADE"),
        "type_errors": text.count("TypeError"),
        "noloss_blocks": text.count("DEEP_LOSS_BLOCKED"),
        "done": "Done in" in text,
        "n_trades": int(m.group(1)) if (m := re.search(r"Done in [\d.]+s \| (\d+) trades", text)) else 0,
    }


original_config = CONFIG_FILE.read_text()
results = []
t_start_all = time.time()

try:
    for i, cfg in enumerate(configs):
        name = cfg["name"]
        log_file = SWEEP_DIR / f"{name}.log"
        print(f"\n[{i+1}/{len(configs)}] {name}")
        print(f"  k_entry<{cfg['RZ_K_ENTRY_MAX']:.0f} k_exit>{cfg['RZ_K_EXIT']:.0f}")
        print(f"  Total elapsed: {(time.time()-t_start_all)/60:.0f}min")

        patch_config(cfg)
        for pyc in Path("__pycache__").glob("*.pyc"):
            pyc.unlink()

        t0 = time.time()
        try:
            with open(log_file, "w") as lf:
                proc = subprocess.run(V8_CMD, stdout=lf, stderr=subprocess.STDOUT, timeout=PER_CONFIG_TIMEOUT)
            elapsed = time.time() - t0
            r = parse_log(log_file)
            r["name"] = name
            r["config"] = {k: v for k, v in cfg.items() if k != "name"}
            r["elapsed_s"] = round(elapsed, 1)
            results.append(r)
            print(f"  DONE: trades={r['n_trades']} v8={r['v8_trades']} rz_ent={r['rz_total_entries']} rz_xit={r['rz_total_exits']} err={r['type_errors']} t={elapsed:.0f}s")
            # Save incrementally
            with open(SWEEP_DIR / f"{name}_result.json", "w") as f:
                json.dump(r, f, indent=2, default=str)
        except subprocess.TimeoutExpired:
            elapsed = time.time() - t0
            r = parse_log(log_file)
            r["name"] = name
            r["error"] = "TIMEOUT"
            r["elapsed_s"] = round(elapsed, 1)
            results.append(r)
            print(f"  TIMEOUT after {elapsed:.0f}s: trades={r.get('v8_trades', 0)}")
            with open(SWEEP_DIR / f"{name}_result.json", "w") as f:
                json.dump(r, f, indent=2, default=str)
        except Exception as e:
            print(f"  ERROR: {e}")
            results.append({"name": name, "error": str(e)})
finally:
    CONFIG_FILE.write_text(original_config)
    print("\nConfig restored.")

summary = SWEEP_DIR / "redzone_v3_summary.json"
with open(summary, "w") as f:
    json.dump(results, f, indent=2, default=str)

print(f"\n{'='*90}")
print(f"RED ZONE SWEEP v3 — {len(results)} configs in {(time.time()-t_start_all)/60:.0f}min")
print(f"{'='*90}")
ranked = sorted([r for r in results if r.get("n_trades", 0) > 0], key=lambda x: x.get("n_trades", 0), reverse=True)
print(f"\n{'Config':<30} {'Trades':>7} {'V8Tr':>6} {'RzEnt':>6} {'RzXit':>6} {'Err':>5} {'Time':>6}")
print("-" * 80)
for r in ranked:
    print(f"{r['name']:<30} {r.get('n_trades',0):>7} {r.get('v8_trades',0):>6} {r.get('rz_total_entries',0):>6} {r.get('rz_total_exits',0):>6} {r.get('type_errors',0):>5} {r.get('elapsed_s',0):>5.0f}s")
print(f"\nSaved to {summary}")
