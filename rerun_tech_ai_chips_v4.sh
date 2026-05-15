#!/usr/bin/env bash
# rerun_tech_ai_chips_v4.sh
# Runs tech_ai_chips in 3 sub-groups of ≤10 syms.
# Root cause: 29 syms × 3yr tradier > 3600s timeout even with no competitors.
# Fix: split to 10+10+9, each run completes in ~20 min.
#
# Usage (on S1):
#   nohup bash rerun_tech_ai_chips_v4.sh > ~/logs/sector_rerun_v4_$(date +%Y%m%d_%H%M%S).log 2>&1 < /dev/null &
#
# Created: 2026-05-14

PYTHON=/home/niels/.conda/envs/binance_env/bin/python
WORKDIR=/home/niels/binance-sandbox
LOG_DIR="$HOME/logs"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

kill_competing_engines() {
    local pids
    pids=$(pgrep -af "backtest_v8_engine|backtest_v8_precompute|sweep_coordinator" 2>/dev/null \
           | grep -v "$$\|grep\|bash -c\|rerun_tech_ai" \
           | awk '{print $1}')
    if [[ -n "$pids" ]]; then
        log "  Killing competing engines: $pids"
        echo "$pids" | xargs kill -9 2>/dev/null
        sleep 3
    fi
    local vec_pids
    vec_pids=$(pgrep -af "vec_matrix_runner" 2>/dev/null \
               | grep -v "$$\|grep\|bash -c\|rerun_tech_ai" \
               | awk '{print $1}')
    if [[ -n "$vec_pids" ]]; then
        log "  Killing vec_matrix_runner: $vec_pids"
        echo "$vec_pids" | xargs kill -9 2>/dev/null
        sleep 3
    fi
}

wait_for_clear_engines() {
    local waited=0
    while true; do
        local running
        running=$(pgrep -af "backtest_v8_engine|backtest_v8_precompute" 2>/dev/null \
                  | grep -v "$$\|grep\|bash -c\|rerun_tech_ai" | wc -l)
        [[ "$running" -eq 0 ]] && break
        [[ $waited -eq 0 ]] && log "  Waiting for $running competing engine(s)..."
        sleep 30
        waited=$((waited + 30))
    done
    [[ $waited -gt 0 ]] && log "  Engines cleared after ${waited}s."
}

run_sector_syms() {
    local mode="$1"
    local label="$2"
    local tier="$3"
    local syms="$4"
    local start_date="$5"
    local timeout_s="${6:-3600}"

    local count
    count=$(echo "$syms" | tr ',' '\n' | grep -c .)
    local TS
    TS=$(date +%Y%m%d_%H%M%S)
    local logfile="$LOG_DIR/${mode}_sector_sweep_${label}_${TS}.log"

    kill_competing_engines
    wait_for_clear_engines

    log ">>> START $mode/$label ($count syms, start=$start_date)"
    cd "$WORKDIR" || { log "ERROR: cannot cd $WORKDIR"; return; }

    V8_USE_VEC_ALL=1 V8_SWEEP_MODE=1 V8_RATE_GUARD_DISABLED=1 \
    "$PYTHON" backtest_v8_sweep.py \
        --mode "$mode" \
        --tier "$tier" \
        --symbols "$syms" \
        --start "$start_date" \
        --workers 1 \
        --timeout "$timeout_s" \
        > "$logfile" 2>&1

    local rc=$?
    log "<<< DONE $mode/$label rc=$rc log=$logfile"
    sleep 30
}

log "=== tech_ai_chips v4 (split 10+10+9) starting ==="

# tech_ai_chips: 29 syms → split 10+10+9
# Previous runs timed out at 3600s; 10 syms should complete in ~20 min each.
run_sector_syms tradier tech_ai_chips_a tradier_sector_baseline \
    "NVDA,AMD,AVGO,MU,INTC,LRCX,TXN,ARM,PLTR,CRWD" \
    2023-05-13 3600

run_sector_syms tradier tech_ai_chips_b tradier_sector_baseline \
    "CIBR,IBM,SAP,ACN,MA,PYPL,TTD,WDAY,FIVN,AXON" \
    2023-05-13 3600

run_sector_syms tradier tech_ai_chips_c tradier_sector_baseline \
    "OLED,SNDK,RBLX,ASTS,CRWV,MSTR,GOOGL,ROBO,BOTZ" \
    2023-05-13 3600

log "=== tech_ai_chips v4 complete: 3 sub-sector runs ==="
log "Results in: $WORKDIR/data/sweep_results/"
