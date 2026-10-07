#!/bin/bash
# AVG2 2026-10-01: aggregate REAL deltas from run20 + run19 progress on s1/s2/s5. One file per sym_side (select_latest: newest valid file whose real-cell count >= 50% of the sym_side's best), never double counted.
# usage: tools/v15_avg2_aggregate.sh OUT_XLSX
set -e
cd /Users/niels/Documents/binance
OUT=${1:-/tmp/agg_avg2.xlsx}
W=data/wiring/avg2; mkdir -p $W
: > $W/scan_all.tsv
for h in s1-pub s2 s5; do
  timeout 60 scp -q tools/v15_vector_delta_rebuild.py $h:/tmp/v15_vector_delta_rebuild.py
  timeout 60 scp -q tools/avg2_sources.py data/avg2_sources.json $h:/tmp/
  timeout 600 ssh $h 'ls /home/niels/v15_run*_2026*/progress/*_v14_progress.json /home/niels/v15_run*_2026*/contaminated_*/progress/*_v14_progress.json 2>/dev/null | grep -E "v15_run(19|2[0-9])_" > /tmp/avg2_all.txt; cd ~/binance-sandbox && nice -n 5 .venv/bin/python /tmp/v15_vector_delta_rebuild.py --files /tmp/avg2_all.txt --scan /tmp/avg2_scan.tsv | tail -1'
  timeout 60 scp -q $h:/tmp/avg2_scan.tsv $W/scan_$h.tsv
  awk -v h=$h '{print $0"\t"h}' $W/scan_$h.tsv >> $W/scan_all.tsv
done
python3 - <<'PY'
import sys,collections
sys.path.insert(0,'tools')
import v15_vector_delta_rebuild as V
rows=[]
for l in open('data/wiring/avg2/scan_all.tsv'):
    p=l.rstrip('\n').split('\t')
    if len(p)<7: continue
    rows.append((p[0],int(p[1]),int(p[2]),p[3],float(p[4]),p[5],p[6]))
sys.path.insert(0,'tools')
import avg2_sources as S
cfg=S.load('data/avg2_sources.json')
def _is_clean(r):
    kind='contaminated' if '/contaminated_' in r[5] else 'progress'
    return S.is_clean(r[5],kind,r[4],cfg)
sel=V.select_latest([(r[0],r[1],r[2],r[3],r[4],r[5],r[6]) for r in rows],is_clean=_is_clean)
per=collections.defaultdict(list)
src=collections.Counter()
for ss,r in sel.items():
    per[r[6]].append(r[5]); src['run20' if 'run20' in r[5] else 'run19']+=1
    src['sel_'+V.SELECT_FLAGS.get(ss,'clean')]+=1
for h,l in per.items(): open(f'data/wiring/avg2/manifest_{h}.txt','w').write('\n'.join(l)+'\n')
import json; json.dump({'selected':len(sel),'source':dict(src),'flags':dict(V.SELECT_FLAGS),'mtimes':{ss:r[4] for ss,r in sel.items()}},open('data/wiring/avg2/selection.json','w'))
print('selected',len(sel),dict(src))
PY
P=""
for h in s1-pub s2 s5; do
  [ -s $W/manifest_$h.txt ] || continue
  timeout 60 scp -q $W/manifest_$h.txt $h:/tmp/avg2_manifest.txt
  timeout 600 ssh $h 'cd ~/binance-sandbox && nice -n 5 .venv/bin/python /tmp/v15_vector_delta_rebuild.py --files /tmp/avg2_manifest.txt --emit-partial /tmp/avg2_partial.json | tail -1'
  timeout 60 scp -q $h:/tmp/avg2_partial.json $W/partial_$h.json
  P="$P,$W/partial_$h.json"
done
python3 tools/v15_vector_delta_rebuild.py --merge "${P#,}" --out "$OUT" 2>&1 | tail -6
echo AGG_DONE $OUT
