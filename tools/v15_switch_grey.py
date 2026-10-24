"""v15_switch_grey — THE sanctioned grey tool (USER 2026-10-24: row removal is impossible)."""
import argparse
import copy
import datetime
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import openpyxl  # noqa: E402
from openpyxl.styles import Font  # noqa: E402
from v15_switch_add import FILES, SWITCH_SHEETS, die  # noqa: E402

GREY = "FFBFBFBF"  # pilot skip color


def main():
    ap = argparse.ArgumentParser(description="Grey (skip) or un-grey template rows. Values/bolds/positions never touched.")
    ap.add_argument("--switch", required=True)
    ap.add_argument("--reason", required=True, help="why (recorded in output; dead keys cite the destroy/audit)")
    ap.add_argument("--venues", default="both", choices=["both", "CRYPTO", "STOCKS"])
    ap.add_argument("--sides", default="both", choices=["both", "LONG", "SHORT"])
    ap.add_argument("--ungrey", action="store_true")
    ap.add_argument("--restore-from", default=None, help="backup xlsx to copy the row back from (restore mode)")
    ap.add_argument("--row", type=int, default=None, help="original row number in --restore-from/--tab")
    ap.add_argument("--tab", default=None, help="tab for --restore-from")
    ap.add_argument("--templates-dir", default=str(ROOT / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM"))
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    sw = a.switch.strip().upper()
    if len(a.reason.strip()) < 8:
        die("a real --reason is required (8+ chars) — grey rows must cite their destroy/audit")
    grey = not a.ungrey
    tdir = Path(a.templates_dir)
    if a.restore_from:
        return _main_restore(a, sw, grey, tdir)
    total = 0
    for cs, fn in FILES.items():
        v, s = cs.split("_")
        if a.venues != "both" and v != a.venues:
            continue
        if a.sides != "both" and s != a.sides:
            continue
        p = tdir / fn
        if not p.exists():
            die(f"missing {p}")
        hits = plan_file(p, sw)
        if not hits:
            print(f"[{cs}] {fn}: no rows", flush=True)
            continue
        print(f"[{cs}] {fn}: " + "; ".join(f"{t} rows {[x[0] for x in v]} (grey={sum(1 for x in v if x[2])}/{len(v)})" for t, v in hits.items()), flush=True)
        if a.apply:
            total += apply_file(p, sw, grey, datetime.datetime.now().strftime("%Y%m%d%H%M"))
    print(("greyed " if grey else "un-greyed ") + f"{total} rows of {sw} — {a.reason}", flush=True)
    return 0


def _main_restore(a, sw, grey, tdir):
    if a.row is None or not a.tab:
        die("--restore-from needs --row and --tab")
    for cs, fn in FILES.items():
        v, s = cs.split("_")
        if a.venues != "both" and v != a.venues:
            continue
        if a.sides != "both" and s != a.sides:
            continue
        p = tdir / fn
        if not p.exists():
            die(f"missing {p}")
        print(f"[{cs}] {fn}: restore dry-run — {sw} {a.tab} r{a.row} from {Path(a.restore_from).name} (grey={grey})", flush=True)
        if a.apply:
            restore_row(p, sw, a.restore_from, a.tab, a.row, grey, datetime.datetime.now().strftime("%Y%m%d%H%M"))
    return 0


def _grey_state(cell):
    f = cell.font
    if f is None or f.color is None:
        return False
    return str(getattr(f.color, "rgb", "") or "").upper() == GREY


def _set_grey(cell, grey):
    f = cell.font
    base = {"name": (f.name if f else None) or "Arial", "size": (f.size if f else None) or 10, "bold": bool(f.b) if f else False}
    cell.font = Font(color=GREY if grey else None, **base)


def plan_file(path, sw):
    wb = openpyxl.load_workbook(str(path))
    try:
        hits = {}
        for tab in SWITCH_SHEETS:
            if tab not in wb.sheetnames:
                continue
            ws = wb[tab]
            rows = [r for r in range(3, ws.max_row + 1) if ws.cell(r, 1).value == sw]
            if rows:
                hits[tab] = [(r, ws.cell(r, 2).value, _grey_state(ws.cell(r, 1))) for r in rows]
        return hits
    finally:
        wb.close()


def apply_file(path, sw, grey, ts):
    shutil.copy2(path, ROOT / "backups" / f"before_switch_grey_{sw}_{ts}_{path.name}")
    wb = openpyxl.load_workbook(str(path))
    try:
        n = 0
        for tab in SWITCH_SHEETS:
            if tab not in wb.sheetnames:
                continue
            ws = wb[tab]
            for r in range(3, ws.max_row + 1):
                if ws.cell(r, 1).value == sw and _grey_state(ws.cell(r, 1)) != grey:
                    _set_grey(ws.cell(r, 1), grey)
                    n += 1
        tmp = str(path) + ".greytmp"
        wb.save(tmp)
        with zipfile.ZipFile(tmp) as z:
            if len(z.namelist()) < 10:
                import os
                os.remove(tmp)
                die(f"refusing truncated save {path.name}")
        import os
        os.replace(tmp, str(path))
        return n
    finally:
        wb.close()


def restore_row(path, sw, backup, tab, row, grey, ts):
    wb_b = openpyxl.load_workbook(str(backup), data_only=False)
    wb = openpyxl.load_workbook(str(path))
    try:
        ws_b, ws = wb_b[tab], wb[tab]
        src = [ws_b.cell(row, c) for c in range(1, ws_b.max_column + 1)]
        if src[0].value != sw:
            die(f"backup {Path(backup).name}!{tab} r{row} holds {src[0].value!r}, not {sw}")
        shutil.copy2(path, ROOT / "backups" / f"before_switch_grey_restore_{sw}_{ts}_{path.name}")
        ws.insert_rows(row)
        for c, s in enumerate(src, 1):
            d = ws.cell(row=row, column=c)
            d.value = s.value
            d.font = copy.copy(s.font)
            d.fill = copy.copy(s.fill)
            d.alignment = copy.copy(s.alignment)
            d.border = copy.copy(s.border)
        if grey:
            _set_grey(ws.cell(row=row, column=1), True)
        tmp = str(path) + ".greytmp"
        wb.save(tmp)
        with zipfile.ZipFile(tmp) as z:
            if len(z.namelist()) < 10:
                import os
                os.remove(tmp)
                die(f"refusing truncated save {path.name}")
        import os
        os.replace(tmp, str(path))
        print(f"[{path.name}] restored {sw} at {tab} r{row} (grey={grey})", flush=True)
    finally:
        wb_b.close()
        wb.close()


if __name__ == "__main__":
    sys.exit(main())
