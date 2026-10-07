#!/usr/bin/env python3
"""v15_refill_from_json — refill XLS from JSON so compute is never lost.

Source of truth is data/reports/lifecycle_pilot/{SYM}_v14_progress.json (done dict).
XLS may be empty/truncated due to OOM or earlier bug where E2 was BASELINE and F/G were None.
This script ensures XLS can be written to (valid zip) and all calculations are kept in JSON,
then fills XLS from JSON so compute is not lost.

Usage:
  python tools/v15_refill_from_json.py [--sym SYM_LONG] [--all] [--dry-run]
  --all refills every JSON with done>0 where XLS is missing/empty/truncated
  Progress dir = $V15_PROGRESS_DIR when set, else data/reports/lifecycle_pilot.
  Restores E/E2/F/G/H/yellows/K from done records; C left to the live pilot
  (C-SUPERSEDE needs chain order, keeper-phase precedent writes F/G/yellows only).
"""
import argparse
import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL"
PROGRESS_DIR = pathlib.Path(os.environ["V15_PROGRESS_DIR"]) if os.environ.get("V15_PROGRESS_DIR") else ROOT / "data" / "reports" / "lifecycle_pilot"
def _template_for(symside: str) -> pathlib.Path:
    from v15_pilot import get_template_for_symside
    return get_template_for_symside(symside)


TEMPLATE = ROOT / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM" / "TEMPLATE_CRYPTO_LONG.xlsx"

import openpyxl
from openpyxl.styles import Font, PatternFill

SWITCH_SHEETS = [
    "STDEV_SLOPE_SIZING", "ENTRY_REVERSAL_BOUNCE", "ENTRY_BREAKOUT_CHANNEL", "ENTRY_CONFIRMATION_GATES",
    "EXIT_STRUCTURAL", "EXIT_VELOCITY", "REENTRY_WINDOWED", "REENTRY_ADAPTIVE",
    "AUGMENT_TREND", "AUGMENT_RISK_SIZING", "REDUCE_PROFIT_LOCK", "REDUCE_SIGNAL_RATER", "GLOBAL_RISK_GATES",
]

def is_valid_zip(p: pathlib.Path) -> bool:
    if not p.exists() or p.stat().st_size < 50000:
        return False
    try:
        import zipfile
        z = zipfile.ZipFile(str(p))
        ok = len(z.namelist()) >= 10
        z.close()
        return ok
    except Exception:
        return False

def ensure_xls_exists(symside: str) -> pathlib.Path:
    target = OUT_DIR / f"{symside}_30d_matrix.xlsx"
    if is_valid_zip(target):
        return target
    # clone from the symside's own independent template (USER 2026-10-07: 4 templates, never generic)
    tmpl = _template_for(symside)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.load_workbook(str(tmpl))
    new_baseline = f"{symside}_BASELINE_METRICS"
    old_baseline = None
    for cand in ["TEMPLATE_BASELINE_METRICS", "ADP_LONG_BASELINE_METRICS"]:
        if cand in wb.sheetnames:
            old_baseline = cand
            break
    if old_baseline:
        ws = wb[old_baseline]
        ws.title = new_baseline
        for sheet_name in wb.sheetnames:
            ws2 = wb[sheet_name]
            for row in ws2.iter_rows():
                for c in row:
                    if isinstance(c.value, str) and old_baseline in c.value:
                        c.value = c.value.replace(old_baseline, new_baseline)
    wb.save(str(target))
    print(f"[refill] cloned {symside} -> {target.name}")
    return target

