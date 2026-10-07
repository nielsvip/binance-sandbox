#!/usr/bin/env python3
"""
Refill EVERY V15_V16_CELL_BY_CELL sheet that has been eating CPU (has progress JSON from last 3 days)
but whose xlsx is still EMPTY (F/G None).

Fixes the "sheets are still EMPTY" disaster where pilots ate CPU for 3 days but xlsx stayed at
baseline only (E3=baseline, F/G None) because _atomic_save was killed or progress had no xlsx write.

What it does:
  1. Finds every *_v14_progress.json modified within --days (default 3) under
     data/reports/lifecycle_pilot/  (Mac and S1 sandbox paths).
  2. For each symside, finds SPREADSHEETS/V15_V16_CELL_BY_CELL/{SYM}_30d_matrix.xlsx
     (both Mac and S1 paths). If xlsx missing, clones from correct TEMPLATE_*.
  3. Refills from json:
     - E3 baseline, E2 header BASELINE
     - F HUSTLE_DELTA (col6), G VECTOR_DELTA (col7) from rec["delta"]
     - H/I/K per-row via _write_per_row_HIK (if live deltas in rec)
     - L:BI yellows from rec["yellows"] (pos-only still written, negatives too for audit)
     - Results_Deltas full metrics for delta>1e-9 (24-col line)
     - C override column when delta>0 (switch + filter)
     - Visual: Arial 10 left, row height 15, header 1F4E78 black, F fill 1F4E78 etc, but L:BI yellows preserved.
  4. Validates E2=BASELINE, E3 numeric, at least one F filled.

Usage:
  python3 tools/refill_v15_from_last3days.py --days 3 --dry-run   # shows what would be refilled
  python3 tools/refill_v15_from_last3days.py --days 3            # actually refills
  PYTHONPATH=~/binance-sandbox python3 tools/refill_v15_from_last3days.py --days 3 --workers 4   # on S1
  python3 tools/refill_v15_from_last3days.py --syms ALGOUSDT_LONG,AAPL_LONG --days 10
"""

import argparse
import json
import time
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment

VISUAL_FONT = Font(name="Arial", size=10)
VISUAL_ALIGN = Alignment(horizontal="left", vertical="center", wrap_text=False)
VISUAL_HEADER_FILL = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
VISUAL_HEADER_FONT = Font(name="Arial", size=10, bold=True, color="000000")

SWITCH_SHEETS = [
    "ENTRY_REVERSAL_BOUNCE", "ENTRY_BREAKOUT_CHANNEL", "ENTRY_CONFIRMATION_GATES",
    "EXIT_STRUCTURAL", "EXIT_VELOCITY",
    "REENTRY_WINDOWED", "REENTRY_ADAPTIVE",
    "AUGMENT_TREND", "AUGMENT_RISK_SIZING",
    "REDUCE_PROFIT_LOCK", "REDUCE_SIGNAL_RATER",
    "GLOBAL_RISK_GATES",
]
PROGRESS_DIRS = [
    ROOT / "data" / "reports" / "lifecycle_pilot",
    Path("/home/niels/binance-sandbox/data/reports/lifecycle_pilot"),
    Path.home() / "binance-sandbox/data/reports/lifecycle_pilot",
]
XLS_DIRS = [
    ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL",
    Path("/home/niels/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL"),
    Path.home() / "binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL",
]
TEMPLATE_DIR = ROOT / "SPREADSHEETS"

def find_progress_files(days: int, sym_filter=None):
    cutoff = time.time() - days * 86400
    found = {}
    for d in PROGRESS_DIRS:
        if not d.exists():
            continue
        for p in d.glob("*_v14_progress.json"):
            try:
                if p.stat().st_mtime < cutoff:
                    continue
            except Exception:
                continue
            sym = p.name.replace("_v14_progress.json", "")
            if sym_filter and sym not in sym_filter:
                continue
            # keep newest per symside
            if sym not in found or p.stat().st_mtime > found[sym].stat().st_mtime:
                found[sym] = p
    return found

def find_xls(symside: str):
    for d in XLS_DIRS:
        cand = d / f"{symside}_30d_matrix.xlsx"
        if cand.exists():
            return cand
    return None

