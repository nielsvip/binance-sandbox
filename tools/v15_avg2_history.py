#!/usr/bin/env python3
"""AVG2 server-side phase: per sym_side history over ALL evidence progress JSONs on this host (30D rows). Same NO-LIES exclusions as v15_vector_delta_rebuild.add_file.
EVID 2026-10-01: file list from data/avg2_sources.json (tools/avg2_sources.py): live progress dirs AND contaminated_* quarantine folders (look-ahead crypto results stay in POS_SYM / n_sym);
e3zero / sawtooth quarantines are excluded (invalid deltas). Every file is classified clean|contaminated (crypto clean only after the AUDIT/001 cutoff; stocks always clean).
out: {symside: {"TAB\tkind\tname": [any_pos(0/1), n_files, first_mtime, last_mtime, latest_value, pos_clean, n_clean, pos_cont, n_cont, latest_clean_value|None]}}
usage: v15_avg2_history.py OUT.json [SOURCES.json]"""

import json, os, sys

sys.path.insert(0, "/tmp")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import v15_vector_delta_rebuild as V
import avg2_sources as S

cfg = S.load(sys.argv[2] if len(sys.argv) > 2 else None)
files = S.list_files(cfg)
out = {}
used = skipped = used_cont = 0
for p, kind in sorted(files, key=lambda x: os.path.getmtime(x[0])):
    mt = os.path.getmtime(p)
    ss = os.path.basename(p)[: -len("_v14_progress.json")]
    try:
        s, real, is56, fg, _done_n = V.scan_file(p)
    except Exception:
        skipped += 1
        continue
    if not (is56 and mt >= V.FIX_CUTOFF_EPOCH and real > 0):
        skipped += 1
        continue
    agg, seen = V.new_agg()
    V.add_file(p, agg, seen)
    cs = V.cat_side_of(ss)
    if cs is None:
        continue
    clean = S.is_clean(p, kind, mt, cfg)
    d = out.setdefault(ss, {})
    for k, lst in agg[cs].items():
        v = lst[0]
        pos = 1 if v > V.EPS else 0
        e = d.get(k)
        if e is None:
            e = [0, 0, mt, mt, v, 0, 0, 0, 0, None]
            d[k] = e
        e[0] = 1 if (e[0] or pos) else 0
        e[1] += 1
        e[3] = mt
        e[4] = v
        if clean:
            e[5] = 1 if (e[5] or pos) else 0
            e[6] += 1
            e[9] = v
        else:
            e[7] = 1 if (e[7] or pos) else 0
            e[8] += 1
    used += 1
    used_cont += 0 if clean else 1
json.dump(out, open(sys.argv[1], "w"))
print(
    f"[hist] files_used={used} contaminated_used={used_cont} skipped={skipped} symsides={len(out)}"
)
