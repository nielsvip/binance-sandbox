#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# SWEEP AUTO-CHAIN — monitors Phase 1, auto-launches follow-ups
# Run on each server: screen -dmS autochain bash sweep_autochain.sh [s1|s2]
#
# Monitors entry_gates completion → launches:
#   Phase 1b: reentry block combos (512 cfgs)
#   Phase 2:  top-50 on full symbols
#   Phase 3:  exit tuning
#   ...continuous
# ═══════════════════════════════════════════════════════════════
set -uo pipefail

MACHINE="${1:-s1}"
LOGFILE="/tmp/sweep_autochain_${MACHINE}.log"

log() { echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] $*" | tee -a "$LOGFILE"; }

if [[ "$MACHINE" == "s1" ]]; then
    PY="/home/niels/.conda/envs/binance_env/bin/python"
    BASE="/home/niels/binance-sandbox"
    W_CRYPTO=4
    W_TRADIER=4
elif [[ "$MACHINE" == "s2" ]]; then
    PY="/home/niels/miniconda3/envs/binance_env/bin/python"
    BASE="/home/niels/binance-sandbox"
    W_CRYPTO=0
    W_TRADIER=6
else
    PY="/opt/anaconda3/envs/binance_env/bin/python"
    BASE="/Users/niels/Documents/binance"
    W_CRYPTO=3
    W_TRADIER=3
fi

SWEEP_DIR="$BASE/data/sweep_results"
cd "$BASE"

# ═══ Wait for Phase 1 entry_gates to finish ═══
log "Waiting for Phase 1 entry_gates to complete on $MACHINE..."
while true; do
    # Check if any quick_sweep screen is still running
    SCREENS=$(screen -ls 2>/dev/null | grep -c "quick_" || true)
    if [[ $SCREENS -eq 0 ]]; then
        log "No quick_sweep screens running — Phase 1 likely complete"
        break
    fi
    # Also check if CSV has all 3072 results
    for csv in "$SWEEP_DIR"/v8_quick_*_entry_gates_*.csv; do
        if [[ -f "$csv" ]]; then
            LINES=$(wc -l < "$csv" 2>/dev/null || echo 0)
            if [[ $LINES -gt 3070 ]]; then
                log "CSV $csv has $LINES lines — Phase 1 complete for this mode"
            fi
        fi
    done
    sleep 60
done

log "═══ PHASE 1 COMPLETE — launching Phase 1b + Phase 2 ═══"

# ═══ Phase 1b: Reentry block combos (512 configs) ═══
if [[ $W_CRYPTO -gt 0 ]]; then
    log "Launching Phase 1b: reentry blocks (crypto, 512 cfgs, $W_CRYPTO workers)"
    screen -dmS rb_crypto bash -c "cd $BASE && $PY -u sweep_reentry_blocks.py --mode crypto --symbols fast --start 2022-01-01 --workers $W_CRYPTO --resume 2>&1 | tee /tmp/reentry_blocks_crypto.log"
fi

if [[ $W_TRADIER -gt 0 ]]; then
    log "Launching Phase 1b: reentry blocks (tradier, 512 cfgs, $W_TRADIER workers)"
    screen -dmS rb_tradier bash -c "cd $BASE && $PY -u sweep_reentry_blocks.py --mode tradier --symbols fast --start 2024-01-01 --workers $W_TRADIER --resume 2>&1 | tee /tmp/reentry_blocks_tradier.log"
fi

# Wait for reentry blocks to finish
log "Waiting for Phase 1b reentry blocks to complete..."
while screen -ls 2>/dev/null | grep -q "rb_"; do
    sleep 30
done
log "═══ PHASE 1b COMPLETE ═══"

# ═══ Phase 2: Exit tuning on fast symbols ═══
log "Launching Phase 3: exit tuning"
if [[ $W_CRYPTO -gt 0 ]]; then
    screen -dmS exit_crypto bash -c "cd $BASE && $PY -u v8_quick_sweep.py --mode crypto --symbols fast --start 2022-01-01 --tier exit_tuning --workers $W_CRYPTO --resume 2>&1 | tee /tmp/v8_exit_crypto.log"
fi
if [[ $W_TRADIER -gt 0 ]]; then
    screen -dmS exit_tradier bash -c "cd $BASE && $PY -u v8_quick_sweep.py --mode tradier --symbols fast --start 2024-01-01 --tier exit_tuning --workers $W_TRADIER --resume 2>&1 | tee /tmp/v8_exit_tradier.log"
fi

log "Waiting for exit tuning to complete..."
while screen -ls 2>/dev/null | grep -q "exit_"; do
    sleep 60
done
log "═══ PHASE 3 COMPLETE ═══"

# ═══ Phase 5+: Full combinatorial (runs forever) ═══
log "Launching Phase 5: full combinatorial (continuous)"
if [[ $W_CRYPTO -gt 0 ]]; then
    screen -dmS full_crypto bash -c "cd $BASE && $PY -u v8_quick_sweep.py --mode crypto --symbols fast --start 2022-01-01 --tier full --workers $W_CRYPTO --resume 2>&1 | tee /tmp/v8_full_crypto.log"
fi
if [[ $W_TRADIER -gt 0 ]]; then
    screen -dmS full_tradier bash -c "cd $BASE && $PY -u v8_quick_sweep.py --mode tradier --symbols fast --start 2024-01-01 --tier full --workers $W_TRADIER --resume 2>&1 | tee /tmp/v8_full_tradier.log"
fi

log "═══ ALL PHASES LAUNCHED — full combinatorial running indefinitely ═══"
log "Results accumulate in $SWEEP_DIR/*.csv"
log "Monitor: tail -f /tmp/v8_full_*.log"

# Keep alive and log hourly progress
while true; do
    sleep 3600
    log "--- HOURLY STATUS ---"
    for csv in "$SWEEP_DIR"/*.csv; do
        if [[ -f "$csv" ]]; then
            LINES=$(wc -l < "$csv")
            BEST=$(head -2 "$csv" | tail -1 | cut -d, -f3 2>/dev/null || echo "?")
            log "  $(basename $csv): ${LINES} results, best_sharpe=$BEST"
        fi
    done
done
