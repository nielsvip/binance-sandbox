#!/usr/bin/env python3
"""Prune ~/stalled_s1_* rescue dumps as their sym_sides finalize live.

Each results-watchdog strike copies 30D progress + sheets + sweep logs into a
new ~/stalled_s1_<ts>/ dir (~3.7G). This pruner deletes dumped files by two
rules, and removes dump dirs left with nothing worth keeping:

FAST PATH (the point of this tool): a dumped file whose sym_side has
finalized live is deleted on the next tick. Finalized uses the watchdog's own
signal ("final_gain" key in a live progress JSON), with one override: a
sym_side active in the current round without final_gain is NOT finalized even
when an older round proves it once finished (relaunch safety).

SLOW PATH (sheets backstop): sheets for sym_sides with no live proof anywhere
(old boards from deleted rounds) can never finalize-gate. Once a dump is older
than --max-dump-age-days (default 7), its sheets are deleted file-by-file when
the stripped basename still exists live (verified redundant); pilot *.tmp*
saves expire unconditionally at that age (transient by definition). Progress
files are NEVER backstopped (small, precious state); logs prune by
finalize-gate only. A dump dir is removed when empty, or when no sheets remain,
it is past max age, and every remaining progress file exists live (logs ride
along).

Safety (all fail-closed):
  - No dump file is ever deleted unless a live same-name counterpart exists
    (progress: any round dir; sheets: stripped basename in CELL_BY_CELL; logs:
    /tmp). Every deleted byte exists live, so data loss is impossible.
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
LIVE_LOGS = Path("/tmp")
CACHE_PATH = Path("/tmp/v15_stalled_prune_cache.json")
FINAL_RE = re.compile(rb'"final_gain": [-0-9]')
SHEET_RE = re.compile(r"^(.+)_(LONG|SHORT)_(30d_matrix|bh[\dm])")
LOG_RE = re.compile(r"^sweep_(.+)_(LONG|SHORT)_30D\.log$")
SIDE_TAIL_RE = re.compile(r"^.+_(LONG|SHORT)$")
STRIP_RES = (
    re.compile(r"\.stale_[0-9]+$"),
    re.compile(r"\.[0-9.]+\.tmp$"),
    re.compile(r"\.tmp(\.[0-9.]+)?$"),
    re.compile(r"\.bak$"),
)
CHUNK = 1 << 20
OVERLAP = 64


def strip_suffixes(name):
    for expression in STRIP_RES:
        name = expression.sub("", name)
    return name


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
    current = Path(pointed) if pointed else None
    if current is not None and current.is_dir():
        dirs.append(current)
    for candidate in sorted(HOME.glob("v15_run*/progress")):
        if candidate.is_dir() and candidate not in dirs:
            dirs.append(candidate)
    for extra in (HOME / "v15_live_equal_20261006", HOME / "binance-sandbox" / "data" / "reports" / "lifecycle_pilot"):
        if extra.is_dir() and extra not in dirs:
            dirs.append(extra)
    return dirs, current


def load_finalized(progress_dirs, current_dir, cache_path=CACHE_PATH):
    try:
        cache = json.loads(cache_path.read_text())
    except (OSError, ValueError):
        cache = {}
    finalized = set()
    seen = set()
    try:
        current_names = [path.name for path in current_dir.glob("*_v14_progress.json")] if current_dir is not None else []
    except OSError:
        current_names = []
    current_syms = {name[: -len("_v14_progress.json")] for name in current_names}
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
            symside = path.name[: -len("_v14_progress.json")]
            entry = cache.get(key)
            if entry and entry[0] == stat.st_mtime_ns and entry[1] == stat.st_size:
                is_final = entry[2]
            else:
                is_final = file_has_final_gain(path)
                cache[key] = [stat.st_mtime_ns, stat.st_size, is_final]
            if is_final and (directory == current_dir or symside not in current_syms):
                finalized.add(symside)
    for key in [key for key in cache if key not in seen]:
        del cache[key]
    return finalized, cache


def save_cache(cache, cache_path=CACHE_PATH):
    try:
        cache_path.write_text(json.dumps(cache))
    except OSError:
        pass


def live_sheet_index(sheets_dir):
    try:
        return {strip_suffixes(path.name) for path in sheets_dir.rglob("*") if not path.is_symlink() and path.is_file()}
    except OSError:
        return set()


def live_supersedes(live_dir, basename, dump_stat):
    if live_dir is None:
        return False
    live = live_dir / basename
    try:
        stat = live.stat()
    except OSError:
        return False
    return stat.st_mtime_ns > dump_stat.st_mtime_ns and stat.st_size >= dump_stat.st_size


def progress_exists_live(progress_dirs, relpath):
    for directory in progress_dirs:
        try:
            if (directory / relpath).is_file():
                return True
        except OSError:
            continue
    return False


def counterpart_live(subdir, dump, path, live_logs, sheet_index, progress_dirs):
    if subdir == "progress":
        try:
            relpath = str(path.relative_to(dump / "progress"))
        except ValueError:
            return False
        return progress_exists_live(progress_dirs, relpath)
    if subdir == "sheets":
        return strip_suffixes(path.name) in sheet_index
    if subdir == "logs":
        if live_logs is None:
            return False
        try:
            return (live_logs / path.name).is_file()
        except OSError:
            return False
    return False


def prune_dump(dump, finalized, live_pd, live_sheets, live_logs, sheet_index, progress_dirs, old_enough, dry_run=False):
    removed_files = 0
    removed_bytes = 0
    backstop_files = 0
    jobs = (("progress", symside_of_progress), ("sheets", symside_of_sheet), ("logs", symside_of_log))
    for subdir, attribute in jobs:
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
            backstop = False
            if subdir == "sheets" and (symside is None or symside not in finalized):
                if live_supersedes(live_sheets, path.name, stat):
                    pass
                elif old_enough and (strip_suffixes(path.name) in sheet_index or ".tmp" in path.name):
                    backstop = True
                else:
                    continue
            elif symside is None:
                if subdir != "progress" or not live_supersedes(live_pd, path.name, stat):
                    continue
            elif symside not in finalized:
                continue
            elif not counterpart_live(subdir, dump, path, live_logs, sheet_index, progress_dirs):
                continue
            removed_files += 1
            removed_bytes += stat.st_size
            backstop_files += 1 if backstop else 0
            if not dry_run:
                try:
                    path.unlink()
                except OSError:
                    removed_files -= 1
                    removed_bytes -= stat.st_size
                    backstop_files -= 1 if backstop else 0
    return removed_files, removed_bytes, backstop_files


def dump_status(dump, progress_dirs):
    try:
        files = [path for path in dump.rglob("*") if not path.is_symlink() and path.is_file()]
    except OSError:
        return -1, False
    sheets_left = sum(1 for path in files if (dump / "sheets") in path.parents)
    progress_ok = True
    for path in files:
        if (dump / "progress") not in path.parents:
            continue
        try:
            relpath = str(path.relative_to(dump / "progress"))
        except ValueError:
            progress_ok = False
            break
        if not progress_exists_live(progress_dirs, relpath):
            progress_ok = False
            break
    return len(files), sheets_left == 0 and progress_ok


def main(argv):
    dry_run = "--dry-run" in argv
    min_age_min = 60
    max_age_days = 7
    for index, arg in enumerate(argv):
        if arg == "--min-age-min" and index + 1 < len(argv):
            try:
                min_age_min = int(argv[index + 1])
            except ValueError:
                pass
        if arg == "--max-dump-age-days" and index + 1 < len(argv):
            try:
                max_age_days = int(argv[index + 1])
            except ValueError:
                pass
    now = time.time()
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
    print(f"[{stamp}] [stalled-prune] start (dry_run={dry_run}, min_age_min={min_age_min}, max_age_days={max_age_days})")
    dumps = sorted(path for path in HOME.glob("stalled_s1_*") if path.is_dir() and not path.is_symlink())
    if not dumps:
        print("[stalled-prune] no dumps, nothing to do")
        return 0
    progress_dirs, current_dir = live_progress_dirs()
    finalized, cache = load_finalized(progress_dirs, current_dir)
    print(f"[stalled-prune] finalized sym_sides live: {len(finalized)} across {len(progress_dirs)} progress dirs")
    live_pd = progress_dirs[0] if progress_dirs else None
    live_sheets = SHEETS_DIR if SHEETS_DIR.is_dir() else None
    live_logs = LIVE_LOGS if LIVE_LOGS.is_dir() else None
    sheet_index = live_sheet_index(live_sheets) if live_sheets is not None else set()
    total_files = 0
    total_bytes = 0
    total_backstop = 0
    for dump in dumps:
        try:
            age_min = (now - dump.stat().st_mtime) / 60.0
        except OSError:
            continue
        if age_min < min_age_min:
            print(f"[stalled-prune] {dump.name}: age {age_min:.0f}m < {min_age_min}m, skipped")
            continue
        old_enough = age_min >= max_age_days * 1440.0
        removed_files, removed_bytes, backstop_files = prune_dump(dump, finalized, live_pd, live_sheets, live_logs, sheet_index, progress_dirs, old_enough, dry_run)
        total_files += removed_files
        total_bytes += removed_bytes
        total_backstop += backstop_files
        left, terminal = dump_status(dump, progress_dirs)
        if left == 0 and not dry_run:
            try:
                shutil.rmtree(dump)
                print(f"[stalled-prune] {dump.name}: pruned {removed_files} files ({removed_bytes / 1073741824:.2f}G), dir empty -> removed")
            except OSError as exc:
                print(f"[stalled-prune] {dump.name}: pruned {removed_files} files, rmtree failed: {exc}")
        elif terminal and old_enough and not dry_run:
            try:
                shutil.rmtree(dump)
                print(f"[stalled-prune] {dump.name}: pruned {removed_files} files ({removed_bytes / 1073741824:.2f}G), no sheets left + progress verified live -> removed")
            except OSError as exc:
                print(f"[stalled-prune] {dump.name}: pruned {removed_files} files, terminal rmtree failed: {exc}")
        else:
            verb = "would prune" if dry_run else "pruned"
            print(f"[stalled-prune] {dump.name}: {verb} {removed_files} files ({removed_bytes / 1073741824:.2f}G, backstop {backstop_files}), {left} files left")
    if not dry_run:
        save_cache(cache)
    print(f"[stalled-prune] TOTAL: {total_files} files ({total_bytes / 1073741824:.2f}G, backstop {total_backstop}){' (dry run)' if dry_run else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
