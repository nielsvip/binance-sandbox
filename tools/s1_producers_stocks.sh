#!/bin/bash
# LIVE FAILOVER S1 stocks producers (ExecStart of live-failover-stocks-producers.service, on demand,
# started by tools/live_failover_s1.py when the Mac's Tradier klines/indicators are >180 s stale in
# the NYSE session). Live-mirror copies in ~/binance (NOT the disabled sandbox service), conda env,
# S1 egress IP. tradier_klines.py -> ~/binance/klines_cache/tradier, tradier_indicators.py ->
# ~/binance/data/tradier/tradier_indicators_latest.json. Both die with the unit (cgroup).
set -u
cd /home/niels/binance || exit 1
PY=/home/niels/.conda/envs/binance_env/bin/python
export EZ_LOG_DIR=/home/niels/binance/logs
( while true; do "$PY" -u tradier_klines.py >> /home/niels/logs/lf_tradier_klines.log 2>&1; sleep 30; done ) &
while true; do
    "$PY" -u tradier_indicators.py >> /home/niels/logs/lf_tradier_indicators.log 2>&1
    echo "[$(date -u +%FT%TZ)] tradier_indicators exited rc=$? — restart in 10s" >> /home/niels/logs/lf_tradier_indicators.log
    sleep 10
done
