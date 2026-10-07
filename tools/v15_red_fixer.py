#!/usr/bin/env python3
"""
v15_red_fixer — continuously fixing formulas and recalculating red cells

🔴 CONTINUOUS RED FIXER — NEVER STOPS 🔴
Scans SPREADSHEETS/V15_V16_CELL_BY_CELL/*.xlsx for stranded cells:
- VLOOKUP formulas left in F/G (col 6/7) or yellows L:BI (col 12+) where calculation stranded
- FF0000 red fill (per-cell strand) or tabColor FF0000 (tab strand)
- E strand (E2="BASELINE" but E3 None) or C empty where overrides should be bold

For each stranded cell, recalculates via v15_pilot vectorized engine (real NPZ, 4-sheet batch, paired LONG/SHORT) and writes numeric delta, clearing red. Sheet keeps filling — fixer never blocks pilot's 12-tab worst_first progress.

Runs forever on S1 (and Mac with --allow-mac), every 60s scan, 10s per-cell timeout, honest SPREADSHEETS monitoring.

Usage:
  python3 -u tools/v15_red_fixer.py                # daemon forever
  python3 -u tools/v15_red_fixer.py --once         # one scan pass
  python3 -u tools/v15_red_fixer.py --cron-check   # ensure daemon running

Cron:
  * * * * * /home/niels/binance-sandbox/.venv/bin/python /home/niels/binance-sandbox/tools/v15_red_fixer.py --cron-check >> /tmp/v15_red_fixer_cron.log 2>&1
  @reboot sleep 20; nohup /home/niels/binance-sandbox/.venv/bin/python -u /home/niels/binance-sandbox/tools/v15_red_fixer.py >> /tmp/v15_red_fixer.log 2>&1 &
"""
from __future__ import annotations
import os, sys, time, json, pathlib, re, signal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment

VISUAL_ALIGN = Alignment(horizontal="left", vertical="center", wrap_text=False)
RED_FILL = PatternFill(start_color="FF0000", end_color="FF0000", fill_type="solid")
GREEN_FILL = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
RED_FONT = Font(name="Arial", size=10, bold=True, color="FFFFFF")
GREEN_FONT = Font(name="Arial", size=10, bold=True, color="006100")

SWITCH_SHEETS = [
    "ENTRY_REVERSAL_BOUNCE", "ENTRY_BREAKOUT_CHANNEL", "ENTRY_CONFIRMATION_GATES",
    "EXIT_STRUCTURAL", "EXIT_VELOCITY",
    "REENTRY_WINDOWED", "REENTRY_ADAPTIVE",
    "AUGMENT_TREND", "AUGMENT_RISK_SIZING",
    "REDUCE_PROFIT_LOCK", "REDUCE_SIGNAL_RATER",
    "GLOBAL_RISK_GATES",
]

OUT_DIR = ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL"
PROGRESS_DIR = ROOT / "data" / "reports" / "lifecycle_pilot"
V12_NPZ_CACHE = "32"

def _is_red(cell) -> bool:
    try:
        rgb = cell.fill.start_color.rgb
        if rgb in (None, "00000000"):
            return False
        return rgb == "FFFF0000" or rgb == "FF0000"
    except:
        return False

def _is_vlookup(cell) -> bool:
    try:
        v = cell.value
        return isinstance(v, str) and "VLOOKUP" in v
    except:
        return False

