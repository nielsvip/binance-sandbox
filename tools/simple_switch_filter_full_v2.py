#!/usr/bin/env python3
"""Redo simple_calc with every filter on top of generic default filter delta for each switch.
Baseline = all defaults, then for each switch (alt), compute switch delta, then for each filter compute switch+filter delta vs baseline AND vs switch alone (filter on top of generic).
Writes CSV with baseline, switch delta, filter delta, and filter-on-top delta.
"""
from pathlib import Path
import sys, csv, time
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import openpyxl
from tools.opt.evaluate_v12 import evaluate_many
from tools.opt.v14_pilot import load_switches, load_filters, coerce, alt_for

def run_one(symside, window, template_path, out_path):
    switches=load_switches(template_path)
    filters=load_filters(template_path)
    # Keep all switches where alt != default
    switches=[(s,w,d) for s,w,d in switches if alt_for(d) != d]
    print(f"{symside} {window}d: {len(switches)} switches x {len(filters)} filters = {len(switches)*len(filters)} F cells + generic")
    # Baseline all defaults
    from tools.opt.evaluate_v12 import evaluate
    base=evaluate(symside, {}, window_days=window)
    bg=float(base.get("gain_pct") or 0)
    bt=int(base.get("trades") or 0)
    print(f"  baseline gain {bg:.4f} trades {bt} bh {base.get('bh_pct')} valid {base.get('valid')}")
    # For each switch, first compute switch alone delta (generic default filter = no filter)
    # Then for each filter, compute switch+filter vs baseline and vs switch alone
    # Batch all switch alone + switch+filter together for speed via evaluate_many
    overrides=[]
    meta=[]
    # First, switch alone entries
    for sheet, sw, default in switches:
        alt=alt_for(default)
        overrides.append({sw: alt})
        meta.append((sheet, sw, alt, "GENERIC_DEFAULT", "none", "switch_alone"))
    # Then switch+filter
    for sheet, sw, default in switches:
        alt=alt_for(default)
        for filt, fval in filters:
            overrides.append({sw: alt, filt: coerce(fval)})
            meta.append((sheet, sw, alt, filt, fval, "switch_filter"))
    print(f"  evaluating {len(overrides)} combos (switch alone + switch+filter) ...")
    t0=time.time()
    chunk=200
    results=[]
    for i in range(0, len(overrides), chunk):
        chunk_ovs=overrides[i:i+chunk]
        res=evaluate_many(symside, chunk_ovs, window_days=window)
        results.extend(res)
        if (i//chunk+1) % 20 == 0 or i+chunk >= len(overrides):
            print(f"    chunk {i//chunk+1}/{(len(overrides)+chunk-1)//chunk} done {len(results)}/{len(overrides)} elapsed {time.time()-t0:.1f}s", flush=True)
    print(f"  done {len(results)} evals in {time.time()-t0:.1f}s")
    # Split results: first len(switches) are switch alone, rest are switch+filter
    n_sw=len(switches)
    switch_alone=results[:n_sw]
    switch_filter=results[n_sw:]
    # Map switch alone gain per switch
    sw_gain={}
    for (sheet, sw, alt, _, _, _), res in zip(meta[:n_sw], switch_alone):
        sw_gain[(sheet, sw, alt)] = float(res.get("gain_pct") or 0)
    # Write CSV
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="") as f:
        w=csv.writer(f)
        w.writerow(["switch_sheet","switch","switch_value","filter","filter_value","baseline_gain","switch_gain","switch_delta_vs_baseline","new_gain","delta_vs_baseline","delta_vs_switch_alone","baseline_trades","switch_trades","new_trades","delta_trades_vs_baseline","delta_trades_vs_switch","window_days","symside","valid"])
        idx=0
        for sheet, sw, default in switches:
            alt=alt_for(default)
            sg=sw_gain[(sheet, sw, alt)]
            # Find switch alone trades
            # Need to find corresponding switch alone result
            # We have it in order
            sw_idx=switches.index((sheet, sw, default))
            sw_res=switch_alone[sw_idx]
            st=int(sw_res.get("trades") or 0)
            s_valid=sw_res.get("valid")
            for filt, fval in filters:
                res=switch_filter[idx]
                ng=float(res.get("gain_pct") or 0)
                nt=int(res.get("trades") or 0)
                delta_baseline=ng-bg
                delta_vs_switch=ng-sg
                w.writerow([sheet, sw, alt, filt, fval, f"{bg:.6f}", f"{sg:.6f}", f"{sg-bg:.6f}", f"{ng:.6f}", f"{delta_baseline:.6f}", f"{delta_vs_switch:.6f}", bt, st, nt, nt-bt, nt-st, window, symside, res.get("valid")])
                idx+=1
    print(f"Wrote {out_path} {out_path.stat().st_size//1024}K {len(switch_filter)+1} lines")
    return out_path

if __name__ == "__main__":
    import argparse
    ap=argparse.ArgumentParser()
    ap.add_argument("--window-days", type=int, default=30)
    ap.add_argument("--max-filters", type=int, default=0)
    args=ap.parse_args()
    tpl=ROOT / "SPREADSHEETS/TEMPLATE.xlsx"
    # Allow limiting for quick test
    # Monkey patch load to limit if needed
    if args.max_filters:
        orig_s, orig_f = load_switches, load_filters
        def lim_s(p):
            s=orig_s(p)
            return s
        def lim_f(p):
            f=orig_f(p)
            return f[:args.max_filters] if args.max_filters else f
        import tools.opt.v14_pilot as v14m
        # patch via globals
        globals()["load_switches"]=lim_s
        globals()["load_filters"]=lim_f
    for sym in ["SNDK_LONG","ZECUSDC_LONG"]:
        out=ROOT / f"data/reports/simple_full_{sym}_{args.window_days}d_v2.csv"
        run_one(sym, args.window_days, tpl, out)
