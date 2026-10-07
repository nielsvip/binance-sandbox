"""Fault -> lever inventory: what switches fix what deficiency, mined from thought traces.

Reads *_thought.json (v15_thought_rundown.py output) and builds:
  lever_inventory.json  per cat_side x fault ranked levers + global switch + family tables
  lever_inventory.md    readable top-10 per fault

Credit assignment: a winner's valued switch diffs are attributed to EACH of the
side's faults (credit shared; n_cochanges recorded so single-change evidence —
clean attribution — can be weighted higher). Rows from DERIVED faults are tagged
(src=derived) and rank below RECORDED-fault evidence at equal n.

Anti-overfit: a lever is QUALIFIED only at n>=15 obs (zero-formula rule);
below that it is listed NON-QUALIFIED. 365D-held rate counts only winners with
measured m365 (NOT MEASURED outcomes are excluded, never counted as holds).
No recompute: pure aggregation. Never raises on a missing/partial file.
"""
import json
import statistics
from collections import defaultdict
from pathlib import Path

MIN_N = 15


def _load(p):
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return None


def _q(rows):
    return "QUALIFIED" if len(rows) >= MIN_N else "NON-QUALIFIED"


def build(d):
    d = Path(d)
    thoughts = [_load(p) for p in sorted(d.glob("*_thought.json"))]
    thoughts = [t for t in thoughts if t]
    per_fault = defaultdict(list)  # (cat, fault) -> [(switch, to, dgain, dtr, held365|None, src, ss, nco)]
    per_switch = defaultdict(list)  # (cat, switch, to) -> same
    per_family = defaultdict(list)  # (cat, group) -> [(gain,trades,tim,dd, ss)]
    for t in thoughts:
        cat = t.get("cat_side", "?")
        b, a = t.get("before") or {}, t.get("after") or {}
        dg = (a.get("gain") or 0) - (b.get("gain") or 0)
        dt = (a.get("trades") or 0) - (b.get("trades") or 0)
        held = None
        if t.get("m365") is not None:
            held = bool(t.get("q365_after"))
        diffs = [w for w in (t.get("winner_vs_book") or []) if not w.get("note")]
        nco = len(diffs)
        faults = t.get("faults") or []
        fsrc = t.get("fault_src", "?")
        for w in diffs:
            sw, to = w.get("switch"), w.get("new")
            row = {"dg": dg, "dt": dt, "held": held, "src": fsrc, "ss": t.get("ss"), "nco": nco}
            per_switch[(cat, sw, json.dumps(to, default=str))].append(row)
            for f in faults:
                per_fault[(cat, f.get("fault"))].append({"switch": sw, "to": to, **row})
        for ab in t.get("ablation") or []:
            if ab.get("gain") is None:
                continue
            per_family[(cat, ab.get("group"))].append(
                {"dg": ab["gain"], "dt": ab.get("trades"), "dtim": ab.get("tim"), "ddd": ab.get("dd"),
                 "changed": ab.get("changed"), "mode": ab.get("mode"), "ss": t.get("ss")})
    inv = {"n_sides": len(thoughts), "min_n": MIN_N, "by_fault": {}, "by_switch": {}, "by_family": {}}
    for (cat, fault), rows in sorted(per_fault.items()):
        by_lev = defaultdict(list)
        for r in rows:
            by_lev[(r["switch"], json.dumps(r["to"], default=str))].append(r)
        levers = []
        for (sw, to), rr in by_lev.items():
            dgs = [r["dg"] for r in rr]
            holds = [r["held"] for r in rr if r["held"] is not None]
            levers.append({"switch": sw, "to": json.loads(to), "n": len(rr),
                           "wins": sum(1 for x in dgs if x > 0),
                           "mean_dgain": round(statistics.mean(dgs), 3),
                           "med_dgain": round(statistics.median(dgs), 3),
                           "mean_dtrades": round(statistics.mean([r["dt"] for r in rr]), 1),
                           "held365": f"{sum(holds)}/{len(holds)}" if holds else "unmeasured",
                           "src": "recorded" if all(r["src"] == "recorded" for r in rr) else "mixed/derived",
                           "single_change_n": sum(1 for r in rr if r["nco"] == 1),
                           "status": _q(rr)})
        levers.sort(key=lambda l: (l["status"] == "QUALIFIED", l["med_dgain"]), reverse=True)
        inv["by_fault"][f"{cat}|{fault}"] = levers
    for (cat, sw, to), rr in sorted(per_switch.items()):
        dgs = [r["dg"] for r in rr]
        holds = [r["held"] for r in rr if r["held"] is not None]
        inv["by_switch"][f"{cat}|{sw}|{to}"] = {
            "n": len(rr), "wins": sum(1 for x in dgs if x > 0),
            "mean_dgain": round(statistics.mean(dgs), 3), "med_dgain": round(statistics.median(dgs), 3),
            "held365": f"{sum(holds)}/{len(holds)}" if holds else "unmeasured",
            "single_change_n": sum(1 for r in rr if r["nco"] == 1), "status": _q(rr)}
    for (cat, grp), rr in sorted(per_family.items()):
        dgs = [r["dg"] for r in rr]
        inv["by_family"][f"{cat}|{grp}"] = {
            "n": len(rr), "mean_abs_dgain": round(statistics.mean([abs(x) for x in dgs]), 3),
            "mean_dgain": round(statistics.mean(dgs), 3),
            "changed": sorted({tuple(r.get("changed") or ()) for r in rr}, key=str)[:4],
            "status": _q(rr)}
    return inv


def render_md(inv):
    L = [f"# LEVER INVENTORY — {inv['n_sides']} sides (QUALIFIED at n>={inv['min_n']})", ""]
    for key in sorted(inv["by_fault"]):
        L.append(f"## {key}")
        for l in inv["by_fault"][key][:10]:
            L.append(f"- {l['switch']} -> {l['to']}: n={l['n']} wins={l['wins']} "
                     f"med_dgain={l['med_dgain']:+} held365={l['held365']} src={l['src']} "
                     f"single={l['single_change_n']} [{l['status']}]")
        L.append("")
    L.append("## TOP FAMILIES (ablation, by mean |d_gain|)")
    fam = sorted(inv["by_family"].items(), key=lambda kv: kv[1]["mean_abs_dgain"], reverse=True)
    for key, f in fam[:15]:
        L.append(f"- {key}: n={f['n']} mean|dg|={f['mean_abs_dgain']} via={f['changed']} [{f['status']}]")
    return "\n".join(L) + "\n"


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    a = ap.parse_args()
    d = Path(a.dir)
    inv = build(d)
    (d / "lever_inventory.json").write_text(json.dumps(inv, indent=1, default=str))
    (d / "lever_inventory.md").write_text(render_md(inv))
    print(f"sides={inv['n_sides']} faults={len(inv['by_fault'])} switches={len(inv['by_switch'])} families={len(inv['by_family'])}")


if __name__ == "__main__":
    main()
