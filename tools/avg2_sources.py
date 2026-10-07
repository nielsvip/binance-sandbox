#!/usr/bin/env python3
"""avg2_sources: one place that says WHICH progress JSONs count as evidence for POS_SYM / n_sym / AVG_DELTA (EVID 2026-10-01).
Config: data/avg2_sources.json (copied to /tmp/avg2_sources.json on the hosts by the callers). Stdlib only (runs on the servers).
  python3 avg2_sources.py list [CONFIG]      -> 'path<TAB>progress|contaminated' for every evidence file present
  python3 avg2_sources.py classify PATH...   -> clean / contaminated / excluded per file"""
import calendar
import glob
import json
import os
import sys
import time

CRYPTO_SUFFIX = ("USDT_LONG", "USDC_LONG", "USDT_SHORT", "USDC_SHORT")


def load(path=None):
    cands = [path] if path else []
    cands += ["/tmp/avg2_sources.json", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "avg2_sources.json")]
    for c in cands:
        if c and os.path.exists(c):
            return json.load(open(c))
    raise SystemExit("avg2_sources.json not found")


def clean_epoch(cfg):
    t = time.strptime(cfg["clean_crypto_epoch"], "%Y-%m-%dT%H:%M:%SZ")
    return calendar.timegm(t)


def is_crypto(symside):
    return symside.endswith(CRYPTO_SUFFIX)


def list_files(cfg):
    """[(path, kind)] kind in progress|contaminated; a file reachable through both globs counts as contaminated."""
    cont = set()
    for g in cfg.get("contaminated_globs", []):
        cont.update(glob.glob(g))
    out = {}
    for g in cfg.get("progress_globs", []):
        for p in glob.glob(g):
            out[p] = "progress"
    for p in cont:
        out[p] = "contaminated"
    return sorted(out.items())


def is_clean(path, kind, mtime, cfg):
    """STOCKS always clean; CRYPTO clean only if written after the AUDIT/001 cutoff and not from a contaminated folder."""
    ss = os.path.basename(path)[: -len("_v14_progress.json")]
    if not is_crypto(ss):
        return True
    if kind == "contaminated":
        return False
    return mtime >= clean_epoch(cfg)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "list":
        cfg = load(sys.argv[2] if len(sys.argv) > 2 else None)
        for p, k in list_files(cfg):
            print(f"{p}\t{k}")
    elif cmd == "classify":
        cfg = load()
        kinds = dict(list_files(cfg))
        for p in sys.argv[2:]:
            k = kinds.get(p)
            if k is None:
                print(f"{p}\texcluded_or_unknown")
                continue
            print(f"{p}\t{'clean' if is_clean(p, k, os.path.getmtime(p), cfg) else 'contaminated'}")
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
