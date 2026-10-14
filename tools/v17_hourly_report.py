#!/usr/bin/env python3
"""V17 hourly throughput + lift report (USER 2026-10-14: "99% time savings and 10x bh in the next round").

Runs on S1 (cron, hourly). For every host in tools/fleet_hosts_final.json it reads the finished boards of that host's current
progress dir (real progress JSONs only — NO-LIES: every number is read from what the pilot wrote) and reports:
  * boards/hour/server (by result_done_utc hour), wall/board, core-min/board (result_cpu_s when present = real CPU of the
    pilot + its reaped pool workers; else wall x workers as an upper bound, labelled),
  * per board: tier, bh, baseline -> final, final/|bh|, lift per V17 phase (P3_* iterations summed),
  * phase statistics over NEW-BUILD boards (result_start_utc >= --since): n boards that ran the phase, boards it lifted,
    total lift/min -> data/v17_phase_stats.json (the pilot cuts a cuttable phase with n >= 20 and lifted == 0) and pushes
    that file to every host,
  * carry precision: rows eligible-for-carry that the rotation re-evaluated (carry_sampled) and stayed |naked| < eps.
Outputs data/reports/v17_hourly.json and appends a section to data/reports/v17_hourly.md.
"""
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REMOTE = r'''
import json, glob, os, re, sys
since = sys.argv[1]
pd = open(os.path.expanduser("~/v15_current_progress_dir.txt")).read().strip()
out = []
for f in glob.glob(pd + "/*_v14_progress.json"):
    try:
        d = json.load(open(f))
    except Exception:
        continue
    e, s = d.get("result_done_utc"), d.get("result_start_utc")
    if not e or e < since:
        continue
    v = d.get("v17") or {}
    ph = {}
    for n, r in (v.get("phases") or {}).items():
        k = re.sub(r"_\d+$", "", n)
        a = ph.setdefault(k, [0.0, 0.0, 0, 0])
        a[0] += float(r.get("lift") or 0); a[1] += float(r.get("wall_s") or 0); a[2] += int(r.get("evals") or 0); a[3] += int(r.get("promotions") or 0)
    t = d.get("tier"); t = t.get("tier") if isinstance(t, dict) else t
    cs = [x for x in (d.get("done") or {}).values() if isinstance(x, dict) and x.get("carry_sampled") and x.get("naked_delta") is not None]
    carried = sum(1 for x in (d.get("done") or {}).values() if isinstance(x, dict) and str(x.get("reason") or "").startswith("CARRIED_DEAD"))
    out.append({"ss": os.path.basename(f)[:-len("_v14_progress.json")], "start": s, "done": e, "tier": t, "bh": d.get("bh"),
                "base": d.get("initial_baseline_gain", d.get("baseline_gain")), "final": d.get("final_gain"), "cpu_s": d.get("result_cpu_s"),
                "workers": d.get("result_workers"), "pilot_md5": (d.get("result_pilot_md5") or "")[:8], "v17": bool(v.get("on")), "phases": ph,
                "p3_ext": v.get("p3_extended", 0), "carried": carried, "carry_sampled": len(cs),
                "carry_sampled_dead": sum(1 for x in cs if abs(float(x["naked_delta"])) < 1e-6)})
print(json.dumps(out))
'''


def _ts(x):
    return dt.datetime.fromisoformat(str(x)[:19]).replace(tzinfo=dt.timezone.utc)


def collect(hosts, since):
    res = {}
    for h in hosts:
        name = h["name"]
        cmd = ["python3", "-c", REMOTE, since]
        try:
            if name == "s1":
                o = subprocess.run(cmd, capture_output=True, text=True, timeout=120).stdout
            else:
                o = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", h["ssh"][0], "python3 - " + since], input=REMOTE, capture_output=True, text=True, timeout=120).stdout
            res[name] = json.loads(o or "[]")
        except Exception as e:
            res[name] = {"error": str(e)[:200]}
    return res


