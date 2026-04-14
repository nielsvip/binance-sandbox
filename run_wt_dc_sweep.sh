#!/bin/bash
# WT + DC Parameter Sweep Orchestrator
# Runs on server2 — tests ESA/CHAN/DC period combinations across all TFs
# Runtime: ~45 min for 480 configs at 4 concurrent (22s each)
# Results: ~/binance-sandbox/wt_dc_results/

PYTHON="/home/niels/miniconda3/envs/binance_env/bin/python"
BASE="/home/niels/binance-sandbox"
RDIR="$BASE/wt_dc_results"
MAX_CONC=8
SCRIPT="$BASE/backtest_wt_dc_sweep.py"
RESULTS_CSV="$RDIR/wt_dc_sweep.csv"

mkdir -p "$RDIR"
log() { echo "[$(date -u '+%H:%M:%S UTC')] $1"; }

count_running() {
    ps aux | grep "backtest_wt_dc_sweep" | grep -v grep | grep -v "bash -c" | wc -l
}

wait_for_slot() {
    while true; do
        RUNNING=$(count_running)
        [ "$RUNNING" -lt "$MAX_CONC" ] && return
        sleep 5
    done
}

run_config() {
    local ESA="$1" CHAN="$2" DC="$3" SIG_TF="$4" EXIT_TF="$5"
    local NAME="wt_e${ESA}_c${CHAN}_d${DC}_s${SIG_TF}_x${EXIT_TF}"
    local OUTF="$RDIR/${NAME}.csv"
    [ -f "$OUTF" ] && return  # skip if done
    wait_for_slot
    setsid bash -c "$PYTHON $SCRIPT --wt-esa $ESA --wt-chan $CHAN --dc-period $DC --signal-tf $SIG_TF --exit-tf $EXIT_TF --entry-dc-tf 1h --start 2024-01-01 > $OUTF 2>&1" &
    disown $!
}

# Write CSV header
echo "esa,chan,dc_period,signal_tf,exit_tf,entry_dc_tf,n_symbols,total_trades,win_rate,sharpe,pnl_pct" > "$RESULTS_CSV"

log "===== WT+DC PARAMETER SWEEP ====="
log "Grid: ESA×CHAN×DC×SignalTF×ExitTF"
log ""

# === FULL GRID ===
# ESA (channel_length): 5 values
# CHAN (average_length): 4 values
# DC period: 4 values
# Signal TF (entry): 3 values
# Exit TF: 3 values
# Total: 5×4×4×3×3 = 720 configs, ~45 min at 4 concurrent
LAUNCHED=0
for ESA in 5 8 10 13 21; do
    for CHAN in 13 21 25 34; do
        for DC in 10 15 20 25; do
            for SIG_TF in 15m 1h 4h; do
                for EXIT_TF in 1h 4h D; do
                    # Skip if signal_tf == exit_tf (no point)
                    [ "$SIG_TF" = "$EXIT_TF" ] && continue
                    run_config "$ESA" "$CHAN" "$DC" "$SIG_TF" "$EXIT_TF"
                    LAUNCHED=$((LAUNCHED + 1))
                done
            done
        done
    done
done

log "All $LAUNCHED configs dispatched. Waiting for completion..."

# Wait for all to finish
while true; do
    RUNNING=$(count_running)
    [ "$RUNNING" -eq "0" ] && break
    log "  $RUNNING still running..."
    sleep 30
done

log "All done. Merging results..."

# Merge all CSV results
for f in "$RDIR"/wt_e*.csv; do
    [ -f "$f" ] || continue
    tail -1 "$f" >> "$RESULTS_CSV" 2>/dev/null
done

# Sort by sharpe descending
TMPF="$RDIR/sorted.csv"
HEAD=$(head -1 "$RESULTS_CSV")
echo "$HEAD" > "$TMPF"
tail -n +2 "$RESULTS_CSV" | sort -t',' -k10 -rn >> "$TMPF"
mv "$TMPF" "$RESULTS_CSV"

log "Top 20 configs by Sharpe:"
head -21 "$RESULTS_CSV"
log ""
log "Results: $RESULTS_CSV"
