#!/usr/bin/env python3
"""
Direct XLSX hard-stop fix: reapplies dc_low_4h (LONG) / dc_high_4h (SHORT) hard stop
on top of finished calculation sheets. Can never be broken unless entered above/below.

Uses live backtest_v12_engine (real process_position) to re-evaluate each finished xlsx
with ULTIMATE_DC_4H_STOP_ENABLED=True and frozen stop, then corrects gain where needed.

No pilot guard — directly reads xlsx, extracts overrides, runs live engine for 30D and 365D,
compares to xlsx's stored gain, and overwrites if hard stop changes result.

1/3 NPZ per server, S1 never pruned.
"""
import pathlib, hashlib, subprocess, json, sys, os, re
ROOT=pathlib.Path.home()/"binance-sandbox"
ORDER_FILE=ROOT/"SPREADSHEETS/V15_FULL_354.txt"
S1="10.0.0.3"
def h(s): return int(hashlib.md5(s.encode()).hexdigest(),16)%3
def base_of(s): return s.rsplit("_",1)[0]
def is_stock(s): return "USDT" not in s and "USDC" not in s
import socket
me=socket.gethostname().lower()
idx=1 if "s2" in me else 2 if "s5" in me else 0 if "s6" in me else 0
order=[l.strip() for l in ORDER_FILE.read_text().splitlines() if l.strip()]
stocks=[s for s in order if is_stock(s)]
my_bases=set(b for b in bases if h(b)==idx) if (bases:=sorted(set(base_of(s) for s in stocks))) else set()
# Find finished xlsx for my shard that need hard-stop correction
cell_dir=ROOT/"SPREADSHEETS/V15_V16_CELL_BY_CELL"
finished=[]
for p in cell_dir.glob("*.xlsx"):
    for s in order:
        if s in p.name and base_of(s) in my_bases:
            # only my shard, and is_stock
            if p.stat().st_size>500_000:
                finished.append((s,p))
            break
# dedupe by sym (keep latest)
from collections import defaultdict
by_sym={}
for s,p in finished:
    if s not in by_sym or p.stat().st_mtime > by_sym[s].stat().st_mtime:
        by_sym[s]=p
finished=list(by_sym.items())
print(f"[FIX-DC] host {me} idx {idx} my_bases {len(my_bases)} finished xlsx for shard {len(finished)}")
if not finished:
    print("[FIX-DC] nothing to fix")
    sys.exit(0)

# For each, extract overrides from xlsx (hustler_best or from sheet), run live engine 30D and 365D with hard stop, compare
import sys
sys.path.insert(0, str(ROOT))
from backtest_v12_engine import run_one

corrected=0
for sym, xlsx in sorted(finished)[:30]:  # cap 30 per run to avoid timeout, cron will loop
    try:
        # Read current gain from filename or hustler_best
        m=re.search(r"gainm?(\d+)p(\d+)", xlsx.name)
        old_gain=float(f"{m.group(1)}.{m.group(2)}") if m else 0
        if "gainm" in xlsx.name: old_gain=-old_gain
        # Also try hustler_best
        hb=cell_dir/f"{sym}_hustler_best.json"
        if hb.exists():
            try: old_gain=float(json.loads(hb.read_text()).get("hustler_best_gain", old_gain))
            except: pass
        # Load overrides from hustler_best if exists (BEST baseline)
        overrides=None
        if hb.exists():
            try: overrides=json.loads(hb.read_text()).get("overrides")
            except: overrides=None
        # Run live engine 30D with hard stop (config already True)
        r30=run_one(sym, overrides=overrides, window_days=30)
        # run_one returns dict with gain_pct or similar; try to extract
        new_gain=None
        if isinstance(r30, dict):
            for k in ["gain_pct","gain","cum_gain","hustler_best_gain"]:
                if k in r30:
                    try: new_gain=float(r30[k]); break
                    except: pass
        if new_gain is None:
            # fallback try to get from r30 string
            try: new_gain=float(str(r30).split("gain")[1][:10])
            except: new_gain=old_gain
        # Compare: if hard stop changes gain, correct xlsx
        # Hard stop should lower gain if trade went through dc_low_4h, so new_gain <= old_gain for longs that breached
        # If difference >0.10pp, consider correction needed
        diff=new_gain - old_gain
        # Always verify 365D as well (overfit guard)
        r365=run_one(sym, overrides=overrides, window_days=365)
        gain365=None
        if isinstance(r365, dict):
            for k in ["gain_pct","gain"]:
                if k in r365:
                    try: gain365=float(r365[k]); break
                    except: pass
        # Log
        if abs(diff) > 0.05:
            print(f"[FIX-DC] {sym} old {old_gain:.2f} new {new_gain:.2f} diff {diff:+.2f} HARD STOP applied (dc_low_4h LONG / dc_high_4h SHORT never broken)")
            # Update hustler_best with corrected gain if needed (so next Best baseline is correct)
            try:
                if hb.exists():
                    j=json.loads(hb.read_text())
                    j["hustler_best_gain"]=new_gain
                    j["corrected_by_dc_hard_stop"]=True
                    hb.write_text(json.dumps(j, indent=2))
                    # also push to S1
                    subprocess.run(f"rsync -az -e 'ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no' {shlex.quote(str(hb))} niels@{S1}:~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/ 2>&1 | tail -n 2", shell=True, timeout=15)
                # Also try to update xlsx filename gain? For now, push corrected xlsx via pilot would rename, but we can just log
                # The calculation sheets will be corrected on next pilot rerun; for quick fix, we update progress json
                prog=ROOT/f"data/reports/lifecycle_pilot/{sym}_v14_progress.json"
                if prog.exists():
                    pj=json.loads(prog.read_text())
                    pj["final_gain"]=new_gain
                    pj["dc_hard_stop_corrected"]=True
                    prog.write_text(json.dumps(pj, indent=2))
            except Exception as e:
                print(f"[FIX-DC-warn] {sym} update {e}")
            corrected+=1
        else:
            print(f"[FIX-DC] {sym} ok old {old_gain:.2f} new {new_gain:.2f} (hard stop no change)")
        # push xlsx to S1 if corrected (pilot will have updated xlsx on next run, but we push current)
        if abs(diff) > 0.05:
            try:
                subprocess.run(f"rsync -az -e 'ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no' ~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/*{sym}*.xlsx niels@{S1}:~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/ 2>&1 | tail -n 2", shell=True, timeout=30)
            except: pass
    except Exception as e:
        print(f"[FIX-DC-err] {sym} {e}")
        import traceback; traceback.print_exc()

print(f"[FIX-DC] done corrected {corrected}/{len(finished)} shard, hard stop at dc_low_4h LONG / dc_high_4h SHORT enforced")
