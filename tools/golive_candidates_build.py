#!/usr/bin/env python3
"""Build go-live candidates from the DIAGNOSE+REPAIR and TRADE AUTOPSY results (2026-10-06).

Per sym_side picks the best ENGINE-VERIFIED set among: repair best_overrides ({repair_dir}/{SS}.json) and the autopsy's
verified surgical combination ({autopsy_dir}/{SS}_autopsy.json combo_overrides applied on its base set). Writes
data/golive/candidates_{date}.json with set, 30D gain/trades/TIM/DD, 365D verdict (repair report), source, and
`needs` = the gates still open before it may be written to the live books by tools/golive_final.py:
  trade_parity (tools/v15_parity_check.py PASS with PARITY_VEC_EXACT_MODE) and 365D (valid, gain>0, >=80 trades).
Read-only on live books; it never writes them.

usage (on a host with the result dirs): python tools/golive_candidates_build.py --repair ~/v15_repair_20261006 --autopsy ~/v15_autopsy_v2_20261006 --out data/golive
"""
import argparse
import datetime
import json
import os
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repair", action="append", default=[])
    ap.add_argument("--autopsy", action="append", default=[])
    ap.add_argument("--out", default="data/golive")
    a = ap.parse_args()
    cands = {}
    for d in a.repair:
        for f in Path(os.path.expanduser(d)).glob("*.json"):
            if f.name == "summary.jsonl":
                continue
            try:
                r = json.loads(f.read_text())
            except Exception:
                continue
            s = r.get("summary") or {}
            ss = s.get("symside") or f.stem
            after = r.get("after") or {}
            fin = next((x for x in r.get("finalists", []) if x.get("changes") == r.get("changes")), {}) or {}
            cands.setdefault(ss, []).append({"source": "repair", "path": str(f), "overrides": r.get("best_overrides") or {}, "gain_30d": after.get("gain"),
                                             "trades": after.get("trades"), "tim": after.get("tim"), "dd": after.get("dd"), "valid": after.get("valid"),
                                             "q365": fin.get("q365"), "m365": fin.get("m365"), "base_gain": (r.get("before") or {}).get("gain")})
    for d in a.autopsy:
        for f in Path(os.path.expanduser(d)).glob("*_autopsy.json"):
            try:
                r = json.loads(f.read_text())
            except Exception:
                continue
            ss = r.get("symside") or f.name.replace("_autopsy.json", "")
            if not r.get("combo_overrides"):
                continue
            base = None
            for c in cands.get(ss, []):
                if c["source"] == "repair":
                    base = dict(c["overrides"])
            if base is None:
                continue
            ov = dict(base)
            ov.update(r["combo_overrides"])
            last_kept = [v for v in r.get("combo_verified", []) if v.get("kept")]
            cands.setdefault(ss, []).append({"source": "autopsy", "path": str(f), "overrides": ov, "gain_30d": r.get("combo_gain"),
                                             "trades": last_kept[-1]["trades"] if last_kept else None, "tim": None, "dd": None, "valid": True,
                                             "q365": None, "m365": None, "base_gain": (r.get("base") or {}).get("gain")})
    out = {}
    for ss, lst in cands.items():
        best = max(lst, key=lambda c: (c["valid"] is True, c["gain_30d"] if c["gain_30d"] is not None else -1e9))
        needs = ["trade_parity_PASS(PARITY_VEC_EXACT_MODE)"]
        if best["q365"] is not True:
            needs.append("365D_verify")
        out[ss] = {**best, "alternatives": [{"source": c["source"], "gain_30d": c["gain_30d"], "q365": c["q365"]} for c in lst], "needs": needs}
    date = datetime.date.today().strftime("%Y%m%d")
    p = Path(a.out)
    p.mkdir(parents=True, exist_ok=True)
    (p / f"candidates_{date}.json").write_text(json.dumps(out, indent=1, default=str))
    rows = sorted(out.items(), key=lambda kv: -(kv[1]["gain_30d"] or -1e9))
    print(f"{len(out)} candidates -> {p / f'candidates_{date}.json'}")
    for ss, c in rows:
        print(f"{ss:<20} {c['source']:<8} base {c['base_gain'] if c['base_gain'] is None else round(c['base_gain'], 2):>8} -> {round(c['gain_30d'], 2) if c['gain_30d'] is not None else None:>8}  q365={c['q365']}  needs={c['needs']}")


if __name__ == "__main__":
    main()
