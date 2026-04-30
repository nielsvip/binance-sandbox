#!/usr/bin/env python3
"""A/B sweep the 5 race-condition switches + auto-revert any that hurt.

Logic:
  1. Run baseline (all new switches OFF)
  2. Run with each switch flipped ON
  3. For each switch, compare vs baseline on (Sharpe, WR, avg gain)
  4. Keep the switch's config default True ONLY if at least 2 of (Sharpe↑ by ≥0.05, WR↑ by ≥2pp, avg↑ by ≥0.05pp)
     OR not worse on all three (Δ≥0 everywhere)
  5. Otherwise write a config patch that flips the offending default to False.

Supports both crypto (config.py) and tradier (config_tradier.py) patches.
Runs both modes.
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
from v8_quick_engine import QuickConfig, load_npz, simulate, FAST_SYMBOLS_TRADIER

CRYPTO_SYMS_ALL = "ANKRUSDT,ATOMUSDT,BANDUSDT,BATUSDT,BELUSDT,BTCDOMUSDT,BTCUSDT,CELRUSDT,CHRUSDT,COMPUSDT"

SWITCHES = [
    # (name_in_QuickConfig, on_value, off_value, config_file_for_live_patch, live_name, live_off_value)
    ("ENTRY_SYMGATE_ENABLED",   True,  False, ["config.py", "config_tradier.py"], "ENTRY_SYMGATE_ENABLED",   "False"),
    ("REENTRY_SYMGATE_ENABLED", True,  False, ["config.py", "config_tradier.py"], "REENTRY_SYMGATE_ENABLED", "False"),
    ("REENTRY_MIN_GAP_BARS",    5,     0,     None,                                None,                       None),
    ("ENTRY_ZONE_SHORT",        65.0,  100.0, ["config_tradier.py"],              "ENTRY_ZONE_SHORT",         "100.0"),
    ("REENTRY_RALLY_K15M_MAX",  60.0,  100.0, ["config.py", "config_tradier.py"], "REENTRY_RALLY_K15M_MAX",   "100.0"),
]

def run(cfg_overrides, mode, symbols, start, npz_dir, stores):
    cfg = QuickConfig()
    cfg.MODE = mode
    if mode == "tradier":
        cfg.apply_tradier_defaults()
    for k, v in cfg_overrides.items():
        setattr(cfg, k, v)
    return simulate(stores, cfg, 10000.0)

def verdict(baseline, variant):
    dsharpe = variant["sharpe"] - baseline["sharpe"]
    dwr = variant["wr"] - baseline["wr"]
    davg = variant["avg_pnl_pct"] - baseline["avg_pnl_pct"]
    dtrades = variant["trades"] - baseline["trades"]
    improvements = 0
    if dsharpe >= 0.05: improvements += 1
    if dwr >= 2.0: improvements += 1
    if davg >= 0.05: improvements += 1
    not_worse_all = dsharpe >= 0 and dwr >= 0 and davg >= 0
    keep = improvements >= 2 or not_worse_all
    return {"keep": keep, "dsharpe": dsharpe, "dwr": dwr, "davg": davg, "dtrades": dtrades, "improvements": improvements}

def patch_config(config_file, switch_name, new_value_str):
    """Flip a switch's default to new_value_str in the given config file."""
    path = BASE / config_file
    if not path.exists(): return False, "file_missing"
    src = path.read_text()
    pat = re.compile(rf"^(\s+){re.escape(switch_name)}(\s*:\s*[A-Za-z0-9\[\]]+)?\s*=\s*[^#\n]+(\s*#.*)?$", re.MULTILINE)
    m = pat.search(src)
    if not m: return False, "switch_not_found"
    indent = m.group(1); ann = m.group(2) or ""; comment = m.group(3) or ""
    new_line = f"{indent}{switch_name}{ann} = {new_value_str}{comment}"
    new_src = pat.sub(new_line, src, count=1)
    if new_src == src: return False, "no_change"
    # Backup
    bk = BASE / "backups" / f"before_autorevert_{config_file.replace('.py','')}_{int(time.time())}.py"
    bk.write_text(src)
    path.write_text(new_src)
    return True, str(bk)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["crypto", "tradier", "both"], default="both")
    ap.add_argument("--start", default="")
    ap.add_argument("--symbols", default="")
    ap.add_argument("--apply", action="store_true", help="Actually flip config defaults; else dry-run only.")
    args = ap.parse_args()

    modes = ["crypto", "tradier"] if args.mode == "both" else [args.mode]
    summary = {}
    for mode in modes:
        start = args.start or ("2022-01-01" if mode == "crypto" else "2024-01-01")
        syms_str = args.symbols or (CRYPTO_SYMS_ALL if mode == "crypto" else FAST_SYMBOLS_TRADIER)
        symbols = syms_str.split(",")
        npz_dir = str(BASE / "backtest_v8" / "indicators")

        print(f"\n{'='*72}\nMODE={mode}  start={start}  symbols={len(symbols)}\n{'='*72}")
        stores = load_npz(mode, symbols, start, npz_dir)
        print(f"NPZ: {len(stores)} symbols loaded")
        if not stores:
            print("SKIP — no data"); continue

        baseline_overrides = {name: off for name, _on, off, *_ in SWITCHES}
        base = run(baseline_overrides, mode, symbols, start, npz_dir, stores)
        print(f"BASELINE  sharpe={base['sharpe']:+.4f}  trades={base['trades']}  wr={base['wr']:.1f}%  avg={base['avg_pnl_pct']:+.3f}%")
        if base["trades"] < 10:
            print(f"⚠ Only {base['trades']} baseline trades — variants below will be noisy. Results still recorded.")

        summary[mode] = {"baseline": base, "variants": {}, "patches": []}
        for (sw_name, on_val, off_val, cfg_files, live_name, live_off) in SWITCHES:
            ov = dict(baseline_overrides); ov[sw_name] = on_val
            r = run(ov, mode, symbols, start, npz_dir, stores)
            v = verdict(base, r)
            flag = "KEEP" if v["keep"] else "REVERT"
            print(f"  {sw_name:28s} {on_val!r:10s}  sharpe={r['sharpe']:+.4f}({v['dsharpe']:+.4f})  wr={r['wr']:.1f}%({v['dwr']:+.1f})  avg={r['avg_pnl_pct']:+.3f}%({v['davg']:+.3f})  tr={r['trades']}({v['dtrades']:+d})  → {flag}")
            summary[mode]["variants"][sw_name] = {"result": r, "verdict": v, "on_value": on_val, "off_value": off_val}
            if not v["keep"] and cfg_files:
                # Flip config defaults to off_val string (live_off) across matching files
                for cf in cfg_files:
                    # Only apply crypto file for crypto mode if the switch exists in both
                    if mode == "crypto" and cf != "config.py": continue
                    if mode == "tradier" and cf != "config_tradier.py": continue
                    if args.apply:
                        ok, detail = patch_config(cf, live_name, live_off)
                        print(f"    PATCH {cf}: {live_name}→{live_off} — {'OK backup=' + detail if ok else 'FAILED ' + detail}")
                        summary[mode]["patches"].append({"switch": sw_name, "file": cf, "from": on_val, "to": live_off, "ok": ok, "detail": detail})
                    else:
                        print(f"    DRY-RUN patch {cf}: {live_name}→{live_off}")
                        summary[mode]["patches"].append({"switch": sw_name, "file": cf, "to": live_off, "dry_run": True})

    out = BASE / "data" / "sweep_results" / f"ab_auto_revert_{int(time.time())}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2, default=str))
    print(f"\nSaved: {out}")
    if not args.apply:
        print("\n(dry-run — no config files changed. Re-run with --apply to flip.)")

if __name__ == "__main__":
    main()
