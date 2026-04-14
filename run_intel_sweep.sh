#!/bin/bash
# run_intel_sweep.sh — Parallel WT intelligence threshold sweep
# Tests every WT + DC field threshold as entry/exit condition
# Run on server2 with: bash run_intel_sweep.sh [tradier|crypto]
# Results in $RDIR/intel_sweep_*.csv → merged into intel_sweep_merged.csv

MODE="${1:-tradier}"
PYTHON="/home/niels/miniconda3/envs/binance_env/bin/python"
BASE="/home/niels/binance-sandbox"
SCRIPT="$BASE/backtest_wt_intel_sweep.py"

if [ "$MODE" = "crypto" ]; then
    NPZ_DIR="$BASE/backtest_v4/indicators"
    RDIR="$BASE/intel_sweep_crypto"
else
    NPZ_DIR="$BASE/backtest_v4_tradier/indicators"
    RDIR="$BASE/intel_sweep_tradier"
fi

mkdir -p "$RDIR"
MERGED="$RDIR/intel_sweep_merged.csv"
log() { echo "[$(date -u '+%H:%M:%S UTC')] $1"; }

log "===== WT INTEL SWEEP: $MODE ====="
log "NPZ: $NPZ_DIR"
log "Results: $RDIR"

# Get total entry condition count from script
TOTAL_ENTRY=$($PYTHON -c "
import sys
sys.path.insert(0, '$BASE')
from backtest_wt_intel_sweep import build_entry_conditions
print(len(build_entry_conditions()))
" 2>/dev/null || echo 500)

log "Total entry conditions: $TOTAL_ENTRY"

# Split into N_WORKERS parallel chunks
N_WORKERS=8
CHUNK=$(( (TOTAL_ENTRY + N_WORKERS - 1) / N_WORKERS ))

log "Launching $N_WORKERS workers, each handling ~$CHUNK entry conditions..."

for i in $(seq 0 $((N_WORKERS - 1))); do
    ESTART=$((i * CHUNK))
    EEND=$((ESTART + CHUNK))
    [ $EEND -gt $TOTAL_ENTRY ] && EEND=$TOTAL_ENTRY
    OUTF="$RDIR/intel_chunk_${i}.csv"
    [ -f "$OUTF" ] && { log "SKIP chunk $i (already done)"; continue; }
    log "Launching worker $i: entries ${ESTART}:${EEND}"
    setsid bash -c "$PYTHON $SCRIPT --symbols-dir $NPZ_DIR --start 2024-01-01 --entry-chunk ${ESTART}:${EEND} --output $OUTF > $RDIR/worker_${i}.log 2>&1 && echo SUCCESS > $RDIR/worker_${i}.done || echo FAILED > $RDIR/worker_${i}.done" &
    disown $!
    sleep 1
done

log "All $N_WORKERS workers launched."
log "Waiting for completion..."

while true; do
    DONE_COUNT=$(ls "$RDIR"/worker_*.done 2>/dev/null | wc -l)
    RUNNING=$(ps aux | grep backtest_wt_intel_sweep | grep -v grep | grep -v "bash -c" | wc -l)
    [ "$DONE_COUNT" -ge "$N_WORKERS" ] && break
    log "  $DONE_COUNT/$N_WORKERS done, $RUNNING running..."
    sleep 30
done

log "All workers done. Merging results..."

# Merge all chunk CSVs
echo "entry,exit,direction,n_sym,trades,win_rate,sharpe,pnl_pct" > "$MERGED"
for f in "$RDIR"/intel_chunk_*.csv; do
    [ -f "$f" ] || continue
    tail -n +2 "$f" >> "$MERGED" 2>/dev/null
done

# Sort by sharpe descending
TMPF="$RDIR/sorted_tmp.csv"
HEAD=$(head -1 "$MERGED")
echo "$HEAD" > "$TMPF"
tail -n +2 "$MERGED" | sort -t',' -k7 -rn >> "$TMPF"
mv "$TMPF" "$MERGED"

log "Merged: $(wc -l < $MERGED) result rows"
log ""
log "=== TOP 50 LONG entries by Sharpe ==="
grep ",LONG," "$MERGED" | head -50
log ""
log "=== TOP 50 SHORT entries by Sharpe ==="
grep ",SHORT," "$MERGED" | head -50

log "===== INTEL SWEEP COMPLETE: $MODE ====="