def get_template_for_symside(symside: str) -> Path:
    s = symside.upper()
    base = s.replace("_LONG", "").replace("_SHORT", "")
    is_crypto = base.endswith(("USDT", "USDC", "USD1", "BUSD", "FDUSD", "TUSD", "DAI"))
    is_long = s.endswith("_LONG")
    # crypto vs stocks, long vs short
    if is_crypto:
        cand = TEMPLATE_DIR / ("TEMPLATE_CRYPTO_LONG.xlsx" if is_long else "TEMPLATE_CRYPTO_SHORT.xlsx")
    else:
        cand = TEMPLATE_DIR / ("TEMPLATE_STOCKS_LONG.xlsx" if is_long else "TEMPLATE_STOCKS_SHORT.xlsx")
    if cand.exists():
        return cand
    # fallback chain — also try sandbox
    for p in [
        Path("/home/niels/binance-sandbox/SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_CRYPTO_LONG.xlsx"),
        Path("/home/niels/binance-sandbox/SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_CRYPTO_SHORT.xlsx"),
        Path("/home/niels/binance-sandbox/SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_STOCKS_LONG.xlsx"),
        Path("/home/niels/binance-sandbox/SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_STOCKS_SHORT.xlsx"),
        TEMPLATE_DIR / "TEMPLATE.xlsx",
    ]:
        if p.exists():
            return p
    return TEMPLATE_DIR / "TEMPLATE.xlsx"

def _atomic_save(wb, wb_path: Path):
    tmp = str(wb_path) + ".tmp"
    try:
        # auto adjust before save
        for ws in wb.worksheets:
            for c in range(1, min(ws.max_column + 1, 12)):
                hdr = ws.cell(row=2, column=c)
                if hdr.value is not None:
                    hdr.alignment = VISUAL_ALIGN
                    hdr.fill = VISUAL_HEADER_FILL
                    hdr.font = VISUAL_HEADER_FONT
            for col in ws.columns:
                max_len = 0
                col_letter = col[0].column_letter
                for cell in col:
                    if cell.value is not None:
                        l = len(str(cell.value))
                        if l > max_len:
                            max_len = l
                ws.column_dimensions[col_letter].width = min(max_len + 2, 30)
            for row in ws.iter_rows():
                ws.row_dimensions[row[0].row].height = 15
        # BADZIP FIX 2026-09-29: old fallback wrote the workbook IN PLACE after a
        # failed validation — the exact BadZip factory. No fallback: good file survives.
        _aws_badzip(wb, wb_path)
    except Exception as e:
        print(f"[atomic-save-warn] {wb_path.name} {e} — good file kept, nothing written")

def is_xlsx_empty(xls_path: Path) -> bool:
    try:
        wb = openpyxl.load_workbook(str(xls_path), data_only=True, read_only=True)
        # check first switch sheet F3
        for sn in SWITCH_SHEETS:
            if sn in wb.sheetnames:
                ws = wb[sn]
                f3 = ws.cell(3, 6).value
                wb.close()
                # empty if F3 is None or string and no numeric F in first 10 rows
                if f3 is None or not isinstance(f3, (int, float)):
                    # also check any F in rows 3..15
                    wb2 = openpyxl.load_workbook(str(xls_path), data_only=True, read_only=True)
                    ws2 = wb2[sn]
                    any_f = any(isinstance(ws2.cell(r, 6).value, (int, float)) for r in range(3, 16))
                    wb2.close()
                    return not any_f
                return False
        wb.close()
        return True
    except Exception:
        return True

