#!/usr/bin/env python3
"""365D confirm before live - test best 30D TF on 365D window, keep only if 365D pos"""
import json, pathlib, sys
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.dc_simple_8_sweep import load_per_sym_maps, eval_gain
import v12_quick_engine as V
from tools.opt.evaluate_v12 import _exact_30d_slice
def confirm_365(window=30):
    for venue in ["crypto","stocks"]:
        p30=ROOT/f"data/reports/dc_simple_8_sweep_{venue}_{window}d.json"
        if not p30.exists():
            print(f"{venue} {window}d missing {p30}")
            continue
        d=json.load(open(p30))
        print(f"{venue} {window}d {len(d)} loaded for 365D confirm")
        # for each, test best on 365D
        for x in d[:3]:
            best=x.get("best_overall")
            if not best: continue
            print(f"  {x['sym_side']} best {best['variant']} delta {best['delta']} -> will test on 365D")
