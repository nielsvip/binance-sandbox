#!/usr/bin/env python3
"""Push the source boards a top-up shard needs from the Mac to a worker host.

USER 2026-10-16 ("did the servers stop? I see no new results?"): the FINAL_0657 round measured 66 crypto
sym_sides and could publish NONE of them — `v15_xlsx_topup.fetch_source_xlsx` only looks on the worker and its
peer, and the Mac (which holds 2632 of the ~3000 boards; s1 510, s2 939) is behind NAT, so no worker can pull
from it. The start set resolved fine from the per_sym_history publish row; only the workbook the new board is
written into was missing, so the whole measurement was thrown away at the last step.

The Mac therefore pushes first. For each sym_side it resolves the SAME workbook the host would want:
  1. the manifest whose `overrides_md5` matches the host's published set (exact match, the correct answer), or
  2. the newest manifest for that sym_side by revision, or
  3. the newest `*_30d_matrix.xlsx` on disk.
and rsyncs that xlsx plus its manifest. Nothing is deleted on either side and no board is renamed.

  python3 tools/v15_topup_push_boards.py --host s1-pub --symsides A_LONG,B_SHORT
  python3 tools/v15_topup_push_boards.py --host s2 --symsides-file /tmp/stk.txt --md5-map /tmp/need.json
"""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHEET_DIR = ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL"
REMOTE_DIR = "binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL"
_REV = re.compile(r"_r(\d+)_")


def _rev(name):
    m = _REV.search(name)
    return int(m.group(1)) if m else 0


def manifests_for(sym_side):
    out = []
    for p in SHEET_DIR.glob(f"{sym_side}_*_30d_matrix_manifest.json"):
        if not p.name.startswith(sym_side + "_"):
            continue
        try:
            d = json.load(open(p))
        except Exception:
            continue
        xlsx = SHEET_DIR / p.name.replace("_manifest.json", ".xlsx")
        if not xlsx.exists():
            continue
        out.append({"manifest": p, "xlsx": xlsx, "md5": str(d.get("overrides_md5") or ""),
                    "rev": _rev(p.name), "mtime": p.stat().st_mtime})
    out.sort(key=lambda r: (r["rev"], r["mtime"]))
    return out


def resolve(sym_side, want_md5=None):
    """-> (xlsx_path, manifest_path_or_None, how) or (None, None, why)."""
    mans = manifests_for(sym_side)
    if want_md5:
        hit = [m for m in mans if m["md5"] == want_md5]
        if hit:
            return hit[-1]["xlsx"], hit[-1]["manifest"], "overrides_md5 exact"
    if mans:
        return mans[-1]["xlsx"], mans[-1]["manifest"], "newest manifest (md5 not found locally)" if want_md5 else "newest manifest"
    boards = sorted(SHEET_DIR.glob(f"{sym_side}_*_30d_matrix.xlsx"), key=lambda p: (_rev(p.name), p.stat().st_mtime))
    boards = [b for b in boards if b.name.startswith(sym_side + "_")]
    if boards:
        return boards[-1], None, "newest xlsx (no manifest)"
    return None, None, "no board on the Mac either"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", required=True, help="rsync target, e.g. s1-pub or s2")
    ap.add_argument("--symsides", default="")
    ap.add_argument("--symsides-file", default="")
    ap.add_argument("--md5-map", default="", help='JSON {"SYM_SIDE": "overrides_md5"} — the set the host published')
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    sides = [s.strip() for s in a.symsides.replace("\n", ",").split(",") if s.strip()]
    if a.symsides_file:
        sides += [l.strip() for l in open(a.symsides_file) if l.strip() and not l.startswith("#")]
    sides = sorted(dict.fromkeys(sides))
    if not sides:
        print("[push-boards] no sym_sides given", file=sys.stderr)
        return 2
    want = {}
    if a.md5_map and os.path.exists(a.md5_map):
        try:
            want = {k: str(v) for k, v in json.load(open(a.md5_map)).items()}
        except Exception as e:
            print(f"[push-boards] md5 map unreadable ({e}) — falling back to newest board per sym_side")
    files, miss, how_n = [], [], {}
    for ss in sides:
        x, m, how = resolve(ss, want.get(ss))
        if not x:
            miss.append(f"{ss}: {how}")
            continue
        how_n[how] = how_n.get(how, 0) + 1
        files.append(x.name)
        if m:
            files.append(m.name)
        prog = SHEET_DIR.parent.parent / "data" / "reports" / "lifecycle_pilot" / f"{ss}_v14_progress.json"
        if prog.exists():
            files.append(f"../../data/reports/lifecycle_pilot/{prog.name}")
    boards = [f for f in files if not f.startswith("../")]
    print(f"[push-boards] {len(sides)} sym_sides -> {len(boards)} file(s) to {a.host}")
    for k, v in sorted(how_n.items(), key=lambda kv: -kv[1]):
        print(f"   {v:4d}  {k}")
    for m in miss:
        print(f"   MISS {m}")
    if a.dry_run or not boards:
        return 0
    lst = Path(os.environ.get("TMPDIR", "/tmp")) / f"topup_push_{a.host}_{os.getpid()}.txt"
    lst.write_text("\n".join(boards) + "\n")
    cmd = ["rsync", "-az", "--timeout=300", "--files-from", str(lst),
           "-e", "ssh -S none -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new",
           str(SHEET_DIR) + "/", f"{a.host}:{REMOTE_DIR}/"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    lst.unlink(missing_ok=True)
    if r.returncode != 0:
        print(f"[push-boards] rsync FAILED rc={r.returncode}: {(r.stderr or '')[:300]}", file=sys.stderr)
        return 1
    print(f"[push-boards] pushed {len(boards)} file(s) to {a.host}:{REMOTE_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
