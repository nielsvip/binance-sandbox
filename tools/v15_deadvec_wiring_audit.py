#!/usr/bin/env python3
"""v15_deadvec_wiring_audit — fail-closed proof that no LIVE-WIRED switch is excluded from per-sym_side delta testing.

WHY THIS EXISTS (USER 2026-10-15: "this stinks like complete filter skipping ... this is FATAL for the system"):
the 2026-09-28 filter-wiring census decided a switch was dead by COUNTING `getattr(cfg, NAME)` hits. Live code reads
per-sym_side settings through a MULTI-LINE call:

    _cfg(
        "EXIT_R1_R2_FILTER_TF",
        "15m",
        account_key, symbol, position_side,
    )

A line-oriented getattr scan never sees that, so 30 of the 62 names in v15_pilot.DEAD_VEC_SWITCHES were wired all
along. v15_pilot therefore never delta-tested them, and they carried un-backtested values into live money. On
2026-10-15 that shipped EXIT_R1_R2_FILTER_TF 15m->D on 103/197 stock sym_sides, which in r2_eff_tfs() collapses the
peak-then-collapse exit legs from (1h,4h,D) to (D,) — the 1h+4h profit-protection exit, silently gone — plus
FROZEN_STOP_FILTER_TF 15m->OFF on 103. Both 100% one-directional: a per-sym_side delta search cannot produce that.
13 Tradier positions gave back their entire peak the same day.

THE RULE THIS ENFORCES: a switch may be excluded from testing ONLY if it is unreachable. If it has a live consumer
AND a vec reader, it is testable and MUST be tested. Exclusion lists are never justified by a getattr count.

    python3 tools/v15_deadvec_wiring_audit.py                 # audit, exit 1 on any violation
    python3 tools/v15_deadvec_wiring_audit.py --json          # machine-readable
    python3 tools/v15_deadvec_wiring_audit.py --require-row   # also demand a template row per testable switch
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# ez_positions_quick.py carries live consumers too (e.g. the HA_WICK_QUALITY_* leg) — omitting it hid real wiring.
LIVE_FILES = ("ez_manage.py", "tradier_manage.py", "ez_positions_quick.py")
VEC_FILES = ("v12_quick_engine.py",)
VEC_DIR = "vec_decisions"
# 🔴 The four 2026-09-28/09-30 NO-LIES purges. Each is `return`-first with its body erased, so ANY name whose only
# vec presence is inside them — or is merely a QuickConfig dataclass FIELD or AUTO_WIRED_PARAMS membership — has a
# vec delta of STRUCTURALLY 0 and must NOT be called vec-wired. The first version of this file accepted exactly that
# and so "proved" 18 inert stop/trail/breakeven switches were testable; they were put back on 2026-10-15.
VEC_DEAD_FUNCS = (
    "_apply_auto_wired_params",
    "_apply_universal_distinctness_fallback",
    "_batch1_template_wiring",
    "_wire_07_exit_stops_tranche",
)
# 🔴 UNREACHABLE VEC TWINS. A read inside vec_decisions/ counts as vec wiring only if the engine actually calls the
# containing function. Proving that needs a cross-module call graph, which this tool does not build, so a twin that
# is defined-but-never-called reads as "vec=yes" and the switch looks testable when its delta is structurally 0.
# Each entry below is a switch whose vec read sits in such an orphan twin, with the proof recorded. Keep this list
# SHORT and justified: it is an exception to the tool's own verdict, so an unproven entry here re-creates exactly the
# blind spot this file exists to close. Re-check an entry whenever the engine gains a call site for its twin.
VEC_UNREACHABLE_TWINS = {
    # live tradier_manage.py:15191 calls wt_crossunder_final_exit(..., "5m") directly; the twin is defined at
    # vec_decisions/twin_entry_ports_b.py:415 and grepped for a call site in v12_quick_engine.py,
    # backtest_v12_engine.py and all of vec_decisions/ on 2026-10-15 — there is none. The 15m NPZ also has no
    # wt1_5m/k_5m to evaluate it with (only sma_200_1m[_prev]). 40 of 2026-10-15's exit reasons came from it.
    "WT_CROSSUNDER_FINAL_ENABLED": "twin_entry_ports_b.wt_crossunder_final_exit has no engine call site; NPZ lacks wt1_5m/k_5m",
}
# 🔴 LIVE COVERAGE FARMS — the live-side mirror of VEC_DEAD_FUNCS. These functions exist only to touch every knob
# once so a getattr/_cfg census counts it as "wired"; their own docstrings say so ("values are read but not used to
# veto or force trades", ez_manage.py:7622 / tradier_manage.py:6580) and NONE of them has a caller anywhere in the
# repo (`_ensure_ez_all` / `_ensure_tradier_all`: grepped 2026-10-15, zero call sites; ez_manage.py:42150 calls the
# first one "never-called" in its own comment). The repo's other tools already encode the same list —
# tools/build_switch_bible.py:40 ("reads there are NOT consumers"), tools/unwd_triage.py:25 FARM_FN,
# tests/test_per_sym_chain_parity.py:124 ("uncalled touch-blocks"). This tool's stub test only discarded the
# single-statement `_ = _cfg("NAME", ...)` shape, so the farms' OTHER shape — `if bool(_cfg("NAME", d, acct, sym,
# side)): _ = 1` — still read as a live consumer (tradier_manage.py:59783-61298 is ~1500 such lines). Excluding the
# ranges is a true-negative removal, never an undercount: a farm read changes no trade, so it can never justify
# keeping a switch out of delta testing. It can only turn a reported violation into "legitimately excluded" —
# it can never hide a real consumer, because a real consumer lives outside these functions by definition.
LIVE_DEAD_FUNCS = (
    "_ensure_ez_all",
    "_ensure_tradier_all",
    "_full_coverage_read",
    "_full_coverage_read_ez",
)
# HARD sets claim "not reachable / no semantics to mirror" — a live+vec hit there is a false claim and fails the build.
# LIVE_ONLY_SWITCHES is ADVISORY: it claims something different and defensible — the live path needs PORTFOLIO state
# (L/S ratio across open positions, EOD slim, sentiment rebalance) that a single-symbol NPZ eval cannot see, so a
# QuickConfig field may exist while per-sym_side delta testing is still meaningless. Reported, never fatal, so this
# guard stays green-able and therefore actually gets run — an always-red check is an ignored check, which is how the
# DEAD_VEC mis-census survived from 2026-09-28 to 2026-10-15.
# 🔴 ESSENTIAL — MAY NEVER BE EXCLUDED FROM SWEEPING, ON ANY HOST (USER 2026-10-16:
# "EXIT_R1_R2_FILTER_TF IS ESSENTIAL IT CAN NOT GET DESCLNNECTED AGAIN").
# It gates R2_WT_VEL_SLOW, the peak-then-collapse exit — the mechanism whose absence let 13 positions surrender their
# entire peak on 2026-10-15 (CRWD_SHORT +13.01% -> -0.20%). It is real on both sides (vec v12_quick_engine.py:17114 /
# :17113 behind WT_15M_VEL_SLOW_AT_ZERO_GAIN_ENABLED; live inside the R2_WT_VEL_SLOW gate).
# HOW IT GOT DISCONNECTED TWICE: (1) it sat in v15_pilot.DEAD_VEC_SWITCHES from the 2026-09-28 getattr census —
# removed 2026-10-15; (2) it ALSO sat in data/vec_unwired.json["filters"], a SEPARATE data-file exclusion, and that
# copy was only fixed on the Mac — S1 (where boards actually run) still carried it at 2026-10-16 00:5xZ with
# filters=18/switches=182 vs the Mac's 17/169, so every board still refused to sweep it. Two exclusion mechanisms,
# three hosts: fixing one on one box is not fixing it.
# This guard therefore checks BOTH mechanisms, and `--hosts` checks the fleet copies too.
ESSENTIAL_NEVER_EXCLUDE = {
    "EXIT_R1_R2_FILTER_TF": "gates R2_WT_VEL_SLOW peak-then-collapse exit (USER 2026-10-16: essential)",
}
HARD_SETS = ("DEAD_VEC_SWITCHES", "GLOBAL_ONLY_KEYS")
ADVISORY_SETS = ("LIVE_ONLY_SWITCHES",)
EXCLUSION_SETS = HARD_SETS + ADVISORY_SETS
TEMPLATE_DIR = "SPREADSHEETS/TEMPLATE_FINAL_NORM"


def _read(rel: str) -> str:
    p = ROOT / rel
    try:
        return p.read_text(errors="replace")
    except OSError:
        return ""


def exclusion_set(src: str, name: str) -> set:
    """The literal names inside `NAME = frozenset({...})`, comments stripped so a commented-out name never counts."""
    m = re.search(rf"^{name} = frozenset\(\{{(.*?)^\}}\)", src, re.S | re.M) or re.search(rf"^{name} = frozenset\(\{{([^}}]*)\}}\)", src, re.M)
    if not m:
        return set()
    body = re.sub(r"#[^\n]*", "", m.group(1))
    return set(re.findall(r'"([A-Z0-9_]+)"', body))


def _func_ranges(src: str, names) -> list:
    """Line ranges (0-based, half-open) of the named top-level functions."""
    lines = src.splitlines()
    defs = [(i, m.group(1)) for i, l in enumerate(lines) if (m := re.match(r"^def (\w+)", l))]
    out = []
    for idx, (i, nm) in enumerate(defs):
        if nm in names:
            out.append((i, defs[idx + 1][0] if idx + 1 < len(defs) else len(lines)))
    return out


def live_consumer(name: str, blobs: dict) -> list:
    """Real per-sym_side reads. MULTI-LINE AWARE (the bug the 2026-09-28 getattr census had) AND stub-aware (the bug
    THIS file had on its first version). The fake-audit farm migrated from `_ = getattr(cfg, X)` to
    `_ = _cfg("X", ...)` — 456 such lines in tradier_manage.py alone — so an accessor match is not enough: the
    result must be USED. A line whose whole statement is `_ = <accessor>(...)` is discarded, and so is any line
    inside a LIVE_DEAD_FUNCS coverage farm (those use the `if bool(_cfg(...)): _ = 1` shape, which the statement
    test above cannot see, and they have no callers — see the LIVE_DEAD_FUNCS note)."""
    accessors = (
        rf'_cfg(?:_auto)?\(\s*\n?\s*"{name}"',
        rf'_gx_c\(\s*\n?\s*"{name}"',
        rf'_ftf_ps\(\s*\n?\s*"{name}"',
        rf'_ftf_twins\.[a-z_]+\(\s*\n?\s*[^)]*"{name}"',
        rf'_psym(?:_cs)?_get\(\s*\n?\s*[^)]*"{name}"',  # ez_manage's actual convention (`_cfg(` appears once there)
        rf'_psym(?:_cs)?_get\(\s*\n?\s*[^)]*\b{name}\b',
    )
    out = []
    for f, src in blobs.items():
        lines = src.splitlines()
        farms = _func_ranges(src, LIVE_DEAD_FUNCS)
        for i, ln in enumerate(lines):
            if name not in ln:
                continue
            # FARM TEST: a read inside a never-called coverage farm changes no trade (see LIVE_DEAD_FUNCS).
            if any(a <= i < b for a, b in farms):
                continue
            # a match may start on this line and close on a later one; join a small window to test the call shape
            window = "\n".join(lines[max(0, i - 1):i + 6])
            if not any(re.search(p, window) for p in accessors):
                continue
            # STUB TEST: discard `_ = _cfg("NAME", ...)` / `_ = getattr(...)` — assigned to throwaway, never used.
            stmt = ln.strip()
            if re.match(r"^_\s*=\s*(?:_cfg(?:_auto)?|_gx_c|_ftf_ps|getattr|_psym(?:_cs)?_get)\(", stmt):
                continue
            out.append(f)
            break
    return sorted(set(out))


def _vec_dead_ranges(src: str) -> list:
    """Line ranges (0-based, half-open) of the purged passthrough functions + the QuickConfig field block + the
    AUTO_WIRED_PARAMS list. A name seen only inside these is inert, not wired."""
    lines = src.splitlines()
    defs = [(i, m.group(1)) for i, l in enumerate(lines) if (m := re.match(r"^def (\w+)", l))]
    ranges = []
    for idx, (i, nm) in enumerate(defs):
        if nm in VEC_DEAD_FUNCS:
            ranges.append((i, defs[idx + 1][0] if idx + 1 < len(defs) else len(lines)))
    for pat in (r"class QuickConfig.*?(?=\nclass |\ndef )", r"AUTO_WIRED_PARAMS\s*[:=].*?\n(?=\S)"):
        m = re.search(pat, src, re.S)
        if m:
            ranges.append((src[:m.start()].count("\n"), src[:m.end()].count("\n")))
    return ranges


def vec_reader(name: str, vec_src: str, vec_dir_src: str) -> bool:
    """True only for a REAL vec read: `cfg.NAME` / `getattr(cfg,"NAME")` on a live line of v12_quick_engine (outside
    every dead range), or any read in vec_decisions/*.py. A bare \\bNAME\\b match is NOT accepted — that is what let a
    dataclass field declaration and inert AUTO_WIRED_PARAMS membership pass as wiring."""
    if name in VEC_UNREACHABLE_TWINS:
        return False
    dead = _vec_dead_ranges(vec_src)
    for i, ln in enumerate(vec_src.splitlines()):
        if not re.search(rf'(?:cfg\.{name}\b|getattr\(\s*cfg\s*,\s*"{name}")', ln):
            continue
        if any(a <= i < b for a, b in dead):
            continue
        return True
    return bool(re.search(rf'(?:cfg\.{name}\b|getattr\(\s*cfg\s*,\s*"{name}"|["\']{name}["\'])', vec_dir_src))


def template_rows(name: str) -> int:
    """How many template switch-rows offer this name (column A). 0 => the pilot has nothing to iterate."""
    try:
        import openpyxl
    except ImportError:
        return -1
    n = 0
    for xf in sorted((ROOT / TEMPLATE_DIR).glob("TEMPLATE_*.xlsx")):
        try:
            wb = openpyxl.load_workbook(xf, read_only=True, data_only=True)
        except Exception:
            continue
        for sn in wb.sheetnames:
            for row in wb[sn].iter_rows(min_row=1, max_col=1, values_only=True):
                if row and row[0] is not None and str(row[0]).strip() == name:
                    n += 1
        wb.close()
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--require-row", action="store_true", help="also fail when a testable switch has no template row")
    args = ap.parse_args()

    pilot = _read("v15_pilot.py")
    if not pilot:
        print("FATAL: v15_pilot.py unreadable", file=sys.stderr)
        return 2
    live = {f: _read(f) for f in LIVE_FILES}
    vec = "".join(_read(f) for f in VEC_FILES)
    vecdir = "".join(p.read_text(errors="replace") for p in sorted((ROOT / VEC_DIR).glob("*.py")))

    violations, advisories, ok_excluded, missing_rows = [], [], [], []
    # ESSENTIAL check — BOTH exclusion mechanisms, local copies. Fails the whole audit, not an advisory.
    essential_hits = []
    for _ess, _why in ESSENTIAL_NEVER_EXCLUDE.items():
        for _sn in EXCLUSION_SETS:
            if _ess in exclusion_set(pilot, _sn):
                essential_hits.append({"switch": _ess, "where": f"v15_pilot.{_sn}", "host": "local", "why": _why})
        try:
            _vu = json.loads((ROOT / "data" / "vec_unwired.json").read_text())
            for _k in ("switches", "filters", "switches_manual", "filters_manual"):
                if _ess in (_vu.get(_k) or []):
                    essential_hits.append({"switch": _ess, "where": f"data/vec_unwired.json[{_k}]", "host": "local", "why": _why})
        except (OSError, ValueError) as _ve:
            essential_hits.append({"switch": _ess, "where": f"data/vec_unwired.json UNREADABLE ({_ve})", "host": "local", "why": _why})
    for sname in EXCLUSION_SETS:
        for name in sorted(exclusion_set(pilot, sname)):
            lc = live_consumer(name, live)
            vr = vec_reader(name, vec, vecdir)
            row = {"switch": name, "exclusion_set": sname, "live_consumers": lc, "vec_reader": vr}
            if lc and vr:
                (violations if sname in HARD_SETS else advisories).append(row)
            else:
                ok_excluded.append(row)

    if args.require_row:
        for name in ("PARITY_VEC_EXACT_MODE",):
            if name in exclusion_set(pilot, "GLOBAL_ONLY_KEYS"):
                continue
            n = template_rows(name)
            if n == 0:
                missing_rows.append({"switch": name, "template_rows": 0})
            elif n < 0:
                missing_rows.append({"switch": name, "template_rows": "openpyxl unavailable — UNVERIFIED"})

    report = {
        "violations": violations,
        "n_violations": len(violations),
        "advisories": advisories,
        "n_advisories": len(advisories),
        "n_legitimately_excluded": len(ok_excluded),
        "missing_template_rows": missing_rows,
        "essential_excluded": essential_hits,
        "n_essential_excluded": len(essential_hits),
        "verdict": "FAIL" if (violations or missing_rows or essential_hits) else "PASS",
    }
    if args.json:
        print(json.dumps(report, indent=1))
    else:
        print(f"hard sets: {', '.join(HARD_SETS)}   advisory: {', '.join(ADVISORY_SETS)}")
        if essential_hits:
            print(f"\n🔴🔴 {len(essential_hits)} ESSENTIAL SWITCH(ES) ARE EXCLUDED FROM SWEEPING — THIS MUST NEVER HAPPEN:")
            for h in essential_hits:
                print(f"   {h['switch']:28} excluded in {h['where']:44} ({h['host']}) — {h['why']}")
            print("   Remove it from that list on EVERY host (Mac + S1 + S2) and re-run. Two mechanisms exist:")
            print("   v15_pilot.DEAD_VEC_SWITCHES and data/vec_unwired.json — fixing one, or one host, is not a fix.")
        print(f"legitimately excluded (unreachable on one side): {len(ok_excluded)}")
        if advisories:
            print(f"\nadvisory — {len(advisories)} portfolio-state switch(es) in LIVE_ONLY_SWITCHES have a vec field")
            print(f"   ({', '.join(a['switch'] for a in advisories[:4])}{' ...' if len(advisories) > 4 else ''}) — by construction, not fatal")
        if violations:
            print(f"\n🔴 {len(violations)} LIVE-WIRED SWITCH(ES) EXCLUDED FROM PER-SYM_SIDE TESTING:")
            for v in violations:
                print(f"   {v['switch']:46} in {v['exclusion_set']:20} live={','.join(v['live_consumers'])} vec=yes")
            print("\n   These read a per-sym_side value every bar in live but are never delta-tested,")
            print("   so they carry un-backtested values into real money. Remove them from the set.")
        if missing_rows:
            print(f"\n🔴 {len(missing_rows)} TESTABLE SWITCH(ES) WITH NO TEMPLATE ROW (cannot be delta-tested):")
            for m in missing_rows:
                print(f"   {m['switch']:46} template_rows={m['template_rows']}")
        print(f"\nverdict: {report['verdict']}")
    return 1 if report["verdict"] == "FAIL" else 0


if __name__ == "__main__":
    sys.exit(main())
