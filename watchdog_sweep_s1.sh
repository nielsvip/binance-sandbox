#!/bin/bash
# watchdog_sweep_s1.sh — every 5min: keep backtest_v8_sweep system_combo alive.
# 2026-05-07: v8_quick excluded (results are lies per user mandate). Workers=2 to
# stay under 31GB RAM budget (each backtest_v8_engine peaks at ~6GB × 2 workers = 12GB).
# v8_quick is NOT restarted — it competes for same 31GB and produces lying Sharpe numbers.
LOG=/home/niels/logs/watchdog_sweep_s1.log
TS=$(date -u "+%Y-%m-%d %H:%M:%S UTC")
DIR=/home/niels/binance-sandbox
PYTHON=/home/niels/.conda/envs/binance_env/bin/python

NB=$(pgrep -afc "backtest_v8_sweep.*system_combo" 2>/dev/null | head -1)
NB=${NB:-0}
if [ "$NB" -lt 1 ]; then
    echo "[$TS] backtest_v8_sweep system_combo dead — relaunching (workers=2)" >> "$LOG"
    TS2=$(date +%Y%m%d_%H%M)
    cd "$DIR"
    nohup env V8_RATE_GUARD_DISABLED=1 "$PYTHON" backtest_v8_sweep.py \
        --mode crypto --account ang \
        --start 2026-01-01 \
        --symbols BTCUSDC,ETHUSDC,SOLUSDC,ADAUSDC,BNBUSDC,AVAXUSDC,DOTUSDT,ATOMUSDT \
        --tier system_combo \
        --workers 1 \
        --timeout 2000 \
        > ~/logs/bt_sweep_crypto_8sym_${TS2}.log 2>&1 < /dev/null & disown
    sleep 5
    echo "[$TS] post-relaunch system_combo procs=$(pgrep -afc 'backtest_v8_sweep.*system_combo')" >> "$LOG"
fi
