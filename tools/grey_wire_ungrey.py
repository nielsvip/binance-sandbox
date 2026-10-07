#!/usr/bin/env python3
"""grey_wire_ungrey — un-grey (col-A font color -> None, name/size/bold kept) the named switch groups in the
SWITCH_SHEETS tabs of TEMPLATE_{cat_side}.xlsx after the switch was wired live+vec (grey-wire job 2026-09-30).

Touches ONLY the col-A font colour of rows whose col-A name is in --switch; never values, bold/is_default,
row order, rows or columns. Refuses a switch whose is_default=YES row != the venue config default (the
v15_template_defaults_fix audit rule), or that is not grey in that template. Backup + atomic save + reload check.

  python tools/grey_wire_ungrey.py --cat-side STOCKS_LONG,STOCKS_SHORT --switch NAME[,NAME...] [--dry-run]
"""
import argparse
import datetime
import shutil
import sys
import zipfile
from pathlib import Path

import openpyxl
from openpyxl.styles import Font

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import v15_template_defaults_fix as F  # noqa: E402

GREY = "FFBFBFBF"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cat-side", required=True)
    ap.add_argument("--switch", required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    names = [s.strip() for s in a.switch.split(",") if s.strip()]
    ts = datetime.datetime.now().strftime("%Y%m%d%H%M")
    rc = 0
    for cs in [c.strip() for c in a.cat_side.split(",") if c.strip()]:
        path = ROOT / "SPREADSHEETS" / F.TEMPLATES[cs]
        src = F.cat_side_defaults(cs)
        wb = openpyxl.load_workbook(str(path))
        done, refused = [], []
        for sw in names:
            rows = []
            for tab in F.SWITCH_SHEETS:
                if tab not in wb.sheetnames:
                    continue
                ws = wb[tab]
                idc = F.isdef_col(ws)
                for r in range(F.HDR_ROWS + 1, ws.max_row + 1):
                    if str(ws.cell(row=r, column=1).value or "").strip() == sw:
                        rows.append((ws, r, idc))
            if not rows:
                refused.append((sw, "not in template"))
                continue
            grey = [x for x in rows if str(getattr(getattr(x[0].cell(row=x[1], column=1).font, "color", None), "rgb", "") or "").upper() == GREY]
            if not grey:
                refused.append((sw, "not grey"))
                continue
            s = src.get(sw)
            bad_tabs = []
            for tab in sorted({x[0].title for x in rows}):
                trows = [x for x in rows if x[0].title == tab]
                yes = [x for x in trows if x[2] and str(x[0].cell(row=x[1], column=x[2]).value or "").strip().upper() == "YES"]
                if len(yes) != 1 or s is None or s[1] is None or F.norm(yes[0][0].cell(row=yes[0][1], column=2).value) != F.norm(s[1]):
                    bad_tabs.append(f"{tab}: default rows {[y[0].cell(row=y[1], column=2).value for y in yes]} vs {s[0] if s else None} {s[1] if s else None!r}")
            if bad_tabs:
                refused.append((sw, bad_tabs))
                continue
            for ws, r, _ in rows:
                f = ws.cell(row=r, column=1).font
                ws.cell(row=r, column=1).font = Font(name=(f.name if f else None) or "Arial", size=(f.size if f else None) or 10, bold=f.b if f else False, italic=f.i if f else False)
            done.append((sw, len(rows)))
        print(f"[{cs}] ungrey {done} refused {refused}")
        if refused:
            rc = 1
        if done and not a.dry_run:
            shutil.copy2(path, ROOT / "backups" / f"before_grey_wire_ungrey_{ts}_{path.name}")
            tmp = path.with_suffix(".tmp.xlsx")
            wb.save(str(tmp))
            with zipfile.ZipFile(str(tmp)) as z:
                if len(z.namelist()) < 10:
                    sys.exit(f"REFUSED: {tmp} truncated")
            openpyxl.load_workbook(str(tmp))
            tmp.replace(path)
    return rc


if __name__ == "__main__":
    sys.exit(main())
