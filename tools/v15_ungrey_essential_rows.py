#!/usr/bin/env python3
"""Un-grey the ESSENTIAL_GATE_SWITCHES rows in the live TEMPLATE_FINAL_NORM workbooks.

2026-10-27 incident — why not one board was ever published:

v15_pilot.py:5082 skips any switch row whose column-A FONT is grey (GREY_SKIP_RGB, set by
tools/v15_template_fix.py) — grey means "template-disabled, never sweep this row". Separately,
v15_pilot.py:1596 ESSENTIAL-ROWS GATE refuses to publish ANY sheet while one of the 40
ESSENTIAL_GATE_SWITCHES has no non-zero number in its rows ("protection unproven on this window =
the side must not trade"). Today's template work greyed rows belonging to those very switches, so
the template forbids measuring exactly what the gate demands be measured. Every finished board was
refused: on s6, 27 of 32 otherwise-publishable boards carried
    not_compliant = "ESSENTIAL-ROWS: 7 protection filters without a non-zero number
                     (WT_15M_BOUNCE_FILTER_MODE, WT_15M_BOUNCE_VOLUME_MODE, WT_15M_CROSS_ENTRY_ENABLED, ...)"
while holding real results (AG_SHORT gain 53.2% vs bh 25.8% on 78 trades, workbook already written).

Measured in git: TEMPLATE_STOCKS_SHORT.xlsx at 111b90ebd (10-24 18:10) had these rows NORMAL and
1007 grey rows overall; at 55298ac53 (10-27 09:26) they are GREY with 1128; at afd568f7b (10-27
15:47, live) GREY with 1160.

This tool edits the CURRENT template in place — it never copies an older workbook over a newer one
(CLAUDE.md: find the better version, diff it, then fix the current file forward). It changes ONLY the
column-A font colour of rows whose switch is in ESSENTIAL_GATE_SWITCHES; it never touches a value, a
default, a bold/default marker, a yellow header or any other row, so TEMPLATE<->config parity
(tools/switch_surface_watchdog.py) is unaffected. Backs up each workbook first and validates the
rewritten zip before replacing the original.

Dry-run by default. --apply to write.
"""
import argparse
import datetime
import hashlib
import pathlib
import re
import shutil
import sys
import zipfile

GREY_SKIP_RGB = {"FFBFBFBF", "00BFBFBF"}
ROOT = pathlib.Path(__file__).resolve().parent.parent


def essential_switches(pilot: pathlib.Path) -> set:
    """Parse ESSENTIAL_GATE_SWITCHES (and the WT CORE block feeding it) out of v15_pilot.py."""
    src = pilot.read_text()
    start = src.index("ESSENTIAL_GATE_SWITCHES = frozenset({")
    depth, end = 0, start
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    names = set(re.findall(r'"([A-Z][A-Z0-9_]{3,})"', src[start:end + 1]))
    pre = src[max(0, start - 4000):start]
    if "WT CORE" in pre:
        names |= set(re.findall(r'"([A-Z][A-Z0-9_]{3,})"', pre[pre.rfind("WT CORE"):]))
    return names


def _sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--templates", default=str(ROOT / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM"))
    ap.add_argument("--pilot", default=str(ROOT / "v15_pilot.py"))
    a = ap.parse_args()

    import openpyxl
    from openpyxl.styles import Font

    names = essential_switches(pathlib.Path(a.pilot))
    print("ESSENTIAL_GATE_SWITCHES: %d names" % len(names))
    tdir = pathlib.Path(a.templates)
    stamp = datetime.datetime.now().strftime("%Y%m%d%H%M")
    total = 0
    for tf in sorted(tdir.glob("TEMPLATE_*.xlsx")):
        wb = openpyxl.load_workbook(str(tf))
        changed = []
        for sheet in wb.sheetnames:
            ws = wb[sheet]
            for rr in range(3, ws.max_row + 1):
                cell = ws.cell(row=rr, column=1)
                val = cell.value
                if val is None or str(val).strip() not in names:
                    continue
                font = cell.font
                rgb = str(getattr(font.color, "rgb", "") or "").upper() if (font is not None and font.color is not None) else ""
                if rgb not in GREY_SKIP_RGB:
                    continue
                changed.append((sheet, rr, str(val).strip()))
                if a.apply:
                    # keep every other font property; drop ONLY the grey colour
                    cell.font = Font(name=font.name, size=font.size, bold=font.bold, italic=font.italic,
                                     underline=font.underline, strike=font.strike, vertAlign=font.vertAlign,
                                     color=None)
        total += len(changed)
        print("\n%s  grey essential rows = %d" % (tf.name, len(changed)))
        for sheet, rr, sw in changed[:40]:
            print("    %-28s row %-5d %s" % (sheet, rr, sw))
        if not a.apply or not changed:
            wb.close()
            continue
        backup = ROOT / "backups" / ("before_ungrey_essential_%s_%s" % (stamp, tf.name))
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(tf, backup)
        tmp = tf.with_suffix(".xlsx.ungrey_tmp")
        wb.save(str(tmp))
        wb.close()
        # validate the rewritten zip before replacing the live template (BadZip/truncation guard)
        with zipfile.ZipFile(tmp) as zf:
            n = len(zf.namelist())
            bad = zf.testzip()
        if n < 10 or bad is not None:
            tmp.unlink(missing_ok=True)
            print("    REFUSED: rewritten zip invalid (entries=%d bad=%s) — original untouched" % (n, bad))
            continue
        tmp.replace(tf)
        print("    WROTE %s  entries=%d  backup=%s  sha=%s" % (tf.name, n, backup.name, _sha(tf)))
    print("\nTOTAL grey essential rows %s: %d" % ("un-greyed" if a.apply else "that WOULD be un-greyed", total))
    if not a.apply:
        print("dry-run only — pass --apply to write")
    return 0


if __name__ == "__main__":
    sys.exit(main())
