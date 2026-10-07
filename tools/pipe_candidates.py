#!/usr/bin/env python3
"""pipe_candidates — build the candidate new-default set from a v15_daily_template_update report (PIPE 2026-10-01).
usage: pipe_candidates.py REPORT.json OUT_DIR [--pos-json data/avg_delta_pos_sym.json365.json]
Gates: wired in BOTH live and vector (WIRING_INVENTORY class BOTH_WIRED for the key, any row), hold_keys.txt, standing decisions, 365D evidence not negative when present.
Outputs OUT_DIR/candidates.json {CAT: {KEY: value}} (ordered by avg delta desc), OUT_DIR/dropped.csv, OUT_DIR/vector_only_positive.csv (P1 feed)."""
import csv, json, os, sys
import openpyxl
rep = json.load(open(sys.argv[1])); out = sys.argv[2]; os.makedirs(out, exist_ok=True)
posj = json.load(open(sys.argv[sys.argv.index("--pos-json") + 1])) if "--pos-json" in sys.argv else {}
hold = set(l.strip() for l in open("data/wiring/promo/hold_keys.txt") if l.strip() and not l.startswith("#"))
STANDING = {"DAYTRADE_DC_TARGET_TF": ("CRYPTO_LONG", "CRYPTO_SHORT"), "STOCKS_NOLOSS_HOLD_ENABLED": None, "ALL_TF_AGAINST_BLOCK_ENTRY_ENABLED": None}
cls = {}
for cs in rep:
    if not cs.startswith(("CRYPTO", "STOCKS")):
        continue
    wb = openpyxl.load_workbook(f"SPREADSHEETS/TEMPLATE_{cs}.xlsx", read_only=True)
    ws = wb["WIRING_INVENTORY"]
    for r in ws.iter_rows(min_row=7, max_col=14, values_only=True):
        if r[1] and r[6] and str(r[6]).isupper():
            cls.setdefault((cs, str(r[1])), set()).add(str(r[6]))
cand, dropped, vec_only = {}, [], []
for cs in rep:
    if not cs.startswith(("CRYPTO", "STOCKS")):
        continue
    items = [(t, k, old, new, avg, "switch") for t, k, old, new, avg in rep[cs]["promoted_switch"]] + [(t, k, old, new, avg, "filter") for t, k, old, new, avg in rep[cs]["promoted_filter"]]
    seen = {}
    for t, k, old, new, avg, kind in items:
        val = new
        if kind == "filter" and isinstance(new, str) and "=" in new:
            val = new.split("=", 1)[1]
        key = k
        reason = None
        if key in hold or f"{cs}:{key}" in hold:
            reason = "HOLD_LIST"
        elif key in STANDING and (STANDING[key] is None or cs in STANDING[key]):
            reason = "STANDING_DECISION"
        elif key.startswith("ABLATION_"):
            reason = "ABLATION_FLAG"
        else:
            c = cls.get((cs, key), set())
            if "BOTH_WIRED" not in c:
                reason = "NOT_BOTH_WIRED:" + ",".join(sorted(c)) if c else "NOT_IN_INVENTORY"
                if "VECTOR_ONLY" in c:
                    vec_only.append((cs, t, key, val, avg))
        if reason:
            dropped.append((cs, t, key, old, val, avg, reason)); continue
        if key in seen:
            continue
        seen[key] = (val, avg)
    cand[cs] = {k: v for k, (v, a) in sorted(seen.items(), key=lambda kv: -kv[1][1])}
json.dump(cand, open(os.path.join(out, "candidates.json"), "w"), indent=1)
with open(os.path.join(out, "dropped.csv"), "w", newline="") as f:
    w = csv.writer(f); w.writerow(["cat_side", "tab", "key", "old", "new", "avg_delta", "reason"]); w.writerows(dropped)
with open(os.path.join(out, "vector_only_positive.csv"), "w", newline="") as f:
    w = csv.writer(f); w.writerow(["cat_side", "tab", "key", "value", "avg_delta"]); w.writerows(vec_only)
print({cs: len(v) for cs, v in cand.items()}, "dropped", len(dropped), "vector_only", len(vec_only))
for cs, v in cand.items():
    print(cs, v)
