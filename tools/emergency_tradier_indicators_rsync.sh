#!/bin/bash
# emergency_tradier_indicators_rsync.sh — heals stale tradier indicators when timestamped is missing/stale
# Called by tradier_manage market_data_sync_loop every 10s when local timestamped >15s old.
# Previously this file DID NOT EXIST, so the Popen silently failed and market_snapshot stayed 13 days stale.
# New: self-heal from _latest + try gateway/S1 pull.
set -e
BASE_DIR="/Users/niels/Documents/binance"
DATA_DIR="$BASE_DIR/data/tradier"
LATEST="$DATA_DIR/tradier_indicators_latest.json"
# 1. Self-heal: if _latest fresh (<60s), copy to timestamped so poller can pick it up
if [ -f "$LATEST" ]; then
    AGE=$(python3 -c "import time,os; print(time.time()-os.path.getmtime('$LATEST'))" 2>/dev/null || echo 9999)
    AGE_INT=${AGE%.*}
    if [ "$AGE_INT" -lt 60 ]; then
        TS="$DATA_DIR/tradier_indicators_$(date +%s).json"
        if [ ! -f "$TS" ]; then
            cp -p "$LATEST" "$TS" 2>/dev/null && echo "[emergency_rsync] healed $TS from _latest age ${AGE_INT}s" || true
        fi
    fi
fi
# 2. Try pulling timestamped set from gateway (klines + indicators mirror)
# Gateway indicators live at ~/binance/data/tradier/ on gateway host
for HOST in gateway-internal 157.90.168.35; do
    if ssh -o ConnectTimeout=3 -o BatchMode=yes -o StrictHostKeyChecking=no "$HOST" "ls ~/binance/data/tradier/tradier_indicators_latest.json" >/dev/null 2>&1; then
        rsync -az --timeout=10 "$HOST:~/binance/data/tradier/tradier_indicators_*.json" "$DATA_DIR/" 2>/dev/null || true
        # Also try s1_timestamped dir if gateway has it
        rsync -az --timeout=10 "$HOST:~/binance/data/tradier/" "$DATA_DIR/s1_timestamped/" 2>/dev/null || true
        break
    fi
done
# 3. Ping tradier_indicators.py to force a save if writer is alive but not saving
# (no-op: writer saves on its own 30s cadence)