def summarise(res, since, now):
    rep = {"at": now.isoformat(), "since": since, "hosts": {}, "phases": {}, "top_vs_bh": [], "carry": {"sampled": 0, "stayed_dead": 0, "carried_rows": 0}}
    allb = []
    for name, boards in res.items():
        if not isinstance(boards, list):
            rep["hosts"][name] = boards
            continue
        by_h = {}
        for b in boards:
            by_h.setdefault(b["done"][:13], []).append(b)
            wall = (_ts(b["done"]) - _ts(b["start"])).total_seconds() / 60 if b.get("start") else None
            b["wall_min"] = round(wall, 1) if wall is not None else None
            b["core_min"] = round(b["cpu_s"] / 60, 1) if b.get("cpu_s") else None
            b["core_min_bound"] = round(wall * int(b.get("workers") or 4), 1) if wall is not None else None
            b["host"] = name
            allb.append(b)
        rep["hosts"][name] = {h: {"boards": len(v), "avg_wall_min": round(sum(x["wall_min"] or 0 for x in v) / len(v), 1),
                                  "avg_core_min": (round(sum(x["core_min"] for x in v if x["core_min"]) / max(1, sum(1 for x in v if x["core_min"])), 1) if any(x["core_min"] for x in v) else None),
                                  "avg_core_min_bound_wall_x_workers": round(sum(x["core_min_bound"] or 0 for x in v) / len(v), 1)} for h, v in sorted(by_h.items())}
    for b in allb:
        if not b.get("v17"):
            continue
        for k, (lift, wall, ev, prom) in (b.get("phases") or {}).items():
            if wall <= 0 and ev <= 0:
                continue
            p = rep["phases"].setdefault(k, {"n": 0, "lifted": 0, "lift_sum": 0.0, "wall_min": 0.0, "evals": 0})
            p["n"] += 1
            p["lifted"] += 1 if lift > 1e-6 else 0
            p["lift_sum"] = round(p["lift_sum"] + lift, 4)
            p["wall_min"] = round(p["wall_min"] + wall / 60, 2)
            p["evals"] += ev
        rep["carry"]["sampled"] += int(b.get("carry_sampled") or 0)
        rep["carry"]["stayed_dead"] += int(b.get("carry_sampled_dead") or 0)
        rep["carry"]["carried_rows"] += int(b.get("carried") or 0)
    for p in rep["phases"].values():
        p["lift_per_min"] = round(p["lift_sum"] / p["wall_min"], 4) if p["wall_min"] > 0 else None
    for b in allb:
        try:
            bh = float(b["bh"]) if b.get("bh") is not None else None  # side-signed: > 0 = B&H favours this side
            fin = float(b["final"]) if b.get("final") is not None else None
        except Exception:
            bh = fin = None
        b["x_bh"] = round(fin / bh, 2) if (bh is not None and fin is not None and bh >= 2.0) else None  # 10x-bh target only where bh >= 2
    rep["top_vs_bh"] = [{k: b.get(k) for k in ("host", "ss", "tier", "bh", "base", "final", "x_bh", "wall_min", "core_min", "p3_ext", "carried")} | {"phase_lift": {k: round(v[0], 2) for k, v in (b.get("phases") or {}).items() if abs(v[0]) > 1e-6}}
                        for b in sorted([b for b in allb if b.get("x_bh") is not None], key=lambda b: -b["x_bh"])[:15]]
    rep["boards"] = len(allb)
    return rep, allb


def to_md(rep):
    L = [f"\n## V17 hourly {rep['at'][:16]}Z (boards finished since {rep['since']}: {rep['boards']})", "", "| host | hour | boards | avg wall min | avg core-min (real CPU) | wall x workers |", "|---|---|---|---|---|---|"]
    for h, hrs in rep["hosts"].items():
        if not isinstance(hrs, dict) or "error" in hrs:
            L.append(f"| {h} | ERROR {hrs} | | | | |")
            continue
        for hr, v in hrs.items():
            L.append(f"| {h} | {hr[11:13]}Z | {v['boards']} | {v['avg_wall_min']} | {v['avg_core_min']} | {v['avg_core_min_bound_wall_x_workers']} |")
    L += ["", "| phase | boards ran | boards lifted | lift sum pp | wall min | pp/min |", "|---|---|---|---|---|---|"]
    for k, p in sorted(rep["phases"].items()):
        L.append(f"| {k} | {p['n']} | {p['lifted']} | {p['lift_sum']} | {p['wall_min']} | {p['lift_per_min']} |")
    c = rep["carry"]
    L += ["", f"carry: rows carried {c['carried_rows']}, rotation re-checks {c['sampled']} of which stayed dead {c['stayed_dead']}", "",
          "| top vs bh | tier | bh | base | final | x bh | wall | core-min | phase lift |", "|---|---|---|---|---|---|---|---|---|"]
    for b in rep["top_vs_bh"]:
        L.append(f"| {b['host']}:{b['ss']} | {b['tier']} | {b['bh'] if b['bh'] is None else round(float(b['bh']), 1)} | {b['base'] if b['base'] is None else round(float(b['base']), 1)} | {b['final'] if b['final'] is None else round(float(b['final']), 1)} | {b['x_bh']} | {b['wall_min']} | {b['core_min']} | {b['phase_lift']} |")
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=os.environ.get("V17_NEWBUILD_SINCE", "2026-10-14T01:00"))
    ap.add_argument("--no-push", action="store_true")
    a = ap.parse_args()
    now = dt.datetime.now(dt.timezone.utc)
    hosts = json.load(open(ROOT / "tools" / "fleet_hosts_final.json"))["hosts"]
    res = collect(hosts, a.since)
    rep, _ = summarise(res, a.since, now)
    (ROOT / "data" / "reports").mkdir(parents=True, exist_ok=True)
    (ROOT / "data" / "reports" / "v17_hourly.json").write_text(json.dumps(rep, indent=1))
    with open(ROOT / "data" / "reports" / "v17_hourly.md", "a") as fh:
        fh.write(to_md(rep))
    stats = {"at": rep["at"], "since": a.since, "phases": rep["phases"]}
    sp = ROOT / "data" / "v17_phase_stats.json"
    sp.with_suffix(".tmp").write_text(json.dumps(stats, indent=1))
    os.replace(sp.with_suffix(".tmp"), sp)
    if not a.no_push:
        for h in hosts:
            if h["name"] == "s1":
                continue
            subprocess.run(["scp", "-q", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", str(sp), f"{h['ssh'][0]}:binance-sandbox/data/v17_phase_stats.json"], timeout=60)
    print(to_md(rep))


if __name__ == "__main__":
    main()
