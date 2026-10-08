#!/usr/bin/env python3
"""PROMO4: best positive-avg-delta option per switch/filter per cat_side, restricted to rows wired in live AND vector (SWITCH_BIBLE + rowcoverage)."""
import csv, json, sys, collections, importlib.util, os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
MIN_POS = int(os.environ.get("P4_MIN_POS", "3"))
MIN_N = int(os.environ.get("P4_MIN_N", "8"))
TOPK = int(os.environ.get("P4_TOPK", "1"))
OUTSFX = os.environ.get("P4_OUTSFX", "")
RISK = {"AUGMENT_ONLY_WHEN_PROFITABLE_TRADIER", "VIGILANCE_CONSEC_LOSSES", "VIGILANCE_GUARD_ENABLED", "MAX_AUGMENTS_PER_POSITION", "REENTRY_TIER1_SIZE_MULT_TRADIER", "DAYTRADE_DC_TARGET_TF", "STOCKS_NOLOSS_HOLD_ENABLED", "ALL_TF_AGAINST_BLOCK_ENTRY_ENABLED"}
def norm(v):
    if isinstance(v, bool): return str(v)
    s = str(v).strip()
    if s.lower() in ("true", "false"): return s.capitalize()
    try:
        f = float(s)
        return str(int(f)) if f == int(f) else repr(f)
    except Exception: return s
d = json.load(open("data/avg_delta_pos_sym.json"))
cat = json.load(open("data/per_sym_settings.json"))
sb = json.load(open("data/SWITCH_BIBLE.json"))["switches"]
rc = collections.defaultdict(dict)
for r in csv.DictReader(open("data/rowcoverage/latest.csv")):
    rc[r["template"]][(r["tab"] + "!" + r["switch"] + "=" + r["cand"])] = r["status"]
spec = importlib.util.spec_from_file_location("v12", "v12_quick_engine.py")
v12 = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(v12)
    QC = v12.QuickConfig
except Exception as e:
    QC = None
    print("QuickConfig import failed", e, file=sys.stderr)
hold = set()
for l in open("data/wiring/promo/hold_keys.txt"):
    l = l.strip()
    if l and not l.startswith("#"): hold.add(l)
out, vec_only = [], []
for cs in ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT"):
    venue = "crypto" if cs.startswith("CRYPTO") else "stocks"
    groups = collections.defaultdict(dict)  # name -> cand -> list of (tab, stats)
    for key, st in d[cs].items():
        tab, rest = key.split("!", 1)
        name, cand = rest.rsplit("=", 1)
        if st.get("avg_delta") is None or st.get("pos_sym") is None: continue
        groups[name].setdefault(norm(cand), []).append((tab, key, st))
    for name, cands in groups.items():
        e = sb.get(name) or {}
        lrc = (e.get("live_read_count") or {}).get(venue, 0) or 0
        dead = (e.get("live_dead_or_stub") or {}).get(venue, 0) or 0
        live_real = lrc - dead > 0
        cur = cat[cs].get(name, None)
        if cur is None and QC is not None and hasattr(QC, name): cur = getattr(QC, name)
        curn = norm(cur) if cur is not None else None
        cl = []
        for cand, lst in cands.items():
            if curn is not None and cand == curn: continue
            tab, key, st = max(lst, key=lambda x: x[2]["avg_delta"])
            stat = rc[cs].get(key, "")
            if st["avg_delta"] > 0 and st["pos_sym"] >= MIN_POS and st["n_sym"] >= MIN_N:
                cl.append((cand, key, st, stat, tab))
        cl.sort(key=lambda x: -x[2]["avg_delta"])
        for cand, key, st, stat, tab in cl[:TOPK]:
            risk = int(name in hold or cs + ":" + name in hold or name in RISK or name.startswith("ABLATION_"))
            row = [cs, name, cur, cand, round(st["avg_delta"], 4), st["pos_sym"], st["n_sym"], tab, stat, int(live_real), risk]
            (out if live_real else vec_only).append(row)
hdr = ["cat_side", "name", "current_default", "best_cand", "avg_delta", "pos_sym", "n_sym", "tab", "rowcov_status", "live_real", "held"]
os.makedirs("data/wiring/promo4", exist_ok=True)
for fn, rows in (("candidates_both%s.csv" % OUTSFX, out), ("vector_only_positive%s.csv" % OUTSFX, vec_only)):
    rows.sort(key=lambda r: (r[0], -r[4]))
    with open("data/wiring/promo4/" + fn, "w", newline="") as f:
        w = csv.writer(f); w.writerow(hdr); w.writerows(rows)
print("MIN_POS", MIN_POS, "MIN_N", MIN_N, "both", collections.Counter(r[0] for r in out), "vec_only", collections.Counter(r[0] for r in vec_only))
