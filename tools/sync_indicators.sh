#!/bin/bash
# sync_indicators.sh — S1 is source for backtest_v8/indicators, push to s2/s3/s5 24/7
set -e
for ip in 10.0.0.6 10.0.0.5 10.0.0.4; do
  rsync -az --timeout=60 ~/binance-sandbox/backtest_v8/indicators/ niels@$ip:~/binance-sandbox/backtest_v8/indicators/ > /tmp/sync_ind_${ip##*.}.log 2>&1 || echo "$(date) fail $ip" >> /tmp/sync_indicators.log
  echo "$(date -u +%FT%TZ) $ip OK $(ls ~/binance-sandbox/backtest_v8/indicators 2>&1 | wc -l) files" >> /tmp/sync_indicators.log
done
