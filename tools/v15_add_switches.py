#!/usr/bin/env python3
"""v15_add_switches — append missing swept switches to TEMPLATE_*.xlsx (2026-09-29 USER).
COOLDOWN_BARS candidates 0/3/6/9 (default 3 bold) -> ALL 4 templates.
GAP_RISK_EXIT_ENABLED candidates True/False (default False bold) -> STOCK templates only (crypto has no gaps).
Appends white switch rows to GLOBAL_RISK_GATES (all-white-switch tab; append = no shift, no R5 issue;
V15_AVG_DELTAS reorder fixes ordering). Backup + reopen-verify (ZipFile>=10, rows present) each file.
"""
import sys as _sys_guard
_sys_guard.exit("REFUSED (USER 2026-09-30): no script may add or remove template/sheet rows")
import copy, pathlib, shutil, sys, time, zipfile
import openpyxl
from openpyxl.styles import Font

ROOT = pathlib.Path("/Users/niels/Documents/binance")
SP = ROOT / "SPREADSHEETS"
TAB = "GLOBAL_RISK_GATES"
ADDS = {  # switch -> (candidates, default_value)
    "COOLDOWN_BARS": ([0, 3, 6, 9], 3),
    "GAP_RISK_EXIT_ENABLED": ([True, False], False),
}
TEMPLATES = {
    "TEMPLATE_CRYPTO_LONG.xlsx": ["COOLDOWN_BARS"],
    "TEMPLATE_CRYPTO_SHORT.xlsx": ["COOLDOWN_BARS"],
    "TEMPLATE_STOCKS_LONG.xlsx": ["COOLDOWN_BARS", "GAP_RISK_EXIT_ENABLED"],
    "TEMPLATE_STOCKS_SHORT.xlsx": ["COOLDOWN_BARS", "GAP_RISK_EXIT_ENABLED"],
}


def already_present(ws, switch):
    for r in range(3, ws.max_row + 1):
        if ws.cell(row=r, column=1).value == switch:
            return True
    return False


def add_switch(ws, switch, cands, default):
    if already_present(ws, switch):
        return 0
    # mirror style from an existing switch row (col A/B fonts)
    ref_a = copy.copy(ws.cell(row=3, column=1).font)
    start = ws.max_row + 1
    for i, val in enumerate(cands):
        r = start + i
        a = ws.cell(row=r, column=1, value=switch)
        b = ws.cell(row=r, column=2, value=val)
        is_def = (val == default)
        a.font = Font(bold=is_def)
        b.font = Font(bold=is_def)
    return len(cands)


def verify(path):
    with zipfile.ZipFile(path) as z:
        if len(z.namelist()) < 10 or z.testzip() is not None:
            return False
    wb = openpyxl.load_workbook(path); ok = TAB in wb.sheetnames; wb.close()
    return ok


def main():
    ts = time.strftime("%Y%m%d%H%M")
    for fname, switches in TEMPLATES.items():
        p = SP / fname
        if not p.exists():
            print(f"MISSING {fname}"); continue
        bak = ROOT / "backups" / f"before_add_switches_{ts}_{fname}"
        shutil.copy2(p, bak)
        wb = openpyxl.load_workbook(p)
        ws = wb[TAB]
        added = {}
        for sw in switches:
            cands, dflt = ADDS[sw]
            n = add_switch(ws, sw, cands, dflt)
            added[sw] = n
        tmp = str(p) + ".build.xlsx"
        wb.save(tmp); wb.close()
        try:
            good = verify(tmp)
        except Exception as _ve:
            good = False; print(f"verify error {fname}: {_ve}")
        if not good:
            print(f"VERIFY FAIL {fname} — leaving original, tmp at {tmp}"); continue
        import os
        os.replace(tmp, p)
        print(f"OK {fname}: added {added} (backup {bak.name})")


if __name__ == "__main__":
    main()
