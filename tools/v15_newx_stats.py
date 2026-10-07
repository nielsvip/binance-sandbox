#!/usr/bin/env python3
"""v15_newx_stats — pull NEWX results from s1/s2/s5 into data/newx/<CAT>/ and summarise (rows x sym_sides, n_sym>=20, positives, sampling effect).
usage: v15_newx_stats.py [--no-pull]"""
import argparse, collections, json, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATS = ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT")
HOSTS = ("s1-pub", "s2", "s5")


def pull():
    for h in HOSTS:
        for c in CATS:
            dst = ROOT / "data" / "newx" / c / h.replace("-pub", "")
            dst.mkdir(parents=True, exist_ok=True)
            subprocess.run(["rsync", "-az", "-e", "ssh -o ConnectTimeout=10", f"{h}:binance-sandbox/data/newx/{c}/", f"{dst}/"], timeout=120, check=False)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--no-pull", action="store_true"); a = ap.parse_args()
    if not a.no_pull:
        pull()
    pj = json.load(open(ROOT / "data" / "avg_delta_pos_sym.json"))
    out = {}
    for c in CATS:
        files = sorted((ROOT / "data" / "newx" / c).glob("*/*_newx.json"))
        nsym = collections.defaultdict(set); pos = collections.defaultdict(set); yel_n = collections.Counter(); yel_pos = collections.Counter()
        syms, evals, inval = set(), 0, 0
        tiers = collections.Counter()
        for f in files:
            d = json.load(open(f)); ss = f.name[: -len("_newx.json")]; syms.add(ss); tiers[d.get("tier")] += 1
            for k, e in d["done"].items():
                row = k.split(":", 1)[0].split("!", 1)[0] + "!" + k.split(":", 1)[1]
                if isinstance(e.get("delta"), (int, float)) and not e.get("delta_invalid"):
                    evals += 1
                    nsym[row].add(ss)
                    if e["delta"] > 1e-9:
                        pos[row].add(ss)
                elif e.get("delta_invalid"):
                    inval += 1
                for h, v in (e.get("yellows") or {}).items():
                    yel_n[(row, h)] += 1
                    if v > 1e-9:
                        yel_pos[(row, h)] += 1
        rows = set(nsym)
        n20 = sum(1 for r in rows if len(nsym[r]) >= 20)
        pos_rows = sum(1 for r in rows if pos[r])
        # sampling effect: rows currently below the n>=20 evidence bar in the pooled json that reach >=20 with NEWX evidence added (conservative: sym sets may overlap)
        cur = pj.get(c, {})
        newly = 0; thinnable = 0
        for r in rows:
            e = cur.get(r) or {}
            base_n = int(e.get("n_sym") or 0)
            tot = base_n + len(nsym[r])  # upper bound (overlap with earlier evidence not removed)
            if base_n < 20 and len(nsym[r]) >= 20:
                newly += 1
            posn = int(e.get("pos_sym") or 0) + len(pos[r])
            if (base_n >= 20 or len(nsym[r]) >= 20) and posn <= 3 and not e.get("new") is False and posn < 4:
                thinnable += 1
        out[c] = {"sym_sides": len(syms), "files": len(files), "tier_files": dict(tiers), "evals": evals, "invalid": inval, "rows_with_data": len(rows), "rows_n_sym_ge_20": n20,
                  "rows_with_any_positive": pos_rows, "rows_pos_ge_3": sum(1 for r in rows if len(pos[r]) >= 3), "filter_cells": len(yel_n),
                  "filter_cells_positive": sum(1 for x in yel_pos.values() if x > 0), "rows_newly_reaching_n20": newly, "rows_thinnable_pos_le3_with_n20": thinnable}
    json.dump(out, open(ROOT / "data" / "newx" / "STATS.json", "w"), indent=1)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
