#!/usr/bin/env python3
"""v15_template_is_default_mirror — make the is_default column mirror the BOLD row. Nothing else.

OPERATOR LAW (2026-10-27): the TEMPLATE bold is the ONLY authority for a default. config.py /
config_tradier.py / QuickConfig are FOLLOWERS. The is_default column is only a machine-readable
MIRROR of the bold, so when the two disagree the BOLD is right and is_default is repaired.

This is deliberately NOT tools/v15_template_defaults_fix.py, which picks the default by looking at
the live config value and re-bolds the row that matches it — that tool runs the authority backwards
and can silently overwrite an operator's bold.

Rule, per (tab, switch) group, in SWITCH_SHEETS tabs only:
  exactly one bold column-B row -> that row gets is_default=YES, every other row of the group NO.
  0 or >1 bold rows            -> REFUSED for that group, reported, left exactly as found.
Never changes a bold, a value, a fill, a font colour, a row order; never adds or removes a row;
touches only the is_default cell of a group that needs it. Grey (retired) groups are mirrored too:
the pilot skips them by font colour, so a correct mirror there costs nothing and keeps the audit clean.

  python3 tools/v15_template_is_default_mirror.py                 # report only
  python3 tools/v15_template_is_default_mirror.py --apply         # backup, write, re-validate
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import openpyxl  # noqa: E402

from v15_switch_add import FILES, SWITCH_SHEETS  # noqa: E402

SKIP_A = ("switch", "general", "blanket", "filter", "option value", "sheets applicable", "gates")


def _is_default_col(ws) -> int | None:
    for c in range(1, ws.max_column + 1):
        if str(ws.cell(row=2, column=c).value or "").strip().lower().startswith("is_default"):
            return c
    return None


def plan_workbook(path: Path):
    """[(tab, switch, row, old, new)] edits and [(tab, switch, reason)] refusals."""
    wb = openpyxl.load_workbook(str(path))
    edits, refused = [], []
    try:
        for tab in SWITCH_SHEETS:
            if tab not in wb.sheetnames:
                continue
            ws = wb[tab]
            idc = _is_default_col(ws)
            if idc is None:
                refused.append((tab, "*", "no is_default column"))
                continue
            groups = {}
            for r in range(3, ws.max_row + 1):
                a = ws.cell(row=r, column=1).value
                if a in (None, "") or str(a).strip().lower() in SKIP_A:
                    continue
                groups.setdefault(str(a).strip(), []).append(r)
            for sw, rows in groups.items():
                bold = [r for r in rows if ws.cell(row=r, column=2).font is not None and ws.cell(row=r, column=2).font.b]
                if len(bold) != 1:
                    refused.append((tab, sw, f"{len(bold)} bold rows {bold} — the sheet states no single default"))
                    continue
                for r in rows:
                    want = "YES" if r == bold[0] else "NO"
                    cur = ws.cell(row=r, column=idc).value
                    if str(cur or "").strip().upper() != want:
                        edits.append((tab, sw, r, cur, want))
        return edits, refused
    finally:
        wb.close()


def apply_workbook(path: Path, edits: list) -> None:
    """Write only the is_default cells named in `edits`, then zip-validate before replacing."""
    wb = openpyxl.load_workbook(str(path))
    try:
        cols = {}
        for tab in {e[0] for e in edits}:
            cols[tab] = _is_default_col(wb[tab])
        for tab, _sw, r, _old, new in edits:
            wb[tab].cell(row=r, column=cols[tab]).value = new
        tmp = path.with_suffix(".mirror.tmp")
        wb.save(str(tmp))
    finally:
        wb.close()
    with zipfile.ZipFile(tmp) as z:
        n = len(z.namelist())
        if n < 10 or z.testzip() is not None:
            tmp.unlink(missing_ok=True)
            raise SystemExit(f"REFUSED: {tmp.name} validated to {n} zip entries — not replacing {path.name}")
    tmp.replace(path)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--templates-dir", default=str(ROOT / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM"))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--yes-only", action="store_true", help="write only the missing is_default=YES cells (what the pilot's DEFAULTS-GATE actually requires) and leave blank NO cells alone, so the workbook bytes move as little as possible")
    ap.add_argument("--json", default="")
    a = ap.parse_args()

    stamp = datetime.datetime.now().strftime("%Y%m%d%H%M")
    report = {"templates_dir": a.templates_dir, "applied": bool(a.apply), "workbooks": {}}
    rc = 0
    for cs, fn in FILES.items():
        p = Path(a.templates_dir) / fn
        if not p.exists():
            print(f"[{cs}] MISSING {p}", flush=True)
            rc = 1
            continue
        edits, refused = plan_workbook(p)
        if a.yes_only:
            edits = [e for e in edits if e[4] == "YES"]
        before = hashlib.md5(p.read_bytes()).hexdigest()
        print(f"[{cs}] is_default cells to repair: {len(edits)} | groups REFUSED (no single bold): {len(refused)} | md5 {before[:12]}", flush=True)
        for e in edits[:12]:
            print(f"    {e[0]}!{e[1]} row {e[2]}: is_default {e[3]!r} -> {e[4]}", flush=True)
        for r in refused[:12]:
            print(f"    REFUSED {r[0]}!{r[1]}: {r[2]}", flush=True)
        after = before
        if a.apply and edits:
            bak = ROOT / "backups" / f"before_is_default_mirror_{stamp}_{fn}"
            bak.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, bak)
            apply_workbook(p, edits)
            after = hashlib.md5(p.read_bytes()).hexdigest()
            left, still = plan_workbook(p)
            if a.yes_only:
                left = [e for e in left if e[4] == "YES"]
            print(f"    applied -> md5 {after[:12]} | backup {bak.name} | re-scan: {len(left)} cells left, {len(still)} groups still refused", flush=True)
            if left:
                rc = 1
        report["workbooks"][cs] = {"file": fn, "edits": [list(e) for e in edits], "refused": [list(r) for r in refused],
                                   "md5_before": before, "md5_after": after}
    if a.json:
        out = Path(a.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=1, default=str))
        print(f"wrote {out}", flush=True)
    print("is_default is a MIRROR of the bold. A refused group means the sheet itself states no single "
          "default — fix it in the sheet, never from config.", flush=True)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
