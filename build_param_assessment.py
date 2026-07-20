#!/usr/bin/env python3
"""build_param_assessment.py — EVERY SINGLE param-value that needs assessing, both
systems, one row per (system, param, candidate_value). No param hidden, nothing claimed
"done" that wasn't. Surfaces the long/short / direction / ratio / hedge settings first
because those drive whether longs vs shorts fire — the daily bleed.

Per cell:
  assessability  — BACKTESTABLE   (engine reads it -> OFAT/sweep can test it)
                   FORWARD_TEST    (live-only: live reads it, NO backtest path -> must be
                                    judged on live forward results; cannot be swept)
                   DEAD_REVIEW     (referenced in no engine/live source -> verify + remove,
                                    not a backtest target)
                   NEEDS_RANGE     (engine-readable but non-numeric/no candidate grid yet)
  is_current_value — Y if this candidate equals the live config value now
  tested_ever      — Y if this exact value appears in the results-DB-derived history
  is_long_short    — Y if the param controls long/short/direction/ratio/hedge behavior
  priority_rank    — churn/exit/entry/sizing/LS levers first

NO-LIES: this is an assessment WORKLIST, not a result. It promotes nothing. tested_ever=Y
means "a run touched this value", NOT "verified good" — verification still needs Tier-2 at
the sample floor. The honest headline: how few of the >14k cells have ever been assessed.
"""
import csv
import json
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent
MODES = ["crypto", "tradier"]
LS_KW = ("LONG", "SHORT", "DIRECTION", "_SIDE", "BIAS", "RATIO", "HEDGE",
         "SKIP_SHORT", "SKIP_LONG", "L_S", "LS_")
PRIORITY_KW = ["R1_", "R2_", "DC_LOW", "DC_HIGH", "NOLOSS", "EXIT", "STOP", "GIVEBACK",
               "FROZEN", "PEAK", "WT_VEL", "HOLD", "REENTRY", "AUGMENT", "FORCE_OPEN",
               "ENTRY", "WT_DC", "GR_", "SCORE", "ALIGN", "SIZE", "MIN_GAIN", "DUP",
               "OVERTRADE", "COOLDOWN", "HEDGE", "RATIO", "SHORT", "LONG", "DIRECTION"]


def priority(name):
    up = name.upper()
    for i, kw in enumerate(PRIORITY_KW):
        if kw in up:
            return i
    return len(PRIORITY_KW)


def is_ls(name):
    up = name.upper()
    return any(k in up for k in LS_KW)


def derive_grid(name, default):
    if isinstance(default, bool):
        return [True, False]
    if isinstance(default, (int, float)):
        v = float(default)
        if v == 0:
            grid = [0, 1, 2] if ("MIN" in name.upper() or "TFS" in name.upper()) else [0.0, 0.5, 1.0]
        else:
            grid = sorted({round(v * m, 6) for m in (0.5, 0.75, 1.0, 1.25, 1.5)})
        return [int(x) if isinstance(default, int) and float(x).is_integer() else x for x in grid]
    return None


