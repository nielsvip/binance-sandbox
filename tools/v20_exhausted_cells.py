#!/usr/bin/env python3
"""V20 EXHAUSTED-CELL MAP — which (tab, switch=cand, filter=opt) cells have been measured so often, on so many sym_sides, with
so few positives, that a further evaluation is not expected to find a delta (USER 2026-10-27: "reduce their testing frequency by
a lot ... without losing reliability or deltas").

Reads the brain spills (`v18_brain.py extract` output, `spill_*.tsv.gz`, the same corpus V20 trains on) and writes one file per
cat_side: `exhausted_cells_<CS>.json` (contract `exhausted_cells_v1`). Read-only on the corpus; never deletes anything.

Only evaluations whose row naked was NOT positive count: on a positive naked the pilot re-runs every skipped cell on the spot
(PRIOR-REVIVED), so there the throttle can neither save nor lose anything, and the cell's positive delta is inherited from the
naked rather than earned by the filter. A "positive" is therefore a RESCUE: cell delta > 1e-9 while the naked was <= 1e-9.
A cell is EXHAUSTED when, over those fresh (non-cached) evaluations:
  n >= --n-min  AND  distinct sym_sides >= --s-min  AND  rescues <= --max-pos  (default 20 / 5 / 0: one rescue re-opens it).
The map alone is NOT a skip list. The selector (`tools/v15_v20_selector.py`, flag V20_EXHAUSTED) throttles a mapped cell only
while its filter has also been silent on the running board itself (>= V20_EXH_BOARD_K = 20 non-positive-naked evals, no rescue,
read from the board's own delta log), and then keeps it with probability `p_recheck` (default 0.25), never below the floor.
MUST / ESSENTIAL / evidence cells and cells positive on the board's own sym_side are never throttled, and a positive naked
revives the whole row as before. Chronological replay 2026-10-27 (2.88M fresh cells, run30-37): cell eval-seconds saved
CL 5.0% / CS 5.7% / SL 0.6% / SS 1.6%, rescue delta lost 0.12% / 0.03% / 0.01% / 0.11%. A cat_side count without the board
condition lost 4-15% of rescue delta at any useful cut, because a filter's value is board-specific and bursty.

Usage:
  v20_exhausted_cells.py build --spill-glob '~/v20_spills/spill_*.tsv.gz' [--spill-glob ...] --out-dir DIR [--n-min 20 --s-min 5 --max-pos 0 --p-recheck 0.25]
"""
import argparse
import datetime as dt
import glob
import gzip
import json
import math
import os
from pathlib import Path

CONTRACT = "exhausted_cells_v1"
EPS = 1e-9
Z = 1.645
CAT_SIDES = ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT")


def cat_side_of(symside):
    if not symside or "_" not in str(symside):
        return None
    sym, side = str(symside).rsplit("_", 1)
    if side not in ("LONG", "SHORT"):
        return None
    return ("CRYPTO_" if sym.endswith(("USDT", "USDC")) else "STOCKS_") + side


def wilson_ub(pos, n):
    if n <= 0:
        return 1.0
    p = pos / n
    den = 1 + Z * Z / n
    c = p + Z * Z / (2 * n)
    r = Z * math.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n))
    return min(1.0, (c + r) / den)


def aggregate(paths):
    """-> ({(cs, tab, row, hdr): [n, pos, secs, syms(set), pos_syms(set), max_d]}, stats). Rows deduplicated by the spill's `rk`."""
    cells, seen, stats = {}, set(), {"files": 0, "rows": 0, "dup_rows": 0, "cells_fresh": 0, "last_ts": ""}
    for path in paths:
        stats["files"] += 1
        with gzip.open(path, "rt") as fh:
            for line in fh:
                ts, _, js = line.rstrip("\n").partition("\t")
                if '"row"' not in js[:16]:
                    continue
                try:
                    ev = json.loads(js)
                except Exception:
                    continue
                if ev.get("e") != "row":
                    continue
                cs = cat_side_of(ev.get("s"))
                if cs is None:
                    continue
                if ev.get("rk") in seen:
                    stats["dup_rows"] += 1
                    continue
                seen.add(ev.get("rk"))
                stats["rows"] += 1
                stats["last_ts"] = max(stats["last_ts"], str(ev.get("t") or ts))
                nk = ev.get("n")
                nd = nk[0] if (nk is not None and nk[0] is not None) else None
                for c in ev.get("y") or []:
                    d = c[-1]
                    if d is None:
                        continue
                    if nd is not None and nd > EPS:  # positive naked: the pilot re-runs skipped cells anyway (PRIOR-REVIVED) — no evidence about the filter
                        stats["cells_revived_ctx"] = stats.get("cells_revived_ctx", 0) + 1
                        continue
                    key = (cs, ev.get("sh"), ev.get("k"), c[0])
                    v = cells.get(key)
                    if v is None:
                        v = cells[key] = [0, 0, 0.0, set(), set(), -1e9]
                    v[0] += 1
                    v[2] += float(c[3] or 0.0)
                    v[3].add(ev.get("s"))
                    if d > EPS:
                        v[1] += 1
                        v[4].add(ev.get("s"))
                    v[5] = max(v[5], d)
                    stats["cells_fresh"] += 1
    return cells, stats


def build(a):
    paths = sorted({p for g in a.spill_glob for p in glob.glob(os.path.expanduser(g))})
    if not paths:
        raise SystemExit("no spill files matched")
    cells, stats = aggregate(paths)
    out_dir = Path(os.path.expanduser(a.out_dir))
    out_dir.mkdir(parents=True, exist_ok=True)
    built = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rule = {"n_min": a.n_min, "s_min": a.s_min, "max_pos": a.max_pos, "z": Z, "p_recheck": a.p_recheck, "board_k_default": 20}
    summary = {}
    for cs in CAT_SIDES:
        rows, n_seen, n_exh, secs_all, secs_exh = {}, 0, 0, 0.0, 0.0
        for (c0, tab, rk, hdr), v in cells.items():
            if c0 != cs:
                continue
            n_seen += 1
            secs_all += v[2]
            ub = wilson_ub(v[1], v[0])
            if v[0] >= a.n_min and len(v[3]) >= a.s_min and v[1] <= a.max_pos:
                n_exh += 1
                secs_exh += v[2]
                rows.setdefault(f"{tab}!{rk}", {})[hdr] = {"n": v[0], "s": len(v[3]), "pos": v[1], "ub": round(ub, 5), "pos_syms": sorted(v[4])}
        meta = {"contract": CONTRACT, "cat_side": cs, "built_at": built, "rule": rule, "corpus": {"files": len(paths), "rows": stats["rows"], "last_ts": stats["last_ts"]},
                "cells_seen": n_seen, "cells_exhausted": n_exh, "secs_share_exhausted": round(secs_exh / secs_all, 4) if secs_all else None}
        tmp = out_dir / f".exhausted_cells_{cs}.json.tmp"
        tmp.write_text(json.dumps({"meta": meta, "rows": rows}, separators=(",", ":")))
        os.replace(tmp, out_dir / f"exhausted_cells_{cs}.json")
        summary[cs] = meta
    print(json.dumps({"stats": stats, "summary": summary}, indent=1))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--spill-glob", action="append", required=True)
    b.add_argument("--out-dir", required=True)
    b.add_argument("--n-min", type=int, default=20)
    b.add_argument("--s-min", type=int, default=5)
    b.add_argument("--max-pos", type=int, default=0)
    b.add_argument("--p-recheck", type=float, default=0.25)
    a = ap.parse_args(argv)
    return build(a)


if __name__ == "__main__":
    raise SystemExit(main())
