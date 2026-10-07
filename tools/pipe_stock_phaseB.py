#!/usr/bin/env python3
"""pipe_stock_phaseB — detached poller (PIPE 2026-10-01). Every 5 min: mirror stock 365D evidence, count finished stock sym_sides per cat_side (each name once);
at >=40 finished per cat_side OR at the 00:30Z cutoff: summarize (stdlib tool), write the stock yellow PROPOSAL files (no repaint) and the per-symbol COUNTER_TREND_ADD_BLOCK set
(stock sym_sides with 30D alone delta >0 AND stock 365D delta >=0). Does NOT change templates/configs/live (defaults unchanged for stocks). Later finished results refresh the files (rerun every 20 min until 03:00Z).
Status: data/wiring/pipe/STOCK_PHASE_B.md"""
import csv, collections, datetime, glob, json, os, re, subprocess, sys, time
os.chdir("/Users/niels/Documents/binance")
LOG = "data/wiring/pipe/STOCK_PHASE_B.md"
def log(m):
    with open(LOG, "a") as f: f.write(f"{datetime.datetime.utcnow():%H:%MZ} {m}\n")
def now(): return datetime.datetime.utcnow()
CUT = now().replace(hour=0, minute=30, second=0, microsecond=0)
if CUT < now(): CUT += datetime.timedelta(days=1)
def mirror_gate():
    subprocess.run(["bash", "tools/r365_mirror.sh"], capture_output=True, timeout=280)
    r = subprocess.run([sys.executable, "tools/pipe_stock_gate.py", "--merge-out", "data/newx365/stocks365_merged"], capture_output=True, text=True, timeout=120)
    return r.returncode == 0, r.stdout.strip().replace("\n", " | ")
def derive():
    d = "data/wiring/pipe/stocks365_summary"; os.makedirs(d, exist_ok=True)
    subprocess.run([sys.executable, "tools/v15_row365_summarize.py", "--dir", "data/newx365/stocks365_merged", "--out", d], capture_output=True, timeout=300)
    by = collections.defaultdict(lambda: {"B_bright": [], "B_evaluated": [], "cnt": collections.Counter()})
    p = os.path.join(d, "filter_proposal.csv")
    if os.path.exists(p):
        for r in csv.DictReader(open(p)):
            cs = r["cat_side"]; k = f"{r['switch']}={r['cand']}\t{r['filter']}"
            by[cs]["cnt"][r["verdict"]] += 1; by[cs]["B_evaluated"].append(k)
            if r["verdict"] == "KEEP_YELLOW": by[cs]["B_bright"].append(k)
    for cs, v in by.items():
        json.dump({"_doc": "PIPE PROPOSAL (stock 365D, filters on non-zero rows): B_bright=KEEP_YELLOW; paint only on explicit user request", "filters": {}, "rows": {}, "B_bright": v["B_bright"], "B_evaluated": v["B_evaluated"], "verdict_counts": dict(v["cnt"])}, open(f"data/wiring/pipe/yellow_proposal_{cs}.json", "w"))
    def load(globs):
        out = collections.defaultdict(list)
        for g in globs:
            for f in glob.glob(g):
                try: pj = json.load(open(f))
                except Exception: continue
                if pj.get("invalid_baseline") or pj.get("unverifiable"): continue
                ss = os.path.basename(f).replace("_newx.json", "").replace("_v14_progress.json", "")
                for k, e in (pj.get("done") or {}).items():
                    if not isinstance(e, dict) or e.get("delta") is None or e.get("delta_invalid"): continue
                    m = re.match(r"^([A-Z0-9_]+)!\d+:(.+)$", k)
                    if m and m.group(2) == "COUNTER_TREND_ADD_BLOCK_ENABLED=True": out[ss].append(float(e["delta"]))
        return out
    a30 = load(["data/newx/*/*/*_newx.json", "data/newx/*/*_newx.json"]); a365 = load(["data/newx365/stocks365_merged/*_v14_progress.json"])
    res = {}
    for ss in sorted(a30):
        if ss.endswith(("USDT_LONG", "USDC_LONG", "USDT_SHORT", "USDC_SHORT")): continue
        m30 = sum(a30[ss]) / len(a30[ss]); m365 = (sum(a365[ss]) / len(a365[ss])) if a365.get(ss) else None
        res[ss] = (round(m30, 3), None if m365 is None else round(m365, 3))
    ap = sorted(ss for ss, (a, b) in res.items() if a > 0 and b is not None and b >= 0)
    json.dump({"_doc": "PIPE per-symbol COUNTER_TREND_ADD_BLOCK_ENABLED=True (stock sym_sides: 30D alone >0 AND stock 365D >=0). cat_side default stays False. STAGED ONLY: the live stocks per-sym layer (_full_recipe_live_cfgs) rejects entries without a promotion-validation envelope", "apply": {ss: {"COUNTER_TREND_ADD_BLOCK_ENABLED": True} for ss in ap}, "evidence_30d_365d": res, "only30_positive_no_365": sorted(ss for ss, (a, b) in res.items() if a > 0 and b is None)}, open("data/wiring/pipe/per_sym_counter_trend.json", "w"), indent=1)
    return {cs: dict(v["cnt"]) for cs, v in by.items()}, len(ap), len(res)
log("poller started; cutoff %s" % CUT.strftime("%H:%MZ"))
done_at = None; last = 0
END = now() + datetime.timedelta(hours=5)
while now() < END:
    try:
        ok, msg = mirror_gate()
    except Exception as e:
        log(f"mirror/gate error {e}"); ok, msg = False, "error"
    log(f"gate ok={ok} {msg[:300]}")
    if done_at is None and (ok or now() >= CUT):
        s, n_ap, n_ev = derive(); done_at = now()
        log(f"PHASE B stock evidence applied ({'threshold met' if ok else 'CUTOFF'}): yellow proposal counts {s}; per-sym COUNTER_TREND apply={n_ap} of {n_ev} stock sym_sides with 30D evidence")
    elif done_at is not None and (time.time() - last) > 1200:
        s, n_ap, n_ev = derive(); last = time.time(); log(f"refresh: yellow {s}; counter_trend apply={n_ap}/{n_ev}")
    if done_at and now() > done_at + datetime.timedelta(hours=2): break
    time.sleep(300)
log("poller finished")
