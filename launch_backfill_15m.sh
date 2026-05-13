#!/usr/bin/env bash
# Launch 15m crypto klines backfill on S1
cd /home/niels/binance-sandbox || exit 1
PY=/home/niels/.conda/envs/binance_env/bin/python
TS=$(date +%H%M%S)
LOG=/home/niels/logs/klines_backfill_15m_${TS}.log
pkill -f "python.*backfill_crypto_klines" 2>/dev/null
sleep 1
nohup $PY -u backfill_crypto_klines_2022.py --interval 15m > "$LOG" 2>&1 < /dev/null &
BF_PID=$!
disown $BF_PID
echo "pid=$BF_PID log=$LOG"