def main():
    rows = []
    head = {}
    for mode in MODES:
        man = json.loads((BASE / f"data/param_sweep_manifest_{mode}.json").read_text())["params"]
        sp = BASE / f"data/param_baseline_spec_{mode}.json"
        spec = json.loads(sp.read_text()) if sp.exists() else {}
        active = spec.get("active_search_space", {})
        n_cells = n_back = n_tested = n_ls = n_ls_untested = 0
        for name in sorted(man, key=lambda n: (0 if is_ls(n) else 1, priority(n), n)):
            v = man[name]
            tier = v["sweep_tier"]
            default = v.get("default")
            tv = v.get("test_values")
            grid = tv if tv else derive_grid(name, default)
            if not grid:
                grid = [default]
                assess = "NEEDS_RANGE" if tier in ("VEC_SCREEN", "ENGINE_SCREEN") else (
                    "FORWARD_TEST" if tier == "LIVE_ONLY" else "DEAD_REVIEW")
            else:
                assess = ("BACKTESTABLE" if tier in ("VEC_SCREEN", "ENGINE_SCREEN")
                          else "FORWARD_TEST" if tier == "LIVE_ONLY" else "DEAD_REVIEW")
            seen = set(str(x) for x in (active.get(name, {}).get("distinct_values_seen", []) or []))
            ls = is_ls(name)
            for cand in grid:
                cur = (str(cand) == str(default))
                tested = str(cand) in seen
                n_cells += 1
                if assess == "BACKTESTABLE":
                    n_back += 1
                if tested:
                    n_tested += 1
                rows.append({
                    "system": mode,
                    "param": name,
                    "is_long_short": "Y" if ls else "",
                    "sweep_tier": tier,
                    "assessability": assess,
                    "current_value": default,
                    "candidate_value": cand,
                    "is_current_value": "Y" if cur else "",
                    "tested_ever": "Y" if tested else "",
                    "priority_rank": priority(name),
                    "assessment_method": {
                        "BACKTESTABLE": "OFAT/Tier-2 sweep on NPZ (in grind)",
                        "FORWARD_TEST": "live-only — judge on forward live results (no backtest path)",
                        "DEAD_REVIEW": "no consumer — verify dead & remove (not a test target)",
                        "NEEDS_RANGE": "engine-readable but needs a candidate grid defined",
                    }[assess],
                    "notes": "",
                })
            if ls:
                n_ls += 1
                if not any(str(c) in seen for c in grid):
                    n_ls_untested += 1
        head[mode] = dict(cells=n_cells, backtestable=n_back, tested=n_tested,
                          ls_params=n_ls, ls_untested=n_ls_untested, params=len(man))
    out = BASE / "data/param_assessment_full.csv"
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    # summary
    s = ["# Full param-value assessment worklist — EVERY value that needs assessing\n"]
    tot = sum(h["cells"] for h in head.values())
    tb = sum(h["backtestable"] for h in head.values())
    tt = sum(h["tested"] for h in head.values())
    s.append(f"**{tot} value-cells across {sum(h['params'] for h in head.values())} params.** "
             f"Backtestable: {tb}. Ever-tested (any value touched in history): {tt} "
             f"({100.0*tt/tot:.1f}%). **Untested: {tot-tt} ({100.0*(tot-tt)/tot:.1f}%).**\n")
    for mode, h in head.items():
        s.append(f"- **{mode}**: {h['cells']} cells / {h['params']} params · backtestable={h['backtestable']} · "
                 f"tested_ever={h['tested']} · long/short params={h['ls_params']} "
                 f"(untested L/S params={h['ls_untested']})")
    s.append("")
    s.append("## How each non-backtestable bucket gets assessed (honest)")
    s.append("- **BACKTESTABLE**: covered by the OFAT grind on S1 (NPZ, representative syms). The only set we can sweep faithfully.")
    s.append("- **FORWARD_TEST (live-only)**: the live code reads these but NO backtest engine path exists — they cannot be swept; they must be judged on forward live results. Many long/short gates live here.")
    s.append("- **DEAD_REVIEW**: referenced in no engine/live source — verify truly dead and remove; sweeping them = 0-effect lying rows (banned).")
    s.append("")
    s.append("Sorted so long/short / direction / ratio / hedge params come FIRST. Filter the CSV: "
             "`is_long_short=Y` + `tested_ever=''` = the L/S settings hurting you that have never been assessed.")
    (BASE / "data/param_assessment_SUMMARY.md").write_text("\n".join(s) + "\n")
    print(f"assessment -> {out}  ({tot} value-cells)")
    print(f"  backtestable={tb}  tested_ever={tt} ({100.0*tt/tot:.1f}%)  UNTESTED={tot-tt}")
    for mode, h in head.items():
        print(f"  {mode}: {h['cells']} cells, {h['ls_params']} L/S params ({h['ls_untested']} untested)")
    print("summary -> data/param_assessment_SUMMARY.md")


if __name__ == "__main__":
    main()
