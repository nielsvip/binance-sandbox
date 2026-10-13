#!/bin/bash
# S1 failover monitor (2026-07-20 USER MANDATE): "if for any reason macbook is not running
# ez_/tradier_ (market hours), s1 immediately kicks in and takes over live trading. Whenever
# macbook runs fine s1 should remain dedicated to sandbox testing."
#
# Runs every 1 min via S1 crontab. Reads the heartbeat MacBook pushes every 1 min
# (mac_live_heartbeat.py -> data/mac_live_heartbeat.json). Decides per-account:
#   - Mac confirmed down (heartbeat stale/missing OR explicitly reports the account dead) AND
#     this account should be live right now -> start it here (ONLY if armed — see below).
#   - Mac confirmed alive for an account this box previously failed over -> stop it here
#     immediately (ALWAYS, regardless of arm state) — this is the anti-double-trade guard.
#
# ARMING: auto-START is disabled by default (kill-switch pattern used throughout this repo
# for any new/unproven capability). Detection + logging always run. To arm:
#   touch /home/niels/binance/data/S1_FAILOVER_ARMED
# To disarm: rm /home/niels/binance/data/S1_FAILOVER_ARMED
# Auto-STOP (recovery) is NEVER gated by the arm flag — if S1 ever holds a live position it
# started, it must always be able to hand back to Mac the moment Mac is confirmed alive again.
#
# DEBOUNCE (2026-07-20 incident fix): a single down-reading used to arm an instant takeover —
# a one-cycle pgrep miss during a Mac watchdog self-heal restart (ang/inf both self-heal
# routinely) raced against a live Mac and produced a real double-trading incident (S1 ran
# duplicate ez_manage for inf/flz/men/fin ~19:54-20:17 UTC while Mac was fine the whole time).
# Now requires DOWN_DEBOUNCE_CYCLES consecutive down-readings (tracked per account in
# s1_failover_downcount_<mode>_<acct>) before a takeover is even attempted, armed or not.
# Recovery (stand-down) stays instant/undebounced — erring toward Mac owning the account.
set -u
BASE=/home/niels/binance
DATA="$BASE/data"
LOG=/home/niels/logs/s1_failover_monitor.log
HEARTBEAT="$DATA/mac_live_heartbeat.json"
ARM_FLAG="$DATA/S1_FAILOVER_ARMED"
STALE_THRESHOLD_S=300
DOWN_DEBOUNCE_CYCLES=3
CRYPTO_ACCOUNTS="ang inf flz men fin"
STOCK_ACCOUNTS="trb trc"
mkdir -p "$DATA"
ts(){ date -u +%FT%TZ; }
log(){ echo "[$(ts)] $1" >> "$LOG"; }

is_market_open_et(){
    # America/New_York 9:30-16:00 Mon-Fri, computed independently on S1 (never trust a
    # possibly-stale heartbeat field for this — Mac could die exactly at a market boundary).
    TZ=America/New_York /home/niels/.conda/envs/binance_env/bin/python -c "
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo
now = datetime.now(ZoneInfo('America/New_York'))
import sys
sys.exit(0 if (now.weekday() < 5 and dt_time(9,30) <= now.time() <= dt_time(16,0)) else 1)
"
}

now_epoch=$(date +%s)
heartbeat_fresh=0
if [ -f "$HEARTBEAT" ]; then
    hb_epoch=$(jq -r '.epoch // 0' "$HEARTBEAT" 2>/dev/null | cut -d. -f1)
    hb_epoch=${hb_epoch:-0}
    age=$(( now_epoch - hb_epoch ))
    if [ "$age" -lt "$STALE_THRESHOLD_S" ]; then heartbeat_fresh=1; fi
else
    age=999999
fi

mac_alive_for(){ # mac_alive_for <acct> -> 0 (alive) or 1 (down/unknown)
    local acct="$1"
    if [ "$heartbeat_fresh" != "1" ]; then return 1; fi
    local v
    v=$(jq -r --arg a "$acct" '.accounts[$a]' "$HEARTBEAT" 2>/dev/null)
    [ "$v" = "true" ] && return 0 || return 1
}

process_account(){ # process_account <mode:crypto|stocks> <acct> <should_be_live:0|1>
    local mode="$1" acct="$2" should_be_live="$3"
    local marker="$DATA/s1_failover_active_${mode}_${acct}.flag"
    local downcount_file="$DATA/s1_failover_downcount_${mode}_${acct}"
    if mac_alive_for "$acct"; then
        rm -f "$downcount_file"
        if [ -f "$marker" ]; then
            log "RECOVERY: Mac confirmed alive for $mode:$acct — standing down S1 failover"
            bash "$BASE/tools/s1_failover_stop.sh" "$mode" "$acct" >> "$LOG" 2>&1
            rm -f "$marker"
        fi
        return
    fi
    # Mac is down (or unknown) for this account.
    if [ "$should_be_live" != "1" ]; then
        # Market closed (stocks) — nothing should be live either way; clean up any marker.
        rm -f "$downcount_file"
        [ -f "$marker" ] && { bash "$BASE/tools/s1_failover_stop.sh" "$mode" "$acct" >> "$LOG" 2>&1; rm -f "$marker"; }
        return
    fi
    if [ -f "$marker" ]; then
        return  # already failed over, nothing new to do
    fi
    local downcount
    downcount=$(cat "$downcount_file" 2>/dev/null || echo 0)
    downcount=$((downcount + 1))
    echo "$downcount" > "$downcount_file"
    if [ "$downcount" -lt "$DOWN_DEBOUNCE_CYCLES" ]; then
        log "WATCH: Mac heartbeat stale/missing (age=${age}s) or reports $mode:$acct down (cycle $downcount/$DOWN_DEBOUNCE_CYCLES, not yet acting)."
        return
    fi
    log "ALERT: Mac heartbeat stale/missing (age=${age}s, threshold=${STALE_THRESHOLD_S}s) or reports $mode:$acct down while it should be live ($downcount consecutive cycles)."
    if [ -f "$ARM_FLAG" ]; then
        log "ARMED — S1 taking over $mode:$acct"
        bash "$BASE/tools/s1_failover_start.sh" "$mode" "$acct" >> "$LOG" 2>&1
        touch "$marker"
    else
        log "DISARMED (no $ARM_FLAG) — NOT starting $mode:$acct on S1. touch $ARM_FLAG to arm."
    fi
}

for acct in $CRYPTO_ACCOUNTS; do
    process_account crypto "$acct" 1
done

if is_market_open_et; then
    stocks_should_be_live=1
else
    stocks_should_be_live=0
fi
for acct in $STOCK_ACCOUNTS; do
    process_account stocks "$acct" "$stocks_should_be_live"
done
