#!/usr/bin/env python3
"""
clean_timestamped_interim — deletes timestamped interim files in SPREADSHEETS/ as soon as superseded.

Rules (user mandate):
- Timestamped interim = any file in SPREADSHEETS/V15_V16_CELL_BY_CELL/*_2026*.xlsx (*_pilot_2026*, *_2026*, *_7d_matrix_2026*)
- Also *.bak and *.tmp in same dir are always interim (always superseded if .xlsx exists)
- A timestamped interim is superseded as soon as a FINAL exists for same sym_side:
  FINAL = .xlsx without _2026 in name, size>500k, same sym_side prefix (e.g., AMZN_LONG_30d_matrix.xlsx, AMZN_LONG_bhm1p01_gain2p30_30d_matrix.xlsx)
  (FINAL includes bh/gain variants, but not _2026)
- Also, if a newer non-timestamped exists, all older timestamped for that sym are deleted.
- Never delete FINAL, never delete audit CSV/TXT/MD, never delete V15_V16_CELL_BY_CELL_FINAL/.
- Logs deletions to /tmp/clean_timestamped_interim.log and prints.

Usage:
  python tools/clean_timestamped_interim.py --dry-run   # preview
  python tools/clean_timestamped_interim.py              # delete
  python tools/clean_timestamped_interim.py --watch      # loop every 5m (for cron)

Also installed via cron on each server and via herd post-save hook.
"""
from __future__ import annotations
import pathlib, re, sys, time
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parents[1]
CELL = ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL"
FINAL_CELL = ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL_FINAL"
LOG = pathlib.Path("/tmp/clean_timestamped_interim.log")

def log(msg: str):
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with LOG.open("a") as f: f.write(line+"\n")
    except: pass

def find_superseded(dry_run=False):
    deleted=[]
    kept=[]
    # All xlsx in CELL
    all_xlsx = list(CELL.glob("*.xlsx"))
    # Group by symside
    groups=defaultdict(list)
    for p in all_xlsx:
        m=re.match(r"(.+_(?:LONG|SHORT))", p.name)
        if m:
            sym=m.group(1)
            groups[sym].append(p)
    # For each sym, find finals (no _2026, size>500k)
    for sym, files in groups.items():
        finals=[f for f in files if "_2026" not in f.name and f.stat().st_size>500_000]
        inters=[f for f in files if "_2026" in f.name]
        if finals and inters:
            # all inters are superseded (final exists)
            for f in inters:
                deleted.append(f)
                if not dry_run:
                    try:
                        f.unlink()
                        log(f"DELETE superseded timestamped {f.name} (final exists: {finals[0].name})")
                    except Exception as e:
                        log(f"FAIL delete {f} {e}")
                else:
                    log(f"DRY would delete {f.name}")
        elif inters:
            # no final yet, keep inters (not yet superseded)
            kept.extend(inters)
        # finals kept
        kept.extend(finals)
    # Baks and tmps: always delete if corresponding xlsx exists (superseded)
    # Actually always delete baks/tmps - they are never final
    baks=list(CELL.glob("*.bak"))
    tmps=list(CELL.glob("*.tmp"))
    for p in baks+tmps:
        try:
            if time.time() - p.stat().st_mtime < 1800:
                continue  # USER-SAFETY 2026-10-01: a live pilot's atomic save (*.xlsx.<pid>.tmp) is still being written/renamed — deleting it crashed pilots
        except OSError:
            continue
        # check if base xlsx exists (without .bak/.tmp)
        base_name=p.name.replace(".bak","").replace(".tmp","")
        # if base xlsx exists, it's superseded; if not, still interim but may be orphaned - delete as well (always interim)
        deleted.append(p)
        if not dry_run:
            try:
                p.unlink()
                log(f"DELETE interim {p.name}")
            except Exception as e:
                log(f"FAIL delete {p} {e}")
        else:
            log(f"DRY would delete interim {p.name}")
    # Also check V15_V16_CELL_BY_CELL subdirs for timestamped? No, only top level
    # Check SPREADSHEETS root for timestamped xlsx interim? None, but audit files are 20260920.csv etc - keep
    # Specifically, delete any SPREADSHEETS/*_2026*.xlsx that has a non-timestamped superseding? For now, only CELL
    return deleted, kept

def main():
    dry="--dry-run" in sys.argv
    watch="--watch" in sys.argv
    if watch:
        log("WATCH mode — loop every 5m")
        while True:
            find_superseded(dry_run=False)
            time.sleep(300)
    else:
        deleted, kept=find_superseded(dry_run=dry)
        log(f"Done dry={dry} deleted={len(deleted)} kept_groups={len(kept)}")
        # Also report disk saved
        if not dry:
            # du
            import subprocess
            try:
                out=subprocess.check_output(["du","-sh",str(CELL)], text=True)
                log(f"After clean {out.strip()}")
            except: pass

if __name__=="__main__":
    main()
