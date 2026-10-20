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
# 2026-10-19 ACCOUNT SCOPE: with lease "accounts": {"stocks": ["trb"]} S1 holds ONLY those accounts —
# siblings keep trading on the Mac (per-account gate answers in lf_lease_fenced). Scope detail lives in
# the gate; here each account is started when UNfenced-for-S1 and TERM-stopped when fenced-away.
stop_account_traders() {
    local mode="$1" acct="$2" p
    if [ "$mode" = "crypto" ]; then
        for pat in "ez_manage.py --account $acct" "ez_positions_service_main.py $acct" "ez_positions_realtime.py $acct"; do
            for p in $(pgrep -f "$pat" 2>/dev/null); do kill -TERM "$p" 2>/dev/null; done
        done
    else
        for pat in "tradier_manage.py --accounts $acct" "tradier_positions.py --accounts $acct"; do
            for p in $(pgrep -f "$pat" 2>/dev/null); do kill -TERM "$p" 2>/dev/null; done
        done
    fi
}
# USER 2026-10-20: "s1 should keep trading until macbook is stable" — which it cannot do if it takes a third of an
# hour to get its traders up. MEASURED that morning, with S1 holding crypto+stocks across the 13:30Z bell:
#   starting crypto:ang 13:53:48  inf 13:54:30  flz 13:55:12  men 13:55:54  fin 13:56:36   (one per 42 s lap)
#   starting stocks:trb 13:46:06  (16 min AFTER the open)      trc 14:04:00   tra never in that window
# The cause is here, not in the lease: s1_failover_start.sh is called SYNCHRONOUSLY and waits up to 240 s in its own
# positions gate, so one account per lap is the ceiling, and the stocks block sits behind the whole crypto ramp even
# though crypto trades 24/7 and the stock session has a bell.
# Two changes, both narrow:
#   * starts run in the BACKGROUND, at most START_INFLIGHT at a time. The existing single-flight pgrep guard already
#     prevents a duplicate start per account (the thing that "multiplies broker polling during IP bans"); what it
#     never needed was for the starts to be serial. Children stay in this unit's cgroup, so `systemctl stop` and
#     the _shutdown trap are unaffected.
#   * inside the stocks window stocks are started FIRST. Crypto loses nothing by waiting one lap; stocks lose the open.
# 2026-10-20 ALL EIGHT AT ONCE (USER: "even macbook can start all 8 accounts simultaneously so s1 can do it twice
# as fast in <30 seconds ... if it kicks in after 2 min it has to be trading before the 3rd min passes" and
# "NEVER EVER EVER AGAIN CAN WE HAVE 3 MINUTES OR MORE WITHOUT TRADING ON ANY ACCOUNT").
# 3 in flight meant the 8 accounts needed 3 laps; with the 20 s retry and the positions gate that put the last
# account minutes past the takeover. The Mac starts all 8 concurrently, so S1 has no reason not to. The single-flight
# pgrep guard per account still prevents a duplicate start — serialisation was never what protected anything.
START_INFLIGHT=${S1_SUP_MAX_START_INFLIGHT:-8}
# `pgrep -fc` prints 0 AND exits 1 on no match, so `pgrep -fc ... || echo 0` yields TWO lines and the
# arithmetic test below dies on "0\n0". Piping through wc -l always prints exactly one integer and always exits 0.
_starts_in_flight() { pgrep -f "s1_failover_start\.sh" 2>/dev/null | wc -l | tr -d ' '; }
start_venue() {
    local mode="$1" script="$2" pat="$3" acct
    shift 3
    for acct in "$@"; do
        if lf_lease_fenced "$script" "$BIN" "$acct"; then
            stop_account_traders "$mode" "$acct"
            continue
        fi
        pgrep -f "s1_failover_start.sh $mode $acct" >/dev/null && continue
        pgrep -f "$pat $acct" >/dev/null && continue
        if [ "$(_starts_in_flight)" -ge "$START_INFLIGHT" ]; then
            log "start queue full ($START_INFLIGHT in flight) — $mode:$acct next lap"
            continue
        fi
        log "starting $mode:$acct"
        setsid bash "$BIN/tools/s1_failover_start.sh" "$mode" "$acct" >> /home/niels/logs/s1_failover.log 2>&1 </dev/null &
    done
}
while true; do
    if ! grep -q '"holder": *"s1"' "$BIN/data/live_failover/lease.json" 2>/dev/null; then
        log "lease no longer held by S1 — exiting (systemd stops all S1 traders)"
        exit 0
    fi
    if stocks_window; then
        start_venue stocks tradier_manage.py "tradier_manage.py --accounts" trb trc tra
    fi
    start_venue crypto ez_manage.py "ez_manage.py --account" ang inf flz men fin
    _venue_held=0
    for acct in ang inf flz men fin; do
        lf_lease_fenced ez_manage.py "$BIN" "$acct" || { _venue_held=1; break; }
    done
    if [ "$_venue_held" = "1" ]; then
        for extra in ez_positions_watchdog.py ez_reentry_daemon.py; do
            if [ -f "$BIN/$extra" ] && ! pgrep -f "python.*$extra" >/dev/null; then
                setsid nohup "$PY" -u "$extra" >> "/home/niels/logs/failover_${extra}.log" 2>&1 </dev/null &
                log "started $extra"
            fi
        done
    fi
    sleep 20
done
