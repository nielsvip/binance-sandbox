#!/usr/bin/env python3
"""Extend an EXISTING switch's candidate rows in the live TEMPLATE_FINAL_NORM workbooks.

tools/v15_switch_add.py deliberately adds only NEW switches and refuses duplicates, so it cannot
give an existing switch more options -- it also refuses on a config-default mismatch. That is the gap
this fills, for the operator's 2026-10-27 instruction:
  "Do all template work, leave any ema blanket options greyed out so it just applies the current
   settings that wwere tested before. KINDERGARTEN_STRICT_TFS should have 5 on both sides
   EMA_9_21_FILTER_MIN_TFS should have 4 on both sides. Take out HTF gatee where an int is expected"

Why an existing switch can need this: a switch with ONE candidate row cannot produce a non-zero
delta, because that row IS the running configuration (RUNNING_IDENTITY). If such a switch is also an
ESSENTIAL_GATE_SWITCHES member, the ESSENTIAL-ROWS gate refuses every board on that venue forever --
which is exactly what happened to WT_15M_BOUNCE_FILTER_MODE/VOLUME_MODE after rows were deleted on
2026-10-25.

What it does, and nothing else:
  * inserts whole rows directly after that switch's LAST existing row in the same tab, so the new
    rows sit inside the switch's own block and inherit its neighbourhood;
  * copies style cell-by-cell from the switch's own non-default row (font/fill/border/alignment/
    number_format), then writes col-A = switch and col-B = the new candidate, non-bold;
  * never touches the bold/default row, never changes an existing value, never changes a font colour
    (so the operator's grey decisions are preserved exactly), never adds a switch that is absent;
  * refuses if the switch is missing, if a candidate already exists, or if the target tab is not a
    SWITCH_SHEETS tab;
  * backs each workbook up, writes to a temp file, zip-validates it (>=10 entries, testzip None) and
    only then replaces the original -- the BadZip/truncation guard that a 225 KB truncated workbook
    taught this repo.

--drop-value removes rows whose col-B equals a given literal (used for the "MTF Gate" label rows on
EMA_9_21_FILTER_MIN_TFS, where an int is expected). It refuses to drop a bold/default row.

Dry-run by default; --apply writes. Prints a per-workbook before/after candidate list so the diff is
auditable, and verifies afterwards that no OTHER switch's row count changed.
"""
import argparse
import collections
import datetime
import pathlib
import shutil
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SWITCH_SHEETS = [
    "STDEV_SLOPE_SIZING", "ENTRY_REVERSAL_BOUNCE", "ENTRY_BREAKOUT_CHANNEL", "ENTRY_CONFIRMATION_GATES",
    "EXIT_STRUCTURAL", "EXIT_VELOCITY", "REENTRY_WINDOWED", "REENTRY_ADAPTIVE", "AUGMENT_TREND",
    "AUGMENT_RISK_SIZING", "REDUCE_PROFIT_LOCK", "REDUCE_SIGNAL_RATER", "GLOBAL_RISK_GATES",
]


def _copy_style(src, dst):
    from copy import copy
    dst.font = copy(src.font)
    dst.fill = copy(src.fill)
    dst.border = copy(src.border)
    dst.alignment = copy(src.alignment)
    dst.number_format = src.number_format
    dst.protection = copy(src.protection)


def _rows_of(ws, switch):
    return [rr for rr in range(3, ws.max_row + 1) if str(ws.cell(row=rr, column=1).value or "").strip() == switch]


def census(wb):
    """{(sheet, switch): n_rows} over the sweepable tabs -- used to prove nothing else moved."""
    out = collections.Counter()
    for sheet in wb.sheetnames:
        if sheet not in SWITCH_SHEETS:
            continue
        ws = wb[sheet]
        for rr in range(3, ws.max_row + 1):
            sw = str(ws.cell(row=rr, column=1).value or "").strip()
            if sw:
                out[(sheet, sw)] += 1
    return out


