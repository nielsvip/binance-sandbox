#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# SWEEP AUTO-CHAIN v2 — RESUME-AWARE + IDEMPOTENT
# Run on each server: screen -dmS autochain bash sweep_autochain.sh [s1|s2]
#
# Design:
# - Each phase checks if its CSV exists + is "done" BEFORE running
# - Each screen is launched only if not already running (idempotent)
# - All python sweeps use --resume so they pick up where they left off
# - Phase completion defined per-phase: CSV line count OR presence of marker file
# - If killed and restarted, skips completed phases instantly
# - Keeps running forever, looping phase 5 (mega_v2 crypto / stock_v2 tradier — NOT full, lacks K15M)
# ═══════════════════════════════════════════════════════════════
set -uo pipefail

MACHINE="${1:-s1}"
LOGFILE="/tmp/sweep_autochain_${MACHINE}.log"

log() { echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] $*" | tee -a "$LOGFILE"; }

if [[ "$MACHINE" == "s1" ]]; then
    PY="/home/niels/.conda/envs/binance_env/bin/python"
    BASE="/home/niels/binance-sandbox"
    W_CRYPTO=3  # was 4 — stay inside safe mem envelope alongside backtest_v8_engine
    W_TRADIER=0  # S1=crypto only; tradier workers on S1 are wrong-machine errors
elif [[ "$MACHINE" == "s2" ]]; then
    PY="/home/niels/miniconda3/envs/binance_env/bin/python"
    BASE="/home/niels/binance-sandbox"
    W_CRYPTO=0
    W_TRADIER=3  # was 6 — 6×3.6GB=21.6GB → OOM on 31GB box; 3×3.6GB=10.8GB safe
else
    PY="/opt/anaconda3/envs/binance_env/bin/python"
    BASE="/Users/niels/Documents/binance"
    W_CRYPTO=3
    W_TRADIER=3
fi

SWEEP_DIR="$BASE/data/sweep_results"
mkdir -p "$SWEEP_DIR"
cd "$BASE"

# ═══ HELPERS ═══
screen_running() {
    screen -ls 2>/dev/null | grep -qE "[0-9]+\.${1}\b"
}

# Returns 0 if a phase is "complete" (CSV exists with enough lines).
# $1 = glob pattern, $2 = min lines required
phase_done() {
    local pattern="$1" min_lines="$2"
    for csv in $SWEEP_DIR/$pattern; do
        [[ -f "$csv" ]] || continue
        local lines=$(wc -l < "$csv" 2>/dev/null || echo 0)
        if [[ $lines -ge $min_lines ]]; then
            return 0
        fi
    done
    return 1
}

# Launch a screen idempotently — if already running, skip
launch_screen() {
    local name="$1" cmd="$2"
    if screen_running "$name"; then
        log "  [SKIP] $name already running"
        return 0
    fi
    log "  [LAUNCH] $name"
    screen -dmS "$name" bash -c "$cmd"
    sleep 1
    if screen_running "$name"; then
        log "  [OK] $name launched"
    else
        log "  [FAIL] $name did not start"
    fi
}

# Wait until all screens matching a pattern have exited
wait_screens_gone() {
    local pattern="$1" timeout="${2:-86400}"
    local start=$(date +%s)
    while screen -ls 2>/dev/null | grep -qE "[0-9]+\.${pattern}"; do
        local elapsed=$(( $(date +%s) - start ))
        if [[ $elapsed -gt $timeout ]]; then
            log "  [TIMEOUT] waiting for $pattern after ${elapsed}s"
            return 1
        fi
        sleep 60
    done
    return 0
}

log "═══════════════════════════════════════════════════════════"
log "AUTOCHAIN v2 START on $MACHINE (resume-aware)"
log "SWEEP_DIR=$SWEEP_DIR  W_CRYPTO=$W_CRYPTO  W_TRADIER=$W_TRADIER"
log "═══════════════════════════════════════════════════════════"

