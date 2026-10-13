#!/bin/bash
# S1 full-trading-failover supervisor (2026-10-13 LIVE FAILOVER S1). ExecStart of
# live-failover-trading.service (NOT enabled at boot; started only by tools/live_failover_s1.py
# after it wrote lease.json holder=s1). Every 20 s while S1 holds the lease:
#   crypto: ez_manage --account {ang,inf,flz,men,fin} + the non-stack feeds via
#           tools/s1_failover_start.sh (ez_prices/ez_klines/ez_indicators come from the always-on
#           ez_s1_stack, bridged into ~/binance by the watchdog), plus ez_positions_watchdog.py and
#           ez_reentry_daemon.py (the Mac runs both).
#   stocks: tradier_manage --accounts {trb,trc,tra} (+ tradier feeds) on NYSE session days from
#           open-35 min (same as the Mac's 13:00Z start_stocks.sh).
# Lease lost -> exit 0 (systemd then stops the unit and SIGTERMs every child in its cgroup).
# All children live in this unit's cgroup (setsid does not leave it), so `systemctl stop`
# is the single clean stop path. Never flattens or closes anything.
set -u
BIN=/home/niels/binance
cd "$BIN" || exit 1
PY=/home/niels/.conda/envs/binance_env/bin/python
LOG=/home/niels/logs/live_failover.log
source "$BIN/tools/live_failover_lease_gate.sh"
ts(){ date -u +%FT%TZ; }
log(){ echo "[$(ts)] [SUPERVISOR] $1" >> "$LOG"; }
export FAILOVER_SKIP_FEEDS="ez_prices.py ez_klines.py ez_indicators.py"
stocks_window(){
    "$PY" - <<'EOF'
import sys
sys.path.insert(0, "/home/niels/binance/tools")
import live_failover_common as C
s = C.nyse_session()
sys.exit(0 if s and s[0] - 2100 <= C.now_s() <= s[1] + 1800 else 1)
EOF
}
log "START pid=$$ lease=$(cat "$BIN/data/live_failover/lease.json" 2>/dev/null | tr -d '\n ' | cut -c1-200)"
while true; do
    if lf_lease_fenced ez_manage.py "$BIN"; then
        log "lease no longer held by S1 — exiting (systemd stops all S1 traders)"
        exit 0
    fi
    for acct in ang inf flz men fin; do
        pgrep -f "ez_manage.py --account $acct" >/dev/null || { log "starting crypto:$acct"; bash "$BIN/tools/s1_failover_start.sh" crypto "$acct"; }
    done
    for extra in ez_positions_watchdog.py ez_reentry_daemon.py; do
        if [ -f "$BIN/$extra" ] && ! pgrep -f "python.*$extra" >/dev/null; then
            setsid nohup "$PY" -u "$extra" >> "/home/niels/logs/failover_${extra}.log" 2>&1 </dev/null &
            log "started $extra"
        fi
    done
    if stocks_window; then
        for acct in trb trc tra; do
            pgrep -f "tradier_manage.py --accounts $acct" >/dev/null || { log "starting stocks:$acct"; bash "$BIN/tools/s1_failover_start.sh" stocks "$acct"; }
        done
    fi
    sleep 20
done
