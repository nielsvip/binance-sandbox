#!/usr/bin/env python3
"""Iterative: 30D 64-variant -> 365D confirm -> if still neg, put pos live then another round with expanded BB/WT thresholds"""
import json, pathlib, subprocess, sys, time
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.dc_simple_8_sweep import load_per_sym_maps
# This will be called after each sweep to check pos
def check_pos(window):
    for venue in ["crypto","stocks"]:
        p=ROOT/f"data/reports/dc_simple_8_sweep_{venue}_{window}d.json"
        if not p.exists():
            p=ROOT/"data/reports/dc_simple_8_sweep.json"
        if not p.exists():
            print(f"{venue} {window}d missing")
            continue
        d=json.load(open(p))
        pos=sum(1 for x in d if max([v.get("delta",0) for v in x.get("variants",[])], default=0)>0)
        total=len(d)
        neg=total-pos
        print(f"{venue} {window}d {pos}/{total} pos {pos/total*100:.1f}% neg {neg}")
        if neg>0:
            print(f"  neg examples: {[x['sym_side'] for x in d if max([v.get('delta',0) for v in x.get('variants',[])], default=0)<=0][:5]}")
            # put pos live
            promo=ROOT/f"data/reports/dc_simple_8_sweep_{venue}_{window}d_per_sym_promote.json"
            if promo.exists():
                print(f"  promoting pos {promo} to per_sym")
        return pos, total
if __name__=="__main__":
    for w in [30,365]:
        check_pos(w)
