#!/bin/bash
# Kill any crypto (USDT/USDC) pilots while stocks not done — run on each server s1/s2/s3/s5
# Safe: only kills v15_pilot with USDT/USDC, does NOT kill stock pilots
echo "[$(date -u +%FT%TZ)] kill_crypto guard — stocks must finish first (204 stocks, 48 NEW remain)"
STOCKS_DONE=$(ls ~/binance-sandbox/data/reports/lifecycle_pilot/*_v14_progress.json 2>/dev/null | wc -l)
echo "stocks progress files: $STOCKS_DONE"
# Count stocks done vs crypto done
STOCKS_FINAL=$(grep -l '"final_gain"' ~/binance-sandbox/data/reports/lifecycle_pilot/*_v14_progress.json 2>/dev/null | xargs grep -L "USDT\|USDC" 2>/dev/null | wc -l)
CRYPTO_RUNNING=$(ps aux 2>/dev/null | grep -E "v15_pilot.*USDT|v15_pilot.*USDC" | grep -v grep || true)
if [ -n "$CRYPTO_RUNNING" ]; then
  echo "CRYPTO STILL RUNNING — killing:"
  echo "$CRYPTO_RUNNING"
  pkill -f "v15_pilot.*USDT" || true
  pkill -f "v15_pilot.*USDC" || true
  sleep 2
  echo "after kill:"
  ps aux 2>/dev/null | grep -E "v15_pilot" | grep -v grep || echo "no v15 pilots"
else
  echo "no crypto pilots running — OK"
fi
echo "queues now (should be 0 crypto):"
for f in ~/binance-sandbox/SPREADSHEETS/V15_SERVER_QUEUE_S*.txt; do echo "--- $f"; cat "$f" | head -n 5; echo "lines $(wc -l < "$f") crypto $(grep -c "USDT\|USDC" "$f" || echo 0)"; done
