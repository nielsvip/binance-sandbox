#!/usr/bin/env python3
"""Causal map: switch=value -> result, aggregated across the fleet's DIAGNOSE+REPAIR / AUTOPSY runs (director, 2026-10-06).

Input: per-sym_side repair JSONs (tools/v15_repair_driver.py / v15_pilot _diagnose_repair). Each holds `lever_map`
(single-step engine-measured delta of every TEMPLATE row vs that sym_side's base), `diagnosis_before` (fault codes),
`steps` (rows the greedy/surgical search actually kept) and, when the autopsy ran, `row_recommendations` (rows that
fix specific bad trades). All numbers are real engine evaluations; nothing here is estimated.

Output (data/causal/):
  causal_map_{cat_side}.csv   one row per switch=value: n sym_sides measured, mean/median dgain, share positive,
                              mean dtrades/dtim/ddd, times kept by the search, autopsy recommendation count
  fault_levers.csv            fault code x cat_side -> best levers (mean dgain on sym_sides that HAVE that fault)
  causal_map.md               top levers per cat_side and per fault, for v15_pilot / agents
usage: python tools/v15_causal_map.py data/causal/raw/*/*.json
"""
import csv
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path


def cat_side(symside):
    sym, _, side = symside.rpartition("_")
    venue = "CRYPTO" if sym.endswith(("USDT", "USDC")) else "STOCKS"
    return f"{venue}_{side}"


def load(paths):
    runs = {}
    for p in paths:
        try:
            r = json.loads(Path(p).read_text())
        except Exception:
            continue
        ss = (r.get("summary") or {}).get("symside") or Path(p).stem
        if not r.get("lever_map"):
            continue
        if ss not in runs or len(r.get("lever_map")) >= len(runs[ss].get("lever_map")):
            runs[ss] = r
    return runs


