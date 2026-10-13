#!/usr/bin/env python3
"""v15_unfinished_inventory — authoritative unfinished-board list from PROGRESS truth (2026-10-13, RESUME-FROM-CELL).

Runs on S1. Scans every campaign progress dir (~/v15_run*/progress + ~/v15_import_*/progress) on S1 and on the
scan hosts (default s2 = 10.0.0.4) and keeps, per sym_side, the NEWEST progress file (mtime). A sym_side is
UNFINISHED when that newest board has done rows but no final_gain (or carries needs_redo) — i.e. an older final
never hides a newer half-filled board. xlsx mtimes are ignored (re-stamped by sidecars). Only sym_sides in the
tradeable universe (tools/v15_universe allowed_sym_sides) are listed unless --all.
  python3 tools/v15_unfinished_inventory.py [--hosts 10.0.0.4] [--out data/v15_unfinished_<date>.json] [--all]
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

SCAN = r'''
import glob, json, os, socket
out = []
cur = ""
try:
    cur = open(os.path.expanduser("~/v15_current_progress_dir.txt")).read().strip()
except Exception:
    pass
for d in sorted(glob.glob(os.path.expanduser("~/v15_run*/progress")) + glob.glob(os.path.expanduser("~/v15_import_*/progress"))):
    for f in glob.glob(os.path.join(d, "*_v14_progress.json")):
        ss = os.path.basename(f)[:-len("_v14_progress.json")]
        try:
            j = json.load(open(f))
        except Exception:
            continue
        if not isinstance(j, dict):
            continue
        fg = j.get("final_gain")
        fin = isinstance(fg, (int, float)) and not isinstance(fg, bool)
        dn = j.get("done") or {}
        out.append({"ss": ss, "host": socket.gethostname(), "dir": d, "current": d == cur, "mtime": os.path.getmtime(f), "rows": len(dn) if hasattr(dn, "__len__") else 0,
                    "final": fin, "redo": bool(j.get("needs_redo")), "npz_id": j.get("npz_id") if isinstance(j.get("npz_id"), dict) else None})
print(json.dumps(out))
'''


def scan_local():
    r = subprocess.run([sys.executable, "-c", SCAN], capture_output=True, text=True, timeout=600)
    return json.loads(r.stdout or "[]")


def scan_remote(host):
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, "python3 -"], input=SCAN, capture_output=True, text=True, timeout=600)
    return json.loads(r.stdout or "[]")


def classify(records, allowed=None):
    """newest record per sym_side; unfinished = rows>0 and (not final or needs_redo). Pure (unit-tested)."""
    newest = {}
    for r in records:
        if allowed is not None and r["ss"] not in allowed:
            continue
        if r["ss"] not in newest or r["mtime"] > newest[r["ss"]]["mtime"]:
            newest[r["ss"]] = r
    unfinished = {ss: r for ss, r in newest.items() if r.get("rows", 0) > 0 and (not r.get("final") or r.get("redo"))}
    return newest, unfinished


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hosts", default="10.0.0.4", help="comma list of extra scan hosts (ssh targets)")
    ap.add_argument("--out", default="")
    ap.add_argument("--all", action="store_true", help="do not filter by the tradeable universe")
    a = ap.parse_args()
    recs = scan_local()
    for h in [x for x in a.hosts.split(",") if x]:
        try:
            recs += scan_remote(h)
        except Exception as e:
            print(f"[inv] scan {h} failed: {e}", file=sys.stderr)
    allowed = None
    if not a.all:
        import v15_universe as U
        allowed = set(U.load_or_build(ROOT, None, write=False)[0].get("allowed_sym_sides") or []) or None
    newest, unf = classify(recs, allowed)
    by_loc = {}
    for ss, r in unf.items():
        loc = f"{r['host']}:{r['dir'].replace(os.path.expanduser('~'), '~')}"
        by_loc[loc] = by_loc.get(loc, 0) + 1
    rep = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "n_records": len(recs), "n_sides": len(newest), "n_unfinished": len(unf), "by_location": by_loc,
           "unfinished": {ss: {k: r[k] for k in ("host", "dir", "current", "rows", "redo", "mtime", "npz_id")} for ss, r in sorted(unf.items())}}
    out = Path(a.out or ROOT / "data" / f"v15_unfinished_{time.strftime('%Y-%m-%d', time.gmtime())}.json")
    out.write_text(json.dumps(rep, indent=1))
    print(json.dumps({k: rep[k] for k in ("ts", "n_records", "n_sides", "n_unfinished", "by_location")}, indent=1))
    print(f"[inv] wrote {out}")


if __name__ == "__main__":
    main()
