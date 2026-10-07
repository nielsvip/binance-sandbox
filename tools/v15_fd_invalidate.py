#!/usr/bin/env python3
"""v15_fd_invalidate — engine changed: drop STALE filter-discovery results so the incremental supervisor recomputes them (Agent Y).
  --filters F1,F2 | --filters-file J.json   drop only results of cells whose FILTER name is listed (plus nothing else: naked/baseline results stay)
  --all                                     archive the whole fd dir (results of the old engine are kept as legacy under <dir>_<oldmd5>) and start fresh
Selective mode is only correct when the changed engine code affects just those filters (baselines/naked rows unchanged) — the wiring agent must list them
(data/engine_deploy/changed_filters.json: {"filters": [...]}); otherwise use --all."""
import argparse, glob, json, os, shutil, sys
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--dir", required=True)
ap.add_argument("--filters")
ap.add_argument("--filters-file")
ap.add_argument("--all", action="store_true")
ap.add_argument("--tag", default="old")
a = ap.parse_args()
d = Path(a.dir)
if a.all:
    dst = d.parent / f"{d.name}_{a.tag}"
    shutil.move(str(d), str(dst))
    d.mkdir(parents=True)
    print(f"[invalidate] archived {d} -> {dst}")
    sys.exit(0)
names = set(x.strip() for x in (a.filters or "").split(",") if x.strip())
if a.filters_file:
    j = json.load(open(a.filters_file))
    names |= set(j.get("filters", j) if isinstance(j, dict) else j)
dropped = 0
for pf in glob.glob(str(d / "plan_*.json")):
    pl = json.load(open(pf))
    bad = set()
    for h, ck in (pl.get("A") or {}).items():
        if h.split("=", 1)[0].strip() in names:
            bad.add(ck)
    for sect in ("B", "F"):
        for key, ck in (pl.get(sect) or {}).items():
            if key.split("\t")[1].split("=", 1)[0].strip() in names:
                bad.add(ck)
    if not bad:
        continue
    rf = pf.replace("plan_", "results_").replace(".json", ".jsonl")
    if not os.path.exists(rf):
        continue
    keep = []
    for l in open(rf):
        try:
            if json.loads(l)["ck"] in bad:
                dropped += 1
                continue
        except Exception:
            continue
        keep.append(l)
    tmp = rf + ".tmp"
    open(tmp, "w").writelines(keep)
    os.replace(tmp, rf)
print(f"[invalidate] dropped {dropped} result lines for filters {sorted(names)[:8]}...")
