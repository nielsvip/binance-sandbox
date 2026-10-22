#!/usr/bin/env python3
"""v15_must_merge — union per-host must_pos_<CS>.json files into one index.

Each host builds its own index from local delta logs
(tools/v15_v18_must_index.py --out-dir /tmp/must_local); this tool merges
them on s1: union of item keys, syms union, best = max delta. Inputs are
small JSONs (no bulk log copies). Overwrites --out-dir atomically.

Usage:
  python3 tools/v15_must_merge.py --inputs s1=data/learn s5=/tmp/must_s5 ...
"""
import argparse
import datetime
import json
import os
import pathlib

CAT_SIDES = ["CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT"]


def load_items(path):
    try:
        return json.loads(pathlib.Path(path).read_text()).get("items") or {}
    except Exception:
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inputs", nargs="+", required=True,
                    help="host=dir entries holding must_pos_<CS>.json")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--backup-dir", default="")
    a = ap.parse_args()
    host_dirs = {}
    for entry in a.inputs:
        host, d = entry.split("=", 1)
        host_dirs[host] = d
    out = pathlib.Path(a.out_dir)
    if a.backup_dir:
        bdir = pathlib.Path(a.backup_dir)
        bdir.mkdir(parents=True, exist_ok=True)
        for cs in CAT_SIDES:
            src = out / f"must_pos_{cs}.json"
            if src.exists():
                (bdir / f"must_pos_{cs}.json").write_text(src.read_text())
    merged = {cs: {} for cs in CAT_SIDES}
    for host, d in host_dirs.items():
        for cs in CAT_SIDES:
            for k, v in load_items(os.path.join(d, f"must_pos_{cs}.json")).items():
                e = merged[cs].setdefault(k, {"syms": [], "best": None})
                for ss in v.get("syms") or []:
                    if ss not in e["syms"]:
                        e["syms"].append(ss)
                b = v.get("best")
                if b is not None and (e["best"] is None or float(b) > e["best"]):
                    e["best"] = round(float(b), 6)
    at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for cs in CAT_SIDES:
        tmp = out / f"must_pos_{cs}.json.tmp"
        tmp.write_text(json.dumps({"meta": {"cat_side": cs, "built_at": at,
                                            "n_items": len(merged[cs]),
                                            "source": "merged host delta-log indexes"},
                                   "items": merged[cs]}))
        os.replace(tmp, out / f"must_pos_{cs}.json")
    print(json.dumps({"merged": {cs: len(merged[cs]) for cs in CAT_SIDES}}))


if __name__ == "__main__":
    main()
