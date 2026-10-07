#!/usr/bin/env python3
"""pipe_stock_gate — merge the stock 365D folders (each stock name ONCE) and report finished sym_sides per cat_side. PIPE 2026-10-01.
Priority when a (cat_side, sym_side) exists in several folders: s5_npzbad > s2 > s5 > s1 (s5_npzbad/s2/s5 hold NPZs with the s2==s5 md5 pair).
usage: pipe_stock_gate.py [--merge-out DIR] [--min 40]"""
import glob, json, os, shutil, sys, collections
B = "data/newx365"
PRI = ["stocks365_s5_npzbad", "stocks365_s2", "stocks365_s5", "stocks365_s1-pub"]
out = sys.argv[sys.argv.index("--merge-out") + 1] if "--merge-out" in sys.argv else None
MIN = int(sys.argv[sys.argv.index("--min") + 1]) if "--min" in sys.argv else 40
pick = {}
for rank, d in enumerate(PRI):
    for f in glob.glob(f"{B}/{d}/*_v14_progress.json"):
        ss = os.path.basename(f)[: -len("_v14_progress.json")]
        pick.setdefault(ss, (rank, f, d))
res = collections.defaultdict(lambda: collections.Counter())
dup = collections.Counter()
for d in PRI:
    for f in glob.glob(f"{B}/{d}/*_v14_progress.json"):
        dup[os.path.basename(f)] += 1
chosen = {}
for ss, (rank, f, d) in pick.items():
    try: j = json.load(open(f))
    except Exception: continue
    cs = j.get("cat_side") or ("STOCKS_LONG" if ss.endswith("_LONG") else "STOCKS_SHORT")
    st = "unverifiable" if j.get("unverifiable") else "invalid" if j.get("invalid_baseline") else "finished" if j.get("finished") else "partial"
    res[cs][st] += 1; chosen[ss] = (f, st, cs)
print({cs: dict(c) for cs, c in res.items()}, "names in >1 folder:", sum(1 for v in dup.values() if v > 1))
ok = {cs: res[cs]["finished"] >= MIN for cs in ("STOCKS_LONG", "STOCKS_SHORT")}
print("threshold", MIN, ok)
if out:
    shutil.rmtree(out, ignore_errors=True); os.makedirs(out)
    for ss, (f, st, cs) in chosen.items():
        if st in ("finished", "partial"): shutil.copy(f, os.path.join(out, os.path.basename(f)))
    print("merged ->", out, len(os.listdir(out)))
sys.exit(0 if all(ok.values()) else 3)
