#!/bin/bash
# S1-side failover starter (2026-07-20 USER: MacBook-down -> S1 takes over live trading).
# Runs ONLY on S1, invoked by s1_failover_monitor.sh. Never call manually except to test.
# Usage: s1_failover_start.sh <crypto|stocks> <account>
# crypto account in {ang,inf,flz,men,fin}; stocks account in {trb,trc}.
# Idempotent: only starts what is down. Starts the shared read-only feeds too (harmless to
# double-run per the 2026-07-02 FAILOVER GATE fix — each writes its OWN machine-local Redis)
# so the manager has real indicators/prices to read; this adds ~40s startup delay.
set -u
MODE="${1:-}"
ACCT="${2:-}"
cd /home/niels/binance || exit 1
PY=/home/niels/.conda/envs/binance_env/bin/python
LOG=/home/niels/logs
mkdir -p "$LOG"
SYNC_AGE="unknown"
if [ -f "data/livecode_sync.json" ]; then
    SYNC_EPOCH=$(jq -r '.epoch // 0' data/livecode_sync.json 2>/dev/null | cut -d. -f1)
    SYNC_AGE="$(( $(date +%s) - ${SYNC_EPOCH:-0} ))s"
fi
ts(){ date -u +%FT%TZ; }
up(){ pgrep -f "$1" >/dev/null 2>&1; }
start(){ # start <pattern> <cmd...>
    local pattern="$1"; shift
    up "$pattern" || { setsid nohup "$@" >> "$LOG/failover_${pattern// /_}.log" 2>&1 </dev/null & disown; echo "[$(ts)] [FAILOVER_START] started: $pattern" >> "$LOG/s1_failover.log"; }
}
# 2026-10-13 LIVE FAILOVER LEASE: S1 starts traders ONLY while it holds data/live_failover/lease.json (holder s1).
# Applies to every caller (tools/s1_trading_supervisor.sh and the legacy s1_failover_monitor.sh).
if [ -f tools/live_failover_lease_gate.sh ]; then
    source tools/live_failover_lease_gate.sh
    if lf_lease_fenced ez_manage.py /home/niels/binance; then echo "[$(ts)] [FAILOVER_START] REFUSED $MODE:$ACCT — S1 does not hold the live-trading lease" >> "$LOG/s1_failover.log"; exit 3; fi
fi
SKIP=" ${FAILOVER_SKIP_FEEDS:-} "
echo "[$(ts)] [FAILOVER_START] takeover $MODE:$ACCT with live code age ${SYNC_AGE} (Mac push every 15min)" >> "$LOG/s1_failover.log"
case "$MODE" in
  crypto)
    case "$ACCT" in ang|inf|flz|men|fin) ;; *) echo "bad crypto account: $ACCT"; exit 1 ;; esac
    started_feed=0
    for feed in ez_market_data.py ez_orderbook.py ez_positions_service.py ez_positions_quick.py ez_prices.py ez_klines.py ez_indicators.py ez_rankings.py ez_crosses.py; do
        [[ "$SKIP" == *" $feed "* ]] && continue
        up "$feed" || { setsid nohup $PY "$feed" >> "$LOG/failover_${feed}.log" 2>&1 </dev/null & disown; started_feed=1; echo "[$(ts)] [FAILOVER_START] started feed: $feed" >> "$LOG/s1_failover.log"; }
    done
    [ "$started_feed" = "1" ] && sleep 40
    up "ez_manage.py --account $ACCT" || { setsid nohup $PY ez_manage.py --account "$ACCT" >> "$LOG/failover_ez_manage_${ACCT}.log" 2>&1 </dev/null & disown; echo "[$(ts)] [FAILOVER_START] started ez_manage.py --account $ACCT" >> "$LOG/s1_failover.log"; }
    ;;
  stocks)
    case "$ACCT" in trb|trc|tra) ;; *) echo "bad stocks account: $ACCT"; exit 1 ;; esac
    started_feed=0
    for feed in tradier_prices.py tradier_indicators.py tradier_rankings.py; do
        [[ "$SKIP" == *" $feed "* ]] && continue
        up "$feed" || { setsid nohup $PY "$feed" >> "$LOG/failover_${feed}.log" 2>&1 </dev/null & disown; started_feed=1; echo "[$(ts)] [FAILOVER_START] started feed: $feed" >> "$LOG/s1_failover.log"; }
    done
    up "tradier_positions.py --accounts $ACCT" || { setsid nohup $PY tradier_positions.py --accounts "$ACCT" >> "$LOG/failover_tradier_positions_${ACCT}.log" 2>&1 </dev/null & disown; started_feed=1; }
    [ "$started_feed" = "1" ] && sleep 40
    up "tradier_manage.py --accounts $ACCT" || { setsid nohup $PY tradier_manage.py --accounts "$ACCT" >> "$LOG/failover_tradier_manage_${ACCT}.log" 2>&1 </dev/null & disown; echo "[$(ts)] [FAILOVER_START] started tradier_manage.py --accounts $ACCT" >> "$LOG/s1_failover.log"; }
    ;;
  *)
    echo "usage: s1_failover_start.sh <crypto|stocks> <account>"; exit 1 ;;
esac
