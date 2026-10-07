#!/usr/bin/env python3
"""v15_yellow_discovery_proposal — merge the filter-discovery evidence of ALL hosts (+ the 365D audit runs) into ONE template-structure-independent
yellow proposal per cat_side (Agent Y, 2026-10-01).  Keyed by SWITCH=cand + FILTER=opt (never by tab/row number), so tools/v15_yellow_paint_from_proposal.py
can paint ANY template (restored, repaired, or grown) from it.

Evidence (all computed on a recorded engine md5; see 'engines' in the output):
  * Stage A  (tools/v15_filter_discovery.py)  every FILTER=opt alone on each sym_side's final set      -> filters[hdr].A_effect / A_valid masks
  * Stage B  binding switch rows x active filters, delta vs the row's own naked result                   -> B_bright[row\thdr], B_evaluated
  * rows     per sym_side: rows evaluated (naked fingerprint known) and which of them are BINDING        -> rows[row].evaluated / .binding masks
             a NON-binding row inherits Stage A (INHERITED_FROM_BASELINE): effect(row, filter) = Stage A effect on the syms where the row is non-binding
  * audit365 (tools/v15_yellow_365_audit.py, `<dir>/<cat>.json` cells[per_sym.delta_vs_naked])               -> any non-zero valid delta => B_bright too
Colour rule (USER 2026-10-01, 'ANY non-zero delta = bright'; NO-LIES: never evaluated = UNKNOWN, never zero):
  BRIGHT (FFFF00)  any evaluated non-zero effect for this (SWITCH=cand, FILTER=opt)
  none             evaluated at least once on this engine and always exactly zero / inert
  LIGHT (FFF2CC)   UNKNOWN: never evaluated (filter NOT_WIRED_VEC / not in the engine, row never evaluated, Stage B budget not reached)
Verdicts (cell level, for reports): BRIGHT_POS (>=1 positive delta), BRIGHT_NEG_ONLY, ZERO_KNOWN, UNKNOWN.

  python tools/v15_yellow_discovery_proposal.py --date 20261001 [--pull] [--min-n 1]
"""
import argparse
import datetime
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAT_SIDES = ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT")
HOSTS = {"s1": "s1-pub", "s2": "s2", "s5": "s5"}
EPS = 1e-9


