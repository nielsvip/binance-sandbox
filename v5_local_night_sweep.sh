#!/bin/bash
# V5 Local Night Sweep — runs 20:00 UTC to 13:00 UTC
# Shuts down tradier scripts, runs V5 backtests, restarts tradier at market prep

BINANCE_DIR="/Users/niels/Documents/binance"
PYTHON="/opt/anaconda3/envs/binance_env/bin/python"
LOG_DIR="/Users/niels/logs"

echo "$(date -u): === V5 LOCAL NIGHT SWEEP STARTING ==="

# 1. Kill tradier scripts
echo "$(date -u): Stopping tradier_manage..."
pkill -f "tradier_manage.py" 2>/dev/null
pkill -f "tradier_indicators.py" 2>/dev/null
pkill -f "tradier_rankings.py" 2>/dev/null
sleep 5
echo "$(date -u): Tradier scripts stopped."

# 2. Run V5 continuous sweep until 13:00 UTC
cd "$BINANCE_DIR"

# Calculate seconds until 13:00 UTC tomorrow
# If current hour < 13, stop today at 13:00; if >= 13, stop tomorrow at 13:00
STOP_HOUR=13
NOW_EPOCH=$(date -u +%s)
TODAY_13=$(date -u -j -f "%Y-%m-%d %H:%M:%S" "$(date -u +%Y-%m-%d) 13:00:00" +%s 2>/dev/null || date -u -d "$(date -u +%Y-%m-%d) 13:00:00" +%s 2>/dev/null)
if [ "$NOW_EPOCH" -ge "$TODAY_13" ]; then
    # Past 13:00 today, target 13:00 tomorrow
    STOP_EPOCH=$((TODAY_13 + 86400))
else
    STOP_EPOCH=$TODAY_13
fi
TIMEOUT=$((STOP_EPOCH - NOW_EPOCH))
echo "$(date -u): Will run for ${TIMEOUT}s (until $(date -u -r $STOP_EPOCH +%H:%M 2>/dev/null || echo '13:00') UTC)"

# 3. Run sweep with timeout
timeout ${TIMEOUT}s "$PYTHON" -u v5_continuous_sweep.py > "$LOG_DIR/v5_local_sweep.log" 2>&1
echo "$(date -u): Sweep finished (timeout or complete)."

# 4. Restart tradier scripts
echo "$(date -u): Restarting tradier scripts..."
cd "$BINANCE_DIR"
# Use the existing launch mechanism
bash run_with_watchdog.sh tradier_manage.py --accounts trb &
bash run_with_watchdog.sh tradier_manage.py --accounts trc &
bash run_with_watchdog.sh tradier_indicators.py &
bash run_with_watchdog.sh tradier_rankings.py &
echo "$(date -u): Tradier scripts restarted."
echo "$(date -u): === V5 LOCAL NIGHT SWEEP COMPLETE ==="