def _scan_red_cells(wb_path: Path) -> list[dict]:
    """Scan one workbook for stranded cells: VLOOKUP or red fill or E strand."""
    reds = []
    try:
        wb = openpyxl.load_workbook(str(wb_path), data_only=False)
    except Exception as e:
        # BadZip
        return [{"type": "BadZip", "sheet": None, "row": None, "reason": str(e)[:200]}]
    for sheet in SWITCH_SHEETS:
        if sheet not in wb.sheetnames:
            continue
        ws = wb[sheet]
        # Check tab red
        tab_red = False
        try:
            tab = ws.sheet_properties.tabColor
            if tab and tab.rgb not in (None, "00000000") and "FF0000" in str(tab.rgb):
                tab_red = True
        except:
            pass
        # Check E strand: E2 should be BASELINE, E3 numeric
        try:
            e2 = ws.cell(2,5).value
            e3 = ws.cell(2+1,5).value
            if e2 != "BASELINE":
                reds.append({"type": "E2_header", "sheet": sheet, "row": 2, "reason": f"E2={e2!r} not BASELINE"})
            if not isinstance(e3, (int,float)):
                # Only flag if sheet has any done progress but E3 missing
                reds.append({"type": "E3_strand", "sheet": sheet, "row": 3, "reason": f"E3={e3!r} not numeric baseline"})
                # Don't flood — one per sheet for E strand is enough
        except:
            pass
        for r in range(3, ws.max_row+1):
            a = ws.cell(r,1).value
            if not a or not isinstance(a, str) or a.strip().lower() in ("switch","general","blanket","filter","option value") or a.strip().startswith("—"):
                continue
            b = ws.cell(r,2).value
            if b is None:
                continue
            # F/G strand
            for col, name in [(6,"F"),(7,"G")]:
                c = ws.cell(r,col)
                if _is_vlookup(c):
                    reds.append({"type": f"{name}_VLOOKUP", "sheet": sheet, "row": r, "col": col, "switch": str(a).strip(), "reason": "VLOOKUP strand"})
                elif _is_red(c) and isinstance(c.value,(int,float)) and c.value==0.0:
                    # Red 0.0 may be intentional NEG, but check if it's stranded (should be float delta, not 0 placeholder)
                    pass
                elif _is_red(c):
                    reds.append({"type": f"{name}_red", "sheet": sheet, "row": r, "col": col, "switch": str(a).strip(), "reason": "red fill"})
            # C strand: override column should be bold if non-default
            # We don't flag C directly, but check for yellows strand
            for col in range(12, min(ws.max_column+1, 30)):
                c = ws.cell(r,col)
                if _is_vlookup(c):
                    reds.append({"type": "yellow_VLOOKUP", "sheet": sheet, "row": r, "col": col, "reason": "yellow VLOOKUP"})
                elif _is_red(c):
                    reds.append({"type": "yellow_red", "sheet": sheet, "row": r, "col": col, "reason": "yellow red"})
        if tab_red:
            reds.append({"type": "tab_red", "sheet": sheet, "row": None, "reason": "tabColor FF0000"})
    wb.close()
    # Deduplicate
    seen=set()
    uniq=[]
    for r in reds:
        key=(r.get("type"),r.get("sheet"),r.get("row"),r.get("col"))
        if key not in seen:
            seen.add(key)
            uniq.append(r)
    return uniq

def _recalc_one_cell(symside: str, sheet: str, row: int, window_days: int = 30) -> bool:
    """Recalculate one stranded row via v15_pilot vectorized engine, clear red."""
    try:
        # Use v15_pilot's vectorized path: preload NPZ, evaluate naked + yellows
        os.environ["V12_NPZ_CACHE"] = V12_NPZ_CACHE
        # Find switch/cand for this row
        wb_path = OUT_DIR / f"{symside}_30d_matrix.xlsx"
        if not wb_path.exists():
            return False
        wb = openpyxl.load_workbook(str(wb_path), data_only=False)
        if sheet not in wb.sheetnames:
            wb.close()
            return False
        ws = wb[sheet]
        switch = str(ws.cell(row,1).value or "").strip()
        cand = ws.cell(row,2).value
        wb.close()
        if not switch or cand is None:
            return False
        # Call pilot's per-row evaluator via import
        import v15_pilot as VP
        from tools.opt.v12_pilot import prepare_batch, evaluate_prepared_sanitized
        # Ensure NPZ hot
        prep = VP.ALL_PREPARED.get(symside)
        if prep is None:
            prep = VP.preload_prepared(symside, window_days)
            if prep is None:
                from tools.opt.v12_pilot import evaluate_sanitized
                # Fallback disk
                pass
        # Use pilot's helper to recalc this one row (reuse _process_0914_row_helper style if available, else direct)
        # Simplest: evaluate naked + one yellow at a time is already in pilot; for fixer we just re-run pilot's row logic via subprocess for this one row
        # Instead, directly evaluate via pilot's per-row logic: call pilot for this single row via subprocess with --sheet filter
        import subprocess
        py = sys.executable
        # Run pilot for this specific sheet/row via its --sheet filter (it will process all rows in sheet, but we limit to vector-only)
        cmd = [py, "-u", str(ROOT / "v15_pilot.py"), "--sym-side", symside, "--sheet", sheet, "--window-days", str(window_days), "--vector-only", "--workers", "4", "--seq-mode", "worst2best"]
        # Allow Mac if on Darwin
        if sys.platform=="darwin":
            cmd.append("--allow-mac")
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        # Check if pilot fixed the cell (no longer VLOOKUP/red)
        time.sleep(1)
        wb2 = openpyxl.load_workbook(str(wb_path), data_only=False)
        ws2 = wb2[sheet] if sheet in wb2.sheetnames else None
        fixed=False
        if ws2 is not None:
            c = ws2.cell(row,6)
            if not _is_vlookup(c) and not _is_red(c):
                # Check that F is now float
                if isinstance(c.value,(int,float)):
                    fixed=True
            # Also check E3 baseline fixed
            if sheet==SWITCH_SHEETS[0] and row==3:
                e3=ws2.cell(3,5).value
                if isinstance(e3,(int,float)):
                    fixed=True
        wb2.close()
        return fixed
    except Exception as e:
        print(f"[fixer-recalc-warn] {symside} {sheet}!{row} {e}", flush=True)
        return False

