#!/bin/bash
# Health monitor — runs every 5 min, logs process uptimes + errors
LOG="/Users/niels/logs/health_monitor.log"
while true; do
    echo "=== $(date -u +'%Y-%m-%d %H:%M:%S UTC') ===" >> "$LOG"
    for svc in ez_indicators ez_klines ez_prices ez_rankings ez_market_data ez_crosses; do
        pid=$(pgrep -f "python.*${svc}.py" | head -1)
        if [ -n "$pid" ]; then
            uptime=$(ps -o etime= -p $pid 2>/dev/null | tr -d ' ')
            echo "  $svc: UP ($uptime)" >> "$LOG"
            # Alert if uptime < 5 min AND this is NOT the first check
            seconds=$(ps -o etimes= -p $pid 2>/dev/null | tr -d ' ')
            if [ -n "$seconds" ] && [ "$seconds" -lt 300 ]; then
                echo "  ⚠️  $svc RESTARTED RECENTLY (uptime: $uptime)" >> "$LOG"
            fi
        else
            echo "  ❌ $svc: DOWN!" >> "$LOG"
        fi
    done
    for acct in ang inf flz fin men; do
        pid=$(pgrep -f "python.*ez_manage.py --account $acct" | head -1)
        if [ -n "$pid" ]; then
            uptime=$(ps -o etime= -p $pid 2>/dev/null | tr -d ' ')
            echo "  manage_$acct: UP ($uptime)" >> "$LOG"
        else
            echo "  ❌ manage_$acct: DOWN!" >> "$LOG"
        fi
    done
    # Check for errors since last check
    errors=$(grep -h "ERROR\|CRITICAL\|banned\|\-1003" /Users/niels/logs/ez_*_watchdog.log 2>/dev/null | grep "$(date -u +'%Y-%m-%d %H')" | grep -v "EMERGENCY_DEEP_LOSS_BLOCKED" | wc -l | tr -d ' ')
    [ "$errors" -gt 0 ] && echo "  ⚠️  $errors error(s) in watchdog logs this hour" >> "$LOG"
    echo "" >> "$LOG"
    sleep 300
done
