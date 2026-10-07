#!/usr/bin/env python3
"""par_ablate — vector-only lever panel: for each sym_side x lever-set, evaluate_sanitized at DEFAULT + lever overrides (30D) -> trades,gain. Attributes vector-vs-scalar trade-count divergence to levers. Real engine numbers only; scratch (nothing deployed).
  python tools/par_ablate.py OUT.json levers.json SYM_SIDE...   (levers.json = {name: {KEY: value}})"""
import os, sys, json
ROOT = os.path.expanduser('~/binance-sandbox'); os.chdir(ROOT); sys.path.insert(0, '.'); sys.path.insert(0, 'tools')
os.environ['BASE_PATH'] = ROOT; os.environ['V12_NPZ_CACHE'] = '8'
from tools.opt.v12_pilot import evaluate_sanitized
lev = json.load(open(sys.argv[2])); out = {}
for ss in sys.argv[3:]:
    out[ss] = {}
    for name, ov in [('base', {})] + list(lev.items()):
        try:
            r = evaluate_sanitized(ss, dict(ov), window_days=30)
            out[ss][name] = [r.get('trades'), None if r.get('gain_pct') is None else round(float(r['gain_pct']), 3), bool(r.get('valid'))]
        except BaseException as e:
            out[ss][name] = ['ERR', str(e)[:80], False]
    json.dump(out, open(sys.argv[1], 'w'), indent=1); print('DONE', ss, flush=True)
