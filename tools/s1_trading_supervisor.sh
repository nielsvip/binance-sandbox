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
# 2026-10-14 per-venue lease: a venue runs only while lease.json run_venues contains it (lf_lease_fenced
# per venue); s1_failover_start.sh refuses a trader until S1's own position writer matches the broker.
# Lease lost -> exit 0 (systemd then stops the unit and SIGTERMs every child in its cgroup).
# All children live in this unit's cgroup (setsid does not leave it), so `systemctl stop`
# is the single clean stop path. Never flattens or closes anything.
set -u
BIN=/home/niels/binance
cd "$BIN" || exit 1
PY=/home/niels/.conda/envs/binance_env_mac/bin/python  # 2026-10-14: S1 conda env = exact copy of the Mac live env (py 3.11.14, numpy 2.4.6, pandas 3.0.0 …); old env binance_env kept for rollback
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
# 2026-10-17: `systemctl stop live-failover-trading` TIMED OUT and left the unit `failed`
# (Result=timeout, TimeoutStopSec=90, KillMode=control-group) during the first real crypto failover drill —
# observed live at 03:10Z with the lease already handed back to the Mac while S1 still held the unit. A unit stuck
# in `failed` reads to every health check as "the standby is broken", and the orphaned traders are the double-trading
# risk the whole lease design exists to prevent. The header's claim that `systemctl stop` is "the single clean stop
# path" was true in intent and false in practice: this loop had NO signal handling, so TERM reached the children but
# the supervisor could still be mid-`sleep 20` or mid-`s1_failover_start.sh`, and at least one child did not die on
# TERM inside the stop timeout. Stop now means stop — the trap is authoritative and exits the script itself.
# Identical fix, and identical reasoning, to tools/s1_producers_stocks.sh earlier the same night.
_shutdown() {
    log "shutdown signal — stopping S1 traders and exiting"
    for pat in "ez_manage.py --account" "tradier_manage.py --accounts" "ez_positions_watchdog.py" "ez_reentry_daemon.py" "tradier_positions.py"; do
        for p in $(pgrep -f "$pat" 2>/dev/null); do kill -TERM "$p" 2>/dev/null; done
    done
    sleep 5
    for pat in "ez_manage.py --account" "tradier_manage.py --accounts" "ez_positions_watchdog.py" "ez_reentry_daemon.py" "tradier_positions.py"; do
        for p in $(pgrep -f "$pat" 2>/dev/null); do kill -KILL "$p" 2>/dev/null; done
    done
    log "shutdown complete — no S1 trader left"
    exit 0
}
trap _shutdown TERM INT

log "START pid=$$ lease=$(cat "$BIN/data/live_failover/lease.json" 2>/dev/null | tr -d '\n ' | cut -c1-260)"
while true; do
    if ! grep -q '"holder": *"s1"' "$BIN/data/live_failover/lease.json" 2>/dev/null; then
        log "lease no longer held by S1 — exiting (systemd stops all S1 traders)"
        exit 0
    fi
    if ! lf_lease_fenced ez_manage.py "$BIN"; then
        for acct in ang inf flz men fin; do
            pgrep -f "ez_manage.py --account $acct" >/dev/null || { log "starting crypto:$acct"; bash "$BIN/tools/s1_failover_start.sh" crypto "$acct"; }
        done
        for extra in ez_positions_watchdog.py ez_reentry_daemon.py; do
            if [ -f "$BIN/$extra" ] && ! pgrep -f "python.*$extra" >/dev/null; then
                setsid nohup "$PY" -u "$extra" >> "/home/niels/logs/failover_${extra}.log" 2>&1 </dev/null &
                log "started $extra"
            fi
        done
    fi
    if ! lf_lease_fenced tradier_manage.py "$BIN" && stocks_window; then
        for acct in trb trc tra; do
            pgrep -f "tradier_manage.py --accounts $acct" >/dev/null || { log "starting stocks:$acct"; bash "$BIN/tools/s1_failover_start.sh" stocks "$acct"; }
        done
    fi
    sleep 20
done
