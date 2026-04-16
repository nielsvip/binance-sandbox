#!/usr/bin/env python3
"""
build_switch_registry.py — Static scan of all config switches across trading scripts.

Scans: ez_manage.py, tradier_manage.py, ez_positions_quick.py, ez_positions_service.py,
       backtest_v8_engine.py, config.py, config_tradier.py

Extracts for each switch:
  - name (e.g., CT_WT_VELOCITY_GATE_ENABLED)
  - gate_locations: file:line where getattr(config, 'NAME', ...) appears (usage sites)
  - default_location: file:line of the dataclass default
  - default_value: True/False
  - category (entry/exit/reentry/augment/loss_exit/sizing/gate/misc)
  - comment: one-line comment next to the default (if any)

Output: data/sweep_alerts/switch_registry.json
"""
import json
import re
from pathlib import Path
from collections import defaultdict

BASE = Path(__file__).resolve().parent
FILES = [
    BASE / "ez_manage.py",
    BASE / "tradier_manage.py",
    BASE / "ez_positions_quick.py",
    BASE / "ez_positions_service.py",
    BASE / "backtest_v8_engine.py",
]
CONFIG_FILES = [
    BASE / "config.py",
    BASE / "config_tradier.py",
]

GETATTR_RE = re.compile(r"getattr\(\s*(?:config|cfg|self\.config|tm_mod\.config)\s*,\s*['\"](\w+_ENABLED)['\"]")
DEFAULT_RE = re.compile(r"^\s*(\w+_ENABLED)(?:\s*:\s*bool)?\s*=\s*(True|False)\s*(?:#\s*(.*))?$")


def scan_usage(files):
    usages = defaultdict(list)
    for f in files:
        if not f.exists():
            continue
        try:
            for i, line in enumerate(f.read_text().splitlines(), 1):
                for m in GETATTR_RE.finditer(line):
                    name = m.group(1)
                    usages[name].append(f"{f.name}:{i}")
        except Exception as e:
            print(f"[WARN] {f}: {e}")
    return usages


def scan_defaults(files):
    defaults = {}
    for f in files:
        if not f.exists():
            continue
        for i, line in enumerate(f.read_text().splitlines(), 1):
            m = DEFAULT_RE.match(line)
            if m:
                name, val, comment = m.group(1), m.group(2), (m.group(3) or "").strip()
                if name not in defaults:
                    defaults[name] = {
                        "file": f.name,
                        "line": i,
                        "value": val == "True",
                        "comment": comment[:200],
                    }
    return defaults


def categorize(name, comment):
    n = name.upper()
    c = comment.lower()
    if "REENTRY" in n: return "reentry"
    if "AUGMENT" in n: return "augment"
    if "LOSS_EXIT" in n or "DC_RECOVERY" in n or "BB_RECOVERY" in n: return "loss_exit"
    if "EXIT" in n or "SRS" in n or "TIME_ZONE" in n: return "exit"
    if "ENTRY" in n or "MI_ENTRY" in n or "K_ZONE" in n: return "entry"
    if "SIZING" in n or "FG_SIZING" in n: return "sizing"
    if "HEDGE" in n: return "hedge"
    if "GATE" in n or "FILTER" in n or "VETO" in n: return "gate"
    if "TRADIER" in n: return "tradier"
    if any(s in n for s in ("SATOSHIT", "DELTA", "CT_", "DC_", "WT_", "MFI_", "RSI", "ADX", "VWAP")): return "strategy"
    return "misc"


def main():
    usages = scan_usage(FILES)
    defaults = scan_defaults(CONFIG_FILES)

    registry = []
    all_names = set(usages.keys()) | set(defaults.keys())
    for name in sorted(all_names):
        d = defaults.get(name, {})
        entry = {
            "name": name,
            "default_value": d.get("value"),
            "default_location": f"{d.get('file', '?')}:{d.get('line', '?')}" if d else "NOT_FOUND",
            "comment": d.get("comment", ""),
            "category": categorize(name, d.get("comment", "")),
            "usage_count": len(usages.get(name, [])),
            "gate_locations": usages.get(name, [])[:5],  # cap to first 5 usages
        }
        registry.append(entry)

    out = BASE / "data" / "sweep_alerts" / "switch_registry.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(registry, indent=2))

    # Summary by category
    by_cat = defaultdict(list)
    dead_switches = []
    for e in registry:
        by_cat[e["category"]].append(e["name"])
        if e["default_location"] == "NOT_FOUND":
            dead_switches.append(e["name"])

    print(f"\n{'='*70}")
    print(f"  SWITCH REGISTRY — {len(registry)} unique _ENABLED switches found")
    print(f"{'='*70}\n")
    for cat in sorted(by_cat.keys()):
        print(f"  {cat:<15} = {len(by_cat[cat])} switches")
    print(f"\n  Without dataclass default (maybe dead/external): {len(dead_switches)}")
    for n in dead_switches[:20]:
        print(f"    - {n}")
    if len(dead_switches) > 20:
        print(f"    ...+{len(dead_switches)-20} more")

    print(f"\n  Output: {out}")
    print(f"  Size: {out.stat().st_size} bytes")


if __name__ == "__main__":
    main()
