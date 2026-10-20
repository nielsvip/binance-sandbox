#!/usr/bin/env python3
"""switch_surface_watchdog — ONE check that every switch exists on EVERY surface it must (USER 2026-10-20).

USER: "ANYTHING in TEMPLATE_CRYPTO_* HAS TO EXIST IN config and ANY FUCKING LINE IN TEMPLATE_STOCKS_* HAS TO EXIST IN
CONFIG TRADIER" / "YOU CAN SIMPLY PUT A WATCHDOG TO MAKE SURE WHEN ONE CHANGES ALL CHANGE FOR TEMPLATE_* QUICKCONFIG
CONFIG ETC ... THAT WAS SOLVED YEARS AGO".

WHY IT EXISTS: the repo already had a guard for this — tools/verify_switch_parity.py, documented in PARITY_TESTING.md as
"Guard: 351-switch full parity never lost". Measured 2026-10-20 02:38Z it cannot run at all:
    PARITY GUARD FAILED: live_decide or vec_decide not importable — cannot rerun real functions
So the invariant has had NO enforcement for an unknown period, and drift accumulated unnoticed. This watchdog is
deliberately NOT that guard: it imports nothing from the live decision path, so it cannot be disabled by a rename or a
moved symbol in ez_manage/tradier_manage. It only answers the surface question — does the switch EXIST where it must —
which is the half that must never silently rot. verify_switch_parity stays the deeper causal/behavioural check.

THE FOUR SURFACES AND THE RULE
    TEMPLATE_CRYPTO_{LONG,SHORT}.xlsx  ->  config.Config            AND  v12_quick_engine.QuickConfig
    TEMPLATE_STOCKS_{LONG,SHORT}.xlsx  ->  config_tradier.TradierConfig  AND  v12_quick_engine.QuickConfig
A switch the boards sweep on a venue MUST be declared on that venue's live config (or live cannot honour the board) and
on QuickConfig (or the vector cannot model what the board claims to measure). Either absence is a breach: the board
produces deltas for a knob that one side of the system cannot see.

WHAT IS NOT A SWITCH (column A of a TEMPLATE sheet carries more than switches)
The 13 SWITCH_SHEETS tab names, legend rows ("BLUE #DDEBF7 header", "Color"), and the self-inventory banners
("FULL INVENTORY (3691 rows)", "Counts: BOTH_WIRED=2933 | VECTOR_ONLY=51 | ..."). A raw column-A scrape reports ~47
crypto / ~45 stocks "missing" of which most are these — that is why this module filters before it counts, and why a
number from an unfiltered scrape must never be quoted as a breach count.

READ-ONLY. Never writes a TEMPLATE (CLAUDE.md: only tools/v15_autopilot.py stage template may) and never edits a config:
adding a default is a trading decision, so this reports and fails, it does not repair.

Usage:
    tools/switch_surface_watchdog.py                 # report + exit 1 on any breach
    tools/switch_surface_watchdog.py --json          # machine-readable only
    tools/switch_surface_watchdog.py --quiet         # cron: print only on breach
Report: data/reports/switch_surface/surface_<UTCSTAMP>.json (+ latest.json)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "reports" / "switch_surface"

# The 13 SWITCH_SHEETS tab names appear in column A of the index/other sheets; they are sheet labels, not switches.
SHEET_NAMES = frozenset({
    "STDEV_SLOPE_SIZING", "ENTRY_REVERSAL_BOUNCE", "ENTRY_BREAKOUT_CHANNEL", "ENTRY_CONFIRMATION_GATES",
    "EXIT_STRUCTURAL", "EXIT_VELOCITY", "REENTRY_WINDOWED", "REENTRY_ADAPTIVE", "AUGMENT_TREND",
    "AUGMENT_RISK_SIZING", "REDUCE_PROFIT_LOCK", "REDUCE_SIGNAL_RATER", "GLOBAL_RISK_GATES",
})
SWITCH_RX = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$")  # UPPER_SNAKE_CASE, at least one underscore
VENUES = {
    "crypto": (("SPREADSHEETS/TEMPLATE_CRYPTO_LONG.xlsx", "SPREADSHEETS/TEMPLATE_CRYPTO_SHORT.xlsx"), "config", "Config"),
    "stocks": (("SPREADSHEETS/TEMPLATE_STOCKS_LONG.xlsx", "SPREADSHEETS/TEMPLATE_STOCKS_SHORT.xlsx"), "config_tradier", "TradierConfig"),
}


def is_switch(name) -> bool:
    """A real switch name, not a sheet label, legend row or inventory banner."""
    if not isinstance(name, str):
        return False
    s = name.strip()
    if not s or s in SHEET_NAMES:
        return False
    return bool(SWITCH_RX.match(s))


def template_switches(paths):
    """Union of real switch names in column A of every non-BASELINE sheet. Missing workbook -> reported, not fatal."""
    import openpyxl

    found, missing = set(), []
    for rel in paths:
        p = ROOT / rel
        if not p.exists():
            missing.append(rel)
            continue
        wb = openpyxl.load_workbook(str(p), read_only=True, data_only=True)
        try:
            for ws in wb.worksheets:
                if "BASELINE" in ws.title.upper():
                    continue
                for row in ws.iter_rows(min_row=3, max_col=1, values_only=True):
                    if is_switch(row[0]):
                        found.add(row[0].strip())
        finally:
            wb.close()
    return found, missing


def surface(mod_name, cls_name):
    """An instantiated config surface, or None with the reason (fail LOUD: a surface we cannot load is a breach)."""
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        mod = __import__(mod_name)
        return getattr(mod, cls_name)(), ""
    except Exception as exc:
        return None, f"{type(exc).__name__}: {str(exc)[:120]}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--quiet", action="store_true", help="cron: print only when a breach exists")
    args = ap.parse_args()
    now = dt.datetime.now(dt.timezone.utc)
    rep = {"generated_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "venues": {}, "breaches": 0, "surface_errors": []}
    vec, vec_err = surface("v12_quick_engine", "QuickConfig")
    if vec_err:
        rep["surface_errors"].append(f"QuickConfig unloadable — {vec_err}")
    for venue, (paths, mod_name, cls_name) in VENUES.items():
        sw, absent_books = template_switches(paths)
        cfg, cfg_err = surface(mod_name, cls_name)
        if cfg_err:
            rep["surface_errors"].append(f"{mod_name}.{cls_name} unloadable — {cfg_err}")
        miss_cfg = sorted(k for k in sw if cfg is not None and not hasattr(cfg, k))
        miss_vec = sorted(k for k in sw if vec is not None and not hasattr(vec, k))
        rep["venues"][venue] = {
            "templates": list(paths), "templates_absent": absent_books, "switches": len(sw),
            "live_config": f"{mod_name}.{cls_name}",
            "missing_from_live_config": miss_cfg, "n_missing_from_live_config": len(miss_cfg),
            "missing_from_quickconfig": miss_vec, "n_missing_from_quickconfig": len(miss_vec),
        }
        rep["breaches"] += len(miss_cfg) + len(miss_vec)
    rep["verdict"] = "OK" if (rep["breaches"] == 0 and not rep["surface_errors"]) else "BREACH"
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"surface_{now.strftime('%Y%m%dT%H%MZ')}.json").write_text(json.dumps(rep, indent=1))
    (OUT / "latest.json").write_text(json.dumps(rep, indent=1))
    if args.json:
        print(json.dumps(rep, indent=1))
    elif not (args.quiet and rep["verdict"] == "OK"):
        for e in rep["surface_errors"]:
            print(f"[surface] SURFACE ERROR: {e}")
        for venue, v in rep["venues"].items():
            print(f"[surface] {venue}: {v['switches']} template switches -> {v['live_config']}")
            for label, keys in (("live config", v["missing_from_live_config"]), ("QuickConfig", v["missing_from_quickconfig"])):
                if keys:
                    print(f"    MISSING from {label} ({len(keys)}): {', '.join(keys[:12])}{' …' if len(keys) > 12 else ''}")
            if v["templates_absent"]:
                print(f"    TEMPLATES ABSENT: {v['templates_absent']}")
        print(f"[surface] {rep['verdict']}: {rep['breaches']} breach(es) -> {OUT / 'latest.json'}")
    return 0 if rep["verdict"] == "OK" else 1


if __name__ == "__main__":
    sys.exit(main())