# ═══════════════════════════════════════════════════════════════
# PHASE 1: entry_gates (3072 configs, fast symbols)
# ═══════════════════════════════════════════════════════════════
phase1() {
    log "--- PHASE 1: entry_gates ---"

    if [[ $W_CRYPTO -gt 0 ]]; then
        if phase_done "v8_quick_crypto_entry_gates_*.csv" 3000; then
            log "  [DONE] crypto entry_gates already has 3000+ results"
        else
            launch_screen "quick_crypto" \
                "cd $BASE && $PY -u v8_quick_sweep.py --mode crypto --symbols fast --start 2022-01-01 --tier entry_gates --workers $W_CRYPTO --resume 2>&1 | tee /tmp/v8_quick_crypto.log"
        fi
    fi

    if [[ $W_TRADIER -gt 0 ]]; then
        if phase_done "v8_quick_tradier_entry_gates_*.csv" 3000; then
            log "  [DONE] tradier entry_gates already has 3000+ results"
        else
            launch_screen "quick_tradier" \
                "cd $BASE && $PY -u v8_quick_sweep.py --mode tradier --symbols fast --start 2024-01-01 --tier entry_gates --workers $W_TRADIER --resume 2>&1 | tee /tmp/v8_quick_tradier.log"
        fi
    fi

    # Wait for all quick_* screens to finish
    wait_screens_gone "quick_(crypto|tradier)\b"
    log "--- PHASE 1 COMPLETE ---"
}

# ═══════════════════════════════════════════════════════════════
# PHASE 1b: reentry block combos (2^9 = 512 per mode)
# ═══════════════════════════════════════════════════════════════
phase1b() {
    log "--- PHASE 1b: reentry_blocks ---"

    if [[ $W_CRYPTO -gt 0 ]]; then
        if phase_done "reentry_blocks_crypto_*.csv" 500; then
            log "  [DONE] reentry_blocks crypto"
        else
            launch_screen "rb_crypto" \
                "cd $BASE && $PY -u sweep_reentry_blocks.py --mode crypto --symbols fast --start 2022-01-01 --workers $W_CRYPTO --resume 2>&1 | tee /tmp/rb_crypto.log"
        fi
    fi

    if [[ $W_TRADIER -gt 0 ]]; then
        if phase_done "reentry_blocks_tradier_*.csv" 500; then
            log "  [DONE] reentry_blocks tradier"
        else
            launch_screen "rb_tradier" \
                "cd $BASE && $PY -u sweep_reentry_blocks.py --mode tradier --symbols fast --start 2024-01-01 --workers $W_TRADIER --resume 2>&1 | tee /tmp/rb_tradier.log"
        fi
    fi

    wait_screens_gone "rb_(crypto|tradier)\b"
    log "--- PHASE 1b COMPLETE ---"
}

# ═══════════════════════════════════════════════════════════════
# PHASE 2: exit_tuning
# ═══════════════════════════════════════════════════════════════
phase2() {
    log "--- PHASE 2: exit_tuning ---"

    if [[ $W_CRYPTO -gt 0 ]]; then
        if phase_done "v8_quick_crypto_exit_tuning_*.csv" 1000; then
            log "  [DONE] exit_tuning crypto"
        else
            launch_screen "exit_crypto" \
                "cd $BASE && $PY -u v8_quick_sweep.py --mode crypto --symbols fast --start 2022-01-01 --tier exit_tuning --workers $W_CRYPTO --resume 2>&1 | tee /tmp/v8_exit_crypto.log"
        fi
    fi

    if [[ $W_TRADIER -gt 0 ]]; then
        if phase_done "v8_quick_tradier_exit_tuning_*.csv" 1000; then
            log "  [DONE] exit_tuning tradier"
        else
            launch_screen "exit_tradier" \
                "cd $BASE && $PY -u v8_quick_sweep.py --mode tradier --symbols fast --start 2024-01-01 --tier exit_tuning --workers $W_TRADIER --resume 2>&1 | tee /tmp/v8_exit_tradier.log"
        fi
    fi

    wait_screens_gone "exit_(crypto|tradier)\b"
    log "--- PHASE 2 COMPLETE ---"
}

