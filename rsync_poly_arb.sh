#!/bin/bash
# Continuous rsync of poly arb engine data from server to local
# Run: bash rsync_poly_arb.sh

SERVER="niels@157.180.125.52"
REMOTE="/home/niels/binance/data/poly/arb_engine/"
LOCAL="/Users/niels/Documents/binance/data/poly/arb_engine/"
LOG_REMOTE="/home/niels/logs/poly_arb_engine.log"
LOG_LOCAL="/Users/niels/Documents/binance/data/poly/arb_engine/server.log"

echo "Syncing poly arb engine data from server every 30s..."
while true; do
    rsync -az "$SERVER:$REMOTE" "$LOCAL" 2>/dev/null
    rsync -az "$SERVER:$LOG_REMOTE" "$LOG_LOCAL" 2>/dev/null
    # Also sync highconf scanner data
    rsync -az "$SERVER:/home/niels/binance/data/poly/highconf/" "/Users/niels/Documents/binance/data/poly/highconf/" 2>/dev/null
    rsync -az "$SERVER:/home/niels/logs/poly_scanner.log" "/Users/niels/Documents/binance/data/poly/highconf/server.log" 2>/dev/null
    sleep 30
done
