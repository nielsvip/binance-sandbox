"""Cell-level pos_sym evidence: aggregate per-yellow deltas from progress JSONs.

Row evidence (data/avg_delta_pos_sym.json) has no per-cell keys, so the pilot's
yellow sampling falls back to the filter's orange-row proxy. This tool builds
the missing cell map: TAB!SWITCH=cand@HEADER -> {pos_sym, n_sym, avg_delta},
counted over sym_sides with a REAL numeric evaluated delta (None skipped).

Usage: python3 tools/v15_cell_evidence.py [progress_dir] [out_json]
progress_dir may be comma-separated (union, newest file wins per sym_side).
"""
import glob
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

CRYPTO_SUFFIX = ("USDT", "USDC", "USD1", "BUSD", "FDUSD", "TUSD", "DAI")


def cat_of(symside):
    s = (symside or "").upper()
    side = "SHORT" if s.endswith("_SHORT") else "LONG"
    base = s[: -len("_" + side)] if s.endswith("_" + side) else s
    return ("CRYPTO_" if base.endswith(CRYPTO_SUFFIX) else "STOCKS_") + side


def main():
    pdir = sys.argv[1] if len(sys.argv) > 1 else "data/reports/lifecycle_pilot"
    out = sys.argv[2] if len(sys.argv) > 2 else "data/avg_delta_pos_sym_cell.json"
    pdirs = [p.strip() for p in str(pdir).split(",") if p.strip()]
    cand = [f for p in pdirs for f in glob.glob(os.path.join(p, "*_progress.json"))]
    files, _seen = [], set()
    for f in sorted(cand, key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0, reverse=True):
        ss0 = os.path.basename(f).replace("_v14_progress.json", "")
        if ss0 in _seen:
            continue
        _seen.add(ss0)
        files.append(f)
    files.sort()
    agg = defaultdict(lambda: [set(), 0, 0.0])
    nfiles = 0
    for f in files:
        try:
            d = json.load(open(f))
        except Exception:
            continue
        nfiles += 1
        ss = d.get("symside") or os.path.basename(f).replace("_v14_progress.json", "")
        cat = cat_of(ss)
        for k, v in (d.get("done") or {}).items():
            if not isinstance(v, dict):
                continue
            try:
                tab, rest = str(k).split("!", 1)
                rkey = rest.split(":", 1)[1]
            except Exception:
                continue
            base = f"{cat}\t{tab}!{rkey}"
            for h, dt in (v.get("yellows") or {}).items():
                if not isinstance(dt, (int, float)) or isinstance(dt, bool):
                    continue
                e = agg[base + "@" + str(h).strip()]
                e[0].add(ss)
                if dt > 1e-9:
                    e[1] += 1
                e[2] += float(dt)
    cats = {}
    for ck, (syms, pos, total) in agg.items():
        n = len(syms)
        if n < 3:
            continue
        cat, cell = ck.split("\t", 1)
        cats.setdefault(cat, {})[cell] = {"pos_sym": pos, "n_sym": n, "avg_delta": total / n}
    payload = {"meta": {"at": datetime.now(timezone.utc).isoformat(), "source": pdir, "files": nfiles, "cells": sum(len(v) for v in cats.values())}, "cat_sides": cats}
    tmp = out + ".tmp"
    json.dump(payload, open(tmp, "w"))
    os.replace(tmp, out)
    print(f"files={nfiles} cells={payload['meta']['cells']} -> {out}")
    rdir = os.path.join(os.path.dirname(out) or ".", "cell_evidence")
    os.makedirs(rdir, exist_ok=True)
    for _cat, _cells in cats.items():
        _pruned = {k: {"pos_sym": 0, "n_sym": v["n_sym"], "avg_delta": round(v["avg_delta"], 6)} for k, v in _cells.items() if v["pos_sym"] == 0 and v["n_sym"] >= 10}
        _rp = os.path.join(rdir, f"{_cat}.json")
        json.dump({"meta": payload["meta"], "cells": _pruned}, open(_rp + ".tmp", "w"))
        os.replace(_rp + ".tmp", _rp)
        print(f"  runtime {_cat}: {len(_pruned)} condemned cells -> {_rp} ({os.path.getsize(_rp) // 1024}KB)")
    for cat, cells in sorted(cats.items()):
        import collections as _c
        t = _c.Counter()
        for v in cells.values():
            if v["pos_sym"] == 0:
                t["pos0"] += 1
                for th in (5, 10, 15, 20):
                    if v["n_sym"] >= th:
                        t[f"pos0_n{th}"] += 1
        print(cat, "ncells:", len(cells), dict(t))


if __name__ == "__main__":
    main()
