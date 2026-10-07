#!/usr/bin/env python3
"""switch_revival_diff (Agent G) — union of every template version vs the live template, per cat_side.
-> data/switch_revival/<ts>/union_<cat>.json, missing_<cat>.json (white switches / orange rows in some version but not in live), sizes_<cat>.txt"""
import collections, glob, json, os, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN = ROOT / "data" / "switch_revival" / "scan"
CATS = ["CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT"]
ts = sys.argv[1] if len(sys.argv) > 1 else time.strftime("%Y%m%d%H%M")
OUT = ROOT / "data" / "switch_revival" / ts
OUT.mkdir(parents=True, exist_ok=True)
for cat in CATS:
    files = []
    for j in glob.glob(str(SCAN / cat / "*.json")):
        d = json.load(open(j))
        white = sum(1 for t, rows in d["tabs"].items() for r in rows if not r[5])
        orange = sum(1 for t, rows in d["tabs"].items() for r in rows if r[5])
        names = {r[0] for t, rows in d["tabs"].items() for r in rows if not r[5]}
        files.append((d["file"], d["mtime"], d["size"], white, orange, len(names), d))
    files.sort(key=lambda x: -x[5])
    live_rel = f"SPREADSHEETS/TEMPLATE_{cat}.xlsx"
    live = next((f for f in files if f[0] == live_rel), None)
    with open(OUT / f"sizes_{cat}.txt", "w") as fh:
        fh.write("file white orange distinct_white_names size mtime\n")
        for f in files[:40]:
            fh.write(f"{f[0]} {f[3]} {f[4]} {f[5]} {f[2]} {time.strftime('%m-%d %H:%M', time.gmtime(f[1]))}\n")
        fh.write(f"LIVE {live[0] if live else None} {live[3:6] if live else None}\n")
    union = {}      # name -> info (white)
    orange_u = collections.defaultdict(lambda: collections.defaultdict(lambda: {"files": 0, "last": 0, "opts": collections.Counter()}))  # tab -> filter -> info
    for fn, mt, sz, w, o, nn, d in files:
        if fn.startswith("data/switch_revival/gitv") or True:
            pass
        for tab, rows in d["tabs"].items():
            seen_here = set()
            for name, opt, fam, isdef, bold, orange, grey in rows:
                if orange:
                    e = orange_u[tab][name]
                    e["opts"][str(opt)] += 1
                    if (name, tab) not in seen_here:
                        e["files"] += 1; seen_here.add((name, tab)); e["last"] = max(e["last"], mt)
                    continue
                u = union.setdefault(name, {"name": name, "tabs": collections.Counter(), "options": collections.defaultdict(collections.Counter), "fam": collections.Counter(), "defaults": collections.Counter(), "grey_files": 0, "files": set(), "first": mt, "last": mt, "src_last": fn})
                u["tabs"][tab] += 1
                u["options"][tab][str(opt)] += 1
                if fam: u["fam"][str(fam)] += 1
                if str(isdef).upper() == "YES" or bold: u["defaults"][str(opt)] += 1
                if grey: u["grey_files"] += 1
                u["files"].add(fn)
                u["first"] = min(u["first"], mt)
                if mt >= u["last"]:
                    u["last"] = mt; u["src_last"] = fn
    live_names = set()
    live_orange = collections.defaultdict(set)
    live_tab_of = {}
    if live:
        for tab, rows in live[6]["tabs"].items():
            for name, opt, fam, isdef, bold, orange, grey in rows:
                if orange:
                    live_orange[tab].add(name)
                else:
                    live_names.add(name); live_tab_of.setdefault(name, tab)
    miss = {}
    for name, u in union.items():
        if name in live_names:
            continue
        miss[name] = {"tabs": dict(u["tabs"]), "options": {t: dict(c) for t, c in u["options"].items()}, "fam": dict(u["fam"]), "defaults": dict(u["defaults"]),
                      "n_files": len(u["files"]), "first": time.strftime("%m-%d %H:%M", time.gmtime(u["first"])), "last": time.strftime("%m-%d %H:%M", time.gmtime(u["last"])),
                      "src_last": u["src_last"], "grey_files": u["grey_files"]}
    omiss = {}
    for tab, d in orange_u.items():
        for name, e in d.items():
            if name not in live_orange.get(tab, set()):
                omiss.setdefault(tab, {})[name] = {"files": e["files"], "opts": dict(e["opts"]), "last": time.strftime("%m-%d %H:%M", time.gmtime(e["last"]))}
    json.dump({"live_white_names": len(live_names), "union_white_names": len(union), "missing_white": miss, "missing_orange": omiss, "live_orange_counts": {t: len(v) for t, v in live_orange.items()},
               "union_orange_counts": {t: len(v) for t, v in orange_u.items()}}, open(OUT / f"missing_{cat}.json", "w"), indent=1)
    print(cat, "files", len(files), "| live white names", len(live_names), "union", len(union), "missing white", len(miss), "| missing orange (tab:filters)", {t: len(v) for t, v in omiss.items()})
print(OUT)
