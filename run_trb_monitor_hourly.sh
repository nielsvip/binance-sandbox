#!/bin/bash
# TRB hedge monitor — runs during market hours (13:00–21:00 UTC, Mon–Fri)
# Sends macOS notifications on DANGER positions or unhedged losing sectors.

PYTHON=/opt/anaconda3/envs/binance_env/bin/python
SCRIPT=/Users/niels/Documents/binance/monitor_trb_hedge.py
LOGDIR=/Users/niels/Documents/binance/logs
LOG="$LOGDIR/trb_monitor_hourly.log"

HOUR=$(date -u +%-H)
DOW=$(date -u +%u)   # 1=Mon … 7=Sun

# Only run Mon–Fri, 13:00–21:00 UTC
if [ "$DOW" -gt 5 ] || [ "$HOUR" -lt 13 ] || [ "$HOUR" -ge 21 ]; then
    exit 0
fi

TS=$(date -u '+%Y-%m-%d %H:%M UTC')
echo "=== $TS ===" >> "$LOG"

OUTPUT=$("$PYTHON" "$SCRIPT" 2>&1)
echo "$OUTPUT" >> "$LOG"
echo "" >> "$LOG"

# DANGER alert (gain < -5%)
DANGER_LINE=$(echo "$OUTPUT" | grep "DANGER")
if [ -n "$DANGER_LINE" ]; then
    SYMS=$(echo "$DANGER_LINE" | awk '{print $2}' | tr '\n' ' ' | sed 's/ *$//')
    UNHEDGED=$(echo "$OUTPUT" | grep "NO SECTOR HEDGE" | awk '{print $2, $3}' | head -3 | tr '\n' ' ')
    MSG="DANGER: $SYMS"
    [ -n "$UNHEDGED" ] && MSG="$MSG | Unhedged: $UNHEDGED"
    osascript -e "display notification \"$MSG\" with title \"TRB MONITOR 🔴 DANGER\" sound name \"Sosumi\""
fi

# Unhedged losing sectors (without full DANGER)
UNHEDGED_SEC=$(echo "$OUTPUT" | grep "UNHEDGED LOSING SECTORS" | head -1)
if [ -n "$UNHEDGED_SEC" ] && [ -z "$DANGER_LINE" ]; then
    SECTORS=$(echo "$OUTPUT" | grep -A5 "UNHEDGED LOSING SECTORS" | grep "  " | head -3 | sed 's/^  //' | tr '\n' ' ')
    osascript -e "display notification \"$SECTORS\" with title \"TRB MONITOR ⚠️ Unhedged\" sound name \"Basso\""
fi

# Alert level (gain < -2%) — log only, no notification unless unhedged
ALERT_LINE=$(echo "$OUTPUT" | grep "ALERT")
if [ -n "$ALERT_LINE" ] && [ -z "$DANGER_LINE" ]; then
    echo "[ALERT] $ALERT_LINE" >> "$LOG"
fi
