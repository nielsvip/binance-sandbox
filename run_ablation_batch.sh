#!/bin/bash
# Server-side tradier ablation orchestrator - runs on server2 directly
# Each job ~7.5GB RAM. Max 3 concurrent on 30GB server.
PYTHON="/home/niels/miniconda3/envs/binance_env/bin/python"
BASE="/home/niels/binance-sandbox"
RDIR="$BASE/tradier_ablation_results"
MAX_CONC=2

mkdir -p "$RDIR"
log() { echo "[$(date -u '+%H:%M:%S UTC')] $1"; }

count_running() {
    ps aux | grep "backtest_v5_full_tradier" | grep -v "bash -c" | grep -v grep | wc -l
}

run_mode() {
    local MODE="$1"
    local LOGF="$RDIR/abl_${MODE}.log"
    local DONEF="$RDIR/abl_${MODE}.done"
    [ -f "$DONEF" ] && { log "SKIP $MODE (already done: $(cat $DONEF))"; return; }
    if ps aux | grep -v grep | grep -v "bash -c" | grep "backtest_v5_full_tradier" | grep -q "ablation ${MODE}"; then
        log "SKIP $MODE (already running)"
        return
    fi
    while true; do
        RUNNING=$(count_running)
        [ "$RUNNING" -lt "$MAX_CONC" ] && break
        log "  $RUNNING/$MAX_CONC running, waiting 30s..."
        sleep 30
    done
    log "Launching $MODE..."
    setsid bash -c "cd $BASE && $PYTHON backtest_v5_full_tradier.py --all --start 2024-01-01 --capital 70000 --account trb --ablation ${MODE} --noloss 0 > ${LOGF} 2>&1 && echo SUCCESS > ${DONEF} || echo FAILED > ${DONEF}" &
    disown $!
    sleep 5
    RUNNING=$(count_running)
    log "  Launched $MODE | now running: $RUNNING"
}

log "===== TRADIER ABLATION SWEEP (max $MAX_CONC concurrent) ====="
for MODE in ALL NO_STOP NO_OPEN NO_AUGMENT NO_REENTRY STOP_ONLY OPEN_ONLY WT_EXIT; do
    run_mode "$MODE"
done

log "All modes dispatched. Monitoring..."
for MODE in ALL NO_STOP NO_OPEN NO_AUGMENT NO_REENTRY STOP_ONLY OPEN_ONLY WT_EXIT; do
    DONEF="$RDIR/abl_${MODE}.done"
    LOGF="$RDIR/abl_${MODE}.log"
    T0=$(date +%s)
    while true; do
        if [ -f "$DONEF" ]; then
            STATUS=$(cat "$DONEF")
            ELAPSED=$(( $(date +%s) - T0 ))
            log "  $MODE: $STATUS (${ELAPSED}s)"
            break
        fi
        ELAPSED=$(( $(date +%s) - T0 ))
        [ $ELAPSED -gt 7200 ] && { log "  TIMEOUT $MODE after ${ELAPSED}s"; break; }
        PROGRESS=$(tail -1 "$LOGF" 2>/dev/null || echo "waiting...")
        log "  $MODE: $PROGRESS"
        sleep 60
    done
done
log "===== ALL ABLATION DONE ====="
