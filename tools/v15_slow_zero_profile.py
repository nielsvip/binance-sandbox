#!/usr/bin/env python3
"""USER 2026-10-13 V16 SPEED ("not wasting time waiting for functions that will never give a result").

Mines every v15 delta log (bible §20) for NAKED evals and writes, per cat_side and per `TAB!SWITCH=cand`:
n_sym (distinct sym_sides measured), n_nonzero (valid |delta| >= 1e-9), mean/max worker secs, n_timeout.
A row is SLOW_ZERO when it was measured on >= --min-n sym_sides, NEVER moved the ledger and costs
>= --min-secs per eval (or timed out). The pilot skips those rows (settled ZERO_FORMULA_SLOW_ROW, booked,
rotating 1/25 retest). Read-only on the logs; writes one JSON. Real evals only — nothing estimated.

    python3 tools/v15_slow_zero_profile.py [--globs ...] [--out data/causal/slow_zero.json]
"""
import argparse
import collections
import glob
import json
import os
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_GLOBS = [
    os.path.expanduser("~/v15_run*/progress/v15_delta_log/*_jump.jsonl"),
    os.path.expanduser("~/v15_run*/v15_delta_log/*_jump.jsonl"),
    os.path.join(ROOT, "data/reports/lifecycle_pilot/v15_delta_log/*_jump.jsonl"),
]


def cat_side_of(symside: str) -> str:
    side = symside.rsplit("_", 1)[-1]
    sym = symside.rsplit("_", 1)[0]
    venue = "CRYPTO" if (sym.endswith("USDT") or sym.endswith("USDC")) else "STOCKS"
    return f"{venue}_{side}"


def mine(paths):
    acc = collections.defaultdict(lambda: {"syms": set(), "nz_syms": set(), "secs": 0.0, "n": 0, "max": 0.0, "timeouts": 0})
    for p in paths:
        ss = os.path.basename(p).replace("_jump.jsonl", "")
        cs = cat_side_of(ss)
        try:
            fh = open(p, errors="ignore")
        except Exception:
            continue
        with fh:
            for line in fh:
                if '"naked"' not in line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get("label") != "naked":
                    continue
                k = f"{r.get('sheet')}!{str(r.get('switch')).strip()}={str(r.get('cand')).strip()}"
                a = acc[(cs, k)]
                s = float(r.get("secs") or 0.0)
                a["syms"].add(ss)
                a["secs"] += s
                a["n"] += 1
                a["max"] = max(a["max"], s)
                if "TIMEOUT" in str(r.get("err") or ""):
                    a["timeouts"] += 1
                d = r.get("delta")
                if d is not None and r.get("valid", True) and abs(float(d)) >= 1e-9:
                    a["nz_syms"].add(ss)
    return acc


def build(acc, min_n, min_secs):
    out = {"_meta": {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "min_n": min_n, "min_secs": min_secs}, "cat_sides": {}}
    for (cs, k), a in acc.items():
        n_sym = len(a["syms"])
        mean = a["secs"] / max(1, a["n"])
        rec = {"n_sym": n_sym, "n_nonzero_sym": len(a["nz_syms"]), "mean_secs": round(mean, 3), "max_secs": round(a["max"], 3), "n_evals": a["n"], "timeouts": a["timeouts"]}
        rec["slow_zero"] = bool(n_sym >= min_n and not a["nz_syms"] and (mean >= min_secs or a["timeouts"] > 0))
        if rec["slow_zero"] or mean >= min_secs:
            out["cat_sides"].setdefault(cs, {})[k] = rec
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--globs", nargs="*", default=DEFAULT_GLOBS)
    ap.add_argument("--out", default=os.path.join(ROOT, "data/causal/slow_zero.json"))
    ap.add_argument("--min-n", type=int, default=8)
    ap.add_argument("--min-secs", type=float, default=3.0)
    a = ap.parse_args()
    paths = sorted({p for g in a.globs for p in glob.glob(g)})
    acc = mine(paths)
    out = build(acc, a.min_n, a.min_secs)
    out["_meta"]["files"] = len(paths)
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    tmp = a.out + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    os.replace(tmp, a.out)
    tally = {cs: sum(1 for r in d.values() if r["slow_zero"]) for cs, d in out["cat_sides"].items()}
    cost = {cs: round(sum(r["mean_secs"] * r["n_evals"] / max(1, r["n_sym"]) for r in d.values() if r["slow_zero"]), 1) for cs, d in out["cat_sides"].items()}
    print(json.dumps({"files": len(paths), "slow_zero_rows": tally, "secs_per_board_saved": cost, "out": a.out}))


if __name__ == "__main__":
    main()
