#!/bin/bash
# Sync latest_market_data.json to server every 15s for limitless_trader
while true; do
    rsync -az /Users/niels/Documents/binance/data/latest_market_data.json s1-int:/home/niels/_binance_PAUSED_UNTIL_WED18/data/ 2>/dev/null
    sleep 15
done