def main():
    runs = load(sys.argv[1:])
    out = Path("data/causal")
    out.mkdir(parents=True, exist_ok=True)
    lev = defaultdict(lambda: defaultdict(list))
    kept = defaultdict(lambda: defaultdict(int))
    recs = defaultdict(lambda: defaultdict(int))
    fault_lev = defaultdict(lambda: defaultdict(list))
    meta = {}
    for ss, r in runs.items():
        cs = cat_side(ss)
        faults = sorted({f[0].split(":")[0] if f[0].startswith(("LOSING_EXIT", "LOSING_ENTRY")) else f[0] for f in r.get("diagnosis_before") or []} | {f[0] for f in r.get("diagnosis_before") or [] if ":" in f[0]})
        for row in r["lever_map"]:
            if row.get("blocked") or row.get("valid") is False:
                continue
            d = row.get("d") or {}
            if d.get("gain") is None:
                continue
            key = f"{row['switch']}={row['cand']}"
            lev[cs][key].append((ss, d.get("gain"), d.get("trades") or 0, d.get("tim") or 0, d.get("dd") or 0))
            meta[key] = (row.get("tab"), row.get("row"))
            for f in faults:
                fault_lev[(cs, f)][key].append(d.get("gain"))
        for st in r.get("steps") or []:
            if st.get("applied"):
                kept[cs][st["applied"]] += 1
        for rr in r.get("row_recommendations") or []:
            k = rr.get("set") or rr.get("override") or (f"{rr.get('switch')}={rr.get('cand')}" if rr.get("switch") else None)
            if k:
                recs[cs][k] += 1
    md = ["# Causal map: switch=value -> result (fleet, real engine deltas)", "", f"sym_sides: {len(runs)} | source: lever_map single-step deltas vs each sym_side's base, steps kept by the search, autopsy row recommendations", ""]
    for cs in sorted(lev):
        rows = []
        for key, v in lev[cs].items():
            g = [x[1] for x in v]
            rows.append({"lever": key, "tab": meta[key][0], "row": meta[key][1], "n": len(v), "mean_dgain": round(statistics.mean(g), 3), "median_dgain": round(statistics.median(g), 3),
                         "share_pos": round(sum(1 for x in g if x > 1e-9) / len(g), 3), "share_neg": round(sum(1 for x in g if x < -1e-9) / len(g), 3),
                         "mean_dtrades": round(statistics.mean(x[2] for x in v), 1), "mean_dtim": round(statistics.mean(x[3] for x in v), 2), "mean_ddd": round(statistics.mean(x[4] for x in v), 2),
                         "kept": kept[cs].get(key, 0), "autopsy_recs": recs[cs].get(key, 0),
                         "best_sym": max(v, key=lambda x: x[1])[0], "best_dgain": round(max(g), 3)})
        rows.sort(key=lambda x: (-x["share_pos"] * x["mean_dgain"], -x["kept"]))
        with open(out / f"causal_map_{cs}.csv", "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        n_ss = len({x[0] for v in lev[cs].values() for x in v})
        robust = [x for x in rows if x["n"] >= max(3, n_ss // 3) and x["mean_dgain"] > 0 and x["share_pos"] >= 0.5]
        robust.sort(key=lambda x: -x["mean_dgain"])
        md += [f"## {cs} ({n_ss} sym_sides)", "", "Levers that help most sym_sides (n >= 1/3 of them, mean dgain > 0, positive on >= 50%):", "",
               "| lever | tab/row | n | mean dgain pp | % pos | % neg | dtrades | dTIM | dDD | kept | autopsy |", "|---|---|---|---|---|---|---|---|---|---|---|"]
        for x in robust[:25]:
            md.append(f"| `{x['lever']}` | {x['tab']}/{x['row']} | {x['n']} | {x['mean_dgain']:+.2f} | {x['share_pos']:.0%} | {x['share_neg']:.0%} | {x['mean_dtrades']:+.0f} | {x['mean_dtim']:+.1f} | {x['mean_ddd']:+.1f} | {x['kept']} | {x['autopsy_recs']} |")
        harm = sorted([x for x in rows if x["n"] >= max(3, n_ss // 3) and x["share_neg"] >= 0.6], key=lambda x: x["mean_dgain"])[:10]
        md += ["", "Levers that hurt most sym_sides (never promote as a default):", ""] + [f"- `{x['lever']}` mean {x['mean_dgain']:+.2f}pp, negative on {x['share_neg']:.0%} (n={x['n']})" for x in harm] + [""]
    frows = []
    for (cs, f), d in fault_lev.items():
        best = sorted(((k, statistics.mean(v), len(v), sum(1 for g in v if g > 1e-9) / len(v)) for k, v in d.items() if len(v) >= 2), key=lambda t: -(t[1] * t[3]))[:8]
        for k, m, n, sp in best:
            frows.append({"cat_side": cs, "fault": f, "lever": k, "n": n, "mean_dgain": round(m, 3), "share_pos": round(sp, 3)})
    with open(out / "fault_levers.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["cat_side", "fault", "lever", "n", "mean_dgain", "share_pos"])
        w.writeheader()
        w.writerows(frows)
    md += ["## Fault -> lever (mean dgain on the sym_sides that HAVE the fault)", ""]
    cur = None
    for r in sorted(frows, key=lambda r: (r["cat_side"], r["fault"], -r["mean_dgain"] * r["share_pos"])):
        if (r["cat_side"], r["fault"]) != cur:
            cur = (r["cat_side"], r["fault"])
            md += ["", f"**{cur[0]} / {cur[1]}**"]
        md.append(f"- `{r['lever']}` {r['mean_dgain']:+.2f}pp, positive {r['share_pos']:.0%} (n={r['n']})")
    (out / "causal_map.md").write_text("\n".join(md) + "\n")
    print(f"{len(runs)} sym_sides -> {out}/causal_map_*.csv, fault_levers.csv, causal_map.md")


if __name__ == "__main__":
    main()
