#!/bin/bash
# S1-side failover stopper (2026-07-20 USER: MacBook recovered -> S1 must stand down
# immediately to avoid double-trading the same account from two machines).
# Runs ONLY on S1, invoked by s1_failover_monitor.sh. Never call manually except to test.
# Usage: s1_failover_stop.sh <crypto|stocks> <account>
# Stops ONLY the order-placing manager for this account. Shared read-only feeds are left
# running if any OTHER failover-active account of the same mode still needs them (checked via
# the marker files s1_failover_monitor.sh maintains); stopped too if this was the last one —
# restores "S1 stays dedicated to sandbox" once no account is failed-over.
set -u
MODE="${1:-}"
ACCT="${2:-}"
LOG=/home/niels/logs
MARKER_DIR=/home/niels/binance/data
ts(){ date -u +%FT%TZ; }
kill_pattern(){
    local pattern="$1"
    local pids
    pids=$(pgrep -f "$pattern")
    if [ -n "$pids" ]; then
        kill -TERM $pids 2>/dev/null
        echo "[$(ts)] [FAILOVER_STOP] sent SIGTERM to: $pattern (pids: $pids)" >> "$LOG/s1_failover.log"
    fi
}
other_active_markers(){ # count other s1_failover_active_<mode>_*.flag markers besides this account
    local mode="$1"; local acct="$2"
    ls "$MARKER_DIR"/s1_failover_active_${mode}_*.flag 2>/dev/null | grep -v "_${acct}.flag$" | wc -l | tr -d ' '
}
case "$MODE" in
  crypto)
    case "$ACCT" in ang|inf|flz|men|fin) ;; *) echo "bad crypto account: $ACCT"; exit 1 ;; esac
    kill_pattern "ez_manage.py --account $ACCT"
    if [ "$(other_active_markers crypto "$ACCT")" = "0" ]; then
        for feed in ez_market_data.py ez_orderbook.py ez_positions_service.py ez_positions_quick.py ez_prices.py ez_klines.py ez_indicators.py ez_rankings.py ez_crosses.py; do
            kill_pattern "$feed"
        done
        echo "[$(ts)] [FAILOVER_STOP] last crypto failover account stood down — feeds stopped, S1 back to sandbox-only" >> "$LOG/s1_failover.log"
    fi
    ;;
  stocks)
    case "$ACCT" in trb|trc) ;; *) echo "bad stocks account: $ACCT"; exit 1 ;; esac
    kill_pattern "tradier_manage.py --accounts $ACCT"
    if [ "$(other_active_markers stocks "$ACCT")" = "0" ]; then
        kill_pattern "tradier_prices.py"
        kill_pattern "tradier_indicators.py"
        kill_pattern "tradier_rankings.py"
        kill_pattern "tradier_positions.py --accounts $ACCT"
        echo "[$(ts)] [FAILOVER_STOP] last stocks failover account stood down — feeds stopped, S1 back to sandbox-only" >> "$LOG/s1_failover.log"
    fi
    ;;
  *)
    echo "usage: s1_failover_stop.sh <crypto|stocks> <account>"; exit 1 ;;
esac
