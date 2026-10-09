#!/usr/bin/env python3
"""Prune ~/stalled_s1_* rescue dumps as their sym_sides finalize live.

Each results-watchdog strike copies 30D progress + sheets + sweep logs into a
new ~/stalled_s1_<ts>/ dir (~3.7G). This pruner deletes dumped files whose
sym_side has finalized (live progress JSON carries "final_gain", the exact
signal the watchdog itself counts), and removes dump dirs left empty.

Safety (all fail-closed):
  - A sym_side counts as finalized ONLY when a live progress file proves it.
    Missing/unreadable live file -> keep the dumped copy (rescue purpose).
  - Files no sym_side can be attributed to are deleted only when a live
    same-name file exists that is both newer and not smaller (superseded).
  - Dumps younger than --min-age-min (default 60) are never touched.
  - Nothing is ever deleted under --dry-run (also skips the mtime cache save).
"""
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

HOME = Path.home()
POINTER = HOME / "v15_current_progress_dir.txt"
SHEETS_DIR = HOME / "binance-sandbox" / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL"
CACHE_PATH = Path("/tmp/v15_stalled_prune_cache.json")
FINAL_RE = re.compile(rb'"final_gain": [-0-9]')
SHEET_RE = re.compile(r"^(.+)_(LONG|SHORT)_(30d_matrix|bh[\dm])")
LOG_RE = re.compile(r"^sweep_(.+)_(LONG|SHORT)_30D\.log$")
SIDE_TAIL_RE = re.compile(r"^.+_(LONG|SHORT)$")
CHUNK = 1 << 20
OVERLAP = 64


def symside_of_progress(name):
    for suffix in ("_v14_progress.json", "_jump.jsonl", ".jsonl", ".json"):
        if name.endswith(suffix):
            candidate = name[: -len(suffix)]
            return candidate if SIDE_TAIL_RE.match(candidate) else None
    return None


def symside_of_sheet(name):
    match = SHEET_RE.match(name)
    if not match:
        return None
    return f"{match.group(1)}_{match.group(2)}"


def symside_of_log(name):
    match = LOG_RE.match(name)
    if not match:
        return None
    return f"{match.group(1)}_{match.group(2)}"


def file_has_final_gain(path, chunk_size=CHUNK):
    try:
        with open(path, "rb") as handle:
            tail = b""
            while True:
                block = handle.read(chunk_size)
                if not block:
                    return False
                if FINAL_RE.search(tail + block):
                    return True
                tail = (tail + block)[-OVERLAP:]
    except OSError:
        return False


def live_progress_dirs():
    dirs = []
    try:
        pointed = POINTER.read_text().strip()
    except OSError:
        pointed = ""
    if pointed:
        candidate = Path(pointed)
        if candidate.is_dir():
            dirs.append(candidate)
    for candidate in sorted(HOME.glob("v15_run*/progress")):
        if candidate.is_dir() and candidate not in dirs:
            dirs.append(candidate)
    legacy = HOME / "v15_live_equal_20261006"
    if legacy.is_dir() and legacy not in dirs:
        dirs.append(legacy)
    return dirs


def load_finalized(progress_dirs, cache_path=CACHE_PATH):
    try:
        cache = json.loads(cache_path.read_text())
    except (OSError, ValueError):
        cache = {}
    finalized = set()
    seen = set()
    for directory in progress_dirs:
        try:
            files = sorted(directory.glob("*_v14_progress.json"))
        except OSError:
            continue
        for path in files:
            try:
                stat = path.stat()
            except OSError:
                continue
            key = str(path)
            seen.add(key)
            entry = cache.get(key)
            if entry and entry[0] == stat.st_mtime_ns and entry[1] == stat.st_size:
                is_final = entry[2]
            else:
                is_final = file_has_final_gain(path)
                cache[key] = [stat.st_mtime_ns, stat.st_size, is_final]
            if is_final:
                finalized.add(path.name[: -len("_v14_progress.json")])
    for key in [key for key in cache if key not in seen]:
        del cache[key]
    return finalized, cache


