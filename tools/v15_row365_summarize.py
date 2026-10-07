#!/usr/bin/env python3
"""v15_row365_summarize — turn the per-sym_side outputs of tools/v15_row365_filters.py into the row-level decisions (Agent R365).
stdlib only. usage: v15_row365_summarize.py --dir ~/v15_row365_20261001/progress --out data/wiring/r365 [--min-sym 3]
Outputs (csv + summary.json):
  rows_positive_365.csv     row (cat_side, tab, switch, cand): n_sym evaluated, n_pos (delta>0 on the window), mean delta, mean delta30 (30D vs 365D per row)
  rows_no_effect_365.csv    rows with a non-zero recorded 30D delta whose window delta is exactly 0 / not binding on EVERY evaluated sym_side (candidate useless)
  filter_proposal.csv       (switch=cand, FILTER=opt) cells: n_eval, n_pos_vs_switch (filter helps the row), n_neg, n_noop, mean delta vs row alone, verdict
                            KEEP_YELLOW (positive on >= min-sym sym_sides and mean > 0), INERT (noop on every evaluated sym_side), HURTS (negative on all), MIXED
  filter_global.csv         per filter option over all rows: n cells, n_pos, n_noop -> orange row stays (helps somewhere) / candidate retire (never helps)
"""
import argparse, collections, csv, glob, json, os, statistics


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=os.path.expanduser("~/v15_row365_20261001/progress"))
    ap.add_argument("--out", default="data/wiring/r365")
    ap.add_argument("--min-sym", type=int, default=3)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    rows = collections.defaultdict(lambda: {"d": [], "d30": [], "bind": [], "syms": set()})
    cells = collections.defaultdict(lambda: {"d": [], "dv": [], "noop": 0, "syms": set()})
    fglob = collections.defaultdict(lambda: {"n": 0, "pos": 0, "noop": 0, "neg": 0})
    nfiles = done_files = 0
    meta = collections.Counter()
    for f in sorted(glob.glob(os.path.join(a.dir, "*_v14_progress.json"))):
        nfiles += 1
        try:
            j = json.load(open(f))
        except Exception:
            continue
        if j.get("invalid_baseline") or j.get("unverifiable"):
            meta["skipped_" + ("unverifiable" if j.get("unverifiable") else "invalid_baseline")] += 1
            continue
        done_files += 1
        cat, ss, win = j.get("cat_side"), os.path.basename(f)[: -len("_v14_progress.json")], j.get("window_days")
        if j.get("baseline_soft_invalid"):
            meta["baseline_soft_invalid"] += 1
        for k, e in (j.get("done") or {}).items():
            if not isinstance(e, dict) or not e.get("complete"):
                continue
            try:
                tab, rest = k.split("!", 1)
                _, rest = rest.split(":", 1)
                sw, cand = rest.split("=", 1)
            except Exception:
                continue
            rk = (cat, tab, sw, cand)
            if e.get("delta_invalid") or "delta" not in e:
                meta["row_invalid"] += 1
                continue
            r = rows[rk]
            r["d"].append(e["delta"]); r["d30"].append(e.get("delta30", 0.0)); r["bind"].append(bool(e.get("naked_binding"))); r["syms"].add(ss)
            for h in e.get("noop_yellows", []) or []:
                c = cells[(cat, tab, sw, cand, h)]
                c["noop"] += 1; c["syms"].add(ss)
                g = fglob[(cat, h)]; g["n"] += 1; g["noop"] += 1
            for h, d in (e.get("yellow_vs_switch") or {}).items():
                c = cells[(cat, tab, sw, cand, h)]
                c["dv"].append(d); c["d"].append((e.get("yellows") or {}).get(h)); c["syms"].add(ss)
                g = fglob[(cat, h)]; g["n"] += 1
                if d > 1e-9: g["pos"] += 1
                elif d < -1e-9: g["neg"] += 1
    with open(os.path.join(a.out, "rows_positive_365.csv"), "w", newline="") as fh, open(os.path.join(a.out, "rows_no_effect_365.csv"), "w", newline="") as fn:
        w, wn = csv.writer(fh), csv.writer(fn)
        w.writerow(["cat_side", "tab", "switch", "cand", "n_sym", "n_pos", "mean_delta_window", "mean_delta30", "n_binding"])
        wn.writerow(["cat_side", "tab", "switch", "cand", "n_sym", "mean_delta30", "reason"])
        for (cat, tab, sw, cand), r in sorted(rows.items()):
            n = len(r["d"]); npos = sum(1 for x in r["d"] if x > 1e-9)
            if npos:
                w.writerow([cat, tab, sw, cand, n, npos, round(statistics.mean(r["d"]), 4), round(statistics.mean(r["d30"]), 4), sum(r["bind"])])
            if n >= a.min_sym and all(abs(x) <= 1e-9 for x in r["d"]) and not any(r["bind"]):
                wn.writerow([cat, tab, sw, cand, n, round(statistics.mean(r["d30"]), 4), "window delta exactly 0 and not binding on every evaluated sym_side"])
    verdicts = collections.Counter()
    with open(os.path.join(a.out, "filter_proposal.csv"), "w", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(["cat_side", "tab", "switch", "cand", "filter", "n_eval", "n_pos_vs_row", "n_neg_vs_row", "n_noop", "mean_delta_vs_row", "verdict"])
        for (cat, tab, sw, cand, h), c in sorted(cells.items()):
            dv = c["dv"]; n = len(dv) + c["noop"]
            npos = sum(1 for x in dv if x > 1e-9); nneg = sum(1 for x in dv if x < -1e-9)
            if c["noop"] == n:
                v = "INERT"
            elif npos >= a.min_sym and statistics.mean(dv) > 0:
                v = "KEEP_YELLOW"
            elif npos == 0 and nneg > 0:
                v = "HURTS"
            else:
                v = "MIXED"
            verdicts[v] += 1
            w.writerow([cat, tab, sw, cand, h, n, npos, nneg, c["noop"], round(statistics.mean(dv), 4) if dv else "", v])
    with open(os.path.join(a.out, "filter_global.csv"), "w", newline="") as fg:
        w = csv.writer(fg)
        w.writerow(["cat_side", "filter", "n_cells", "n_pos", "n_neg", "n_noop", "verdict"])
        for (cat, h), g in sorted(fglob.items()):
            v = "STAYS_ORANGE_HELPS_SOMEWHERE" if g["pos"] else ("RETIRE_CANDIDATE_NEVER_HELPS" if g["n"] >= 30 else "TOO_FEW_CELLS")
            w.writerow([cat, h, g["n"], g["pos"], g["neg"], g["noop"], v])
    summ = {"files": nfiles, "files_done": done_files, "rows_evaluated": len(rows), "rows_with_positive_window_delta": sum(1 for r in rows.values() if any(x > 1e-9 for x in r["d"])),
            "cells": len(cells), "cell_verdicts": dict(verdicts), "meta": dict(meta)}
    json.dump(summ, open(os.path.join(a.out, "summary.json"), "w"), indent=1)
    print(json.dumps(summ))


if __name__ == "__main__":
    main()
