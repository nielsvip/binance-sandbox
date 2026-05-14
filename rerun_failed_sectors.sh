#!/usr/bin/env bash
# rerun_failed_sectors.sh
# Re-runs the 11 sectors that failed with rc=-9 (OOM kill from concurrent engines).
# Fixes: wait_for_clear_engines() before each run + V8_USE_VEC_ALL=1 for speed.
#
# Failed tradier (4): tech_ai_chips, energy_oil_gas, precious_metals, base_metals_mining
# Failed crypto (7): all 7 sectors
#
# Usage (on S1):
#   nohup bash rerun_failed_sectors.sh > ~/logs/sector_rerun_$(date +%Y%m%d_%H%M%S).log 2>&1 < /dev/null &
#
# Created: 2026-05-14

PYTHON=/home/niels/.conda/envs/binance_env/bin/python
WORKDIR=/home/niels/binance-sandbox
LOG_DIR="$HOME/logs"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

# ---------------------------------------------------------------------------
# Kill any competing backtest_v8_engine or sweep_coordinator (they cause OOM).
# Uses pgrep -af python (matches conda python binary) to avoid self-match.
# ---------------------------------------------------------------------------
kill_competing_engines() {
    local pids
    pids=$(pgrep -af "backtest_v8_engine|backtest_v8_precompute|sweep_coordinator" 2>/dev/null \
           | grep -v "$$\|grep\|bash -c\|rerun_failed" \
           | awk '{print $1}')
    if [[ -n "$pids" ]]; then
        log "  Killing competing engines/coordinator: $pids"
        echo "$pids" | xargs kill -9 2>/dev/null
        sleep 5
    fi
}

# ---------------------------------------------------------------------------
# Wait for any competing backtest_v8_engine or precompute (uses pgrep -af python)
# ---------------------------------------------------------------------------
wait_for_clear_engines() {
    local waited=0
    while true; do
        local running
        running=$(pgrep -af "backtest_v8_engine|backtest_v8_precompute" 2>/dev/null \
                  | grep -v "$$\|grep\|bash -c\|rerun_failed" | wc -l)
        if [[ "$running" -eq 0 ]]; then
            break
        fi
        if [[ $waited -eq 0 ]]; then
            log "  Waiting for $running competing engine(s)..."
            pgrep -af "backtest_v8_engine|backtest_v8_precompute" 2>/dev/null | grep -v "grep\|bash -c"
        fi
        sleep 30
        waited=$((waited + 30))
    done
    if [[ $waited -gt 0 ]]; then
        log "  Engines cleared after ${waited}s."
    fi
}

# ---------------------------------------------------------------------------
# Run one sector and wait for it
# ---------------------------------------------------------------------------
run_sector() {
    local mode="$1" sector="$2" tier="$3" symbols_file="$4" start_date="$5"
    local timeout_s="${6:-3600}"

    local syms
    syms=$("$PYTHON" -c "
import json, sys
with open('$symbols_file') as f:
    d = json.load(f)
s = '$sector'
if s not in d or s.startswith('_'):
    print('', end='')
    sys.exit(0)
print(','.join(d[s]), end='')
")

    if [[ -z "$syms" ]]; then
        log "  SKIP: '$sector' not in $symbols_file"
        return
    fi

    local count
    count=$(echo "$syms" | tr ',' '\n' | grep -c .)
    local TS; TS=$(date +%Y%m%d_%H%M%S)
    local logfile="$LOG_DIR/${mode}_sector_sweep_${sector}_${TS}.log"

    kill_competing_engines
    wait_for_clear_engines

    log ">>> START $mode/$sector ($count syms, start=$start_date)"
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
    log "<<< DONE $mode/$sector rc=$rc log=$logfile"
    sleep 20
}

# ---------------------------------------------------------------------------
# Main: re-run in interleaved order (match sector_launcher v2 ordering)
# ---------------------------------------------------------------------------
log "=== Sector Re-run starting (11 failed sectors) ==="
log "    V8_USE_VEC_ALL=1 V8_SWEEP_MODE=1 V8_RATE_GUARD_DISABLED=1"

# tradier/tech_ai_chips + crypto/mega_l1
run_sector tradier tech_ai_chips tradier_sector_baseline "$WORKDIR/sectors_tradier.json" 2023-05-13 3600
run_sector crypto mega_l1 crypto_sector_baseline "$WORKDIR/sectors_crypto.json" 2022-01-01 3600

# tradier/energy_oil_gas + crypto/defi_oracle
run_sector tradier energy_oil_gas tradier_sector_baseline "$WORKDIR/sectors_tradier.json" 2023-05-13 3600
run_sector crypto defi_oracle crypto_sector_baseline "$WORKDIR/sectors_crypto.json" 2022-01-01 3600

# tradier/precious_metals + crypto/gaming_nft
run_sector tradier precious_metals tradier_sector_baseline "$WORKDIR/sectors_tradier.json" 2023-05-13 3600
run_sector crypto gaming_nft crypto_sector_baseline "$WORKDIR/sectors_crypto.json" 2022-01-01 3600

# tradier/base_metals_mining + crypto/alt_l1
run_sector tradier base_metals_mining tradier_sector_baseline "$WORKDIR/sectors_tradier.json" 2023-05-13 3600
run_sector crypto alt_l1 crypto_sector_baseline "$WORKDIR/sectors_crypto.json" 2022-01-01 3600

# crypto-only remaining
run_sector crypto ecosystems crypto_sector_baseline "$WORKDIR/sectors_crypto.json" 2022-01-01 3600
run_sector crypto infrastructure crypto_sector_baseline "$WORKDIR/sectors_crypto.json" 2022-01-01 3600
run_sector crypto misc_smallcap crypto_sector_baseline "$WORKDIR/sectors_crypto.json" 2022-01-01 3600

log "=== Re-run complete. Check data/sweep_results/ for new CSVs ==="
