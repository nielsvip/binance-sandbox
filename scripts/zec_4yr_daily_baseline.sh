#!/usr/bin/env bash
# scripts/zec_4yr_daily_baseline.sh — cron entry-point for S1.
# Schedule: 0 13 * * *  (13:00 UTC = 09:00 ET, before US market open)
# Runs zec_4yr_daily_baseline.py and logs to ~/logs/zec_4yr_daily_<YYYYMMDD>.log
set -e
cd /home/niels/binance-sandbox
TODAY=$(date -u +%Y%m%d)
LOG=/home/niels/logs/zec_4yr_daily_${TODAY}.log
mkdir -p /home/niels/logs
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] zec_4yr_daily_baseline START" >>"$LOG"
/home/niels/.conda/envs/binance_env/bin/python3 scripts/zec_4yr_daily_baseline.py --iters 100 >>"$LOG" 2>&1
RC=$?
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] zec_4yr_daily_baseline EXIT rc=$RC" >>"$LOG"
exit $RC
