#!/usr/bin/env python3
"""par_path_compare — join vector (par_vec dir) and scalar (probe dir) per-path counts -> data/wiring/parity/PATH_PARITY_<date>.{csv,md}. Real counts only."""
import json, glob, os, sys, csv, collections, datetime
R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = os.path.join(R, 'data/wiring/parity')
vecd = sys.argv[1] if len(sys.argv) > 1 else os.path.join(P, 'vec_20261001')
scd = sys.argv[2] if len(sys.argv) > 2 else os.path.join(P, 'probe_20261001')
tag = sys.argv[3] if len(sys.argv) > 3 else datetime.date.today().strftime('%Y%m%d')
agg = collections.defaultdict(lambda: [0, 0, 0, 0, 0])  # vec, scalar, n_both, n_vec_only, n_scalar_only
tot = []
for f in sorted(glob.glob(scd + '/*.json')):
    s = json.load(open(f)); ss = s['sym_side']
    vf = os.path.join(vecd, ss + '.json')
    if not os.path.exists(vf) or not s.get('scalar_counts') and not s.get('scalar'):
        continue
    v = json.load(open(vf))
    vc = v.get('vec_counts') or {}; sc = s.get('scalar_counts') or {}
    vt = (v.get('vec') or {}).get('trades'); st = (s.get('scalar') or {}).get('trades')
    ratio = round(vt / st, 2) if vt and st else None
    tot.append((ss, vt, st, ratio, (v.get('vec') or {}).get('gain_pct'), (s.get('scalar') or {}).get('gain_pct')))
    for k in set(vc) | set(sc):
        a, b = vc.get(k, 0), sc.get(k, 0)
        g = agg[k]; g[0] += a; g[1] += b
        if a and b: g[2] += 1
        elif a: g[3] += 1
        else: g[4] += 1
with open(os.path.join(P, f'PATH_PARITY_{tag}.csv'), 'w', newline='') as fh:
    w = csv.writer(fh); w.writerow(['action|path', 'vec_count', 'scalar_count', 'n_symsides_both', 'n_vec_only', 'n_scalar_only', 'verdict'])
    for k, g in sorted(agg.items(), key=lambda kv: -(kv[1][0] + kv[1][1])):
        verdict = 'BOTH' if g[2] and not g[3] and not g[4] else ('VEC_ONLY' if g[0] and not g[1] else 'SCALAR_ONLY' if g[1] and not g[0] else 'MIXED')
        w.writerow([k, g[0], g[1], g[2], g[3], g[4], verdict])
with open(os.path.join(P, f'PATH_PARITY_{tag}.md'), 'w') as fh:
    fh.write(f'# Path parity {tag}: vector (v12_quick) vs scalar live-faithful (backtest_v12_engine), defaults, 30D\n\n| sym_side | vec trades | scalar trades | ratio | vec gain | scalar gain |\n|---|---|---|---|---|---|\n')
    for r in tot: fh.write('| ' + ' | '.join(str(x) for x in r) + ' |\n')
print(len(tot), 'sym_sides joined;', len(agg), 'path keys')
for r in tot: print(r)