def process(path, switch, want, drop_value, apply_, log=print):
    import openpyxl
    wb = openpyxl.load_workbook(str(path))
    before = census(wb)
    report = {"template": path.name, "added": [], "dropped": [], "skipped": [], "error": None}
    try:
        targets = [sh for sh in wb.sheetnames if sh in SWITCH_SHEETS and _rows_of(wb[sh], switch)]
        if not targets and not drop_value:
            report["error"] = "switch %s not present in any SWITCH_SHEETS tab" % switch
            return report, before, before
        # --- drops (label rows where a real value is expected); allowed in ANY tab, incl. dictionaries
        if drop_value is not None:
            for sheet in wb.sheetnames:
                ws = wb[sheet]
                for rr in sorted(_rows_of(ws, switch), reverse=True):
                    bval = ws.cell(row=rr, column=2).value
                    if str(bval).strip() != str(drop_value).strip():
                        continue
                    if ws.cell(row=rr, column=2).font and ws.cell(row=rr, column=2).font.bold:
                        report["skipped"].append("%s!%d bold/default row, refused" % (sheet, rr))
                        continue
                    if apply_:
                        ws.delete_rows(rr, 1)
                    report["dropped"].append("%s!%d:%r" % (sheet, rr, bval))
        # --- additions
        for sheet in targets:
            ws = wb[sheet]
            rows = _rows_of(ws, switch)
            existing = [ws.cell(row=rr, column=2).value for rr in rows]
            existing_s = {str(v).strip() for v in existing}
            style_src = None
            for rr in rows:
                if not (ws.cell(row=rr, column=2).font and ws.cell(row=rr, column=2).font.bold):
                    style_src = rr
                    break
            style_src = style_src if style_src is not None else rows[0]
            anchor = max(rows)
            to_add = [c for c in want if str(c).strip() not in existing_s]
            log("   %-26s %-34s existing=%s -> add %s" % (sheet, switch, existing, to_add or "nothing"))
            if not to_add:
                continue
            if apply_:
                src_cells = [ws.cell(row=style_src, column=c) for c in range(1, ws.max_column + 1)]
                ws.insert_rows(anchor + 1, len(to_add))
                for i, cand in enumerate(to_add):
                    r = anchor + 1 + i
                    for c in range(1, ws.max_column + 1):
                        _copy_style(src_cells[c - 1], ws.cell(row=r, column=c))
                    ws.cell(row=r, column=1).value = switch
                    ws.cell(row=r, column=2).value = cand
                    f = ws.cell(row=r, column=2).font
                    if f is not None and f.bold:
                        from copy import copy as _c
                        nf = _c(f)
                        nf.bold = False
                        ws.cell(row=r, column=2).font = nf
            report["added"] += ["%s:%r" % (sheet, c) for c in to_add]
        after = census(wb)
        if apply_ and (report["added"] or report["dropped"]):
            stamp = datetime.datetime.now().strftime("%Y%m%d%H%M")
            backup = ROOT / "backups" / ("before_extend_%s_%s_%s" % (switch, stamp, path.name))
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, backup)
            tmp = path.with_suffix(".xlsx.extend_tmp")
            wb.save(str(tmp))
            with zipfile.ZipFile(tmp) as zf:
                n, bad = len(zf.namelist()), zf.testzip()
            if n < 10 or bad is not None:
                tmp.unlink(missing_ok=True)
                report["error"] = "rewritten zip invalid (entries=%d bad=%s) - original untouched" % (n, bad)
                return report, before, after
            tmp.replace(path)
            report["backup"] = backup.name
            report["zip_entries"] = n
        return report, before, after
    finally:
        wb.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--switch", required=True)
    ap.add_argument("--candidates", default="", help="comma list to ensure exist (blank = drops only)")
    ap.add_argument("--drop-value", default=None, help="remove rows whose col-B equals this literal")
    ap.add_argument("--templates", default=str(ROOT / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM"))
    ap.add_argument("--only", default="", help="comma list of template basenames to limit to")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    want = [c.strip() for c in a.candidates.split(",") if c.strip()]
    # keep ints as ints so the sheet carries a number, not "2"
    want = [int(c) if c.lstrip("-").isdigit() else c for c in want]
    only = {x.strip() for x in a.only.split(",") if x.strip()}
    print("switch=%s candidates=%s drop=%r apply=%s" % (a.switch, want, a.drop_value, a.apply))
    rc = 0
    for path in sorted(pathlib.Path(a.templates).glob("TEMPLATE_*.xlsx")):
        if only and path.name not in only:
            continue
        print(" %s" % path.name)
        rep, before, after = process(path, a.switch, want, a.drop_value, a.apply)
        if rep["error"]:
            print("   REFUSED: %s" % rep["error"])
            rc = 1
            continue
        moved = {k: (before.get(k, 0), after.get(k, 0)) for k in set(before) | set(after)
                 if before.get(k, 0) != after.get(k, 0) and k[1] != a.switch}
        if moved:
            print("   ** OTHER SWITCHES CHANGED (unexpected): %s" % dict(list(moved.items())[:6]))
            rc = 1
        if rep["added"]:
            print("   added: %s" % ", ".join(rep["added"]))
        if rep["dropped"]:
            print("   dropped: %s" % ", ".join(rep["dropped"]))
        if rep["skipped"]:
            print("   skipped: %s" % ", ".join(rep["skipped"]))
        if rep.get("backup"):
            print("   WROTE (backup %s, zip %d entries)" % (rep["backup"], rep["zip_entries"]))
    if not a.apply:
        print("dry-run only - pass --apply to write")
    return rc


if __name__ == "__main__":
    sys.exit(main())
