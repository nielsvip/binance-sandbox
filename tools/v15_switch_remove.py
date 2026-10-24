"""v15_switch_remove — DISABLED (USER 2026-10-24: removing template rows is impossible).

Row removal is IMPOSSIBLE by standing order. Dead switches are greyed out
(switch-name font grey, pilot skips them everywhere) via tools/v15_switch_grey.py —
rows are never deleted. THIS TOOL unconditionally refuses in every mode (dry-run
included) so no caller, chain step, or agent can delete rows through it.

Usage: python3 tools/v15_switch_grey.py --switch NAME --reason TEXT [--apply]
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
    # USER 2026-10-24: row removal is impossible — this path is dead on purpose.
    die(f"row removal is impossible — grey {sw} instead: python3 tools/v15_switch_grey.py --switch {sw} --reason TEXT [--apply]")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--switch", required=True)
    ap.add_argument("--templates-dir", default=str(ROOT / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM"))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--fleet", action="store_true")
    a = ap.parse_args()
    sw = a.switch.strip().upper()
    die(f"row removal is impossible (USER 2026-10-24) — grey {sw} instead: python3 tools/v15_switch_grey.py --switch {sw} --reason TEXT [--apply]")
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