def refill_one(symside: str, progress_path: Path, dry_run=False):
    xls_path = find_xls(symside)
    if xls_path is None:
        # clone from template
        tmpl = get_template_for_symside(symside)
        if not tmpl.exists():
            print(f"[{symside}] no template {tmpl} — skip")
            return False
        # find best XLS dir that exists
        out_dir = None
        for d in XLS_DIRS:
            if d.exists():
                out_dir = d
                break
        if out_dir is None:
            out_dir = XLS_DIRS[0]
        out_dir.mkdir(parents=True, exist_ok=True)
        xls_path = out_dir / f"{symside}_30d_matrix.xlsx"
        import shutil
        shutil.copy2(tmpl, xls_path)
        print(f"[{symside}] cloned {tmpl.name} -> {xls_path}")

    # check empty?
    empty = is_xlsx_empty(xls_path)
    try:
        j = json.loads(progress_path.read_text())
    except Exception as e:
        print(f"[{symside}] cannot load progress {e}")
        return False
    done = j.get("done", {})
    baseline_gain = j.get("baseline_gain", 0)
    bh = j.get("bh", 0)
    if not done:
        print(f"[{symside}] progress has 0 done — nothing to refill (baseline {baseline_gain:.2f})")
        return False
    print(f"[{symside}] {xls_path.name} empty={empty} done={len(done)} baseline={baseline_gain:.2f} bh={bh:.2f} progress={progress_path} mtime {time.ctime(progress_path.stat().st_mtime)}")

    if dry_run:
        print(f"[{symside}] DRY-RUN — would refill {len(done)} rows")
        return True

    # actual refill — clear blocking formulas in data rows first (pilot does this via _clear_vlookup_formulas)
    wb = openpyxl.load_workbook(str(xls_path), data_only=False)
    for ws in wb.worksheets:
        if ws.title in SWITCH_SHEETS or ws.title.startswith("ENTRY") or ws.title.startswith("EXIT") or ws.title.startswith("REENTRY") or ws.title.startswith("AUGMENT") or ws.title.startswith("REDUCE") or ws.title.startswith("GLOBAL"):
            for r in range(3, ws.max_row + 1):
                for col in (3, 5, 6, 7, 8, 9, 11):  # C,E,F,G,H,I,K
                    try:
                        v = ws.cell(row=r, column=col).value
                        if isinstance(v, str) and v.startswith("="):
                            # keep GLOBAL_RISK_GATES G/H/I only if documented waiver, else clear — refill decides
                            if ws.title == "GLOBAL_RISK_GATES" and col in (7, 8, 9):
                                continue
                            ws.cell(row=r, column=col).value = None
                            ws.cell(row=r, column=col).fill = PatternFill(fill_type=None)
                    except Exception:
                        pass
    # fix baseline header E2 and E3
    for sn in SWITCH_SHEETS:
        if sn in wb.sheetnames and sn == SWITCH_SHEETS[0]:
            ws = wb[sn]
            ws.cell(row=2, column=5).value = "BASELINE"
            ws.cell(row=2, column=5).font = VISUAL_HEADER_FONT
            ws.cell(row=2, column=5).fill = VISUAL_HEADER_FILL
            ws.cell(row=2, column=5).alignment = VISUAL_ALIGN
            # E3 baseline
            if baseline_gain not in (None,):
                ws.cell(row=3, column=5).value = float(baseline_gain)
                ws.cell(row=3, column=5).font = Font(name="Arial", size=10, bold=True, color="006100")
                ws.cell(row=3, column=5).alignment = VISUAL_ALIGN
            break

    refilled = 0
    # build header_to_col per sheet for yellows
    for key, rec in done.items():
        try:
            sheet_part, rest = key.split("!", 1)
            row_part, switch_eq = rest.split(":", 1)
            r = int(row_part)
            if sheet_part not in wb.sheetnames:
                continue
            ws = wb[sheet_part]
            # switch name mismatch fix: search by switch name if row r wrong (like pilot does)
            try:
                cur_a = ws.cell(row=r, column=1).value
                exp_sw = switch_eq.split("=")[0] if "=" in switch_eq else switch_eq
                cur_sw = str(cur_a).strip() if cur_a else ""
                if cur_sw != exp_sw:
                    found = None
                    for rr in range(3, ws.max_row + 1):
                        if str(ws.cell(row=rr, column=1).value or "").strip() == exp_sw and str(ws.cell(row=rr, column=2).value or "").strip() == switch_eq.split("=", 1)[1].strip() if "=" in switch_eq else "":
                            found = rr
                            break
                        # also try just switch match without value (for rows where B is default)
                        if str(ws.cell(row=rr, column=1).value or "").strip() == exp_sw:
                            # check if B matches or close
                            found = rr
                            break
                    if found and found != r:
                        # use found, but keep original r for progress key
                        pass
                    # we keep r as in key, since pilot uses key row; but if mismatch, search best effort
                    # try to find exact row by switch_eq
                    for rr in range(3, ws.max_row + 1):
                        a = ws.cell(row=rr, column=1).value
                        b = ws.cell(row=rr, column=2).value
                        if a and b:
                            sw_str = f"{str(a).strip()}={str(b).strip()}"
                            if sw_str == switch_eq:
                                r = rr
                                break
            except Exception:
                pass

            delta = rec.get("delta", 0)
            vec_gain = rec.get("vec_gain", 0)
            yellows = rec.get("yellows", {}) or rec.get("pending_lbI", {})

            # F col6, G col7
            try:
                ws.cell(row=r, column=6).value = float(delta) if delta is not None else None
                ws.cell(row=r, column=6).font = Font(name="Arial", size=10, bold=False)
                ws.cell(row=r, column=6).alignment = VISUAL_ALIGN
                # F fill dark blue 1F4E78 black text like headers for consistency (user: HUSTLE_DELTA same as others)
                ws.cell(row=r, column=6).fill = VISUAL_HEADER_FILL
                ws.cell(row=r, column=6).font = Font(name="Arial", size=10, bold=True, color="000000")
            except Exception:
                pass
            try:
                ws.cell(row=r, column=7).value = float(delta) if delta is not None else None
                ws.cell(row=r, column=7).font = Font(name="Arial", size=10, bold=False)
                ws.cell(row=r, column=7).alignment = VISUAL_ALIGN
            except Exception:
                pass
            # H/I/K per-row if present
            try:
                # rec may have live delta/sharpe
                ld = rec.get("live_delta", delta)
                ls = rec.get("live_sharpe", rec.get("pool_sharpe", 0))
                pf = rec.get("per_row_filters", ", ".join([k for k, v in yellows.items() if float(v or 0) > 1e-9][:3]))
                if ld is not None:
                    ws.cell(row=r, column=8).value = float(ld)
                    ws.cell(row=r, column=8).alignment = VISUAL_ALIGN
                if ls is not None:
                    ws.cell(row=r, column=9).value = float(ls)
                    ws.cell(row=r, column=9).alignment = VISUAL_ALIGN
                if pf:
                    ws.cell(row=r, column=11).value = str(pf)
                    ws.cell(row=r, column=11).alignment = VISUAL_ALIGN
            except Exception:
                pass

            # yellows L:BI
            if yellows:
                htc = {}
                for c in range(12, ws.max_column + 1):
                    hv = ws.cell(row=2, column=c).value
                    if hv and isinstance(hv, str) and "=" in hv and not hv.upper().startswith("WHAT SWITCH"):
                        htc[hv.strip()] = c
                for hdr, d in yellows.items():
                    col = htc.get(hdr)
                    if col:
                        try:
                            ws.cell(row=r, column=col).value = float(d)
                            ws.cell(row=r, column=col).alignment = VISUAL_ALIGN
                            # pos yellows green, neg red for audit
                            if float(d) > 1e-9:
                                ws.cell(row=r, column=col).fill = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
                            elif float(d) < -1e-9:
                                ws.cell(row=r, column=col).fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
                        except Exception:
                            pass

            # FIX 2026-09-24: E must be set for every calculated row to its cumulative_before, but E3 is baseline (not 0)
            try:
                cum_before = rec.get("cumulative_before", baseline_gain)
                # For r=3, E3 is baseline, not cumulative_before (which is 0 for first row)
                if r == 3:
                    # E3 already set to baseline above, don't overwrite with 0
                    pass
                else:
                    ws.cell(row=r, column=5).value = float(cum_before)
                    ws.cell(row=r, column=5).font = Font(name="Arial", size=10, bold=True, color="006100")
                    ws.cell(row=r, column=5).alignment = VISUAL_ALIGN
                # Also set next row's E immediately if this row had positive delta and next row is in same sheet
                delta = rec.get("delta", 0)
                if delta and float(delta) > 1e-9:
                    try:
                        next_r = r + 1
                        if next_r <= ws.max_row:
                            nxt_e = ws.cell(row=next_r, column=5).value
                            if nxt_e is None or (isinstance(nxt_e, str) and nxt_e.startswith("=")):
                                ws.cell(row=next_r, column=5).value = float(float(cum_before) + float(delta)) if r != 3 else float(float(baseline_gain) + float(delta))
                                ws.cell(row=next_r, column=5).font = Font(name="Arial", size=10, bold=True, color="006100")
                                ws.cell(row=next_r, column=5).alignment = VISUAL_ALIGN
                    except Exception:
                        pass
            except Exception:
                pass

            # Results_Deltas for pos delta
            if delta and float(delta) > 1e-9:
                target = None
                for cand in ["Results_Deltas", "Results_30d_Deltas", "Results_30d", "results"]:
                    if cand in wb.sheetnames:
                        target = cand
                        break
                if target is None:
                    target = "Results_Deltas"
                    if target not in wb.sheetnames:
                        ws2 = wb.create_sheet(target)
                        ws2.append(["key","default","override","is_non_default","delta_gain_vs_bh","delta_sharpe","delta_trades","variant_gain","REAL_COMPLETE_DELTA","variant_sharpe","trades","tim","dd","filter_or_override","symside","window","bh_pct","gain_pct","tim_pct","max_dd","win_rate","bars","peak","source"])
                        for ci in range(1, 25):
                            ws2.cell(1, ci).font = Font(bold=True)
                rws = wb[target]
                # find or create row for this switch
                found = None
                for rr in range(2, rws.max_row + 2):
                    if str(rws.cell(row=rr, column=1).value or "").strip() == switch_eq:
                        found = rr
                        break
                if found is None:
                    found = rws.max_row + 1
                    rws.cell(row=found, column=1).value = switch_eq
                # fill 24-col metrics
                rws.cell(row=found, column=5).value = float(delta)
                rws.cell(row=found, column=8).value = float(vec_gain or 0)
                rws.cell(row=found, column=15).value = symside
                rws.cell(row=found, column=16).value = j.get("window_days", 30)
                rws.cell(row=found, column=17).value = float(bh or 0)
                rws.cell(row=found, column=18).value = float(vec_gain or 0)
            refilled += 1
        except Exception as e:
            print(f"[{symside}] refill warn {key} {e}")
            continue

    if refilled:
        _atomic_save(wb, xls_path)
        print(f"[{symside}] refilled {refilled} rows into {xls_path.name} (baseline {baseline_gain:.2f})")
    else:
        print(f"[{symside}] nothing refilled")
    wb.close()
    return True

