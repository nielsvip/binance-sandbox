#!/bin/bash
# Phase 2: WT+DC Deep Sweep — narrow grid around winners from Phase 1
# Phase 1 showed: ESA=5, CHAN=13-34, DC=10, 15m signal, 1h exit
# Phase 2: finer ESA (3-7), finer CHAN (8-15), add smooth, add HTF confirm
# ~350 configs × 25s = ~2200s = 37 min at MAX_CONC=8

PYTHON="/home/niels/miniconda3/envs/binance_env/bin/python"
BASE="/home/niels/binance-sandbox"
RDIR="$BASE/wt_dc_results_p2"
MAX_CONC=8
SCRIPT="$BASE/backtest_wt_dc_sweep.py"
RESULTS_CSV="$RDIR/wt_dc_p2_sweep.csv"

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
    local ESA="$1" CHAN="$2" DC="$3" SIG_TF="$4" EXIT_TF="$5" SMOOTH="$6" HTF="${7:-none}"
    local NAME="p2_e${ESA}_c${CHAN}_d${DC}_s${SIG_TF}_x${EXIT_TF}_sm${SMOOTH}_h${HTF}"
    local OUTF="$RDIR/${NAME}.csv"
    [ -f "$OUTF" ] && return
    wait_for_slot
    local HTF_ARG=""
    [ "$HTF" != "none" ] && HTF_ARG="--htf-confirm $HTF"
    setsid bash -c "$PYTHON $SCRIPT --wt-esa $ESA --wt-chan $CHAN --dc-period $DC --signal-tf $SIG_TF --exit-tf $EXIT_TF --entry-dc-tf 1h --wt-smooth $SMOOTH $HTF_ARG --start 2024-01-01 > $OUTF 2>&1" &
    disown $!
}

echo "esa,chan,dc_period,signal_tf,exit_tf,entry_dc_tf,smooth,htf_confirm,n_symbols,total_trades,win_rate,sharpe,pnl_pct" > "$RESULTS_CSV"

log "===== WT+DC PHASE 2 DEEP SWEEP ====="
log "Focused on 15m signal → 1h exit (Phase 1 winners)"
log ""

LAUNCHED=0

# === SECTION 1: Fine ESA sweep (3-9) × CHAN (8-34) × DC (5-15) ===
# Signal=15m, Exit=1h, Smooth=4 (baseline), No HTF
for ESA in 3 4 5 6 7 8 9; do
    for CHAN in 8 10 13 16 21; do
        for DC in 5 8 10 12 15; do
            run_config "$ESA" "$CHAN" "$DC" "15m" "1h" "4" "none"
            LAUNCHED=$((LAUNCHED + 1))
        done
    done
done

# === SECTION 2: wt2 smoothing sweep (1-8) for best configs ===
for SMOOTH in 1 2 3 4 5 6 8; do
    for CHAN in 13 21 34; do
        run_config "5" "$CHAN" "10" "15m" "1h" "$SMOOTH" "none"
        LAUNCHED=$((LAUNCHED + 1))
    done
done

# === SECTION 3: HTF confirmation gate (require 1h WT aligned) ===
for ESA in 4 5 6; do
    for CHAN in 13 21 34; do
        for DC in 8 10 15; do
            run_config "$ESA" "$CHAN" "$DC" "15m" "1h" "4" "1h"
            run_config "$ESA" "$CHAN" "$DC" "15m" "1h" "4" "4h"
            LAUNCHED=$((LAUNCHED + 2))
        done
    done
done

# === SECTION 4: Different signal→exit TF combos for top ESA/CHAN ===
for TF_COMBO in "5m 15m" "5m 1h" "15m 4h" "1h 4h" "15m D"; do
    SIG=$(echo $TF_COMBO | cut -d' ' -f1)
    EXT=$(echo $TF_COMBO | cut -d' ' -f2)
    for CHAN in 13 21; do
        run_config "5" "$CHAN" "10" "$SIG" "$EXT" "4" "none"
        LAUNCHED=$((LAUNCHED + 1))
    done
done

log "Dispatched $LAUNCHED configs. Waiting..."

while true; do
    RUNNING=$(count_running)
    [ "$RUNNING" -eq "0" ] && break
    sleep 30
done

log "Done. Merging..."
for f in "$RDIR"/p2_e*.csv; do
    [ -f "$f" ] || continue
    tail -1 "$f" >> "$RESULTS_CSV" 2>/dev/null
done

TMPF="$RDIR/sorted.csv"
HEAD=$(head -1 "$RESULTS_CSV")
echo "$HEAD" > "$TMPF"
tail -n +2 "$RESULTS_CSV" | sort -t',' -k12 -rn >> "$TMPF"
mv "$TMPF" "$RESULTS_CSV"

log "Top 30 by Sharpe:"
head -31 "$RESULTS_CSV"
log "Results: $RESULTS_CSV"
