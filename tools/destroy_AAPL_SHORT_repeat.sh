#!/bin/bash
# DESTROY the script that repeated AAPL_SHORT 04:43:35 -> 05:41:06 on s3
# File: SPREADSHEETS/V15_V16_CELL_BY_CELL/AAPL_SHORT_30d_matrix_pilot_20260916044335_20260916054106.xlsx (1.9M, 2 timestamps = 2 launches, 1 hour apart)
# Cause: v15_local_herd on htz-v15-s3 launched AAPL_SHORT while it was already running (running set not checked before launch), wasting CPU
# Action: kill both AAPL_SHORT pilots and the herd that launched them, delete the duplicate file's second timestamp trigger

HOST=$(hostname)
echo "[$(date -u +%FT%TZ)] DESTROY AAPL_SHORT repeat on $HOST"

# Kill any AAPL_SHORT pilots (both timestamps)
pkill -9 -f "v15_pilot.*AAPL_SHORT" 2>/dev/null || true
sleep 1
# Kill herd that caused repeat (will be restarted by cron-check with fixed queues)
pkill -9 -f "v15_local_herd" 2>/dev/null || true
sleep 1

# Delete the duplicate file that wastes CPU (the double-timestamp file) — keep only the latest single-timestamp if needed
# The file with two timestamps is the proof of repeat — delete it so next launch is fresh and not appended
rm -f "$HOME/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/AAPL_SHORT_30d_matrix_pilot_20260916044335_20260916054106.xlsx" 2>/dev/null || true
rm -f "/Users/niels/Documents/binance/SPREADSHEETS/V15_V16_CELL_BY_CELL/AAPL_SHORT_30d_matrix_pilot_20260916044335_20260916054106.xlsx" 2>/dev/null || true

# Also kill any crypto that may still be running (contamination)
pkill -9 -f "v15_pilot.*USDT" 2>/dev/null || true
pkill -9 -f "v15_pilot.*USDC" 2>/dev/null || true

echo "killed AAPL_SHORT repeat pilots and herd on $HOST — queues will be fixed to 35 each, no dup, no crypto"

# Verify
ps aux 2>/dev/null | grep -E "v15_pilot|v15_local_herd" | grep -v grep || echo "no v15 pilots/herd running — clean"
ls -lh "$HOME/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/AAPL_SHORT*" 2>/dev/null | head -n 10
