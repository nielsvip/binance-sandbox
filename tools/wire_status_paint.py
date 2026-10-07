#!/usr/bin/env python3
"""wire_status_paint — paint col-A font GREY (FFBFBFBF) for template switch rows whose
switch is VECTOR-BLIND in that venue (sweep cannot measure it: no deltas possible);
restore the ORIGINAL col-A font color for rows this tool painted once the switch
becomes vector-readable. VEC_ONLY stays BLACK (real deltas; live parity is the
promotion gate's job, not the sweep's).

Direction A (grey): white non-orange row, col-A not already grey, venue status not
  WIRED_BOTH_*/VEC_ONLY_* -> paint GREY + ledger entry {orig color}.
Direction B (black): ledger entry exists + venue status now WIRED_BOTH_*/VEC_ONLY_*
  -> restore orig color + ledger update.

NEVER touches: row/col order, values, bold, is_default, fills, orange rows, yellow
headers, pre-existing grey rows (no ledger entry -> skip). Backup + atomic save +
reload check. Rows = NEVER DELETED.

  python tools/wire_status_paint.py --dry-run
  python tools/wire_status_paint.py --apply
"""
import argparse
import datetime
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

import openpyxl
from openpyxl.styles import Font

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import v15_template_defaults_fix as F  # noqa: E402
import v15_template_fix as FX  # noqa: E402

GREY = "FFBFBFBF"
LEDGER = ROOT / "data" / "reports" / "wire_paint_ledger.json"
BIBLE = ROOT / "data" / "SWITCH_BIBLE.json"


def col_a_rgb(cell):
    f = cell.font
    if not f or not f.color:
        return None
    try:
        v = str(getattr(f.color, "rgb", "") or "").upper() or None
    except Exception:
        return None
    return v if v and re.fullmatch(r"[0-9A-F]{8}", v) else None


def is_grey(cell):
    return col_a_rgb(cell) == GREY


def vec_measurable(status):
    s = status or ""
    return s.startswith("WIRED_BOTH") or s.startswith("VEC_ONLY")


def wired_both(status):
    return vec_measurable(status)


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    bible = json.loads(BIBLE.read_text())
    sw = bible.get("switches", bible if isinstance(bible.get("switches"), dict) else {})
    if "switches" in bible:
        sw = bible["switches"]
    else:
        sw = bible
    ledger = json.loads(LEDGER.read_text()) if LEDGER.exists() else {"painted": {}}
    painted = ledger.setdefault("painted", {})
    ts = datetime.datetime.now().strftime("%Y%m%d%H%M")
    total_grey, total_black, total_skip = 0, 0, 0
    for cs in ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT"):
        venue = "crypto" if cs.startswith("CRYPTO") else "stocks"
        path = ROOT / "SPREADSHEETS" / F.TEMPLATES[cs]
        wb = openpyxl.load_workbook(str(path))
        n_grey, n_black, n_skip = 0, 0, 0
        for tab in F.SWITCH_SHEETS:
            if tab not in wb.sheetnames:
                continue
            ws = wb[tab]
            for r in range(F.HDR_ROWS + 1, ws.max_row + 1):
                c0 = ws.cell(row=r, column=1)
                name = str(c0.value or "").strip()
                if not name or FX.is_orange(c0):
                    continue
                key = f"{cs}|{tab}|{r}|{name}"
                entry = sw.get(name, {})
                status = entry.get("status", {}).get(venue, "") if isinstance(entry, dict) else ""
                if key in painted:
                    if wired_both(status):
                        orig = painted[key].get("orig")
                        orig = orig if orig and re.fullmatch(r"[0-9A-F]{8}", orig) else None
                        f = c0.font
                        c0.font = Font(name=(f.name if f else None) or "Arial", size=(f.size if f else None) or 10, bold=f.b if f else False, italic=f.i if f else False, color=orig)
                        del painted[key]
                        n_black += 1
                    continue
                if is_grey(c0):
                    n_skip += 1
                    continue
                if not wired_both(status):
                    if a.apply:
                        f = c0.font
                        orig = (str(getattr(getattr(f, "color", None), "rgb", "") or "").upper() or None)
                        c0.font = Font(name=(f.name if f else None) or "Arial", size=(f.size if f else None) or 10, bold=f.b if f else False, italic=f.i if f else False, color=GREY)
                        painted[key] = {"orig": orig, "status": status, "ts": ts}
                    n_grey += 1
        print(f"[{cs}] grey={n_grey} black={n_black} pre_grey_skip={n_skip}")
        total_grey += n_grey
        total_black += n_black
        total_skip += n_skip
        if a.apply and (n_grey or n_black):
            shutil.copy2(path, ROOT / "backups" / f"before_wire_paint_{ts}_{path.name}")
            tmp = path.with_suffix(".tmp.xlsx")
            wb.save(str(tmp))
            with zipfile.ZipFile(str(tmp)) as z:
                if len(z.namelist()) < 10:
                    sys.exit(f"REFUSED: {tmp} truncated")
            openpyxl.load_workbook(str(tmp))
            tmp.replace(path)
        wb.close()
    if a.apply:
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        LEDGER.write_text(json.dumps(ledger, indent=1, sort_keys=True))
    print(f"TOTAL grey={total_grey} black={total_black} pre_grey_skip={total_skip} ledger_entries={len(painted)} mode={'APPLY' if a.apply else 'DRY'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
