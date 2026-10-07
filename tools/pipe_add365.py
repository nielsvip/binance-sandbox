#!/usr/bin/env python3
"""pipe_add365 — add per-row 365D evidence (row365 outputs) to a copy of data/avg_delta_pos_sym.json.
Adds per row: n_sym_365, pos_sym_365, avg_delta_365, n_bind_365 (window deltas of the row alone, naked), window tag 365D; never touches pos_sym/n_sym/avg_delta (30D).
usage: pipe_add365.py IN.json OUT.json DIR [DIR ...]   (DIR holds *_v14_progress.json from tools/v15_row365_filters.py)"""
import collections, glob, json, os, re, sys
inp, outp, dirs = sys.argv[1], sys.argv[2], sys.argv[3:]
j = json.load(open(inp))
acc = collections.defaultdict(lambda: collections.defaultdict(lambda: {"d": [], "bind": 0, "syms": set()}))
files = 0
for d in dirs:
    for f in glob.glob(os.path.join(d, "*_v14_progress.json")):
        try:
            p = json.load(open(f))
        except Exception:
            continue
        if p.get("invalid_baseline") or p.get("unverifiable"):
            continue
        cs, ss = p.get("cat_side"), os.path.basename(f)[: -len("_v14_progress.json")]
        if cs not in j:
            continue
        files += 1
        for k, e in (p.get("done") or {}).items():
            if not isinstance(e, dict) or not e.get("complete") or e.get("delta") is None:
                continue
            m = re.match(r"^([A-Z0-9_]+)!\d+:(.+)$", k)
            if not m:
                continue
            key = f"{m.group(1)}!{m.group(2)}"
            a = acc[cs][key]
            a["d"].append(float(e["delta"])); a["syms"].add(ss); a["bind"] += bool(e.get("naked_binding"))
n = 0
for cs, rows in acc.items():
    for key, a in rows.items():
        r = j[cs].get(key)
        if r is None:
            continue
        ds = a["d"]
        r["n_sym_365"] = len(a["syms"]); r["pos_sym_365"] = sum(1 for x in ds if x > 1e-9)
        r["avg_delta_365"] = round(sum(ds) / len(ds), 6); r["n_bind_365"] = a["bind"]; n += 1
j.setdefault("_meta", {})["row365_files"] = files; j["_meta"]["row365_rows"] = n; j["_meta"]["row365_dirs"] = dirs
json.dump(j, open(outp, "w"))
print("files", files, "rows enriched", n, {cs: len(r) for cs, r in acc.items()})
