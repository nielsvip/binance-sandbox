#!/usr/bin/env python3
"""v15_template_staged_apply — install a STAGED template set (tools/v15_template_restructure_v2.py) into the live templates.
Dry-run by default (verifies the staged files, prints the row/tab diff). --apply: backup live -> atomic install -> yellow json ->
rsync to s1/s2/s5 (~/binance-sandbox + ~/binance) -> md5 verify. Running pilots keep their cloned sheets; NEW pilots use the new templates.
NEVER run while tools/v15_daily_template_update.py is running (it rewrites the same files).
  python tools/v15_template_staged_apply.py --ts <ts> [--apply] [--hosts s1-pub,s2,s5]"""
import os as _os, sys as _sys
if _os.path.exists(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "SPREADSHEETS", "TEMPLATES_FROZEN")) and "--dry-run" not in _sys.argv and not _os.environ.get("TEMPLATES_UNFREEZE"):
    _writes = any(a in _sys.argv for a in ("--apply", "--write", "--out-dir")) or "v15_daily_template_update" in __file__ or "restructure" in __file__
    if _writes and ("--apply" in _sys.argv or "v15_daily_template_update" in __file__ or "restructure" in __file__):
        _sys.exit("REFUSED: SPREADSHEETS/TEMPLATES_FROZEN exists (USER 2026-10-01: templates restored to the pre-trainwreck 20:36 version; no writer may touch them until the user approves a proposal). Remove the file only on explicit user approval.")
import argparse
import datetime
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import v15_template_staged_verify as V  # noqa: E402
import v15_template_restructure_v3 as R  # noqa: E402

SSH = "ssh -S none -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10"


def md5(p):
    return hashlib.md5(Path(p).read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ts", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--hosts", default="s1-pub,s2,s5")
    a = ap.parse_args()
    stg = ROOT / "SPREADSHEETS" / "TEMPLATE_STAGED" / a.ts
    if subprocess.run(["pgrep", "-f", "[v]15_daily_template_update"], capture_output=True).returncode == 0:
        sys.exit("REFUSED: v15_daily_template_update.py is running")
    bad_all = 0
    for cs, name in R.TEMPLATES.items():
        live_now = ROOT / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM" / name
        base_ = ROOT / "data" / "template_audit" / a.ts / "base" / name   # the pre-restructure templates this stage was built from
        bad = V.audit(stg / name, cs, [live_now] + ([base_] if base_.exists() else []), None, base_ if base_.exists() else None)   # structure + typed options + defaults gate + row-integrity vs the CURRENT live template
        print(f"[{cs}] staged audit (structure, option types, defaults, family, row-integrity) violations={len(bad)} {bad[:3]}")
        bad_all += len(bad)
    if bad_all:
        sys.exit("REFUSED: staged templates have violations")
    if not a.apply:
        print("dry-run OK — re-run with --apply (see data/template_audit/%s/report.md for what changes)" % a.ts)
        return
    ts = datetime.datetime.now().strftime("%Y%m%d%H%M")
    for name in R.TEMPLATES.values():
        live = ROOT / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM" / name
        shutil.copy2(live, ROOT / "backups" / f"before_staged_apply_{ts}_{name}")
        tmp = live.with_suffix(".tmp.xlsx")
        shutil.copy2(stg / name, tmp)
        import openpyxl
        openpyxl.load_workbook(str(tmp), read_only=True).close()
        os.replace(tmp, live)
        print("installed", live)
    yj = ROOT / "data" / "yellow_ever_nonzero.json"
    skip_yellow = False
    if (stg / "yellow_ever_nonzero.staged.json").exists():
        import json
        live_b = json.loads(yj.read_text()).get("built_at")
        stg_b = json.loads((stg / "yellow_ever_nonzero.staged.json").read_text()).get("derived_from_built_at")
        if live_b != stg_b:
            skip_yellow = True
            print(f"WARNING: data/yellow_ever_nonzero.json was rebuilt ({live_b}) after this stage derived its re-keyed copy from {stg_b} — NOT overwriting it. Re-stage (tools/v15_template_restructure_v3.py) so the yellow json is re-keyed from the current one.")
        else:
            shutil.copy2(yj, ROOT / "backups" / f"before_staged_apply_{ts}_yellow_ever_nonzero.json")
            shutil.copy2(stg / "yellow_ever_nonzero.staged.json", yj)
    files = [f"SPREADSHEETS/TEMPLATE_FINAL_NORM/{n}" for n in R.TEMPLATES.values()] + ([] if skip_yellow else ["data/yellow_ever_nonzero.json"])
    for h in a.hosts.split(","):
        for d in ("binance-sandbox", "binance"):
            subprocess.run(["rsync", "-az", "-e", SSH] + [str(ROOT / f) for f in files[:4]] + [f"{h}:~/{d}/SPREADSHEETS/TEMPLATE_FINAL_NORM/"], check=False)
            if not skip_yellow:
                subprocess.run(["rsync", "-az", "-e", SSH, str(ROOT / files[4]), f"{h}:~/{d}/data/"], check=False)
        out = subprocess.run(["ssh", "-o", "ConnectTimeout=10", h, "cd binance-sandbox && md5sum " + " ".join(files)], capture_output=True, text=True).stdout
        got = {l.split()[1]: l.split()[0] for l in out.splitlines() if len(l.split()) == 2}
        bad = [f for f in files if got.get(f) != md5(ROOT / f)]
        print(f"[{h}] md5 {'OK' if not bad else 'MISMATCH ' + str(bad)}")


if __name__ == "__main__":
    main()
