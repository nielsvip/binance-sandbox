#!/usr/bin/env python3
"""v15_add_orange_rows — populate PER-TAB orange (GENERAL) rows in TEMPLATE_*.xlsx from the
empirical map (data/opportune_filter_map_empirical.json _orange_per_tab). Orange rows are tagged
by col-A fill FFE699 (v15_pilot.py:1194 detects orange_rows this way) so the existing tab-boundary
_orange_block() evaluates them vs cumulative and promotes the best positive before jumping tab.
Family(col D)=GENERAL. Appended below the tab's white switch rows (R5). Backup + reopen-verify.
Only fills cat_sides passed via --catsides (default stocks; crypto deferred until its clean rerun).
"""
import sys as _sys_guard
_sys_guard.exit("REFUSED (USER 2026-09-30): no script may add or remove template/sheet rows")
import argparse, json, os, pathlib, shutil, time, zipfile
import openpyxl
from openpyxl.styles import Font, PatternFill

ROOT = pathlib.Path("/Users/niels/Documents/binance")
SP = ROOT / "SPREADSHEETS"
ORANGE = PatternFill(start_color="FFE699", end_color="FFE699", fill_type="solid")
TMPL = {"CRYPTO_LONG": "TEMPLATE_CRYPTO_LONG.xlsx", "CRYPTO_SHORT": "TEMPLATE_CRYPTO_SHORT.xlsx",
        "STOCKS_LONG": "TEMPLATE_STOCKS_LONG.xlsx", "STOCKS_SHORT": "TEMPLATE_STOCKS_SHORT.xlsx"}


def coerce_vals(vals):
    out = []
    for v in vals:
        s = str(v).strip()
        if s.lower() in ("true", "false"):
            out.append(s.lower() == "true")
        elif s.startswith("+"):
            try:
                out.append(float(s[1:]))
            except Exception:
                pass
        else:
            try:
                out.append(float(s))
            except Exception:
                out.append(s)
    # dedupe preserve order
    seen = set(); dd = []
    for x in out:
        if str(x) not in seen:
            seen.add(str(x)); dd.append(x)
    return dd


def already(ws, sw):
    for r in range(3, ws.max_row + 1):
        if ws.cell(row=r, column=1).value == sw:
            return True
    return False


def verify(p):
    with zipfile.ZipFile(p) as z:
        if len(z.namelist()) < 10 or z.testzip() is not None:
            return False
    wb = openpyxl.load_workbook(p); ok = len(wb.sheetnames) > 5; wb.close(); return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--catsides", default="STOCKS_LONG,STOCKS_SHORT")
    args = ap.parse_args()
    import v15_pilot as P
    fd = P._load_filter_dictionary()
    valof = {}
    for e in fd:
        f = (e.get("filter") or "").strip()
        if f and f not in valof:
            valof[f] = e.get("vals") or [e.get("opt")]
    m = json.loads((ROOT / "data" / "opportune_filter_map_empirical.json").read_text())
    ts = time.strftime("%Y%m%d%H%M")
    for cs in [c.strip() for c in args.catsides.split(",") if c.strip()]:
        opt = (m.get(cs) or {}).get("_orange_per_tab") or {}
        if not opt:
            print(f"{cs}: no orange_per_tab in map — skip"); continue
        p = SP / TMPL[cs]
        shutil.copy2(p, ROOT / "backups" / f"before_add_orange_{ts}_{TMPL[cs]}")
        wb = openpyxl.load_workbook(p)
        added = {}
        for tab, filters in opt.items():
            if tab not in wb.sheetnames:
                continue
            ws = wb[tab]
            for f in filters:
                if already(ws, f):
                    continue
                for v in coerce_vals(valof.get(f) or []):
                    r = ws.max_row + 1
                    a = ws.cell(row=r, column=1, value=f); a.fill = ORANGE; a.font = Font(bold=False)
                    ws.cell(row=r, column=2, value=v)
                    ws.cell(row=r, column=4, value="GENERAL")
                    added.setdefault(tab, 0); added[tab] += 1
        tmp = str(p) + ".build.xlsx"
        wb.save(tmp); wb.close()
        if verify(tmp):
            os.replace(tmp, p); print(f"OK {TMPL[cs]}: orange rows added {added}")
        else:
            print(f"VERIFY FAIL {TMPL[cs]} — original kept")


if __name__ == "__main__":
    main()
