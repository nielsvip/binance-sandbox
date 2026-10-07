#!/usr/bin/env python3
"""v15_graph_compare — GS (tools/v15_graph_search) vs DR (current pilot DIAGNOSE+REPAIR) on the same sym_sides, base and budget.
Reads the per-method report JSONs ({SS}.gs.json / {SS}.dr.json, newest file per sym_side+method wins) under the given
directories and prints / writes a markdown table. Every number is read from the runs' real engine evaluations.
usage: python tools/v15_graph_compare.py data/encyclopedia_v2/runs/cmp1_s5 data/encyclopedia_v2/runs/cmp2gs_s5 ... [--md out.md]
"""
import argparse
import glob
import json
import os
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import v15_diagnose_repair as DR  # noqa: E402


def f(x, nd=2):
    return "" if x is None else (f"{x:+.{nd}f}" if isinstance(x, (int, float)) else str(x))


def summ(rep: dict) -> dict:
    b, a = rep.get("before") or {}, rep.get("after") or {}
    a365 = rep.get("after_365") or {}
    o = rep.get("origin_365") or {}
    bh = b.get("bh")
    if "q365_after" in rep:
        q_after = rep.get("q365_after")
    else:
        fin = next((x for x in rep.get("finalists", []) if x.get("changes") == rep.get("changes")), None)
        q_after = (fin or {}).get("q365") if rep.get("accepted") else o.get("q365")
        a365 = ((fin or {}).get("m365") or {}) if rep.get("accepted") else (o.get("m365") or {})
    return {"gain_before": b.get("gain"), "gain_after": a.get("gain"), "trades_after": a.get("trades"), "tim_after": a.get("tim"), "dd_after": a.get("dd"), "bh": bh,
            "x_bh_after": (a["gain"] / bh) if (bh and bh > 0 and a.get("gain") is not None) else None, "gain_minus_bh_after": (a["gain"] - bh) if (bh is not None and a.get("gain") is not None) else None,
            "compliant_after": DR.compliant(a) if a.get("trades") is not None and "valid" in a else None, "gain365_before": (o.get("m365") or {}).get("gain"), "q365_before": o.get("q365"),
            "gain365_after": a365.get("gain"), "dd365_after": a365.get("dd"), "q365_after": q_after, "accepted": rep.get("accepted"), "n_changes": len(rep.get("changes") or []),
            "n_evals": rep.get("n_evals"), "secs": rep.get("secs")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="+")
    ap.add_argument("--md", default="")
    ap.add_argument("--pair", default="dr,gs", help="baseline,method e.g. dr2,chain")
    a = ap.parse_args()
    A, B = a.pair.split(",")
    best = {}
    for d in a.dirs:
        for p in glob.glob(os.path.join(d, "*.json")):
            name = os.path.basename(p)
            parts = name.split(".")
            if len(parts) != 3 or parts[1] not in ("gs", "dr", "chain", "dr2"):
                continue
            key = (parts[0], parts[1])
            if key not in best or os.path.getmtime(p) > best[key][0]:
                best[key] = (os.path.getmtime(p), p)
    rows = {}
    for (ss, meth), (_mt, p) in best.items():
        try:
            rep = json.load(open(p))
        except Exception:
            continue
        rows.setdefault(ss, {})[meth] = summ(rep)
    def cat(ss):
        sym, side = ss.rsplit("_", 1)
        return ("CRYPTO" if sym.endswith(("USDT", "USDC")) else "STOCKS") + "_" + side
    hA, hB = A.upper(), B.upper()
    L = [f"| sym_side | B&H | base 30D | base 365D | {hA} 30D | {hA} ×B&H | {hA} 365D | {hA} q365 | {hA} chg | {hA} s | {hB} 30D | {hB} ×B&H | {hB} 365D | {hB} q365 | {hB} chg | {hB} s |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    agg = {"dr": [], "gs": []}
    for ss in sorted(rows, key=lambda s: (cat(s), s)):
        dr, gs = rows[ss].get(A) or {}, rows[ss].get(B) or {}
        any_ = dr or gs
        L.append(f"| {ss} | {f(any_.get('bh'))} | {f(any_.get('gain_before'))} | {f(any_.get('gain365_before'))} | "
                 f"{f(dr.get('gain_after'))} | {f(dr.get('x_bh_after'))} | {f(dr.get('gain365_after'))} | {dr.get('q365_after', '')} | {dr.get('n_changes', '')} | {dr.get('secs', '')} | "
                 f"{f(gs.get('gain_after'))} | {f(gs.get('x_bh_after'))} | {f(gs.get('gain365_after'))} | {gs.get('q365_after', '')} | {gs.get('n_changes', '')} | {gs.get('secs', '')} |")
    both = [s for s in rows if rows[s].get(A) and rows[s].get(B)]
    for k in (A, B):
        xs = [rows[s][k] for s in both]
        if not xs:
            continue
        g = [x["gain_after"] for x in xs if x.get("gain_after") is not None]
        gb = [x["gain_before"] for x in xs if x.get("gain_before") is not None]
        g365 = [x["gain365_after"] for x in xs if x.get("gain365_after") is not None]
        q = sum(1 for x in xs if x.get("q365_after"))
        q0 = sum(1 for x in xs if x.get("q365_before"))
        xbh = [x["x_bh_after"] for x in xs if x.get("x_bh_after") is not None]
        beat = sum(1 for x in xs if x.get("gain_minus_bh_after") is not None and x["gain_minus_bh_after"] > 0)
        L.append(f"- **{k.upper()}** (n={len(xs)} sym_sides with both): median 30D gain {statistics.median(gb):+.2f} -> {statistics.median(g):+.2f} (mean {statistics.mean(gb):+.2f} -> {statistics.mean(g):+.2f}); "
                 f"median 365D gain after {statistics.median(g365) if g365 else float('nan'):+.2f}; 365D qualified {q0} -> {q}; beats B&H {beat}/{len(xs)}; "
                 f"median ×B&H (B&H>0 only, n={len(xbh)}) {statistics.median(xbh) if xbh else float('nan'):+.2f}; median changes {statistics.median([x['n_changes'] for x in xs]):.0f}; median secs {statistics.median([x['secs'] or 0 for x in xs]):.0f}")
    if both:
        w = sum(1 for s in both if (bool(rows[s][B].get("q365_after")), rows[s][B].get("gain_after") or -1e9) > (bool(rows[s][A].get("q365_after")), rows[s][A].get("gain_after") or -1e9))
        t = sum(1 for s in both if (bool(rows[s][B].get("q365_after")), round(rows[s][B].get("gain_after") or -1e9, 4)) == (bool(rows[s][A].get("q365_after")), round(rows[s][A].get("gain_after") or -1e9, 4)))
        w365 = sum(1 for s in both if (rows[s][B].get("gain365_after") or -1e9) > (rows[s][A].get("gain365_after") or -1e9) + 1e-9)
        L.append(f"- {B.upper()} better than {A.upper()} on (365D qualified, 30D gain): {w}/{len(both)} (ties {t}); higher 365D gain: {w365}/{len(both)}")
    out = "\n".join(L)
    print(out)
    if a.md:
        Path(a.md).write_text(out + "\n")


if __name__ == "__main__":
    main()
