#!/usr/bin/env python3
"""S1 ext offload stager — keeps S1 >=40G free without S2, without deletions.

When free space drops below FREE_FLOOR_GB, MOVES (same-filesystem rename, no
extra space) cold timestamped archives into ~/ext_staging/<batch>/ with a
manifest. The Mac ingester pulls staging to ext drives, verifies, then removes
the S1 staged copies. Nothing is ever deleted on S1 by this script.

NEVER staged: klines_*, backtest_v8/indicators, data/reports, data/*.db,
SPREADSHEETS, .venv, ext_staging itself, anything with open file handles.
"""
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

FREE_FLOOR_GB = 40.0
TARGET_FREE_GB = 42.0
HOME = Path.home()
STAGE_ROOT = HOME / "ext_staging"
SANDBOX = HOME / "binance-sandbox"
GB = 1024 ** 3


def log(msg):
    print(f"{datetime.now(timezone.utc):%FT%TZ} [offload] {msg}", flush=True)


def free_gb():
    st = os.statvfs(str(HOME))
    return st.f_bavail * st.f_frsize / GB


def dir_open_files(path):
    try:
        r = subprocess.run(["lsof", "+D", str(path)], capture_output=True, text=True, timeout=120)
        return max(0, len(r.stdout.strip().splitlines()) - 1)
    except Exception:
        return -1


def dir_size(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += (Path(root) / f).stat().st_size
            except OSError:
                pass
    return total


CANDIDATE_DIRS = [
    "excluded_tokenized_stocks_archive_202610132015",
    "excluded_tokenized_stocks_archive_202610132037_pass3",
    "v15_run30_20261009",
    "v15_run32_20261011",
    "v15_run28_20261008",
    "v15_import_s5_run32_20261011",
    "canon_stage",
    "stkt_npz_out",
    "npz_backup_fundoi_202610010707",
    "npz_backup_pre_live_equal_20261006",
    "npz_backup_trunc_20261001b",
    "npz_rebuild_20261001",
    "binance-git-backup",
]

CANDIDATE_GLOBS = [
    ("v15_run*_2026*", 1),
    ("npz_backup_*", 0),
    ("excluded_tokenized_*", 0),
]


def _live_run_dirs():
    """2026-10-17: the CURRENT production run dir (~/v15_current_progress_dir.txt -> <run>/progress) is never cold.
    Measured: this stager moved ~/v15_run32_20261011 (the live progress dir) to ext_staging 5x on 2026-10-17
    (11:20, 13:10, 14:50, 15:00, 15:40Z) whenever lsof caught no open handle between writes — every progress JSON
    left S1, boards lost their resume/done state and the scheduler re-dispatched sides it had just finished."""
    out = set()
    try:
        pd = Path((HOME / "v15_current_progress_dir.txt").read_text().strip())
        if str(pd):
            out.add(pd.resolve())
            out.add(pd.resolve().parent)
    except Exception:
        pass
    return out


def collect_candidates():
    found = []
    live = _live_run_dirs()
    for name in CANDIDATE_DIRS:
        p = HOME / name
        if p.is_dir() and not (STAGE_ROOT in p.parents):
            found.append(p)
    for pattern, _keep in CANDIDATE_GLOBS:
        for p in sorted(HOME.glob(pattern)):
            if p.is_dir() and p not in found and STAGE_ROOT not in p.parents:
                found.append(p)
    if not live:
        # fail closed: without the pointer we cannot tell which run is live -> never stage any run dir
        return [p for p in found if not p.name.startswith("v15_run")]
    return [p for p in found if p.resolve() not in live]


def main():
    STAGE_ROOT.mkdir(exist_ok=True)
    free = free_gb()
    staged_now = sum(dir_size(d) for d in STAGE_ROOT.iterdir() if d.is_dir()) / GB if any(STAGE_ROOT.iterdir()) else 0.0
    log(f"free={free:.1f}G floor={FREE_FLOOR_GB:.0f}G already_staged={staged_now:.1f}G")
    if free >= FREE_FLOOR_GB:
        log("above floor — nothing to stage")
        return 0
    need = (TARGET_FREE_GB - free) * GB
    batch = STAGE_ROOT / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    manifest = []
    moved_bytes = 0
    for src in sorted(collect_candidates(), key=dir_size, reverse=True):
        if moved_bytes >= need:
            break
        if dir_open_files(src) != 0:
            log(f"skip {src.name}: open handles or lsof failed")
            continue
        size = dir_size(src)
        if size <= 0:
            continue
        batch.mkdir(exist_ok=True)
        dest = batch / src.name
        try:
            shutil.move(str(src), str(dest))
        except Exception as e:
            log(f"FAIL move {src.name}: {e}")
            continue
        manifest.append({"name": src.name, "bytes": size, "src": str(src)})
        moved_bytes += size
        log(f"staged {src.name} {size / GB:.2f}G")
    if manifest:
        (batch / "manifest.json").write_text(json.dumps({"created_utc": batch.name, "files": manifest, "total_bytes": moved_bytes}, indent=1))
    log(f"batch={batch.name if manifest else 'none'} moved={moved_bytes / GB:.2f}G free_now={free_gb():.1f}G")
    if free_gb() < FREE_FLOOR_GB:
        log("STILL below floor after staging all cold candidates — needs human triage (db writers?)")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
