#!/bin/bash
# Waits for current V5 run to finish, then starts continuous sweep
echo "$(date -u): Waiting for current V5 runs to finish..."
while pgrep -f "backtest_v5_run" > /dev/null 2>&1; do sleep 60; done
echo "$(date -u): All V5 runs done. Starting continuous sweep..."
cd /home/niels/binance
exec /home/niels/.conda/envs/binance_env/bin/python -u v5_continuous_sweep.py > /home/niels/logs/v5_continuous_sweep.log 2>&1
