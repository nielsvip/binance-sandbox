#!/bin/bash
# Wait for klines download to finish, then launch backtests
# Run on server: nohup bash launch_backtests_after_download.sh &

PYTHON="/home/niels/.conda/envs/binance_env/bin/python"
WORKDIR="/home/niels/binance-sandbox"
LOG="/home/niels/logs/backtest_launcher.log"

log() { echo "[$(date +'%Y-%m-%d %H:%M:%S')] $1" >> "$LOG"; }

log "Waiting for klines download to complete..."

# Wait for download process to finish
while pgrep -f "download_klines_bulk" >/dev/null 2>&1; do
    sleep 60
    PROGRESS=$(tail -1 /home/niels/logs/klines_download.log 2>/dev/null | grep -oP '\[\d+/\d+\]' | tail -1)
    log "Download still running... $PROGRESS"
done

log "Download complete! Launching backtests..."

cd "$WORKDIR"

# Launch 15m deep sweep
rm -f data/backtest_wt_15m_deep/checkpoint.json 2>/dev/null
nohup "$PYTHON" -u backtest_wt_15m_deep.py > /dev/null 2>&1 &
log "15m deep sweep launched PID=$!"

sleep 5

# Launch main ultimate backtest
rm -f data/backtest_wt_ultimate/checkpoint.json 2>/dev/null
nohup "$PYTHON" -u backtest_wt_ultimate.py --market both --reset > /dev/null 2>&1 &
log "Main backtest launched PID=$!"

log "All backtests launched. Monitor: tail -f data/backtest_wt_15m_deep/sweep.log"
