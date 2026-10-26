#!/usr/bin/env python3
"""Collect the fleet's 7D results and stage only sides with 09:15 replay proof.

Called by v15_fleet_scheduler once per market day. Staging never changes an
active account or the ordinary 30D store.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.v15_preopen_7d_stage import stage  # noqa: E402
from tools.v15_best_first import tradeable  # noqa: E402


def _atomic(path: Path, doc: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, sort_keys=True, indent=2) + "\n")
    os.replace(tmp, path)


def collect(day: str, hosts_path: Path, root: Path = ROOT) -> dict:
    folder = root / "data/preopen_7d/fleet" / day
    state = json.loads((folder / "state.json").read_text())
    source = json.loads((folder / "queue.json").read_text())
    hosts = {h["name"]: h for h in json.loads(hosts_path.read_text())["hosts"]}
    eligible = tradeable(root)
    accepted = {ss: row for ss, row in state["placements"].items()
                if row.get("status") == "valid" and row.get("replay_status") == "valid"
                and ss in eligible}
    if not accepted:
        raise ValueError("no side has both optimized and 09:15 replay proof")
    gathered = folder / "gathered"
    gathered.mkdir(parents=True, exist_ok=True)

    def pull(ss: str, row: dict):
        host = hosts[row["host"]]
        target = host.get("_via") or host["ssh"][0]
        remote = f"{target}:~/binance-sandbox/data/preopen_7d/fleet/{day}/results/{ss}/"
        dest = gathered / ss
        dest.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(["rsync", "-az", "--timeout=60", "-e",
                                 "ssh -o BatchMode=yes -o ConnectTimeout=5 -o ControlMaster=no -o ControlPath=none",
                                 remote, str(dest) + "/"], capture_output=True, text=True, timeout=75)
        if result.returncode:
            raise RuntimeError(f"{ss} from {row['host']}: {result.stderr[-200:]}")
        replay = json.loads((dest / "replay.json").read_text())
        if replay != row.get("replay"):
            raise ValueError(f"{ss}: replay changed during collection")
        return ss

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(pull, ss, row): ss for ss, row in accepted.items()}
        for future in as_completed(futures):
            future.result()
    subset = {"_meta": dict(source["_meta"]),
              "accounts": {a: sorted(set(sides) & set(accepted)) for a, sides in source["accounts"].items()},
              "sym_sides": {ss: source["sym_sides"][ss] for ss in sorted(accepted)}}
    subset["_meta"].update({"n": len(accepted), "require_replay": True,
                             "excluded_terminal": sorted(set(source["sym_sides"]) - set(accepted))})
    queue_path = folder / "stage_queue.json"
    _atomic(queue_path, subset)
    stock_symbols = sorted(set(json.loads((root / "symbols_trb_long.json").read_text()))
                           | set(json.loads((root / "symbols_trb_short.json").read_text())))
    stock_path = folder / "stock_symbols.json"
    _atomic(stock_path, stock_symbols)
    report = stage(queue_path, gathered,
                   root / "data/hourly_reconfig/per_sym_store.db",
                   root / "data/vec_live/vec_driven_sets.json", stock_path,
                   folder / "stage", dt.datetime.now(dt.timezone.utc).date().isoformat(),
                   root / "data/published_selection.json")
    outcome = {"day": day, "stage_only": True, "accepted": len(accepted),
               "excluded": subset["_meta"]["excluded_terminal"], "stage": report}
    _atomic(folder / "collection.json", outcome)
    return outcome


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", required=True)
    ap.add_argument("--hosts", type=Path, required=True)
    args = ap.parse_args()
    print(json.dumps(collect(args.day, args.hosts)))


if __name__ == "__main__":
    main()
