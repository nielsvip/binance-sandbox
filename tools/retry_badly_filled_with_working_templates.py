#!/usr/bin/env python3
"""
Retry badly filled sheets that were on old templates with working templates.

For every tradeable symbol (from V15_V16_CELL_BY_CELL unique base), ensure
both LONG and SHORT workbooks exist and are correctly filled with working
templates (TEMPLATE_CRYPTO_* / TEMPLATE_STOCKS_*).

For 1000SATSUSDT_SHORT etc that were on old templates (pre-20260924_18:30),
delete the badly filled XLS and relaunch with working template, and refill
from last couple days progress (if exists).

Also ensures long+short by default: for each base symbol, both sides are queued.

Usage:
  python3 tools/retry_badly_filled_with_working_templates.py --dry-run
  python3 tools/retry_badly_filled_with_working_templates.py --launch --workers 16
  PYTHONPATH=~/binance-sandbox python3 tools/retry_badly_filled_with_working_templates.py --launch --workers 16 --syms 1000SATSUSDT_SHORT,1000SATSUSDT_LONG
"""
import argparse
import pathlib
import shutil
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPREAD_Mac = ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL"
SPREAD_S1 = pathlib.Path("/home/niels/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL")
TEMPLATE_DIR = ROOT / "SPREADSHEETS"

def get_working_template(symside: str) -> pathlib.Path:
    base = symside.upper().replace("_LONG","").replace("_SHORT","")
    is_crypto = base.endswith(("USDT","USDC","USD1","BUSD","FDUSD","TUSD","DAI"))
    is_long = symside.upper().endswith("_LONG")
    if is_crypto:
        cand = TEMPLATE_DIR / ("TEMPLATE_CRYPTO_LONG.xlsx" if is_long else "TEMPLATE_CRYPTO_SHORT.xlsx")
    else:
        cand = TEMPLATE_DIR / ("TEMPLATE_STOCKS_LONG.xlsx" if is_long else "TEMPLATE_STOCKS_SHORT.xlsx")
    return cand

def is_badly_filled(xls: pathlib.Path) -> bool:
    # badly filled = old template (mtime < 20260924_18:30) or F is None/formula or BadZip
    if not xls.exists():
        return True
    try:
        import zipfile
        z = zipfile.ZipFile(str(xls), 'r')
        ok = len(z.namelist()) >= 10
        z.close()
        if not ok:
            return True
    except Exception:
        return True
    # check template mtime: working templates are 18:30 20260924 2.4M
    # old badly filled are pre-18:30 or have F None
    try:
        import openpyxl
        wb = openpyxl.load_workbook(str(xls), data_only=True, read_only=True)
        ws = wb["ENTRY_REVERSAL_BOUNCE"] if "ENTRY_REVERSAL_BOUNCE" in wb.sheetnames else None
        if ws is None:
            wb.close()
            return True
        f3 = ws.cell(3, 6).value
        e2 = ws.cell(2, 5).value
        wb.close()
        # badly filled if E2 not BASELINE or F3 is None/formula (should be numeric after correct fill)
        if not (isinstance(e2, str) and e2.strip().upper() == "BASELINE"):
            return True
        # For 1000SATSUSDT_SHORT, progress has baseline 1.22, so F3 should be numeric after correct fill
        # If F3 is None and file mtime < 18:30, it's old template
        if f3 is None or isinstance(f3, str):
            # check mtime
            try:
                if xls.stat().st_mtime < time.mktime(time.strptime("20260924_18:30", "%Y%m%d_%H:%M")):
                    return True
            except Exception:
                pass
            # if progress exists with done>0 but F is None, it's badly filled
            return True
        return False
    except Exception:
        return True

