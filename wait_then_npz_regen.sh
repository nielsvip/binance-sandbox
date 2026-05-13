#!/usr/bin/env bash
# wait_then_npz_regen.sh — Wait for 15m backfill to finish, then trigger NPZ regen.
# Designed to run on S1. Launched with nohup.
#
# Usage: bash wait_then_npz_regen.sh

LOG_DIR=/home/niels/logs
WORKDIR=/home/niels/binance-sandbox
PYTHON=/home/niels/.conda/envs/binance_env/bin/python

echo "[$(date -u +%H:%M:%S)] waiting for backfill to finish..."

# Wait until the backfill process is done (poll every 30s)
while pgrep -f "python.*backfill_crypto_klines" > /dev/null 2>&1; do
    sleep 30
done

echo "[$(date -u +%H:%M:%S)] backfill complete — starting NPZ regen"

# Run the NPZ regen
bash "$WORKDIR/regenerate_crypto_npz_2022.sh"

echo "[$(date -u +%H:%M:%S)] NPZ regen launched — monitor with: ls -lt $LOG_DIR/npz_regen_crypto_2022_*.log | head -1"
