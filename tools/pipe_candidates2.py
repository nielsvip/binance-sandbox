#!/usr/bin/env python3
"""pipe_candidates2 — candidate defaults from row-ALONE-vs-DEFAULT-baseline evidence (NEWX tier A 30D + row365 365D). PIPE 2026-10-01.
Why: pooled sweep avg deltas are relative to the moving chain baseline (cumulative overrides), they do not transfer to a defaults baseline (validated: 0 of 14 candidate keys improved the defaults baseline).
usage: pipe_candidates2.py OUT_DIR [--min-n 8]   -> OUT_DIR/candidates2.json {CAT:{KEY:value}} ordered by mean365+mean30 desc, OUT_DIR/candidates2_evidence.csv"""
import collections, csv, glob, json, os, re, sys
out = sys.argv[1]; MIN_N = int(sys.argv[sys.argv.index("--min-n") + 1]) if "--min-n" in sys.argv else 8
hold = set(l.strip() for l in open("data/wiring/promo/hold_keys.txt") if l.strip() and not l.startswith("#"))
STAND = {"DAYTRADE_DC_TARGET_TF": ("CRYPTO_LONG", "CRYPTO_SHORT"), "STOCKS_NOLOSS_HOLD_ENABLED": None, "ALL_TF_AGAINST_BLOCK_ENTRY_ENABLED": None}
import openpyxl
cls = {}
for cs in ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT"):
    ws = openpyxl.load_workbook(f"SPREADSHEETS/TEMPLATE_{cs}.xlsx", read_only=True)["WIRING_INVENTORY"]
    for r in ws.iter_rows(min_row=7, max_col=14, values_only=True):
        if r[1] and r[6] and str(r[6]).isupper():
            cls.setdefault((cs, str(r[1])), set()).add(str(r[6]))
def load(globs):
    acc = collections.defaultdict(lambda: collections.defaultdict(list))
    for g in globs:
        for f in glob.glob(g):
            try: p = json.load(open(f))
            except Exception: continue
            if p.get("invalid_baseline") or p.get("unverifiable"): continue
            cs = p.get("cat_side") or os.path.basename(os.path.dirname(f)).split("/")[-1]
            for k, e in (p.get("done") or {}).items():
                if not isinstance(e, dict) or e.get("delta") is None or e.get("delta_invalid"): continue
                m = re.match(r"^([A-Z0-9_]+)!\d+:(.+)$", k)
                if not m: continue
                acc[cs][f"{m.group(1)}!{m.group(2)}"].append(float(e["delta"]))
    return acc
a30 = load(["data/newx/*/*/*_newx.json", "data/newx/*/*_newx.json"])
a365 = load(["data/newx365/progress/*_v14_progress.json", "data/newx365/stocks365_*/*_v14_progress.json"])
rows = []; cand = {}
for cs in ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT"):
    best = {}
    for key, d30 in a30.get(cs, {}).items():
        tab, rest = key.split("!", 1); sw, val = rest.split("=", 1)
        d365 = a365.get(cs, {}).get(key, [])
        n30, n365 = len(d30), len(d365)
        m30 = sum(d30) / n30; m365 = sum(d365) / n365 if n365 else None
        p30 = sum(1 for x in d30 if x > 1e-9) / n30
        rows.append((cs, tab, sw, val, n30, round(m30, 4), round(p30, 3), n365, None if m365 is None else round(m365, 4)))
        if n30 < MIN_N or m30 <= 0 or p30 < 0.5: continue
        if n365 >= 1 and (m365 is None or m365 < 0): continue
        if sw in hold or f"{cs}:{sw}" in hold or sw.startswith("ABLATION_"): continue
        if sw in STAND and (STAND[sw] is None or cs in STAND[sw]): continue
        if "BOTH_WIRED" not in cls.get((cs, sw), set()): continue
        score = m30 + (m365 or 0)
        if sw not in best or score > best[sw][0]: best[sw] = (score, val, m30, m365, n30, n365)
    cand[cs] = {sw: v[1] for sw, v in sorted(best.items(), key=lambda kv: -kv[1][0])}
json.dump(cand, open(os.path.join(out, "candidates2.json"), "w"), indent=1)
with open(os.path.join(out, "candidates2_evidence.csv"), "w", newline="") as f:
    w = csv.writer(f); w.writerow(["cat_side", "tab", "switch", "cand", "n30", "mean30_alone", "frac_pos30", "n365", "mean365_alone"]); w.writerows(rows)
print({cs: len(v) for cs, v in cand.items()}, "rows with evidence", len(rows))
for cs, v in cand.items(): print(cs, list(v.items())[:12])
