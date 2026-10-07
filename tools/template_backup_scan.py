#!/usr/bin/env python3
"""template_backup_scan — for each cat_side template: the white switch names that exist in any backup since 2026-09-26 but not in the base template,
with the NEWEST backup (file, tab) that carries each. -> <out>/backup_scan.json  {cs: {name: {file, tab, mtime, n_tabs}}} + extra_names.json
python tools/template_backup_scan.py --base-dir data/template_audit/<ts>/base --out data/template_audit/<ts>"""
import argparse
import collections
import glob
import json
import multiprocessing as mp
import os
import sys
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parents[1]
TABS = ['ENTRY_REVERSAL_BOUNCE', 'ENTRY_BREAKOUT_CHANNEL', 'ENTRY_CONFIRMATION_GATES', 'EXIT_STRUCTURAL', 'EXIT_VELOCITY', 'REENTRY_WINDOWED', 'REENTRY_ADAPTIVE', 'AUGMENT_TREND', 'AUGMENT_RISK_SIZING', 'REDUCE_PROFIT_LOCK', 'REDUCE_SIGNAL_RATER', 'GLOBAL_RISK_GATES']
SINCE = 1790380800  # 2026-09-26 00:00 UTC-ish


def names_of(path):
    try:
        wb = openpyxl.load_workbook(path, read_only=True)
    except Exception:
        return path, None
    out = collections.defaultdict(set)
    for t in TABS:
        if t not in wb.sheetnames:
            continue
        for row in wb[t].iter_rows(min_row=3, max_col=1):
            c = row[0]
            if c.value in (None, ""):
                continue
            fl = c.fill
            org = bool(fl is not None and fl.fill_type == "solid" and str(fl.fgColor.rgb or "").upper().endswith("FFE699"))
            if not org:
                out[str(c.value).strip()].add(t)
    return path, {k: sorted(v) for k, v in out.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-dir", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.chdir(ROOT)
    res, extra = {}, set()
    for cs in ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT"):
        _, base = names_of(str(Path(a.base_dir) / f"TEMPLATE_{cs}.xlsx"))
        basenames = set(base)
        files = [f for f in glob.glob(f"backups/*TEMPLATE*{cs}*.xlsx") if os.path.getmtime(f) >= SINCE and os.path.getsize(f) > 1_000_000 and "FILLED" not in f]
        files.sort(key=os.path.getmtime, reverse=True)   # newest first
        with mp.Pool(4) as pool:
            scans = pool.map(names_of, files)
        found = {}
        for f, nm in scans:
            if not nm:
                continue
            for n, tabs in nm.items():
                if n not in basenames and n not in found:
                    found[n] = {"file": f, "tab": tabs[0], "tabs": tabs, "mtime": os.path.getmtime(f)}
        res[cs] = found
        extra |= set(found)
        print(cs, "backups", len(files), "missing white switches", len(found), flush=True)
    out = Path(a.out)
    (out / "backup_scan.json").write_text(json.dumps(res, indent=1))
    (out / "extra_names.json").write_text(json.dumps(sorted(extra)))


if __name__ == "__main__":
    main()
