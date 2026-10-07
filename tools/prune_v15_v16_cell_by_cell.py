#!/usr/bin/env python3
"""
PRUNE V15_V16_CELL_BY_CELL - KEEP ONLY LATEST VERSION PER SYM_SIDE

Permanent auto-prune: as soon as a newer version of same SYM_SIDE arrives,
older versions are deleted. Runs on S1 FIRST, then Mac, so Mac does not
re-pull stale versions.

SYM_SIDE = SYMBOL + "_" + SIDE (LONG/SHORT), ignoring _bh*_gain* suffix
and timestamp/pilot suffixes after _30d_matrix.

Keeps 1 file per group (newest mtime). Deletes all others.
Also removes _superseded dir and temp dotfiles.
"""
import re, sys
from pathlib import Path
from collections import defaultdict

def base_key(name: str):
    if "30d_matrix" not in name:
        return None
    prefix = name.split("_30d_matrix")[0]
    # strip _bh... suffix (e.g. _bh20p55_gain10p76, _bhm20p55_gainm2p63)
    prefix = re.sub(r"_bh.*$", "", prefix)
    return prefix

def prune_dir(dir_path: Path, dry_run: bool = False) -> tuple:
    if not dir_path.exists():
        return (0, 0)
    files = [p for p in dir_path.iterdir()
             if p.is_file() and not p.name.startswith(".")
             and "30d_matrix" in p.name and p.suffix == ".xlsx"]
    if not files:
        return (0, 0)
    groups = defaultdict(list)
    for f in files:
        k = base_key(f.name)
        if k:
            groups[k].append(f)
    to_delete = []
    to_keep = []
    for k, lst in groups.items():
        lst_sorted = sorted(lst, key=lambda p: (p.stat().st_mtime, p.stat().st_size, p.name), reverse=True)
        to_keep.append(lst_sorted[0])
        to_delete.extend(lst_sorted[1:])
    if dry_run:
        return (len(to_keep), len(to_delete))
    deleted = 0
    for p in to_delete:
        try:
            p.unlink()
            deleted += 1
        except Exception:
            pass
    # cleanup temp dotfiles and _superseded
    for p in list(dir_path.iterdir()):
        if p.name.startswith(".") and p.is_file():
            try:
                if "matrix" in p.name or p.suffix in (".xlsx", ".LBFC4W", ".tmp"):
                    p.unlink()
            except:
                pass
    # remove _superseded dir if it somehow reappears
    superseded = dir_path / "_superseded"
    if superseded.exists():
        import shutil
        try:
            shutil.rmtree(str(superseded))
        except:
            pass
    # also remove quarantine
    q = dir_path / ".quarantine_15h_20260914"
    if q.exists():
        import shutil
        try:
            shutil.rmtree(str(q))
        except:
            pass
    return (len(to_keep), deleted)

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", default=None)
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()
    # auto-detect path
    if args.path:
        target = Path(args.path)
    else:
        # try S1 path first, then Mac path
        candidates = [
            Path("/home/niels/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL"),
            Path("/Users/niels/Documents/binance/SPREADSHEETS/V15_V16_CELL_BY_CELL"),
            Path(__file__).resolve().parents[1] / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL",
        ]
        target = next((p for p in candidates if p.exists()), candidates[1])
    keep, deleted = prune_dir(target, dry_run=args.dry)
    if args.dry:
        print(f"[prune] DRY {target}: keep {keep} delete {deleted}")
    else:
        if deleted > 0:
            print(f"[prune] {target}: kept {keep}, deleted {deleted}")