def save_cache(cache, cache_path=CACHE_PATH):
    try:
        cache_path.write_text(json.dumps(cache))
    except OSError:
        pass


def live_supersedes(live_dir, basename, dump_stat):
    if live_dir is None:
        return False
    live = live_dir / basename
    try:
        stat = live.stat()
    except OSError:
        return False
    return stat.st_mtime_ns > dump_stat.st_mtime_ns and stat.st_size >= dump_stat.st_size


def prune_dump(dump, finalized, live_pd, live_sheets, dry_run=False):
    removed_files = 0
    removed_bytes = 0
    tasks = (("progress", symside_of_progress, live_pd), ("sheets", symside_of_sheet, live_sheets), ("logs", symside_of_log, None))
    for subdir, attribute, live_dir in tasks:
        root = dump / subdir
        if not root.is_dir() or root.is_symlink():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            symside = attribute(path.name)
            if symside is not None:
                if symside not in finalized:
                    continue
            elif not live_supersedes(live_dir, path.name, stat):
                continue
            removed_files += 1
            removed_bytes += stat.st_size
            if not dry_run:
                try:
                    path.unlink()
                except OSError:
                    removed_files -= 1
                    removed_bytes -= stat.st_size
    return removed_files, removed_bytes


def dump_file_count(dump):
    try:
        return sum(1 for path in dump.rglob("*") if not path.is_symlink() and path.is_file())
    except OSError:
        return -1


def main(argv):
    dry_run = "--dry-run" in argv
    min_age_min = 60
    for index, arg in enumerate(argv):
        if arg == "--min-age-min" and index + 1 < len(argv):
            try:
                min_age_min = int(argv[index + 1])
            except ValueError:
                pass
    now = time.time()
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
    print(f"[{stamp}] [stalled-prune] start (dry_run={dry_run}, min_age_min={min_age_min})")
    dumps = sorted(path for path in HOME.glob("stalled_s1_*") if path.is_dir() and not path.is_symlink())
    if not dumps:
        print("[stalled-prune] no dumps, nothing to do")
        return 0
    progress_dirs = live_progress_dirs()
    finalized, cache = load_finalized(progress_dirs)
    print(f"[stalled-prune] finalized sym_sides live: {len(finalized)} across {len(progress_dirs)} progress dirs")
    live_pd = progress_dirs[0] if progress_dirs else None
    live_sheets = SHEETS_DIR if SHEETS_DIR.is_dir() else None
    total_files = 0
    total_bytes = 0
    for dump in dumps:
        try:
            age_min = (now - dump.stat().st_mtime) / 60.0
        except OSError:
            continue
        if age_min < min_age_min:
            print(f"[stalled-prune] {dump.name}: age {age_min:.0f}m < {min_age_min}m, skipped")
            continue
        removed_files, removed_bytes = prune_dump(dump, finalized, live_pd, live_sheets, dry_run)
        total_files += removed_files
        total_bytes += removed_bytes
        left = dump_file_count(dump)
        if left == 0 and not dry_run:
            try:
                shutil.rmtree(dump)
                print(f"[stalled-prune] {dump.name}: pruned {removed_files} files ({removed_bytes / 1073741824:.2f}G), dir empty -> removed")
            except OSError as exc:
                print(f"[stalled-prune] {dump.name}: pruned {removed_files} files, rmtree failed: {exc}")
        else:
            verb = "would prune" if dry_run else "pruned"
            print(f"[stalled-prune] {dump.name}: {verb} {removed_files} files ({removed_bytes / 1073741824:.2f}G), {left} files left")
    if not dry_run:
        save_cache(cache)
    print(f"[stalled-prune] TOTAL: {total_files} files ({total_bytes / 1073741824:.2f}G){' (dry run)' if dry_run else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
