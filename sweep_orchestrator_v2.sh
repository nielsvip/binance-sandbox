#!/bin/bash
# Orchestrator v2 — autonomous cascade toward Sharpe > 1.8
# Runs locally; chains: quality_sniper → exit_sniper → sizing → reentry_blocks → full
set -uo pipefail

BASE="${1:-/Users/niels/Documents/binance}"
PY="${2:-/opt/anaconda3/envs/binance_env/bin/python}"
cd "$BASE"
LOG="/tmp/sweep_orchestrator_v2.log"
PROGRESS="$BASE/data/sweep_results/PROGRESS_LOG.md"

log() { echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] $*" | tee -a "$LOG"; }

append_progress() {
    local phase="$1" mode="$2" best="$3" n="$4" notes="$5"
    local ts=$(date -u '+%H:%M')
    echo "| $ts | $phase | ${best}_$mode | — | $n | $notes |" >> "$PROGRESS"
}

wait_for_process() {
    local name="$1"
    while pgrep -f "$name" >/dev/null 2>&1; do
        sleep 60
    done
}

current_best() {
    local pattern="$1"
    $PY -c "
import csv, glob
files = glob.glob('$BASE/data/sweep_results/$pattern')
if not files: print(0); exit()
best = 0
for f in sorted(files, reverse=True)[:1]:
    try:
        for r in csv.DictReader(open(f)):
            try:
                s = float(r.get('sharpe', 0) or 0)
                if s > best: best = s
            except: pass
    except: pass
print(f'{best:.3f}')
" 2>/dev/null
}

log "═══ ORCHESTRATOR V2 STARTED — targeting Sharpe > 1.8 ═══"

# Phase Q1: Quality sniper (should already be running)
log "Waiting for quality_sniper to finish..."
wait_for_process "sweep_quality_sniper.py"
SNIPER_TRADIER=$(current_best "quality_sniper_tradier*.csv")
SNIPER_CRYPTO=$(current_best "quality_sniper_crypto*.csv")
log "Quality sniper complete: tradier=$SNIPER_TRADIER crypto=$SNIPER_CRYPTO"
append_progress "quality_sniper" "" "$SNIPER_TRADIER/$SNIPER_CRYPTO" 768 "Reentry block stacking"

# Phase Q2: Exit sniper cascade
log "Launching exit_sniper (cascades from top-5 quality sniper)..."
$PY -u sweep_exit_sniper.py --mode tradier --symbols fast --start 2024-01-01 --workers 3 > /tmp/exit_sniper_tradier.log 2>&1 &
TR_PID=$!
$PY -u sweep_exit_sniper.py --mode crypto --symbols fast --start 2022-01-01 --workers 3 > /tmp/exit_sniper_crypto.log 2>&1 &
CR_PID=$!
wait $TR_PID $CR_PID
EXIT_TRADIER=$(current_best "exit_sniper_tradier*.csv")
EXIT_CRYPTO=$(current_best "exit_sniper_crypto*.csv")
log "Exit sniper complete: tradier=$EXIT_TRADIER crypto=$EXIT_CRYPTO"
append_progress "exit_sniper" "" "$EXIT_TRADIER/$EXIT_CRYPTO" "~3600" "Cascade top-5 × exit grid"

# Phase Q3: Reentry block combinations (2^9 = 512)
log "Launching reentry block combo sweep (512 configs per mode)..."
$PY -u sweep_reentry_blocks.py --mode tradier --symbols fast --start 2024-01-01 --workers 3 --resume > /tmp/rb_tradier.log 2>&1 &
RB_T=$!
$PY -u sweep_reentry_blocks.py --mode crypto --symbols fast --start 2022-01-01 --workers 3 --resume > /tmp/rb_crypto.log 2>&1 &
RB_C=$!
wait $RB_T $RB_C
RB_TRADIER=$(current_best "reentry_blocks_tradier*.csv")
RB_CRYPTO=$(current_best "reentry_blocks_crypto*.csv")
log "Reentry block sweep complete: tradier=$RB_TRADIER crypto=$RB_CRYPTO"
append_progress "reentry_blocks" "" "$RB_TRADIER/$RB_CRYPTO" 1024 "2^9 combos"

# Check if target hit
BEST=$(echo "$EXIT_TRADIER $EXIT_CRYPTO $RB_TRADIER $RB_CRYPTO" | tr ' ' '\n' | sort -gr | head -1)
log "═══ ORCHESTRATOR V2 COMPLETE ═══"
log "OVERALL BEST SHARPE: $BEST"
if (( $(echo "$BEST > 1.8" | bc -l 2>/dev/null) )); then
    log "🎯🎯🎯 TARGET HIT — Sharpe $BEST exceeds 1.8"
    append_progress "TARGET_HIT" "" "$BEST" "" "🎯🎯🎯"
else
    log "⏳ Target not yet hit — best=$BEST. Queuing full combinatorial next."
    # Chain into full sweep
    screen -dmS full_combinatorial bash -c "cd $BASE && $PY -u v8_quick_sweep.py --mode tradier --symbols fast --start 2024-01-01 --tier full --workers 3 --resume 2>&1 | tee /tmp/full_combo.log" 2>/dev/null || \
      nohup $PY -u v8_quick_sweep.py --mode tradier --symbols fast --start 2024-01-01 --tier full --workers 3 --resume > /tmp/full_combo.log 2>&1 &
    log "Full combinatorial launched in background"
fi