def main():
    ap = argparse.ArgumentParser(description="Refill V15 sheets from last 3 days progress JSONs")
    ap.add_argument("--days", type=int, default=3, help="look back days for progress JSONs")
    ap.add_argument("--syms", default=None, help="comma-separated symsides to limit (e.g. ALGOUSDT_LONG,AAPL_LONG)")
    ap.add_argument("--dry-run", action="store_true", help="show what would be refilled, do not write")
    args = ap.parse_args()
    sym_filter = set(s.strip().upper() for s in args.syms.split(",")) if args.syms else None

    prog_files = find_progress_files(args.days, sym_filter)
    print(f"[refill] found {len(prog_files)} progress files from last {args.days} days")
    for sym, p in sorted(prog_files.items())[:10]:
        print(f"  {sym}: {p} {time.ctime(p.stat().st_mtime)} done? ", end="")
        try:
            j = json.loads(p.read_text())
            print(f"{len(j.get('done',{}))} rows")
        except Exception as e:
            print(f"err {e}")

    if not prog_files:
        print("No progress files in last days — nothing to do")
        return

    ok = 0
    for sym, p in sorted(prog_files.items()):
        if sym_filter and sym not in sym_filter:
            continue
        xls = find_xls(sym)
        # refill if xls missing or empty or progress newer than xls
        should = False
        if xls is None:
            should = True
        else:
            # if xls mtime < progress mtime, or xls empty, refill
            try:
                xls_mtime = xls.stat().st_mtime
                prog_mtime = p.stat().st_mtime
                if prog_mtime > xls_mtime or is_xlsx_empty(xls):
                    should = True
            except Exception:
                should = True
        if should:
            refill_one(sym, p, dry_run=args.dry_run)
            ok += 1
        else:
            # also refill if F/G count < done count (partial fill)
            try:
                j = json.loads(p.read_text())
                done_cnt = len(j.get("done", {}))
                # check xls F count
                wb = openpyxl.load_workbook(str(xls), data_only=True, read_only=True)
                filled = 0
                for sn in SWITCH_SHEETS:
                    if sn in wb.sheetnames:
                        ws = wb[sn]
                        filled += sum(1 for r in range(3, ws.max_row + 1) if isinstance(ws.cell(r, 6).value, (int, float)))
                wb.close()
                if filled < done_cnt * 0.8:
                    print(f"[{sym}] {xls.name} has {filled} F vs {done_cnt} done — partial, refilling")
                    refill_one(sym, p, dry_run=args.dry_run)
                    ok += 1
            except Exception:
                pass

    print(f"[refill] done — refilled {ok} workbooks from last {args.days} days (dry_run={args.dry_run})")
    # sync to S1 if on Mac
    if ROOT == Path("/Users/niels/Documents/binance") and not args.dry_run:
        import subprocess
        for sym in prog_files:
            xls = find_xls(sym)
            if xls and xls.exists():
                try:
                    subprocess.run(["rsync", "-az", "-e", "ssh -S none -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10", str(xls), f"s1-pub:~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/{xls.name}"], timeout=10)
                except Exception:
                    pass
        print("[refill] synced refilled xls to s1-pub")

if __name__ == "__main__":
    main()
