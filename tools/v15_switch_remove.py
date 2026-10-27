"""v15_switch_remove — whole-switch removal DISABLED; narrow --values removal allowed.

USER 2026-10-24 standing order: removing template rows is impossible. Dead
switches are GREYED (switch-name font grey, pilot skips them) via
tools/v15_switch_grey.py; rows are never deleted. That order still holds for
removing a SWITCH, and `main()` still refuses it unconditionally.

USER 2026-10-27 EXPLICIT EXCEPTION, verbatim: "in the case of the unnecessary
rows that were added do not grey just take them out completely before they ever
get tested". Context: MIN_HOLD_BARS was ordered swept at 2/4/8/12, but its tab
also carried legacy 3/3/6 rows; the operator refused greying them and ordered
them out so the sheet says exactly what it means and no cell is ever spent on
them. `--values` implements ONLY that: it deletes the named option rows of a
switch that STAYS. Guards that cannot be bypassed (no --force exists):
  * the BOLD / is_default row is never deletable — refuses if named;
  * at least 2 rows must survive per tab, so a switch can never be emptied;
  * every named value must exist, or it refuses (no silent no-op);
  * a value still reachable as a config/QuickConfig DEFAULT is refused — that
    would delete the row the bold contract is checked against;
  * row-guard fingerprints before/after must show ONLY the named rows gone;
  * zip-validated atomic save, per-file backup, S1-only by convention.
Whole-switch removal: use tools/v15_switch_grey.py. That has not changed.
"""
import argparse
import datetime
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import openpyxl  # noqa: E402
from template_row_guard import fingerprints  # noqa: E402
from v15_switch_add import FILES, FLEET, SWITCH_SHEETS, ast_assigns, die  # noqa: E402


def plan_file(path, sw):
    wb = openpyxl.load_workbook(str(path))
    try:
        hits = {}
        for tab in SWITCH_SHEETS:
            if tab not in wb.sheetnames:
                continue
            ws = wb[tab]
            rows = [r for r in range(3, ws.max_row + 1) if ws.cell(r, 1).value == sw]
            if rows:
                hits[tab] = rows
        return hits
    finally:
        wb.close()


def remove_file(path, sw, hits, ts):
    # USER 2026-10-24: whole-switch row removal is impossible — dead on purpose.
    die(f"row removal is impossible — grey {sw} instead: python3 tools/v15_switch_grey.py --switch {sw} --reason TEXT [--apply]")


def _isd_col(ws):
    for c in range(1, ws.max_column + 1):
        if str(ws.cell(2, c).value or "").strip().lower().startswith("is_default"):
            return c
    return None


def plan_values(path, sw, values):
    """Rows of `sw` whose col-B value is in `values`. Refuses on every guard."""
    wb = openpyxl.load_workbook(str(path))
    try:
        plan, seen = {}, set()
        for tab in SWITCH_SHEETS:
            if tab not in wb.sheetnames:
                continue
            ws = wb[tab]
            isd = _isd_col(ws)
            allrows = [r for r in range(3, ws.max_row + 1) if ws.cell(r, 1).value == sw]
            if not allrows:
                continue
            doomed = []
            for r in allrows:
                v = str(ws.cell(r, 2).value).strip()
                if v not in values:
                    continue
                bold = bool(ws.cell(r, 2).font and ws.cell(r, 2).font.bold)
                isdef = str(ws.cell(r, isd).value or "").strip().upper() == "YES" if isd else False
                if bold or isdef:
                    die(f"{path.name}!{tab} r{r}: {sw}={v} is the BOLD/is_default row — the default is the sheet's authority and is never deleted")
                doomed.append((r, v))
                seen.add(v)
            if doomed and len(allrows) - len(doomed) < 2:
                die(f"{path.name}!{tab}: removing {len(doomed)} of {len(allrows)} {sw} rows would leave <2 — a switch must keep at least two testable options")
            if doomed:
                plan[tab] = doomed
        missing = values - seen
        if missing and plan:
            die(f"{path.name}: {sw} has no rows for value(s) {sorted(missing)} — refusing a partial removal that would silently differ per file")
        return plan
    finally:
        wb.close()


def remove_values(path, sw, plan, ts):
    """Delete exactly the planned rows; prove via row-guard fingerprints.

    `fingerprints(ws)` takes a WORKSHEET and returns a Counter of row hashes
    (same contract tools/v15_switch_add.py uses) — the multiset difference must
    be exactly the rows we meant to delete and nothing else: no row may be
    partially moved, altered or gained (template_row_guard, USER 2026-10-01).
    """
    shutil.copy2(path, ROOT / "backups" / f"before_switch_value_remove_{sw}_{ts}_{path.name}")
    wb = openpyxl.load_workbook(str(path))
    n = 0
    try:
        before = {tab: fingerprints(wb[tab]) for tab in plan}
        for tab, doomed in plan.items():
            ws = wb[tab]
            for r, _v in sorted(doomed, reverse=True):  # bottom-up: indices stay valid
                ws.delete_rows(r)
                n += 1
        for tab, doomed in plan.items():
            after = fingerprints(wb[tab])
            lost = sum((before[tab] - after).values())
            gained = sum((after - before[tab]).values())
            if gained:
                die(f"{path.name}!{tab}: row-guard gained {gained} unexpected rows — nothing saved, backup kept")
            if lost != len(doomed):
                die(f"{path.name}!{tab}: row-guard lost {lost} rows but {len(doomed)} were planned — nothing saved, backup kept")
        tmp = str(path) + ".rmtmp"
        wb.save(tmp)
        with zipfile.ZipFile(tmp) as z:
            if len(z.namelist()) < 10:
                os.remove(tmp)
                die(f"refusing truncated save {path.name}")
        os.replace(tmp, str(path))
    finally:
        wb.close()
    return n


