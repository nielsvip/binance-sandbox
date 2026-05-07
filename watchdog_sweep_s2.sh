#!/bin/bash
# watchdog_sweep_s2.sh — every 5min: keep backtest_v8_sweep tradier_param_hunt alive.
# 2026-05-07: v8_quick excluded (results are lies per user mandate). Workers=1 to
# stay under 31GB RAM budget (each backtest_v8_engine peaks at ~10GB; 1 worker = safe).
# S2 runs tradier sweeps ONLY — never crypto (mode-mismatch = lying results).
LOG=/home/niels/logs/watchdog_sweep_s2.log
TS=$(date -u "+%Y-%m-%d %H:%M:%S UTC")
DIR=/home/niels/binance-sandbox
PYTHON=/home/niels/miniconda3/envs/binance_env/bin/python

NB=$(pgrep -afc "backtest_v8_sweep.*tradier_param_hunt" 2>/dev/null | head -1)
NB=${NB:-0}
if [ "$NB" -lt 1 ]; then
    echo "[$TS] backtest_v8_sweep tradier_param_hunt dead — relaunching (workers=1)" >> "$LOG"
    TS2=$(date +%Y%m%d_%H%M)
    cd "$DIR"
    nohup env V8_RATE_GUARD_DISABLED=1 "$PYTHON" backtest_v8_sweep.py \
        --mode tradier --account trb \
        --start 2024-01-01 \
        --symbols AAPL,AMZN,AVGO,AMD,ADBE,ABNB,ARM,ASML,AXON,BA,BABA,ABBV,ABT,ADP,ADM,AEM,AG,AGCO,ALB,ASTS \
        --tier tradier_param_hunt \
        --workers 1 \
        --timeout 2400 \
        > ~/logs/bt_sweep_tradier_param_hunt_${TS2}.log 2>&1 < /dev/null & disown
    sleep 5
    echo "[$TS] post-relaunch tradier procs=$(pgrep -afc 'backtest_v8_sweep.*tradier_param_hunt')" >> "$LOG"
fi