# ═══════════════════════════════════════════════════════════════
# PHASE 3: quality_sniper (reentry block stacks + selectivity)
# ═══════════════════════════════════════════════════════════════
phase3() {
    log "--- PHASE 3: quality_sniper ---"

    if [[ $W_CRYPTO -gt 0 ]]; then
        if phase_done "quality_sniper_crypto_*.csv" 380; then
            log "  [DONE] quality_sniper crypto"
        else
            launch_screen "qs_crypto" \
                "cd $BASE && $PY -u sweep_quality_sniper.py --mode crypto --symbols fast --start 2022-01-01 --workers $W_CRYPTO --resume 2>&1 | tee /tmp/qs_crypto.log"
        fi
    fi

    if [[ $W_TRADIER -gt 0 ]]; then
        if phase_done "quality_sniper_tradier_*.csv" 380; then
            log "  [DONE] quality_sniper tradier"
        else
            launch_screen "qs_tradier" \
                "cd $BASE && $PY -u sweep_quality_sniper.py --mode tradier --symbols fast --start 2024-01-01 --workers $W_TRADIER --resume 2>&1 | tee /tmp/qs_tradier.log"
        fi
    fi

    wait_screens_gone "qs_(crypto|tradier)\b"
    log "--- PHASE 3 COMPLETE ---"
}

# ═══════════════════════════════════════════════════════════════
# PHASE 5: mega_v2 / stock_v2 (continuous — replaces 'full' which lacks K15M)
# ═══════════════════════════════════════════════════════════════
phase5() {
    log "--- PHASE 5: mega_v2/stock_v2 continuous sweep ---"

    if [[ $W_CRYPTO -gt 0 ]]; then
        launch_screen "mega_crypto" \
            "cd $BASE && $PY -u v8_quick_sweep.py --mode crypto --symbols fast --start 2022-01-01 --tier mega_v2 --workers $W_CRYPTO --resume 2>&1 | tee /tmp/v8_mega_crypto.log"
    fi
    if [[ $W_TRADIER -gt 0 ]]; then
        launch_screen "stock_v2_tradier" \
            "cd $BASE && $PY -u v8_quick_sweep.py --mode tradier --symbols fast --start 2024-01-01 --tier stock_v2 --workers $W_TRADIER --resume 2>&1 | tee /tmp/v8_stock_v2_tradier.log"
    fi
    log "--- PHASE 5 running in background (continuous) ---"
}

# ═══════════════════════════════════════════════════════════════
# MAIN — run phases in order, each skipped if already complete.
# Phase 5 keeps running indefinitely; autochain enters status-loop after launch.
# ═══════════════════════════════════════════════════════════════
phase1
phase1b
phase2
phase3
phase5

# ═══════════════════════════════════════════════════════════════
# STATUS LOOP — every 10 min, relaunch any dead phase-5 screens, log best Sharpe.
# This is the "forever" loop that keeps resuming after crashes.
# ═══════════════════════════════════════════════════════════════
log "═══ MAIN CHAIN LAUNCHED — entering supervisor status loop ═══"
while true; do
    sleep 600  # 10 min

    # Re-launch phase-5 sweeps if they died (idempotent — skips if running)
    if [[ $W_CRYPTO -gt 0 ]]; then
        if ! screen_running "mega_crypto"; then
            log "  [RESPAWN] mega_crypto died → relaunching (will --resume from CSV)"
            launch_screen "mega_crypto" \
                "cd $BASE && $PY -u v8_quick_sweep.py --mode crypto --symbols fast --start 2022-01-01 --tier mega_v2 --workers $W_CRYPTO --resume 2>&1 | tee /tmp/v8_mega_crypto.log"
        fi
    fi
    if [[ $W_TRADIER -gt 0 ]]; then
        if ! screen_running "stock_v2_tradier"; then
            log "  [RESPAWN] stock_v2_tradier died → relaunching"
            launch_screen "stock_v2_tradier" \
                "cd $BASE && $PY -u v8_quick_sweep.py --mode tradier --symbols fast --start 2024-01-01 --tier stock_v2 --workers $W_TRADIER --resume 2>&1 | tee /tmp/v8_stock_v2_tradier.log"
        fi
    fi

    # Log best-Sharpe summary
    log "--- STATUS ---"
    for csv in "$SWEEP_DIR"/*.csv; do
        [[ -f "$csv" ]] || continue
        local_lines=$(wc -l < "$csv" 2>/dev/null || echo 0)
        # Best Sharpe = max value in "sharpe" column (column 3 by convention for these CSVs)
        log "  $(basename $csv): ${local_lines} rows"
    done
done