def main_values(a, sw):
    """USER 2026-10-27: take the unnecessary option rows out completely.

    THE LIFT IS SPENT. The operator lifted the no-row-removal order
    "temporarily to delete the rows we just talked about and any duplicate
    switches across tabs - then the rule stands again". That work is done
    (MIN_HOLD_BARS 3/3/6 deleted from both crypto templates 2026-10-27 21:4xZ;
    the cross-tab duplicates were MEASURED as benign — 0 conflicting bolds, 0
    tabs with >1 bold — so nothing was deleted for them). The rule therefore
    stands again: this mode now refuses unless the caller sets
    V15_ROW_REMOVAL_LIFTED=1, which is the audit trail for the NEXT explicit
    operator lift. Do not remove that gate; ask for a new lift.
    """
    if os.environ.get("V15_ROW_REMOVAL_LIFTED") != "1":
        die("row removal is impossible again (USER 2026-10-27: the temporary lift was spent on MIN_HOLD_BARS 3/3/6). "
            "A NEW explicit operator lift is required: re-run with V15_ROW_REMOVAL_LIFTED=1 only when you have one, "
            f"otherwise grey instead: python3 tools/v15_switch_grey.py --switch {sw} --values ... --reason TEXT [--apply]")
    values = {v.strip() for v in a.values.split(",") if v.strip()}
    if not values:
        die("--values given but empty")
    if len(str(a.reason).strip()) < 8:
        die("--values needs a real --reason (8+ chars): a deletion must cite its order")
    for fn, cls in (("config.py", "Config"), ("config_tradier.py", "TradierConfig"), ("v12_quick_engine.py", "QuickConfig")):
        try:
            cur = ast_assigns(ROOT / fn, cls).get(sw)
        except Exception:
            cur = None
        if cur is not None and str(cur).strip() in values:
            die(f"{sw}={cur} is the current {cls} default in {fn} — that row is what the template-bold contract is checked against; change the default first")
    plan = []
    for cs, fn in FILES.items():
        p = Path(a.templates_dir) / fn
        if not p.exists():
            die(f"missing {p}")
        pv = plan_values(p, sw, values)
        plan.append((cs, p, pv))
        print(f"[{cs}] {fn}: " + (", ".join(f"{t} delete rows {[r for r, _ in d]} values {[v for _, v in d]}" for t, d in pv.items()) if pv else "no matching rows"), flush=True)
    if not any(pv for _, _, pv in plan):
        die(f"no {sw} rows match values {sorted(values)} in any template — nothing to do")
    if not a.apply:
        print("dry-run OK — re-run with --apply to DELETE (backs up each file, row-guard verified)", flush=True)
        return 0
    ts = datetime.datetime.now().strftime("%Y%m%d%H%M")
    for cs, p, pv in plan:
        if pv:
            print(f"[{cs}] deleted {remove_values(p, sw, pv, ts)} rows -> {p.name}  ({a.reason})", flush=True)
    print("fleet NOT synced — run on the template source host (S1) and push, or re-run with --fleet", flush=True)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--switch", required=True)
    ap.add_argument("--templates-dir", default=str(ROOT / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM"))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--fleet", action="store_true")
    ap.add_argument("--values", default=None, help="USER 2026-10-27 exception: delete ONLY these option rows (comma list) of a switch that STAYS. Never the bold/is_default row; always leaves >=2 rows.")
    ap.add_argument("--reason", default="", help="required with --values (8+ chars) — the audit trail for a deletion")
    a = ap.parse_args()
    sw = a.switch.strip().upper()
    if a.values:
        return main_values(a, sw)
    die(f"whole-switch row removal is impossible (USER 2026-10-24) — grey {sw} instead: python3 tools/v15_switch_grey.py --switch {sw} --reason TEXT [--apply]")
    for fn, cls in (("config.py", "Config"), ("config_tradier.py", "TradierConfig"), ("v12_quick_engine.py", "QuickConfig")):
        if sw in ast_assigns(ROOT / fn, cls):
            die(f"{sw} is still a {cls} field in {fn} — remove the code first, the template row of a live key is never deleted")
    plan = []
    for cs, fn in FILES.items():
        p = Path(a.templates_dir) / fn
        if not p.exists():
            die(f"missing {p}")
        hits = plan_file(p, sw)
        plan.append((cs, p, hits))
        print(f"[{cs}] {fn}: " + (", ".join(f"{t} rows {r}" for t, r in hits.items()) if hits else "no rows"), flush=True)
    if not a.apply:
        print("dry-run OK — re-run with --apply to delete (backs up each file first)", flush=True)
        return 0
    ts = datetime.datetime.now().strftime("%Y%m%d%H%M")
    for cs, p, hits in plan:
        if hits:
            print(f"[{cs}] removed {remove_file(p, sw, hits, ts)} rows -> {p.name}", flush=True)
    if a.fleet:
        for h in FLEET:
            for cs, p, hits in plan:
                subprocess.run(["rsync", "-az", "-e", "ssh -S none -o StrictHostKeyChecking=accept-new", str(p), f"{h}:~/binance-sandbox/SPREADSHEETS/TEMPLATE_FINAL_NORM/{p.name}"], check=False)
            r = subprocess.run(["ssh", "-S", "none", "-o", "StrictHostKeyChecking=accept-new", h, "cd ~/binance-sandbox/SPREADSHEETS/TEMPLATE_FINAL_NORM && md5sum " + " ".join(p.name for _, p, _ in plan)], capture_output=True, text=True)
            print(f"[{h}] {r.stdout.strip()}", flush=True)
    else:
        print("fleet NOT synced — run on the template source host or re-run with --fleet", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