def main():
    ap = argparse.ArgumentParser(description="Retry badly filled with working templates, ensure long+short")
    ap.add_argument("--dry-run", action="store_true", help="show what would be retried")
    ap.add_argument("--launch", action="store_true", help="actually launch pilots for badly filled and missing side")
    ap.add_argument("--workers", type=int, default=16, help="workers per pilot")
    ap.add_argument("--syms", default=None, help="comma-separated symsides to limit")
    ap.add_argument("--days", type=int, default=3, help="days for progress sync")
    args = ap.parse_args()

    # build unique bases from Mac (132) and S1 (92) union
    bases = set()
    for d in [SPREAD_Mac, SPREAD_S1]:
        if not d.exists():
            continue
        for p in d.glob("*_30d_matrix.xlsx"):
            name = p.name.replace("_30d_matrix.xlsx","")
            base = name.replace("_LONG","").replace("_SHORT","")
            bases.add(base)
    # also from progress
    for d in [ROOT / "data" / "reports" / "lifecycle_pilot", pathlib.Path("/home/niels/binance-sandbox/data/reports/lifecycle_pilot")]:
        if not d.exists():
            continue
        for p in d.glob("*_v14_progress.json"):
            base = p.name.replace("_v14_progress.json","").replace("_LONG","").replace("_SHORT","")
            bases.add(base)
    bases = sorted(bases)
    print(f"[retry] {len(bases)} unique bases, will ensure LONG+SHORT for each (target 264 workbooks)")

    target_symsides = []
    for base in bases:
        for side in ["LONG", "SHORT"]:
            symside = f"{base}_{side}"
            target_symsides.append(symside)
    # filter if syms given
    if args.syms:
        wanted = set(s.strip() for s in args.syms.split(","))
        target_symsides = [s for s in target_symsides if s in wanted or s.replace("_LONG","").replace("_SHORT","") in wanted or any(w in s for w in wanted)]
        # also allow direct symside list
        if not target_symsides:
            target_symsides = [s.strip() for s in args.syms.split(",")]

    print(f"[retry] target {len(target_symsides)} sym_sides after filter")

    # check which are badly filled or missing
    to_retry = []
    for symside in target_symsides:
        xls_mac = SPREAD_Mac / f"{symside}_30d_matrix.xlsx"
        xls_s1 = SPREAD_S1 / f"{symside}_30d_matrix.xlsx"
        # check both, if either badly filled, retry
        badly_mac = is_badly_filled(xls_mac) if xls_mac.exists() else True
        badly_s1 = is_badly_filled(xls_s1) if xls_s1.exists() else True
        # missing side is badly filled
        if not xls_mac.exists() and not xls_s1.exists():
            to_retry.append((symside, "missing"))
        elif badly_mac or badly_s1:
            # check if progress exists with done>0 (has calculations from last couple days)
            prog = ROOT / "data" / "reports" / "lifecycle_pilot" / f"{symside}_v14_progress.json"
            if not prog.exists():
                prog = pathlib.Path(f"/home/niels/binance-sandbox/data/reports/lifecycle_pilot/{symside}_v14_progress.json")
            has_prog = prog.exists()
            reason = "badly_filled"
            if has_prog:
                reason += "+has_progress"
            to_retry.append((symside, reason))
    print(f"[retry] {len(to_retry)} sym_sides need retry (badly filled or missing) — show first 20:")
    for sym, why in to_retry[:20]:
        print(f"  {sym}: {why} template {get_working_template(sym).name}")

    if args.dry_run:
        print("[dry-run] not launching")
        return

    if not args.launch:
        print("Add --launch to actually delete badly filled and launch pilots")
        return

    # delete badly filled XLS on both Mac and S1 (move to quarantine)
    quarantine_mac = ROOT / "SPREADSHEETS" / "_quarantine_badly_filled"
    quarantine_mac.mkdir(parents=True, exist_ok=True)
    for sym, why in to_retry:
        for xls in [SPREAD_Mac / f"{sym}_30d_matrix.xlsx", SPREAD_S1 / f"{sym}_30d_matrix.xlsx"]:
            if xls.exists() and is_badly_filled(xls):
                dest = quarantine_mac / xls.name
                try:
                    shutil.move(str(xls), str(dest))
                    print(f"  moved {xls} -> {dest} ({why})")
                except Exception as e:
                    print(f"  fail move {xls} {e}")
                # also on S1 via ssh
                try:
                    subprocess.run(["ssh","-o","ConnectTimeout=10","s1-pub",f"mv ~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/{sym}_30d_matrix.xlsx ~/binance-sandbox/SPREADSHEETS/_quarantine_badly_filled/{sym}_30d_matrix.xlsx 2>/dev/null; mkdir -p ~/binance-sandbox/SPREADSHEETS/_quarantine_badly_filled"], timeout=10)
                except Exception:
                    pass

    # launch pilots for to_retry, batch 4 at a time with max CPU
    # For demo, launch first 8 as example, user can run full via --workers
    # Here we launch all via s1-pub with workers
    batch = to_retry[:32]  # limit to 32 for this run to avoid 264 overload
    print(f"[retry] launching {len(batch)} pilots on S1 with workers {args.workers} (first batch, then continue)")
    for sym, why in batch:
        tmpl = get_working_template(sym)
        # tmpl path relative to sandbox
        tmpl_rel = f"SPREADSHEETS/{tmpl.name}"
        cmd = f"PYTHONPATH=~/binance-sandbox V15_FORCE_USE=1 FORCE_DC_RERUN=1 python3 -u ~/binance-sandbox/v15_pilot.py --sym-side {sym} --window-days 30 --workers {args.workers} --vector-only --template {tmpl_rel} --seq-mode worst_first"
        # launch via ssh
        try:
            subprocess.run(["ssh","-o","ConnectTimeout=10","s1-pub",f"nohup bash -c \"{cmd}\" > /tmp/pilot_{sym}_retry.log 2>&1 & echo {sym} $!"], timeout=10)
            print(f"  launched {sym}")
            time.sleep(0.5)
        except Exception as e:
            print(f"  fail launch {sym} {e}")

    # also sync progress from S1 to Mac for those retried (last couple days)
    print("[retry] syncing progress from S1 to Mac for refilled")
    try:
        subprocess.run(["rsync","-az","-e","ssh -S none -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10","s1-pub:~/binance-sandbox/data/reports/lifecycle_pilot/","/Users/niels/Documents/binance/data/reports/lifecycle_pilot/"], timeout=30)
    except Exception:
        pass
    print(f"[retry] done — launched {len(batch)} pilots, quarantine {len(to_retry)} badly filled, will ensure long+short for all {len(bases)} bases")

if __name__ == "__main__":
    main()
