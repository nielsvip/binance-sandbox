#!/usr/bin/env bash
# launch_all_sectors_sequential.sh
# Waits for NPZ regen AND any competing backtest_v8_engine to finish,
# then runs all stock sectors + all crypto sectors ONE AT A TIME.
#
# Fixes vs v1: proper engine-wait guard (avoids OOM from concurrent sweeps),
#              V8_USE_VEC_ALL=1 V8_SWEEP_MODE=1 V8_RATE_GUARD_DISABLED=1 for speed.
#
# Usage (run on S1):
#   nohup bash launch_all_sectors_sequential.sh > ~/logs/sector_launcher.log 2>&1 < /dev/null &
#
# Created: 2026-05-14

PYTHON=/home/niels/.conda/envs/binance_env/bin/python
WORKDIR=/home/niels/binance-sandbox
LOG_DIR="$HOME/logs"

STOCK_SECTORS=(
    tech_ai_chips
    energy_oil_gas
    precious_metals
    base_metals_mining
    uranium_nuclear
    agriculture_fertilizer
    defense_aerospace
    consumer_media
    commodities_crypto_etf
)

CRYPTO_SECTORS=(
    mega_l1
    defi_oracle
    gaming_nft
    alt_l1
    ecosystems
    infrastructure
    misc_smallcap
)

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

# ---------------------------------------------------------------------------
# Kill any competing backtest_v8_engine or sweep_coordinator (they cause OOM)
# ---------------------------------------------------------------------------
kill_competing_engines() {
    local pids
    pids=$(pgrep -af "backtest_v8_engine|backtest_v8_precompute|sweep_coordinator" 2>/dev/null \
           | grep -v "$$\|grep\|bash -c\|launch_all" \
           | awk '{print $1}')
    if [[ -n "$pids" ]]; then
        log "  Killing competing engines: $pids"
        echo "$pids" | xargs kill -9 2>/dev/null
        sleep 5
    fi
}

# ---------------------------------------------------------------------------
# Wait for any remaining competing engines (uses pgrep -af, avoids self-match)
# ---------------------------------------------------------------------------
wait_for_clear_engines() {
    local waited=0
    while true; do
        local running
        running=$(pgrep -af "backtest_v8_engine|backtest_v8_precompute" 2>/dev/null \
                  | grep -v "$$\|grep\|bash -c\|launch_all" | wc -l)
        if [[ "$running" -eq 0 ]]; then
            break
        fi
        if [[ $waited -eq 0 ]]; then
            log "  Waiting for $running competing engine(s) to finish..."
        fi
        sleep 30
        waited=$((waited + 30))
    done
    if [[ $waited -gt 0 ]]; then
        log "  Engines cleared after ${waited}s — proceeding."
    fi
}

# ---------------------------------------------------------------------------
# Run one sector sweep and wait for it to finish
# ---------------------------------------------------------------------------
run_sector() {
    local mode="$1"
    local sector="$2"
    local tier="$3"
    local symbols_file="$4"
    local start_date="$5"
    local timeout_s="${6:-3600}"

    local syms
    syms=$("$PYTHON" -c "
import json, sys
with open('$symbols_file') as f:
    d = json.load(f)
sector = '$sector'
if sector not in d or sector.startswith('_'):
    print('', end='')
    sys.exit(0)
print(','.join(d[sector]), end='')
")

    if [[ -z "$syms" ]]; then
        log "  SKIP: sector '$sector' not found in $symbols_file"
        return
    fi

    local count
    count=$(echo "$syms" | tr ',' '\n' | grep -c .)
    local TS
    TS=$(date +%Y%m%d_%H%M%S)
    local logfile="$LOG_DIR/${mode}_sector_sweep_${sector}_${TS}.log"

    # Kill competing engines then wait for clear
    kill_competing_engines
    wait_for_clear_engines

    log ">>> START $mode/$sector ($count syms, start=$start_date)"

    cd "$WORKDIR" || { log "ERROR: cannot cd to $WORKDIR"; return; }

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

    # Brief RAM cooldown between sweeps
    sleep 30
}

# ---------------------------------------------------------------------------
# Main: interleave stock + crypto sectors
# ---------------------------------------------------------------------------
log "=== Sector Sweep Launcher v2 starting ==="

STOCK_IDX=0
CRYPTO_IDX=0
STOCK_LEN=${#STOCK_SECTORS[@]}
CRYPTO_LEN=${#CRYPTO_SECTORS[@]}

while [[ $STOCK_IDX -lt $STOCK_LEN || $CRYPTO_IDX -lt $CRYPTO_LEN ]]; do
    if [[ $STOCK_IDX -lt $STOCK_LEN ]]; then
        run_sector \
            tradier \
            "${STOCK_SECTORS[$STOCK_IDX]}" \
            tradier_sector_baseline \
            "$WORKDIR/sectors_tradier.json" \
            2023-05-13 \
            3600
        STOCK_IDX=$((STOCK_IDX + 1))
    fi

    if [[ $CRYPTO_IDX -lt $CRYPTO_LEN ]]; then
        run_sector \
            crypto \
            "${CRYPTO_SECTORS[$CRYPTO_IDX]}" \
            crypto_sector_baseline \
            "$WORKDIR/sectors_crypto.json" \
            2022-01-01 \
            3600
        CRYPTO_IDX=$((CRYPTO_IDX + 1))
    fi
done

log "=== All sector sweeps complete. Stock: $STOCK_LEN done. Crypto: $CRYPTO_LEN done. ==="
log "Results in: $WORKDIR/data/sweep_results/"
