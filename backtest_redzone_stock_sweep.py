#!/usr/bin/env python3
"""
RED ZONE STOCK Sweep — 9 configs, 10 large-cap stocks, 3 months.
Target: ~1 hour per config → 9 hours total.
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

SWEEP_DIR = Path("backtest_v8/sweeps/redzone_stocks")
SWEEP_DIR.mkdir(parents=True, exist_ok=True)

V8_CMD = [
    sys.executable, "backtest_v8_engine.py",
    "--mode", "tradier", "--account", "trb",
    "--start", "2026-01-01", "--capital", "10000",
    "--symbols", "AAPL,MSFT,AMZN,GOOGL,TSLA,NVDA,AMD,META,SPY,QQQ",
]

configs = []
for ke in [40, 50, 60]:
    for kx in [70, 80, 90]:
        configs.append({
            "name": f"RZ_STOCK_k{ke}_kx{kx}_bb85_both",
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

print(f"Total stock configs: {len(configs)}")

CONFIG_FILE = Path("config_tradier.py")
PER_CONFIG_TIMEOUT = 7200


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
    sig_baseline_long = text.count("BASELINE_BOUNCE_LONG")
    sig_baseline_short = text.count("BASELINE_BOUNCE_SHORT")
    sig_breakdown_truck = text.count("BREAKDOWN_TRUCK")
    sig_bottom_bounce = text.count("BOTTOM_HUGE_BOUNCE")
    sig_rejection_load = text.count("REJECTION_OLD_REDZONE")
    sig_top_rejection = text.count("TOP_REJECTION_SHORT")
    sig_delta_entry = text.count("[DELTA_ENTRY]")
    wt_align_blocks = text.count("TRADIER_ALIGNMENT_BLOCK")
    v8_open_wt_dc = sum(1 for line in text.splitlines() if "V8_TRADE" in line and "WT_DC_ENTRY" in line)
    v8_open_delta = sum(1 for line in text.splitlines() if "V8_TRADE" in line and "DELTA_ENTRY" in line)
    v8_open_rz = sum(1 for line in text.splitlines() if "V8_TRADE" in line and ("BASELINE_BOUNCE" in line or "BREAKDOWN_TRUCK" in line or "BOTTOM_HUGE" in line or "REJECTION_OLD" in line))
    v8_reduce = sum(1 for line in text.splitlines() if "V8_TRADE" in line and "REDUCE" in line)
    v8_close = sum(1 for line in text.splitlines() if "V8_TRADE" in line and "CLOSE" in line)
    return {
        "lines": text.count("\n"),
        "sig_baseline_long": sig_baseline_long,
        "sig_baseline_short": sig_baseline_short,
        "sig_breakdown_truck": sig_breakdown_truck,
        "sig_bottom_bounce": sig_bottom_bounce,
        "sig_rejection_load": sig_rejection_load,
        "sig_top_rejection": sig_top_rejection,
        "sig_delta_entry": sig_delta_entry,
        "sig_total_rz_entries": sig_baseline_long + sig_baseline_short + sig_breakdown_truck + sig_bottom_bounce + sig_rejection_load + sig_top_rejection,
        "wt_align_blocks": wt_align_blocks,
        "v8_open_wt_dc": v8_open_wt_dc,
        "v8_open_delta": v8_open_delta,
        "v8_open_rz": v8_open_rz,
        "v8_reduce": v8_reduce,
        "v8_close": v8_close,
        "v8_trades": text.count("V8_TRADE"),
        "type_errors": text.count("TypeError"),
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
            print(f"  DONE: trades={r['n_trades']} v8={r['v8_trades']} rz_buy={r['rz_buy']} err={r['type_errors']} t={elapsed:.0f}s")
            with open(SWEEP_DIR / f"{name}_result.json", "w") as f:
                json.dump(r, f, indent=2, default=str)
        except subprocess.TimeoutExpired:
            r = parse_log(log_file)
            r["name"] = name
            r["error"] = "TIMEOUT"
            r["elapsed_s"] = round(time.time() - t0, 1)
            results.append(r)
            print(f"  TIMEOUT: trades={r.get('v8_trades', 0)}")
            with open(SWEEP_DIR / f"{name}_result.json", "w") as f:
                json.dump(r, f, indent=2, default=str)
        except Exception as e:
            print(f"  ERROR: {e}")
            results.append({"name": name, "error": str(e)})
finally:
    CONFIG_FILE.write_text(original_config)

summary = SWEEP_DIR / "redzone_stock_summary.json"
with open(summary, "w") as f:
    json.dump(results, f, indent=2, default=str)
print(f"\nDONE in {(time.time()-t_start_all)/60:.0f}min. Saved to {summary}")
