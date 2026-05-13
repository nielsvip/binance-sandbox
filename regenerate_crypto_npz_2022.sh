#!/usr/bin/env bash
# regenerate_crypto_npz_2022.sh — Regenerate NPZ for 14 crypto symbols after backfill
# Run on S1 AFTER backfill_crypto_klines_2022.py completes.
# This gives NPZ coverage from 2020-01-01 to present for start=2022 sweeps.
#
# Usage: bash regenerate_crypto_npz_2022.sh
# Runtime: ~10-20 min on S1

PYTHON=/home/niels/.conda/envs/binance_env/bin/python
WORKDIR=/home/niels/binance-sandbox
LOG_DIR=$HOME/logs
SYMBOLS="BTCUSDC,ETHUSDC,SOLUSDC,ADAUSDC,BNBUSDC,AVAXUSDC,XRPUSDC,LINKUSDC,LTCUSDC,UNIUSDC,DOTUSDT,ATOMUSDT,SANDUSDT,MANAUSDT"

mkdir -p "$LOG_DIR"
TS=$(date +%Y%m%d_%H%M%S)
LOG="$LOG_DIR/npz_regen_crypto_2022_${TS}.log"

echo "Regenerating NPZ for 14 crypto symbols (2020-2026)..."
echo "Log: $LOG"

cd "$WORKDIR" || exit 1

nohup $PYTHON backtest_v8_precompute.py \
    --mode crypto \
    --symbols "$SYMBOLS" \
    --workers 1 \
    > "$LOG" 2>&1 < /dev/null &
PID=$!
disown $PID

echo "PID: $PID"
echo "Monitor: tail -f $LOG"