def pull(day_dir: Path, day: str):
    for name, tgt in HOSTS.items():
        cmd = (f"cd ~/binance-sandbox && test -d data/yellow_discovery/{day}/fd && nice -n 10 .venv/bin/python tools/v15_filter_discovery.py --summarize --dir data/yellow_discovery/{day}/fd 2>&1 | tail -2; "
               f"for d in data/yellow_discovery/{day}/audit365_new; do test -d $d && nice -n 10 .venv/bin/python tools/v15_yellow_365_audit.py --summarize --dir $d 2>&1 | tail -1; done")
        r = subprocess.run(["ssh", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=accept-new", tgt, cmd], capture_output=True, text=True, timeout=900)
        print(f"[summarize] {name}: {r.stdout.strip()[-200:]}")
        for sub in ("fd", "audit365_new"):
            dst = day_dir / f"{sub}_{name}"
            dst.mkdir(parents=True, exist_ok=True)
            r = subprocess.run(["rsync", "-az", "-e", "ssh -S none -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10", "--include=evidence_*.json", "--include=summary.json", "--include=zero_real_filters.json",
                                "--include=rerun_queue.jsonl", "--include=CRYPTO_*.json", "--include=STOCKS_*.json", "--exclude=*", f"{tgt}:~/binance-sandbox/data/yellow_discovery/{day}/{sub}/", str(dst) + "/"], capture_output=True, text=True)
            print(f"[pull] {name}/{sub}: rc={r.returncode} {r.stderr.strip()[:100]}")


def bit(i):
    return 1 << i


def build(cs, day_dir: Path):
    fdfiles = sorted(day_dir.glob(f"fd_*/evidence_{cs}.json"))
    aufiles = sorted(day_dir.glob(f"audit365_new_*/{cs}.json"))
    syms, sidx, engines = [], {}, {}
    flt = {}          # hdr -> {A_effect, A_valid, notwired, wiring, pos, neg, zero}
    rows = {}         # row -> {"eval": mask, "bind": mask}
    B_bright = {}     # row\thdr -> {"n":, "pos":, "neg":, "zero":}
    B_eval = set()
    audit_bright = {}

    def idx(ss):
        if ss not in sidx:
            sidx[ss] = len(syms)
            syms.append(ss)
        return sidx[ss]

    for f in fdfiles:
        ev = json.loads(f.read_text())
        for s in ev.get("syms", []):
            idx(s["symside"])
            engines[s.get("engine_md5") or "?"] = engines.get(s.get("engine_md5") or "?", 0) + 1
        for hdr, rec in ev.get("filters_stage_A", {}).items():
            e = flt.setdefault(hdr, {"A_effect": 0, "A_valid": 0, "notwired": 0, "wiring": rec.get("wiring"), "pos": 0, "neg": 0, "zero": 0})
            for ss, v in rec.get("per_sym", {}).items():
                i = idx(ss)
                if v.get("status") == "NOT_WIRED_VEC":
                    e["notwired"] |= bit(i)
                    continue
                if "delta_vs_base" not in v:
                    continue
                e["A_valid"] |= bit(i)
                d = v["delta_vs_base"]
                if v.get("effect"):
                    e["A_effect"] |= bit(i)
                e["pos"] += d > EPS
                e["neg"] += d < -EPS
                e["zero"] += abs(d) <= EPS
        for ss, rp in ev.get("rows_per_sym", {}).items():
            i = idx(ss)
            for r in rp.get("evaluated", []):
                rows.setdefault(r, {"eval": 0, "bind": 0})["eval"] |= bit(i)
            for r in rp.get("binding", []):
                rows.setdefault(r, {"eval": 0, "bind": 0})["bind"] |= bit(i)
        for key, rec in ev.get("cells_stage_B_binding", {}).items():
            B_eval.add(key)
            eff = rec.get("effect_syms", 0)
            if eff or rec.get("pos_sym", 0) or rec.get("neg_sym", 0):
                b = B_bright.setdefault(key, {"n": 0, "pos": 0, "neg": 0, "zero": 0})
                b["n"] += rec["n"]; b["pos"] += rec["pos_sym"]; b["neg"] += rec["neg_sym"]; b["zero"] += rec["zero_sym"]
    for f in aufiles:  # 365D audit (name-token/yellow cells): any non-zero valid delta_vs_naked => bright evidence; evaluated => known
        j = json.loads(f.read_text())
        for key, rec in j.get("cells", {}).items():
            sw, cand, hdr = key.split("\t")
            k = f"{sw}={cand}\t{hdr}"
            vals = [v for v in rec.get("per_sym", {}).values() if v.get("valid") and "delta_vs_naked" in v]
            if not vals:
                continue
            B_eval.add(k)
            pos = sum(1 for v in vals if v["delta_vs_naked"] > EPS)
            neg = sum(1 for v in vals if v["delta_vs_naked"] < -EPS)
            if pos or neg:
                b = audit_bright.setdefault(k, {"n": 0, "pos": 0, "neg": 0, "zero": 0})
                b["n"] += len(vals); b["pos"] += pos; b["neg"] += neg; b["zero"] += len(vals) - pos - neg
    for k, v in audit_bright.items():
        b = B_bright.setdefault(k, {"n": 0, "pos": 0, "neg": 0, "zero": 0})
        for kk in ("n", "pos", "neg", "zero"):
            b[kk] += v[kk]
    out = {"cat_side": cs, "built_at": datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat() + "Z", "engines": engines, "n_syms": len(syms), "syms": syms,
           "sources": [str(f.relative_to(day_dir)) for f in fdfiles + aufiles],
           "filters": {h: {**{k: (hex(v) if k in ("A_effect", "A_valid", "notwired") else v) for k, v in e.items()}} for h, e in flt.items()},
           "rows": {r: {"eval": hex(m["eval"]), "bind": hex(m["bind"])} for r, m in rows.items()},
           "B_bright": B_bright, "B_evaluated": sorted(B_eval),
           "rule": "BRIGHT = any evaluated non-zero effect (B_bright, or Stage-A effect inherited by a non-binding evaluated row); none = evaluated, always zero; LIGHT = UNKNOWN (never evaluated)"}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="20261001")
    ap.add_argument("--pull", action="store_true")
    a = ap.parse_args()
    day_dir = ROOT / "data" / "yellow_discovery" / a.date
    day_dir.mkdir(parents=True, exist_ok=True)
    if a.pull:
        pull(day_dir, a.date)
    summ = {}
    for cs in CAT_SIDES:
        o = build(cs, day_dir)
        (day_dir / f"yellow_proposal_{cs}.json").write_text(json.dumps(o))
        nb = sum(1 for k, v in o["B_bright"].items())
        wired = sum(1 for h, e in o["filters"].items() if int(e["A_valid"], 16))
        print(f"[{cs}] syms={o['n_syms']} filters seen={len(o['filters'])} (with valid Stage A: {wired}) rows={len(o['rows'])} B_bright cells={nb} B_evaluated={len(o['B_evaluated'])} engines={o['engines']} sources={len(o['sources'])}")
        summ[cs] = {"syms": o["n_syms"], "filters": len(o["filters"]), "filters_with_stageA": wired, "rows": len(o["rows"]), "B_bright": nb, "B_evaluated": len(o["B_evaluated"]), "engines": o["engines"]}
    (day_dir / "yellow_proposal_summary.json").write_text(json.dumps({"at": datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat() + "Z", "cat_sides": summ}, indent=1))


if __name__ == "__main__":
    main()
