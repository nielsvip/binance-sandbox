#!/usr/bin/env bash
# s1_live_hermetic_guard — S1 may NEVER spend CPU on live while the MacBook is producing market data.
#
# USER 2026-10-27: "s1 can NEVER spend ANY cpu on live when macbook is producing make that ABSOLUTELY HERMETIC it
# can ONLY pause live because macbook is offline (same thing I said the last 12 million times BY THE WAY)".
#
# WHY IT WAS NEEDED. On 2026-10-27 S1 had been in crypto data failover for 467,395 s (5.4 days) while the Mac held
# the lease and was fresh. Root cause: NOTHING was enforcing stand-down. Every supervision line on S1's crontab was
# commented out -- the per-minute monitor as "#RETIRED_BY_LEASE_20261014#" and the agent keepalive as
# "#BACKTEST_ONLY_20261025" -- and the lease mechanism that replaced the monitor had itself gone stale
# (data/live_failover/lease.json and state.json last written 2026-10-25T23:36Z, while mac_data_heartbeat.json keeps
# updating every minute). A retired enforcer plus a stale replacement equals no enforcer.
#
# THE HERMETIC PROPERTY. This guard can ONLY STOP live on S1. It never starts, enables or resumes anything. The
# worst case it can produce is "S1 runs no live", which is the stated preference: NOT trading beats double trading
# (see memory s1_never_blocks_mac_20261022). Live on S1 may therefore only ever begin through the normal failover
# path, and only while this guard measures the Mac as absent.
#
# THE ONE CONDITION. Live is permitted on S1 only when the Mac's own heartbeat is MISSING or OLDER than
# MAC_OFFLINE_S. Everything else -- lease holder, failover mode, marker files, what some status file claims -- is
# irrelevant to the decision: a fresh Mac heartbeat means the Mac is producing, and that alone forces stand-down.
# The heartbeat is the Mac's evidence about itself (data/live_failover/mac_data_heartbeat.json, pushed every
# minute), so it cannot be faked into freshness by anything on S1.
#
# WHAT IT NEVER TOUCHES. The always-on ez_s1_stack read-only feeds (own cwd). Killing the stack's ez_klines forces a
# REST restart burst and a 418 ban -- that lesson is already encoded in tools/s1_failover_stop.sh and is kept here.
#
# Install: every minute on S1's crontab, tag S1_LIVE_HERMETIC_GUARD (see tools/install_s1_hermetic_guard.sh).
set -u

HB=/home/niels/binance/data/live_failover/mac_data_heartbeat.json
MARKER_DIR=/home/niels/binance/data
LOG=/home/niels/logs/s1_live_hermetic_guard.log
STATUS=/home/niels/binance/data/live_failover/s1_hermetic_guard_status.json
MAC_OFFLINE_S=${MAC_OFFLINE_S:-180}
UNITS="live-failover-crypto-producers.service live-failover-stocks-producers.service live-failover-trading.service"
# Loose live producers (started outside a unit). ez_s1_stack is excluded below and must stay running.
PATTERNS="ez_market_data ez_indicators ez_positions_service s1_position_failover tradier_prices"

ts() { date -u +%FT%TZ; }
log() { echo "[$(ts)] $*" >> "$LOG"; }

mkdir -p "$(dirname "$LOG")" "$(dirname "$STATUS")"

now=$(date -u +%s)
if [ -r "$HB" ]; then
    hb_epoch=$(python3 -c "import json,sys;print(int(float(json.load(open('$HB')).get('epoch') or 0)))" 2>/dev/null || echo 0)
else
    hb_epoch=0
fi
if [ "$hb_epoch" -gt 0 ]; then
    age=$(( now - hb_epoch ))
else
    age=-1
fi

if [ "$age" -lt 0 ] || [ "$age" -gt "$MAC_OFFLINE_S" ]; then
    decision="MAC_ABSENT"
else
    decision="MAC_PRODUCING"
fi

stopped_units=""
killed_pids=""
cleared_markers=0

if [ "$decision" = "MAC_PRODUCING" ]; then
    for unit in $UNITS; do
        if [ "$(systemctl is-active "$unit" 2>/dev/null)" = "active" ]; then
            systemctl stop "$unit" >/dev/null 2>&1 || sudo -n systemctl stop "$unit" >/dev/null 2>&1
            stopped_units="$stopped_units $unit"
            log "STOP unit $unit (mac heartbeat ${age}s old)"
        fi
    done
    for pattern in $PATTERNS; do
        for pid in $(pgrep -f "$pattern" 2>/dev/null); do
            cwd=$(readlink "/proc/$pid/cwd" 2>/dev/null)
            case "$cwd" in
                /home/niels/binance/ez_s1_stack*) continue ;;
            esac
            # A backtest or this guard itself must never be mistaken for a live producer.
            args=$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null)
            case "$args" in
                *v15_pilot*|*v12_quick*|*backtest*|*hermetic_guard*) continue ;;
            esac
            kill -TERM "$pid" 2>/dev/null && killed_pids="$killed_pids $pid"
            log "SIGTERM pid $pid ($pattern) cwd=$cwd"
        done
    done
    for flag in "$MARKER_DIR"/s1_failover_active_*.flag; do
        [ -e "$flag" ] || continue
        rm -f "$flag" && cleared_markers=$(( cleared_markers + 1 ))
        log "CLEAR marker $(basename "$flag")"
    done
fi

python3 - "$STATUS" "$decision" "$age" "$MAC_OFFLINE_S" "$stopped_units" "$killed_pids" "$cleared_markers" <<'PY'
import json, sys, datetime
path, decision, age, thresh, units, pids, markers = sys.argv[1:8]
json.dump({
    "contract": "s1_hermetic_guard_v1",
    "ts": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "decision": decision,
    "mac_heartbeat_age_s": int(age),
    "mac_offline_threshold_s": int(thresh),
    "stopped_units": units.split(),
    "killed_pids": [int(p) for p in pids.split()],
    "cleared_markers": int(markers),
    "note": "this guard can only STOP live on S1; it never starts anything",
}, open(path, "w"), indent=1, sort_keys=True)
PY

if [ "$decision" = "MAC_PRODUCING" ] && [ -z "$stopped_units$killed_pids" ] && [ "$cleared_markers" = "0" ]; then
    :  # already clean: stay silent so the log only ever records real enforcement
else
    log "decision=$decision mac_heartbeat_age=${age}s stopped=[${stopped_units# }] killed=[${killed_pids# }] markers=$cleared_markers"
fi
exit 0
