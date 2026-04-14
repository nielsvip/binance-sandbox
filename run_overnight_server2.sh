#!/bin/bash
# Overnight master orchestrator for server2
# 1. Wait for WT Phase 1 sweep to complete
# 2. Launch WT Phase 2 deep sweep
# 3. After ALL ablation completes, run remaining ablation modes
# 4. Rsync results to local

BASE="/home/niels/binance-sandbox"
PYTHON="/home/niels/miniconda3/envs/binance_env/bin/python"
P1_RDIR="$BASE/wt_dc_results"
P2_RDIR="$BASE/wt_dc_results_p2"
ABL_RDIR="$BASE/tradier_ablation_results"

log() { echo "[$(date -u '+%H:%M:%S UTC')] $1"; }

count_sweep() {
    ps aux | grep "backtest_wt_dc_sweep" | grep -v grep | grep -v "bash -c" | wc -l
}

count_ablation() {
    ps aux | grep "backtest_v5_full_tradier" | grep -v "bash -c" | grep -v grep | wc -l
}

wait_for_sweep_done() {
    log "Waiting for Phase 1 sweep to complete..."
    while true; do
        RUNNING=$(count_sweep)
        [ "$RUNNING" -eq "0" ] && break
        DONE=$(ls "$P1_RDIR"/wt_e*.csv 2>/dev/null | wc -l)
        log "  WT P1: $DONE configs done, $RUNNING still running..."
        sleep 30
    done
    log "Phase 1 sweep DONE"
}

wait_for_all_ablation() {
    log "Waiting for ALL ablation to complete..."
    while true; do
        [ -f "$ABL_RDIR/abl_ALL.done" ] && break
        tail -1 "$ABL_RDIR/abl_ALL.log" 2>/dev/null
        sleep 60
    done
    log "ALL ablation DONE: $(cat $ABL_RDIR/abl_ALL.done)"
}

launch_ablation_mode() {
    local MODE="$1"
    local DONEF="$ABL_RDIR/abl_${MODE}.done"
    [ -f "$DONEF" ] && { log "SKIP $MODE (already done: $(cat $DONEF))"; return; }
    # Wait for free slot (max 1 ablation at a time while sweep runs)
    while [ $(count_ablation) -ge 1 ]; do
        log "  Waiting for ablation slot..."
        sleep 60
    done
    log "Launching ablation: $MODE"
    setsid bash -c "cd $BASE && $PYTHON backtest_v5_full_tradier.py --all --start 2024-01-01 --capital 70000 --account trb --ablation ${MODE} --noloss 0 > $ABL_RDIR/abl_${MODE}.log 2>&1 && echo SUCCESS > $ABL_RDIR/abl_${MODE}.done || echo FAILED > $ABL_RDIR/abl_${MODE}.done" &
    disown $!
    sleep 5
    log "  $MODE launched"
}

wait_ablation_mode() {
    local MODE="$1"
    local DONEF="$ABL_RDIR/abl_${MODE}.done"
    local T0=$(date +%s)
    while true; do
        [ -f "$DONEF" ] && { log "  $MODE DONE: $(cat $DONEF)"; break; }
        ELAPSED=$(( $(date +%s) - T0 ))
        [ $ELAPSED -gt 7200 ] && { log "  TIMEOUT $MODE"; break; }
        LAST=$(tail -1 "$ABL_RDIR/abl_${MODE}.log" 2>/dev/null | grep -oE "[0-9]+%.*$" || echo "running...")
        log "  $MODE: $LAST"
        sleep 120
    done
}

log "====== OVERNIGHT ORCHESTRATION STARTED ======"

# === STEP 1: Wait for ALL ablation to finish ===
wait_for_all_ablation

# === STEP 2: Launch remaining ablation modes ===
log "=== LAUNCHING REMAINING ABLATION MODES ==="
for MODE in NO_AUGMENT STOP_ONLY WT_EXIT OPEN_ONLY NO_REENTRY; do
    launch_ablation_mode "$MODE"
done
log "All ablation modes launched (1 at a time)"

# === STEP 3: Wait for Phase 1 WT sweep ===
wait_for_sweep_done

# === STEP 4: Show Phase 1 top results ===
log "=== PHASE 1 TOP 20 (by Sharpe) ==="
for f in "$P1_RDIR"/wt_e*.csv; do cat "$f" 2>/dev/null; done | sort -t, -k10 -rn | head -20

# === STEP 5: Launch Phase 2 deep sweep ===
log "=== LAUNCHING PHASE 2 DEEP SWEEP ==="
nohup bash "$BASE/run_wt_dc_sweep_p2.sh" > "$P2_RDIR/../wt_dc_p2_orch.log" 2>&1 &
disown $!
log "Phase 2 launched"

# === STEP 6: Monitor ablation + Phase 2 ===
log "=== MONITORING ALL JOBS ==="
for MODE in NO_AUGMENT STOP_ONLY WT_EXIT OPEN_ONLY NO_REENTRY; do
    wait_ablation_mode "$MODE"
done

# Wait for Phase 2 sweep
log "Waiting for Phase 2 sweep..."
while true; do
    RUNNING=$(count_sweep)
    [ "$RUNNING" -eq "0" ] && break
    DONE=$(ls "$P2_RDIR"/p2_e*.csv 2>/dev/null | wc -l)
    log "  WT P2: $DONE configs done, $RUNNING running..."
    sleep 60
done

# === STEP 7: Final results compilation ===
log "=== FINAL RESULTS ==="
log "== ABLATION SUMMARY =="
for MODE in ALL NO_AUGMENT STOP_ONLY WT_EXIT OPEN_ONLY NO_REENTRY; do
    STATUS=$(cat "$ABL_RDIR/abl_${MODE}.done" 2>/dev/null || echo "MISSING")
    LAST=$(tail -3 "$ABL_RDIR/abl_${MODE}.log" 2>/dev/null | grep -E "PnL:|closed" | tail -1 || echo "no log")
    log "  $MODE: $STATUS | $LAST"
done
log ""
log "== WT+DC TOP 20 OVERALL =="
for f in "$P1_RDIR"/wt_e*.csv "$P2_RDIR"/p2_e*.csv; do cat "$f" 2>/dev/null; done | sort -t, -k10 -rn | head -20

log "====== OVERNIGHT COMPLETE ======"
