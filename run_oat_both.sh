#!/bin/bash
# run_oat_both.sh — OAT (one-at-a-time) vec sweep for crypto + tradier.
# Runs v8_vec_sweep --tier oat on all available symbols.
# Quick diagnostic pass (~20-40 min total). Runs after each system_combo cycle.
# Usage: bash run_oat_both.sh [--mode crypto|tradier|both]

set -u
SANDBOX=/home/niels/binance-sandbox
LOGS=/home/niels/logs
PY=/home/niels/.conda/envs/binance_env/bin/python
MODE="${1:-both}"

CRYPTO_SYMS="BTCUSDC,ETHUSDC,SOLUSDC,ADAUSDC,BNBUSDC,AVAXUSDC,XRPUSDC,LINKUSDC,LTCUSDC,UNIUSDC,1INCHUSDT,ALGOUSDT,ANKRUSDT,ATOMUSDT,AXSUSDT,BANDUSDT,BATUSDT,BELUSDT,BTCDOMUSDT,C98USDT,CELRUSDT,CHRUSDT,COMPUSDT,COTIUSDT,DASHUSDT,DOTUSDT,EGLDUSDT,ENJUSDT,ETCUSDT,GRTUSDT,GTCUSDT,HOTUSDT,IOSTUSDT,IOTAUSDT,IOTXUSDT,KAVAUSDT,KNCUSDT,KSMUSDT,LRCUSDT,MANAUSDT,MTLUSDT,NKNUSDT,QTUMUSDT,RLCUSDT,RSRUSDT,RVNUSDT,SANDUSDT,SKLUSDT,SNXUSDT,STORJUSDT,SUSHIUSDT,SXPUSDT,THETAUSDT,TRXUSDT,VETUSDT,XLMUSDT,XMRUSDT,XTZUSDT,YFIUSDT,ZENUSDT"

log() { echo "[$(date -u +%H:%M:%S)] $1"; }

run_oat_crypto() {
    local TS LOGF
    TS=$(date +%Y%m%d_%H%M%S)
    LOGF="$LOGS/oat_sweep_crypto_${TS}.log"
    log "OAT CRYPTO START: 60 syms, 2022-01-01 | log=$LOGF"
    cd "$SANDBOX"
    $PY -u v8_vec_sweep.py \
        --mode crypto \
        --account flz \
        --symbols "$CRYPTO_SYMS" \
        --start 2022-01-01 \
        --tier oat \
        --workers 4 \
        > "$LOGF" 2>&1
    local RC=$?
    local ROWS
    ROWS=$(wc -l < "$SANDBOX/data/sweep_results/oat_sweep_crypto_${TS}.csv" 2>/dev/null || echo 0)
    log "OAT CRYPTO DONE: rc=$RC rows=$ROWS"
}

run_oat_tradier() {
    local TS LOGF
    TS=$(date +%Y%m%d_%H%M%S)
    LOGF="$LOGS/oat_sweep_tradier_${TS}.log"
    # Collect all stock NPZ symbols
    local TRADIER_SYMS
    TRADIER_SYMS=$(ls "$SANDBOX/backtest_v8/indicators/"*.npz 2>/dev/null \
        | xargs -n1 basename | sed 's/.npz$//' \
        | grep -vE 'USDT$|USDC$' | tr '\n' ',' | sed 's/,$//')
    local N
    N=$(echo "$TRADIER_SYMS" | tr ',' '\n' | wc -l)
    if [ "$N" -lt 10 ]; then
        log "OAT TRADIER SKIP: only $N stock NPZs found"
        return 0
    fi
    log "OAT TRADIER START: ${N} stock syms, 2024-01-01 | log=$LOGF"
    cd "$SANDBOX"
    $PY -u v8_vec_sweep.py \
        --mode tradier \
        --account trb \
        --symbols "$TRADIER_SYMS" \
        --start 2024-01-01 \
        --tier oat \
        --workers 2 \
        > "$LOGF" 2>&1
    local RC=$?
    local ROWS
    ROWS=$(wc -l < "$SANDBOX/data/sweep_results/oat_sweep_tradier_${TS}.csv" 2>/dev/null || echo 0)
    log "OAT TRADIER DONE: rc=$RC rows=$ROWS"
}

log "=== run_oat_both.sh MODE=$MODE ==="
case "$MODE" in
    --mode)
        # Called as: bash run_oat_both.sh --mode <mode>
        ACTUAL_MODE="${2:-both}"
        ;;
    crypto|tradier|both)
        ACTUAL_MODE="$MODE"
        ;;
    *)
        ACTUAL_MODE="both"
        ;;
esac

if [[ "$ACTUAL_MODE" == "crypto" || "$ACTUAL_MODE" == "both" ]]; then
    run_oat_crypto
fi
if [[ "$ACTUAL_MODE" == "tradier" || "$ACTUAL_MODE" == "both" ]]; then
    run_oat_tradier
fi
log "=== run_oat_both.sh DONE ==="
