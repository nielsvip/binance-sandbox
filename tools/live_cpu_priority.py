#!/usr/bin/env python3
"""Reserve CPU for closed-bar crypto replay near each UTC 15-minute boundary.

Research and maintenance processes keep running. Their normal affinity is
restored after the bar window. This never changes trade settings or orders.
"""
import argparse
import datetime
import json
import os
from pathlib import Path

RESEARCH = (
    "v15_pilot.py",
    "npz_bar_refresh.py",
    "klines_append.py",
    "klines_guard.py",
    "klines_truncation_guard.py",
    "v15_progress_board.py",
    "klines_vault.py",
)


def processes():
    found = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            args = [part.decode(errors="replace") for part in (entry / "cmdline").read_bytes().split(b"\0") if part]
            if not args:
                continue
            stat = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            found[int(entry.name)] = (int(stat[1]), args)
        except (OSError, ValueError, IndexError):
            continue
    return found


def kind(args, role):
    if not args or "python" not in Path(args[0]).name:
        return None
    scripts = {Path(arg).name for arg in args[1:] if arg.endswith(".py")}
    if "npz_tail_service.py" in scripts and role == "s1" and "crypto" in args:
        return "tail"
    if "vec_bar_trigger.py" in scripts and "--shard" in args:
        shard = args[args.index("--shard") + 1]
        if shard == ("crypto_short" if role == "s1" else "crypto_long"):
            return "trigger"
    if scripts.intersection(RESEARCH):
        return "research"
    return None


def apply(role, dry_run=False):
    minute = datetime.datetime.now(datetime.timezone.utc).minute
    burst = minute % 15 in (14, 0, 1, 2)
    all_cores = set(range(os.cpu_count() or 16))
    research_cores = set(range(4 if role == "s1" else 8)) if burst else all_cores
    found = processes()
    kinds = {pid: kind(args, role) for pid, (_, args) in found.items()}
    trigger_roots = {pid for pid, label in kinds.items() if label == "trigger"}
    trigger_children = set()
    frontier = set(trigger_roots)
    while frontier:
        children = {pid for pid, (ppid, _) in found.items() if ppid in frontier and pid not in trigger_children}
        trigger_children.update(children)
        frontier = children
    counts = {"research": 0, "tail": 0, "trigger": 0, "errors": 0}
    for pid, label in kinds.items():
        if label is None and pid in trigger_children:
            label = "trigger"
        if label is None:
            continue
        try:
            if not dry_run:
                os.sched_setaffinity(pid, research_cores if label == "research" else all_cores)
                if label in ("tail", "trigger"):
                    os.setpriority(os.PRIO_PROCESS, pid, -15 if label == "tail" else -10)
            counts[label] += 1
        except (OSError, PermissionError):
            counts["errors"] += 1
    return {"role": role, "minute": minute, "burst": burst, "counts": counts}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=("s1", "s2"), required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(apply(args.role, args.dry_run), sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
