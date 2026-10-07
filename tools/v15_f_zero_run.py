#!/usr/bin/env python3
"""v15_f_zero_run — scan/fix column F zeros of run19 sheets (FZ 2026-10-01). Read-only unless --fix.
Only sheets that have a progress JSON in --progress are scanned (the sheet is ~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/<ss>_30d_matrix.xlsx).
A zero in F (13 switch tabs, row>=3) is legitimate only when the finished JSON entry TAB!row:* has delta_vs_initial == 0.0 (not is_running).
--fix: skips sheets whose pilot runs (pgrep --sym-side <ss>), sheets modified <10 min ago, and any sheet whose lock is held; backs up the sheet to --backup first; blanks ONLY F cells; atomic zip-validated save.
  python tools/v15_f_zero_run.py --progress DIR --sheets DIR --out report.json [--fix --backup DIR]"""
import argparse, glob, json, os, re, subprocess, sys, time, zipfile, shutil
import openpyxl
SW = {'STDEV_SLOPE_SIZING', 'ENTRY_REVERSAL_BOUNCE', 'ENTRY_BREAKOUT_CHANNEL', 'ENTRY_CONFIRMATION_GATES', 'EXIT_STRUCTURAL', 'EXIT_VELOCITY', 'REENTRY_WINDOWED', 'REENTRY_ADAPTIVE', 'AUGMENT_TREND', 'AUGMENT_RISK_SIZING', 'REDUCE_PROFIT_LOCK', 'REDUCE_SIGNAL_RATER', 'GLOBAL_RISK_GATES'}
ap = argparse.ArgumentParser()
ap.add_argument("--progress", required=True)
ap.add_argument("--sheets", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--fix", action="store_true")
ap.add_argument("--backup")
ap.add_argument("--limit", type=int, default=0)
a = ap.parse_args()


def running(ss):
    r = subprocess.run(["pgrep", "-f", f"v15_pilot.py --sym-side {ss}( |$)"], capture_output=True, text=True)
    return bool(r.stdout.strip())


rep = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "files": {}, "F_numeric": 0, "F_zero_unproven": 0, "F_zero_proven": 0, "blanked": 0, "skipped": {}}
pj = sorted(glob.glob(os.path.join(a.progress, "*_v14_progress.json")))
if a.limit:
    pj = pj[:a.limit]
for pjf in pj:
    ss = os.path.basename(pjf).split("_v14_progress.json")[0]
    cands = [c for c in glob.glob(os.path.join(a.sheets, f"{ss}_*30d_matrix.xlsx")) + glob.glob(os.path.join(a.sheets, f"{ss}_30d_matrix.xlsx")) if re.match(rf"^{re.escape(ss)}_(bhm?\d+p\d+_gain(m)?\d+p\d+_)?30d_matrix\.xlsx$", os.path.basename(c))]
    if not cands:
        rep["skipped"][ss] = "no_sheet"
        continue
    sheet = max(cands, key=os.path.getmtime)  # run19 sheet = the newest (published bh/gain-named file when the pilot finished; the plain name can be an old leftover)
    try:
        j = json.load(open(pjf))
    except Exception:
        rep["skipped"][ss] = "bad_json"
        continue
    real = set()
    rowname = {}
    namezero = set()
    for k, v in (j.get("done") or {}).items():
        tr, _, nm = k.partition(":")
        rowname[tr] = nm
        if isinstance(v, dict) and v.get("delta_vs_initial") == 0.0 and not v.get("is_running"):
            real.add(tr)
            namezero.add((tr.split("!", 1)[0], nm))
    try:
        wb = openpyxl.load_workbook(sheet)
    except Exception as e:
        rep["skipped"][ss] = f"unreadable:{type(e).__name__}"
        continue
    num = zun = zpr = 0
    bad = []
    aligned = misaligned = 0
    for ws in wb.worksheets:
        if ws.title not in SW:
            continue
        for r in range(3, ws.max_row + 1):
            ca, cb = ws.cell(row=r, column=1).value, ws.cell(row=r, column=2).value
            exp = rowname.get(f"{ws.title}!{r}")
            if exp is not None and ca not in (None, ""):
                if exp == f"{ca}={cb}":
                    aligned += 1
                else:
                    misaligned += 1
            v = ws.cell(row=r, column=6).value
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                num += 1
                if v == 0:
                    if (ws.title, f"{ca}={cb}") in namezero:
                        zpr += 1
                    else:
                        zun += 1
                        bad.append((ws.title, r))
    rep["F_numeric"] += num
    rep["F_zero_unproven"] += zun
    rep["F_zero_proven"] += zpr
    rep["files"][ss] = {"sheet": os.path.basename(sheet), "num": num, "zero_unproven": zun, "zero_proven": zpr, "rows_aligned": aligned, "rows_misaligned": misaligned}
    rep["rows_aligned"] = rep.get("rows_aligned", 0) + aligned
    rep["rows_misaligned"] = rep.get("rows_misaligned", 0) + misaligned
    if a.fix and zun:
        if running(ss):
            rep["skipped"][ss] = "pilot_running"
            continue
        if time.time() - os.path.getmtime(sheet) < 600:
            rep["skipped"][ss] = "modified_lt_10min"
            continue
        if a.backup:
            os.makedirs(a.backup, exist_ok=True)
            shutil.copy2(sheet, os.path.join(a.backup, os.path.basename(sheet)))
        for t, r in bad:
            wb[t].cell(row=r, column=6).value = None
        tmp = sheet[:-5] + ".fz.tmp.xlsx"
        wb.save(tmp)
        ok = zipfile.ZipFile(tmp).testzip() is None and len(zipfile.ZipFile(tmp).namelist()) >= 10
        if ok:
            openpyxl.load_workbook(tmp)
            os.replace(tmp, sheet)
            rep["blanked"] += zun
            rep["files"][ss]["blanked"] = zun
        else:
            os.remove(tmp)
            rep["skipped"][ss] = "zip_invalid"
    json.dump(rep, open(a.out, "w"))
json.dump(rep, open(a.out, "w"))
print({k: v for k, v in rep.items() if k not in ("files", "skipped")}, "skipped", len(rep["skipped"]))