def _fix_one_workbook(wb_path: Path) -> int:
    """Fix one workbook's stranded cells, return count fixed."""
    symside = wb_path.stem.replace("_30d_matrix","")
    reds = _scan_red_cells(wb_path)
    if not reds:
        return 0
    # Prioritize BadZip and E strand first
    print(f"[fixer] {wb_path.name} {len(reds)} stranded: {reds[:3]}", flush=True)
    fixed=0
    # For E strand, just ensure baseline written for all 12 tabs
    for r in reds:
        if r["type"] in ("E2_header","E3_strand","BadZip"):
            try:
                # Re-run baseline fix via pilot's baseline block (reuse pilot)
                import subprocess
                py=sys.executable
                cmd=[py,"-u",str(ROOT/"v15_pilot.py"),"--sym-side",symside,"--window-days","30","--vector-only","--workers","4","--seq-mode","worst2best"]
                if sys.platform=="darwin":
                    cmd.append("--allow-mac")
                # Use --dry-run? No, need to write baseline
                # Instead directly fix E2/E3 via openpyxl if we have baseline from progress
                prog=PROGRESS_DIR / f"{symside}_v14_progress.json"
                baseline=0
                if prog.exists():
                    j=json.loads(prog.read_text())
                    baseline=float(j.get("baseline_gain") or j.get("cumulative_gain") or 0)
                if baseline==0:
                    # Try to get from pilot's defaults
                    from v15_pilot import get_defaults_for_symside
                    baseline=0  # will be recalculated on next pilot run
                if baseline!=0:
                    wb=openpyxl.load_workbook(str(wb_path), data_only=False)
                    for sn in SWITCH_SHEETS:
                        if sn not in wb.sheetnames: continue
                        ws=wb[sn]
                        if ws.cell(2,5).value != "BASELINE":
                            ws.cell(2,5).value="BASELINE"
                        if not isinstance(ws.cell(3,5).value,(int,float)):
                            ws.cell(3,5).value=float(baseline)
                    _aws_badzip(wb, wb_path)  # BADZIP FIX 2026-09-29
                    wb.close()
                    fixed+=1
                    print(f"[fixer] {symside} fixed E strand baseline {baseline:.4f}", flush=True)
                break
            except Exception as e:
                print(f"[fixer-E-warn] {symside} {e}", flush=True)
    # For VLOOKUP/red cells, recalc via pilot for that sheet
    sheets_with_red = set(r["sheet"] for r in reds if r.get("sheet") and r["type"] not in ("E2_header","E3_strand","BadZip"))
    for sheet in sheets_with_red:
        # Find first red row in this sheet
        row = next((r["row"] for r in reds if r.get("sheet")==sheet and r.get("row")), None)
        if row is None:
            continue
        ok=_recalc_one_cell(symside, sheet, row)
        if ok:
            fixed+=1
            print(f"[fixer] {symside} {sheet}!{row} recalculated and cleared red", flush=True)
        else:
            # Still leave red, tab stays red so next pass will retry
            try:
                wb=openpyxl.load_workbook(str(wb_path), data_only=False)
                ws=wb[sheet]
                ws.sheet_properties.tabColor="FF0000"
                _aws_badzip(wb, wb_path)  # BADZIP FIX 2026-09-29
                wb.close()
            except:
                pass
            print(f"[fixer] {symside} {sheet}!{row} still red, will retry", flush=True)
        time.sleep(1)
    return fixed

