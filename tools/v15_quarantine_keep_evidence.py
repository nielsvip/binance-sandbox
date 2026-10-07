#!/usr/bin/env python3
"""Quarantine progress files WITHOUT dropping their evidence (EVID 2026-10-01).
Moves <run_dir>/progress/<ss>_v14_progress.json (+ chain/v365/<ss>_365_cycle.json) into <run_dir>/contaminated_<label>/{progress,chain}.
The folder name convention contaminated_* is picked up by data/avg2_sources.json (contaminated_globs): the files keep counting for POS_SYM / n_sym and
are only used for AVG_DELTA as a flagged fallback until a clean result exists. Writes <folder>/REGISTRY.json and (when run next to the repo) appends to data/avg2_sources.json registry.
Files that are INVALID evidence (garbage bars, zeroed baselines) must NOT use this tool: move them to a quarantine_* folder instead (excluded).
usage: v15_quarantine_keep_evidence.py --run-dir ~/v15_run20_20261001 --label crypto_lookahead_20261001 --reason TEXT [--only crypto|stocks|SYMSIDE,SYMSIDE] [--execute]"""
import argparse
import datetime
import glob
import json
import os
import shutil
import sys

CRYPTO = ("USDT_LONG", "USDC_LONG", "USDT_SHORT", "USDC_SHORT")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--reason", required=True)
    ap.add_argument("--only", default="all", help="all | crypto | stocks | comma list of sym_sides")
    ap.add_argument("--execute", action="store_true")
    a = ap.parse_args()
    run = os.path.expanduser(a.run_dir)
    dest = os.path.join(run, "contaminated_" + a.label)
    sel = []
    for p in sorted(glob.glob(os.path.join(run, "progress", "*_v14_progress.json"))):
        ss = os.path.basename(p)[: -len("_v14_progress.json")]
        c = ss.endswith(CRYPTO)
        if a.only == "all" or (a.only == "crypto" and c) or (a.only == "stocks" and not c) or ss in a.only.split(","):
            sel.append(ss)
    print(f"{'EXECUTE' if a.execute else 'DRY-RUN'} {len(sel)} sym_sides -> {dest}")
    if not a.execute:
        return
    os.makedirs(os.path.join(dest, "progress"), exist_ok=True)
    os.makedirs(os.path.join(dest, "chain"), exist_ok=True)
    moved = {"progress": 0, "chain": 0}
    for ss in sel:
        src = os.path.join(run, "progress", f"{ss}_v14_progress.json")
        shutil.move(src, os.path.join(dest, "progress", os.path.basename(src))); moved["progress"] += 1
        ch = os.path.join(run, "chain", "v365", f"{ss}_365_cycle.json")
        if os.path.exists(ch):
            shutil.move(ch, os.path.join(dest, "chain", os.path.basename(ch))); moved["chain"] += 1
    entry = {"at": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%MZ"), "what": a.reason, "folder": dest, "moved": moved, "include_pos_sym": True, "include_n_sym": True, "avg_delta": "fallback only, flagged contaminated"}
    json.dump(entry, open(os.path.join(dest, "REGISTRY.json"), "w"), indent=1)
    cfgp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "avg2_sources.json")
    if os.path.exists(cfgp):
        cfg = json.load(open(cfgp)); cfg.setdefault("registry", []).append(entry)
        tmp = cfgp + ".tmp"; json.dump(cfg, open(tmp, "w"), indent=1); os.replace(tmp, cfgp)
    print("moved", moved, "registered", dest)


if __name__ == "__main__":
    main()
