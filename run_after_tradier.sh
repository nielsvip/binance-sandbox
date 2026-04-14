#!/bin/bash
echo "Waiting for tradier V5 run 1..."
while pgrep -f "backtest_v5_run_tradier" > /dev/null 2>&1; do sleep 30; done
echo "$(date -u): Run 1 done. Starting tradier run 2 (IBS fix + no opposing)..."
cd /home/niels/binance
/home/niels/.conda/envs/binance_env/bin/python -u backtest_v5_run_tradier.py --all --start 2024-01-01 > /home/niels/logs/v5_tradier_run2_ibs.log 2>&1
echo "$(date -u): Tradier run 2 done. Starting crypto..."
/home/niels/.conda/envs/binance_env/bin/python -u backtest_v5_run.py --mode crypto --all --start 2022-01-01 > /home/niels/logs/v5_crypto_final.log 2>&1
echo "$(date -u): All done."
