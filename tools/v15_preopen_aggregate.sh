#!/bin/bash
# Phase 1+2 of the pre-open pipeline (SCH 2026-10-01): aggregate REAL per-sym_side deltas from run19 progress on s1/s2/s5 -> merged workbook (no template touched).
# usage: tools/v15_preopen_aggregate.sh OUT_XLSX   (writes partials to data/wiring/preopen/)
set -e
cd /Users/niels/Documents/binance
OUT=${1:-SPREADSHEETS/v15_vector_delta_preopen.xlsx}
P=""
for h in s1-pub s2 s5; do
  scp -q tools/v15_vector_delta_rebuild.py $h:/tmp/v15_vector_delta_rebuild.py
  ssh $h 'cd ~/v15_run19_20261001/progress && ls $PWD/*_v14_progress.json > /tmp/preopen_manifest.txt && cd ~/binance-sandbox && mkdir -p SPREADSHEETS && (.venv/bin/python /tmp/v15_vector_delta_rebuild.py --files /tmp/preopen_manifest.txt --emit-partial /tmp/preopen_partial.json 2>&1 | tail -2)'
  scp -q $h:/tmp/preopen_partial.json data/wiring/preopen/partial_$h.json
  P="$P,data/wiring/preopen/partial_$h.json"
done
python3 tools/v15_vector_delta_rebuild.py --merge "${P#,}" --out "$OUT" 2>&1 | tail -5
