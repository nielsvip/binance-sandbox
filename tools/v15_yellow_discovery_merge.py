#!/usr/bin/env python3
"""v15_yellow_discovery_merge — ONE template-structure-independent yellow-cell proposal per cat_side (Agent Y, 2026-10-01).

Combines
  * 30D evidence   data/yellow_discovery/<date>/yellow_30d_evidence.json  (tools/v15_yellow_from_deltas.py --collect --new-code-only --out ...):
                   cells = BRIGHT (ever non-zero binding effect), light_yellow_untested = never calculated; every other grid cell = calculated, zero/noop
  * 365D audit     data/yellow_discovery/<date>/audit365_<host>/<cat>.json (tools/v15_yellow_365_audit.py: per cell n, pos_sym, avg_delta_vs_naked)
into data/yellow_discovery/<date>/yellow_proposal_<cat>.json (+ .csv) keyed by SWITCH=cand + FILTER=opt (NOT by tab) so the proposal can be painted
into ANY repaired template later by switch / header name. NO-LIES: a cell that was not computed is UNKNOWN, never zero.

Verdicts
  KEEP     currently yellow (bright/light) AND 365D pos_sym > 0
  ADD      NOT yellow today (name-token-only cell) AND 365D pos_sym > 0
  DROP     currently yellow AND 365D n >= min_n valid sym_sides AND pos_sym == 0   (candidate; small sample caveat)
  UNKNOWN  everything else (not computed / n < min_n); evidence_30d is still reported (active = a non-zero 30D effect was observed)
  python tools/v15_yellow_discovery_merge.py [--date 20261001] [--pull] [--min-n 2]
"""
import argparse
import csv
import datetime
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAT_SIDES = ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT")
HOSTS = {"s1": "s1-pub", "s2": "s2", "s5": "s5"}


def pull(day_dir: Path, day: str):
    for name, target in HOSTS.items():
        subprocess.run(["ssh", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=accept-new", target, f"cd ~/binance-sandbox && test -d data/yellow_discovery/{day}/audit365 && nice -n 10 .venv/bin/python tools/v15_yellow_365_audit.py --summarize --dir data/yellow_discovery/{day}/audit365 2>&1 | tail -1"], capture_output=True, text=True, timeout=300)
        dst = day_dir / f"audit365_{name}"
        dst.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(["rsync", "-az", "-e", "ssh -S none -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10", f"{target}:~/binance-sandbox/data/yellow_discovery/{day}/audit365/", str(dst) + "/"], capture_output=True, text=True)
        print(f"[pull] {name}: rc={r.returncode} {r.stderr.strip()[:120]}")


def load_30d(day_dir: Path):
    p = day_dir / "yellow_30d_evidence.json"
    if not p.exists():
        return None
    d = json.loads(p.read_text())
    out = {}
    for cs in CAT_SIDES:
        b = {tuple(x.split("\t")[1:]) for x in d.get("cells", {}).get(cs, [])}                 # (SWITCH=cand, HDR)
        l = {tuple(x.split("\t")[1:]) for x in d.get("light_yellow_untested", {}).get(cs, [])}
        out[cs] = (b, l)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="20261001")
    ap.add_argument("--pull", action="store_true", help="rsync the audit dirs from s1/s2/s5 first")
    ap.add_argument("--min-n", type=int, default=2)
    a = ap.parse_args()
    day_dir = ROOT / "data" / "yellow_discovery" / a.date
    day_dir.mkdir(parents=True, exist_ok=True)
    if a.pull:
        pull(day_dir, a.date)
    ev30 = load_30d(day_dir)
    summary = {"at": datetime.datetime.utcnow().isoformat() + "Z", "min_n": a.min_n, "30d_evidence": bool(ev30), "cat_sides": {}}
    for cs in CAT_SIDES:
        files = sorted(day_dir.glob(f"audit365_*/{cs}.json"))
        cells365 = {}
        sources = []
        for f in files:
            j = json.loads(f.read_text())
            sources.append(str(f.relative_to(day_dir)))
            for key, rec in j.get("cells", {}).items():
                sw, cand, hdr = key.split("\t")
                k = (f"{sw}={cand}", hdr)
                e = cells365.setdefault(k, {"tabs": set(), "colour": rec["colour"], "per_sym": {}})
                e["tabs"].update(rec.get("tabs") or [])
                e["per_sym"].update({s: v for s, v in rec.get("per_sym", {}).items()})
                rank = {"bright": 0, "light": 1, "token": 2}
                if rank.get(rec["colour"], 9) < rank.get(e["colour"], 9):
                    e["colour"] = rec["colour"]
        rows = []
        b30, l30 = (ev30[cs] if ev30 else (set(), set()))
        for (swc, hdr), e in cells365.items():
            vals = [v for v in e["per_sym"].values() if v.get("valid") and "delta_vs_naked" in v]
            n = len(vals)
            pos = sum(1 for v in vals if v["delta_vs_naked"] > 1e-9)
            avg = round(sum(v["delta_vs_naked"] for v in vals) / n, 6) if n else None
            yellow = e["colour"] in ("bright", "light")
            if pos > 0:
                verdict = "KEEP" if yellow else "ADD"
            elif yellow and n >= a.min_n:
                verdict = "DROP"
            else:
                verdict = "UNKNOWN"
            if not ev30:
                d30 = "no-evidence"
            elif (swc, hdr) in b30:
                d30 = "active"
            elif (swc, hdr) in l30:
                d30 = "untested"
            else:
                d30 = "calculated-zero"
            rows.append({"switch_cand": swc, "filter_opt": hdr, "tabs": sorted(e["tabs"]), "template_colour": e["colour"], "evidence_30d": d30,
                         "n365": n, "pos_sym365": pos, "avg_delta_vs_naked365": avg, "n_syms_attempted": len(e["per_sym"]), "verdict": verdict})
        rows.sort(key=lambda r: (r["verdict"], r["switch_cand"], r["filter_opt"]))
        counts = {}
        for r in rows:
            counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
        (day_dir / f"yellow_proposal_{cs}.json").write_text(json.dumps({"cat_side": cs, "built_at": summary["at"], "sources": sources, "min_n": a.min_n, "counts": counts, "cells": rows}, indent=0))
        with open(day_dir / f"yellow_proposal_{cs}.csv", "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["switch_cand"])
            w.writeheader()
            for r in rows:
                w.writerow({**r, "tabs": "|".join(r["tabs"])})
        summary["cat_sides"][cs] = {"cells": len(rows), "counts": counts, "sources": sources}
        print(f"[{cs}] cells={len(rows)} {counts} sources={len(sources)}")
    (day_dir / "yellow_proposal_summary.json").write_text(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
