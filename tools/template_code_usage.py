#!/usr/bin/env python3
"""template_code_usage — which functions read each switch name (AST function map over the live + vector modules).
python tools/template_code_usage.py --out data/template_audit/<ts>   -> code_usage_full.json {name: [[file, fn, count]]} and code_usage_lines.json {name: [[file, line, fn]]} (non-wiring readers, live files first)."""
import argparse
import ast
import collections
import glob
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXC = ("backtest_", "test_", "v15_", "v11_", "v12_wide", "v12_quick_engine_", "v8_", "v10_", "v9_", "config", "build_", "master_", "tmp_", "chart_", "compare_", "audit_", "verify_", "check_switch", "switch_", "sweep_")
WIRING = re.compile(r"^(<module>|_ensure_.*|_batch\d.*|_wire_.*|apply_tradier_defaults|__init__|apply|run|_apply_research_only_live_gates)$")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.chdir(ROOT)
    names = set()
    for p in glob.glob("SPREADSHEETS/TEMPLATE_STAGED/*/TEMPLATE_*.xlsx") + glob.glob("SPREADSHEETS/TEMPLATE_*.xlsx") + glob.glob("backups/before_staged_apply_*TEMPLATE_*.xlsx"):
        pass
    import openpyxl
    for p in glob.glob("backups/*TEMPLATE*.xlsx"):
        try:
            if os.path.getmtime(p) < 1790400000 or os.path.getsize(p) < 1_000_000:   # since ~2026-09-26
                continue
        except Exception:
            continue
    # name universe = every config/quick field name is too large; use names found in the 4 live templates + the newest backup per template
    src = glob.glob("SPREADSHEETS/TEMPLATE_[CS]*.xlsx") + glob.glob("backups/before_staged_apply_202610010001_TEMPLATE_*.xlsx")
    for p in src:
        wb = openpyxl.load_workbook(p, read_only=True)
        for ws in wb.worksheets:
            if ws.title in ("LEGEND_FILTERS", "INSTRUCTIONS", "FILTERS_EXPLAINED", "INSTRUCTIONS_V2", "Results_Deltas", "FILTER_DICTIONARY_V2", "Results_30d_Deltas", "PARKED_ROWS", "WIRING_INVENTORY"):
                continue
            for r in ws.iter_rows(min_row=3, max_col=1, values_only=True):
                if r[0]:
                    names.add(str(r[0]).strip())
    extra = json.loads(Path(a.out, "extra_names.json").read_text()) if Path(a.out, "extra_names.json").exists() else []
    names |= set(extra)
    files = [f for f in glob.glob("*.py") if not f.startswith(EXC) and not f.endswith("_test.py") and os.path.getsize(f) < 80_000_000] + glob.glob("vec_decisions/*.py")
    pat = {n: re.compile(r"\b" + re.escape(n) + r"\b") for n in names}
    full, lines = {}, {}
    for f in files:
        src_ = open(f, errors="ignore").read()
        tl = src_.splitlines()
        try:
            tree = ast.parse(src_)
            defs = sorted([(n.lineno, n.end_lineno, n.name) for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))], key=lambda x: x[1] - x[0])
        except Exception:
            defs = []
        for i, line in enumerate(tl, 1):
            if line.lstrip().startswith("#"):
                continue
            for n, p in pat.items():
                if n in line and p.search(line):
                    fn = next((d[2] for d in defs if d[0] <= i <= d[1]), "<module>")
                    full.setdefault(n, collections.Counter())[(f, fn)] += 1
                    if not WIRING.match(fn):
                        lines.setdefault(n, []).append([f, i, fn])
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "code_usage_full.json").write_text(json.dumps({k: [[a_, b, c] for (a_, b), c in v.most_common()] for k, v in full.items()}))
    live_first = {k: sorted(v, key=lambda x: (x[0] == "v12_quick_engine.py" or x[0].startswith("vec_decisions/"), x[0]))[:4] for k, v in lines.items()}
    (out / "code_usage_lines.json").write_text(json.dumps(live_first))
    print(len(names), "names;", len(full), "read somewhere;", len(lines), "read by a non-wiring function")


if __name__ == "__main__":
    main()