def refill_one(symside: str, dry_run: bool = False) -> int:
    prog_path = PROGRESS_DIR / f"{symside}_v14_progress.json"
    if not prog_path.exists():
        # try 30d variant
        prog_path = PROGRESS_DIR / f"{symside}_30d_progress.json"
        if not prog_path.exists():
            print(f"[skip] {symside} no progress JSON")
            return 0
    try:
        j = json.loads(prog_path.read_text())
    except Exception as e:
        print(f"[skip] {symside} JSON load fail {e}")
        return 0
    done = j.get("done", {})
    if not done:
        print(f"[skip] {symside} done==0")
        return 0
    baseline_gain = float(j.get("baseline_gain") or j.get("cumulative_gain") or 0)
    bh = float(j.get("bh") or 0)
    # ensure XLS is valid and writable
    xls_path = OUT_DIR / f"{symside}_30d_matrix.xlsx"
    if not is_valid_zip(xls_path):
        if dry_run:
            print(f"[dry] {symside} XLS missing/truncated would clone")
            return 0
        xls_path = ensure_xls_exists(symside)
    else:
        # XLS exists and is valid zip, but may have empty E2/F/G — will refill
        pass
    # open XLS for refill
    wb = openpyxl.load_workbook(str(xls_path))
    # ensure baseline metrics sheet
    bname = f"{symside}_BASELINE_METRICS"
    if bname not in wb.sheetnames:
        ws = wb.create_sheet(bname)
        ws["A1"] = "metric"; ws["B1"] = "value"
    else:
        ws = wb[bname]
    # write baseline if missing, 0, or stale (JSON is source of truth; JSON writes
    # every 15s vs XLS every 120s so JSON is always fresher-or-equal)
    try:
        cur = ws.cell(2, 2).value
        if cur is None or (isinstance(cur, (int,float)) and abs(baseline_gain)>1e-9 and (abs(float(cur))<1e-9 or abs(float(cur)-float(baseline_gain))>1e-6)):
            ws.cell(2, 1).value = "gain_pct"
            ws.cell(2, 2).value = float(baseline_gain)
            ws.cell(2, 2).font = Font(bold=True, color="006100")
            print(f"[refill] {symside} baseline {baseline_gain:.2f} (was {cur})")
    except Exception:
        pass
    # ensure E2 numeric on first sheet
    first_sheet = SWITCH_SHEETS[0]
    if first_sheet in wb.sheetnames:
        ws0 = wb[first_sheet]
        try:
            e2 = ws0.cell(2,5).value
            if e2 is None or (isinstance(e2, str) and e2.strip().upper()=="BASELINE") or (isinstance(e2, (int,float)) and abs(float(e2))<1e-9 and abs(baseline_gain)>1e-9):
                ws0.cell(2,5).value = float(baseline_gain)
                ws0.cell(2,5).font = Font(bold=True, color="006100")
                print(f"[refill] {symside} E2 {baseline_gain:.2f}")
        except Exception:
            pass
    # build header map per sheet
    header_maps = {}
    for sheet in SWITCH_SHEETS:
        if sheet not in wb.sheetnames:
            continue
        ws_s = wb[sheet]
        htc = {}
        for c in range(12, ws_s.max_column+1):
            hv = ws_s.cell(2, c).value
            if hv and isinstance(hv, str) and "=" in hv:
                htc[hv.strip()] = c
        header_maps[sheet] = htc
    refilled = 0
    for key, rec in done.items():
        try:
            # key like "STDEV_SLOPE_SIZING!3:WT_15M_BOUNCE_OPEN_ENABLED=False"
            sheet_part, rest = key.split("!", 1)
            row_part, switch_eq = rest.split(":", 1)
            r = int(row_part)
            if sheet_part not in wb.sheetnames:
                continue
            ws_s = wb[sheet_part]
            # handle stale row numbers after sort: search by switch name if needed
            cur_a = ws_s.cell(r,1).value
            cur_b = ws_s.cell(r,2).value
            exp_sw = switch_eq.split("=")[0] if "=" in switch_eq else switch_eq
            exp_val = switch_eq.split("=",1)[1] if "=" in switch_eq else ""
            cur_sw = str(cur_a).strip() if cur_a else ""
            cur_val = str(cur_b).strip() if cur_b is not None and not isinstance(cur_b,bool) else (str(cur_b) if isinstance(cur_b,bool) else "")
            if cur_sw != exp_sw or cur_val != exp_val:
                found=None
                for rr in range(3, ws_s.max_row+1):
                    a=ws_s.cell(rr,1).value
                    b=ws_s.cell(rr,2).value
                    b_str=str(b).strip() if b is not None and not isinstance(b,bool) else (str(b) if isinstance(b,bool) else "")
                    if str(a).strip()==exp_sw and b_str==exp_val:
                        found=rr
                        break
                if found:
                    r=found
                else:
                    continue
            delta = float(rec.get("delta") or 0)
            vec_gain = float(rec.get("vec_gain") or rec.get("vec",{}).get("gain_pct") or 0)
            live_gain = rec.get("live",{}).get("gain_pct") if isinstance(rec.get("live"), dict) else rec.get("live_gain")
            live_delta = float(live_gain) - float(baseline_gain) if live_gain is not None and baseline_gain else float(rec.get("live_delta") or 0)
            # F is hustle vs baseline, G is greedy vs cum, H is LIVE_DELTA
            # For refill we have delta (greedy) and vec_gain
            # Write F/G/H if not already correct
            cur_f = ws_s.cell(r,6).value
            cur_g = ws_s.cell(r,7).value
            # compute hustle
            hustle = float(vec_gain) - float(baseline_gain) if baseline_gain else float(delta)
            # only write if different
            if not isinstance(cur_f, (int,float)) or abs(float(cur_f)-hustle)>1e-6:
                ws_s.cell(r,6).value = float(hustle)
                ws_s.cell(r,6).font = Font(bold=True, color="006100")
                refilled+=1
            if not isinstance(cur_g, (int,float)) or abs(float(cur_g)-delta)>1e-6:
                ws_s.cell(r,7).value = float(delta)
                # color: green pos, red neg
                if delta>1e-9:
                    ws_s.cell(r,7).font = Font(bold=True, color="006100")
                elif delta < -1e-9:
                    ws_s.cell(r,7).fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
                refilled+=1
            # LIVE_DELTA col 8
            cur_h = ws_s.cell(r,8).value
            if live_gain is not None and (not isinstance(cur_h, (int,float)) or abs(float(cur_h)-live_delta)>1e-6):
                ws_s.cell(r,8).value = float(live_delta)
                if live_delta>1e-9:
                    ws_s.cell(r,8).font = Font(bold=True, color="006100")
                elif live_delta < -1e-9:
                    ws_s.cell(r,8).fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
                refilled+=1
            # write E (baseline) for this row: cumulative_before
            cum_before = float(rec.get("cumulative_before") or baseline_gain)
            cur_e = ws_s.cell(r,5).value
            if not isinstance(cur_e, (int,float)) or abs(float(cur_e)-cum_before)>1e-6:
                ws_s.cell(r,5).value = float(cum_before)
                refilled+=1
            # write C override string if present and positive delta
            if delta>1e-9:
                # prefer full overrides string from rec if available
                ov_str = None
                if rec.get("best_filter"):
                    ov_str = f"{switch_eq} + {rec.get('best_filter')}={rec.get('best_fval')}"
                if ov_str and ws_s.cell(r,3).value != ov_str:
                    ws_s.cell(r,3).value = ov_str
                    ws_s.cell(r,3).font = Font(bold=True, color="006100")
                    refilled+=1
            # yellows L:BI
            yellows = rec.get("yellows") or rec.get("pending_lbI") or {}
            htc = header_maps.get(sheet_part, {})
            for hdr, d in yellows.items():
                col = htc.get(hdr)
                if not col:
                    continue
                cur_y = ws_s.cell(r, col).value
                if not isinstance(cur_y, (int,float)) or abs(float(cur_y)-float(d))>1e-6:
                    ws_s.cell(r, col).value = float(d)
                    # color green pos, red neg
                    if float(d)>1e-9:
                        ws_s.cell(r, col).fill = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
                    elif float(d)<-1e-9:
                        ws_s.cell(r, col).fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
                    refilled+=1
            kf = rec.get("k_filters") or []
            if kf:
                kval = ", ".join(kf)
                if ws_s.cell(r,11).value != kval:
                    ws_s.cell(r,11).value = kval
                    refilled+=1
        except Exception as e:
            print(f"[refill-warn] {key} {e}")
            continue
    if refilled and not dry_run:
        # atomic save with validation
        tmp = str(xls_path) + ".tmp"
        wb.save(tmp)
        try:
            import zipfile, os
            z=zipfile.ZipFile(tmp)
            ok=len(z.namelist())>=10
            z.close()
            if not ok:
                raise RuntimeError("tmp zip bad")
            os.replace(tmp, str(xls_path))
            print(f"[refill] {symside} wrote {refilled} cells -> {xls_path.name} valid zip")
        except Exception as e:
            print(f"[refill-fail] {symside} {e}")
            try:
                pathlib.Path(tmp).unlink(missing_ok=True)
            except: pass
    else:
        print(f"[refill] {symside} no changes needed ({refilled})")
        wb.close()
    return refilled

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sym", default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if args.sym:
        refill_one(args.sym.strip().upper(), dry_run=args.dry_run)
    elif args.all:
        for pp in PROGRESS_DIR.glob("*_v14_progress.json"):
            sym = pp.stem.replace("_v14_progress","")
            # also check _30d
            refill_one(sym, dry_run=args.dry_run)
        for pp in PROGRESS_DIR.glob("*_30d_progress.json"):
            sym = pp.stem.replace("_30d_progress","")
            if not (PROGRESS_DIR / f"{sym}_v14_progress.json").exists():
                refill_one(sym, dry_run=args.dry_run)
    else:
        print("use --sym or --all")

if __name__ == "__main__":
    main()
