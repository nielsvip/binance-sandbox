#!/usr/bin/env python3
"""TEMPLATE IS THE ONLY AUTHORITY — audit every config/QuickConfig default.

OPERATOR 2026-10-27, verbatim and emphatic:
  "NO config or live trading has ANY default that is NOT overridden by TEMPLATE_
   switch default or override. TEMPLATE is the ONLY authority any default that
   exists in any trading or config script can ONLY exist because NO switch
   exists for it just yet"

So for every switch name there are exactly two legal states:
  A. IT HAS A TEMPLATE ROW  -> the template BOLD is the default, and
     config.py / config_tradier.py / QuickConfig must MATCH it exactly. A
     mismatch is a BUG: live would trade a default the sheet never chose.
  B. IT HAS NO TEMPLATE ROW -> a config default may exist, and ONLY because no
     switch exists for it yet. It is a placeholder awaiting a row, not an
     authority.

This tool reports, per cat_side workbook:
  MISMATCH    - has a template bold, but the config/QuickConfig default differs
  NO_ROW      - config default with no template row at all (state B)
  MULTI_BOLD  - more than one bold row for a switch in a tab (no single default)
  NO_BOLD     - has rows but none bold (the sheet states no default)
Exit code is 1 when any MISMATCH exists, so a chain step can gate on it.

  python3 tools/template_is_the_only_authority_audit.py [--json out.json] [--limit 40]
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import openpyxl  # noqa: E402

from v15_switch_add import FILES, SWITCH_SHEETS  # noqa: E402

CAT_CONFIG = {
    "CRYPTO_LONG": "config.py",
    "CRYPTO_SHORT": "config.py",
    "STOCKS_LONG": "config_tradier.py",
    "STOCKS_SHORT": "config_tradier.py",
}
CLASS_OF = {"config.py": "Config", "config_tradier.py": "TradierConfig", "v12_quick_engine.py": "QuickConfig"}


def class_defaults(fname: str, clsname: str) -> dict:
    """Literal class-level defaults, read from the AST (no import side effects)."""
    out = {}
    try:
        tree = ast.parse((ROOT / fname).read_text())
    except Exception:
        return out
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != clsname:
            continue
        for stmt in node.body:
            tgt = None
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                tgt, val = stmt.target.id, stmt.value
            elif isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
                tgt, val = stmt.targets[0].id, stmt.value
            if not tgt or val is None:
                continue
            try:
                out[tgt] = ast.literal_eval(val)
            except Exception:
                pass  # computed default: not a literal authority, skip
    return out


def norm(v):
    """Compare a template cell to a config literal without type noise."""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if v is None:
        return ""
    s = str(v).strip()
    if s.lower() in ("true", "false"):
        return s.upper()
    try:
        f = float(s)
        return str(int(f)) if f == int(f) else str(f)
    except (TypeError, ValueError):
        return s.upper()


def audit_workbook(path: Path):
    """{switch: {'bolds': [...], 'rows': n, 'tabs': [...]}} for one workbook."""
    wb = openpyxl.load_workbook(str(path))
    try:
        per = {}
        for tab in SWITCH_SHEETS:
            if tab not in wb.sheetnames:
                continue
            ws = wb[tab]
            for r in range(3, ws.max_row + 1):
                name = str(ws.cell(r, 1).value or "").strip()
                if not name:
                    continue
                e = per.setdefault(name, {"bolds": [], "rows": 0, "tabs": set()})
                e["rows"] += 1
                e["tabs"].add(tab)
                f = ws.cell(r, 2).font
                if f and f.bold:
                    e["bolds"].append(ws.cell(r, 2).value)
        for e in per.values():
            e["tabs"] = sorted(e["tabs"])
        return per
    finally:
        wb.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--templates-dir", default=str(ROOT / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM"))
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--json", default="")
    a = ap.parse_args()

    quick = class_defaults("v12_quick_engine.py", "QuickConfig")
    report = {"generated_by": "tools/template_is_the_only_authority_audit.py", "cat_sides": {}}
    total_mismatch = 0

    for cs, fn in FILES.items():
        p = Path(a.templates_dir) / fn
        if not p.exists():
            print(f"[{cs}] MISSING {p}", flush=True)
            continue
        per = audit_workbook(p)
        cfg_file = CAT_CONFIG[cs]
        cfg = class_defaults(cfg_file, CLASS_OF[cfg_file])
        mismatch, no_bold, multi_bold = [], [], []
        for name, e in per.items():
            if len(e["bolds"]) > 1 and len(set(norm(b) for b in e["bolds"])) > 1:
                multi_bold.append({"switch": name, "bolds": [str(b) for b in e["bolds"]], "tabs": e["tabs"]})
                continue
            if not e["bolds"]:
                no_bold.append({"switch": name, "rows": e["rows"], "tabs": e["tabs"]})
                continue
            bold = e["bolds"][0]
            for src, defaults in ((cfg_file, cfg), ("QuickConfig", quick)):
                if name not in defaults:
                    continue
                if norm(defaults[name]) != norm(bold):
                    mismatch.append({"switch": name, "template_bold": str(bold),
                                     "source": src, "config_default": str(defaults[name]),
                                     "tabs": e["tabs"]})
        # state B: a config default with no template row at all
        no_row = sorted(k for k in cfg if k not in per and k.isupper())
        report["cat_sides"][cs] = {
            "template_switches": len(per),
            "mismatch": sorted(mismatch, key=lambda x: x["switch"]),
            "mismatch_n": len(mismatch),
            "multi_bold_n": len(multi_bold),
            "no_bold_n": len(no_bold),
            "no_row_n": len(no_row),
            "no_bold": no_bold[:50],
            "multi_bold": multi_bold[:50],
            "no_row_sample": no_row[:50],
        }
        total_mismatch += len(mismatch)
        print(f"[{cs}] template switches {len(per):5d} | MISMATCH {len(mismatch):4d} | "
              f"multi-bold {len(multi_bold):3d} | no-bold {len(no_bold):4d} | "
              f"config default with NO row {len(no_row):4d}", flush=True)
        for m in sorted(mismatch, key=lambda x: x["switch"])[: a.limit]:
            print(f"    MISMATCH {m['switch']:44s} bold={m['template_bold']!r:12s} "
                  f"{m['source']}={m['config_default']!r}", flush=True)

    report["total_mismatch"] = total_mismatch
    print(f"\nTOTAL MISMATCH across the 4 workbooks: {total_mismatch}", flush=True)
    print("TEMPLATE is the ONLY authority: every MISMATCH is a config default the sheet "
          "never chose, and every 'no row' is a placeholder that still needs a switch.", flush=True)
    if a.json:
        out = Path(a.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=1, sort_keys=True, default=str))
        print(f"wrote {out}", flush=True)
    return 1 if total_mismatch else 0


if __name__ == "__main__":
    raise SystemExit(main())
