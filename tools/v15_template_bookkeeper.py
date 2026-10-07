"""THE ONE template-bookkeeping script (USER 2026-10-06).

NO other agent or script may touch TEMPLATE_* files. This script runs once
before market open (S1 cron 12:30 UTC), pulls zero-formula ledgers from the
fleet, and writes the ZERO_FORMULA_BOOK sheet into each of the 4 shared
templates (atomic save) + a standalone BOOK copy per cat_side.

Usage: python3 tools/v15_template_bookkeeper.py [--dry-run] [--templates DIR]
       [--ledger-root DIR] [--hosts h1,h2] [--timeout-s N]
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = {"CRYPTO_LONG": "TEMPLATE_CRYPTO_LONG.xlsx", "CRYPTO_SHORT": "TEMPLATE_CRYPTO_SHORT.xlsx", "STOCKS_LONG": "TEMPLATE_STOCKS_LONG.xlsx", "STOCKS_SHORT": "TEMPLATE_STOCKS_SHORT.xlsx"}
SHEET = "ZERO_FORMULA_BOOK"


def merge_ledgers(ledgers):
    rows, hdrs, syms = {}, {}, set()
    for led in ledgers:
        if not isinstance(led, dict):
            continue
        syms.add(led.get("symside") or "?")
        for k, e in (led.get("rows") or {}).items():
            if not isinstance(e, dict):
                continue
            r = rows.setdefault(k, dict(e))
            r.setdefault("syms_seen", [])
            if led.get("symside") and led.get("symside") not in r["syms_seen"]:
                r["syms_seen"].append(led.get("symside"))
        for h, rks in (led.get("cells_by_header") or {}).items():
            hdrs.setdefault(h, set()).update(rks or [])
    return {"rows": rows, "headers": {h: len(s) for h, s in hdrs.items()}, "syms": sorted(s for s in syms if s != "?"), "cell_rows": sum(len(v.get("cell_rows") or {}) for v in ledgers if isinstance(v, dict))}


def write_book_sheet(wb, cat, merged, symside_ct):
    if SHEET in wb.sheetnames:
        wb.remove(wb[SHEET])
    ws = wb.create_sheet(SHEET)
    ws.append(["ZERO-FORMULA BOOK", cat, f"{symside_ct} sym_sides booked", "SKIPPED_BAD_FORMULA = skipped, booked, NOT OBSOLETE — bad formula, fix then evidence regen lifts the skip"])
    ws.append(["KIND", "TAB", "SWITCH", "CAND", "HEADER_OR_COUNT", "POS", "N", "AVG", "STATUS", "NOTE"])
    for rk in sorted((merged.get("rows") or {})):
        e = merged["rows"][rk]
        ws.cell(row=ws.max_row + 1, column=1).value = "row"
        for c, v in enumerate([e.get("tab"), e.get("switch"), e.get("cand"), "", e.get("pos_sym"), e.get("n_sym"), e.get("avg_delta"), "SKIPPED_BAD_FORMULA", "NOT OBSOLETE — bad formula"], start=2):
            ws.cell(row=ws.max_row, column=c).value = v
    top = sorted((merged.get("headers") or {}).items(), key=lambda kv: -kv[1])[:500]
    for h, n in top:
        ws.cell(row=ws.max_row + 1, column=1).value = "header_top"
        for c, v in enumerate(["", "", "", h, 0, n, "", "SKIPPED_BAD_FORMULA", f"skipped in {n} rows"], start=2):
            ws.cell(row=ws.max_row, column=c).value = v
    return ws.max_row


def atomic_save_xlsx(wb, path, min_entries=10):
    import zipfile
    tmp = str(path) + ".booktmp"
    wb.save(tmp)
    with zipfile.ZipFile(tmp) as z:
        if len(z.namelist()) < min_entries:
            raise RuntimeError(f"refusing truncated save {path}: {len(z.namelist())} zip entries")
    os.replace(tmp, str(path))


def pull_fleet(ledger_root, hosts, timeout_s):
    pulled = 0
    for h in hosts:
        h = h.strip()
        if not h:
            continue
        dest = Path(ledger_root) / "_in" / h.replace(".", "_")
        dest.mkdir(parents=True, exist_ok=True)
        try:
            r = subprocess.run(["rsync", "-az", "-e", "ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new", f"niels@{h}:binance-sandbox/data/zero_formula_book/", str(dest) + "/"], capture_output=True, timeout=timeout_s)
            if r.returncode == 0:
                pulled += 1
            else:
                print(f"[bookkeeper] pull {h} failed rc={r.returncode} — booking local ledgers only", flush=True)
        except Exception as e:
            print(f"[bookkeeper] pull {h} error {e} — booking local ledgers only", flush=True)
    return pulled


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--templates", default=str(ROOT / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM"))
    ap.add_argument("--ledger-root", default=str(ROOT / "data" / "zero_formula_book"))
    ap.add_argument("--hosts", default=os.environ.get("V15_BOOK_HOSTS", ""))
    ap.add_argument("--timeout-s", type=int, default=300)
    a = ap.parse_args()
    try:
        import fcntl
        _lk = open("/tmp/v15_bookkeeper.lock", "w")
        fcntl.flock(_lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except Exception:
        print("[bookkeeper] lock held — another run active, exiting", flush=True)
        return 0
    hosts = [h for h in (a.hosts or "").split(",") if h.strip()]
    if hosts and not a.dry_run:
        pull_fleet(a.ledger_root, hosts, a.timeout_s)
    import openpyxl
    for cat, fname in TEMPLATES.items():
        pats = [os.path.join(a.ledger_root, cat, "*.json")]
        if not a.dry_run:
            pats.append(os.path.join(a.ledger_root, "_in", "*", cat, "*.json"))
        leds = []
        for p in pats:
            for f in glob.glob(p):
                try:
                    leds.append(json.load(open(f)))
                except Exception:
                    continue
        merged = merge_ledgers(leds)
        nrows = len(merged["rows"])
        print(f"[bookkeeper] {cat}: {len(leds)} ledgers, {nrows} row-skips, {len(merged['headers'])} condemned headers", flush=True)
        if a.dry_run:
            continue
        tpath = os.path.join(a.templates, fname)
        if not os.path.exists(tpath):
            print(f"[bookkeeper] missing {tpath} — skipped", flush=True)
            continue
        wb = openpyxl.load_workbook(tpath)
        try:
            nlines = write_book_sheet(wb, cat, merged, len(leds))
            atomic_save_xlsx(wb, tpath)
        finally:
            wb.close()
        bpath = os.path.join(a.ledger_root, f"BOOK_{cat}.xlsx")
        wb2 = openpyxl.Workbook()
        try:
            write_book_sheet(wb2, cat, merged, len(leds))
            if "Sheet" in wb2.sheetnames:
                wb2.remove(wb2["Sheet"])
            atomic_save_xlsx(wb2, bpath, min_entries=4)
        finally:
            wb2.close()
        print(f"[bookkeeper] {cat}: wrote {nlines} book lines -> {fname} + BOOK_{cat}.xlsx", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