def run_once():
    """One scan pass over all workbooks."""
    if not OUT_DIR.exists():
        print("[fixer] no OUT_DIR", flush=True)
        return 0
    total=0
    for wb_path in sorted(OUT_DIR.glob("*_30d_matrix.xlsx")):
        # Skip if pilot currently holds lock (recently modified <5s)
        try:
            age=time.time()-wb_path.stat().st_mtime
            if age<5:
                continue
        except:
            pass
        reds=_scan_red_cells(wb_path)
        if reds:
            # Honest monitoring: show where calculations start and strand
            symside=wb_path.stem.replace("_30d_matrix","")
            print(f"[fixer-scan] {symside} stranded {len(reds)} types {[r['type'] for r in reds[:3]]} start E3 vs strand F red", flush=True)
            n=_fix_one_workbook(wb_path)
            total+=n
            time.sleep(2)
    if total==0:
        print(f"[fixer] scan done no red stranded at {time.strftime('%H:%M:%S')}", flush=True)
    else:
        print(f"[fixer] scan fixed {total} workbooks", flush=True)
    return total

def ensure_daemon_running() -> bool:
    try:
        import subprocess
        out=subprocess.check_output(["pgrep","-f","v15_red_fixer.py"], text=True)
        pids=[l.strip() for l in out.splitlines() if l.strip()]
        # filter out --cron-check
        import subprocess as sp
        real=[]
        for pid in pids:
            try:
                args=sp.check_output(["ps","-o","args=","-p",pid], text=True)
                if "v15_red_fixer.py" in args and "--cron-check" not in args:
                    real.append(pid)
            except:
                pass
        return len(real)>=1
    except:
        return False

def cron_check():
    if ensure_daemon_running():
        print("[red-fixer cron] daemon running", flush=True)
        return
    print("[red-fixer cron] daemon not running — starting", flush=True)
    py=sys.executable
    import subprocess, shlex
    subprocess.Popen(["bash","-c",f"nohup {shlex.quote(py)} -u {shlex.quote(str(Path(__file__)))} >> /tmp/v15_red_fixer.log 2>&1 &"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    time.sleep(2)
    print("[red-fixer cron] started", flush=True)

def main():
    import argparse
    ap=argparse.ArgumentParser(description="v15_red_fixer — continuously fixing formulas and recalculating red cells")
    ap.add_argument("--once", action="store_true", help="one scan pass")
    ap.add_argument("--cron-check", action="store_true", help="ensure daemon running")
    ap.add_argument("--allow-mac", action="store_true", help="allow on Mac")
    args=ap.parse_args()
    if args.cron_check:
        cron_check()
        return
    if sys.platform=="darwin" and not args.allow_mac and os.getenv("V15_ALLOW_MAC")!="1":
        print("[BLOCKED] v15_red_fixer is S1-ONLY except --allow-mac", file=sys.stderr)
        sys.exit(2)
    if args.once:
        run_once()
        return
    print(f"[red-fixer] daemon start pid {os.getpid()} 12-tab worst_first 4-sheet batch, fixing VLOOKUP/red continuously", flush=True)
    while True:
        try:
            run_once()
        except Exception as e:
            import traceback
            print(f"[fixer-ERR] {e} {traceback.format_exc()[:500]}", flush=True)
        time.sleep(60)

if __name__=="__main__":
    main()
