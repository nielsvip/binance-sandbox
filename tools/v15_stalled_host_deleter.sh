#!/bin/bash
# v15_stalled_host_deleter — Mac stall ALERTER. USER 2026-10-13 RULING: the watchdog can NOT delete a server,
# only the user can. This script NEVER deletes anything: on ALERT_HOST_<host>.txt it verifies the stalled data
# copy on S1 and raises a loud alert (log + notification) for the user to decide. Deletion is a manual user act.
# Every 10 min via Mac cron V15_STALLED_HOST_DELETER (kept as alerter; comment updated same ruling).
set -u
LOG=/tmp/v15_stalled_host_deleter.log
log(){ echo "[$(date -u +%FT%TZ)] [deleter] $*" >> $LOG; }
export PATH=/Users/niels/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin
S1=${V15_S1:-s1-int}
# REBOOT alerts (strike 1 where ssh reboot failed): hcloud reboot, never S1/gateway
for A in $(ssh -o BatchMode=yes -o ConnectTimeout=10 $S1 'ls ~/binance-sandbox/data/daily_chain/ALERT_REBOOT_*.txt 2>/dev/null' 2>/dev/null); do
  H=$(basename "$A" .txt | sed 's/ALERT_REBOOT_//'); case "$H" in s1|niels|gateway|"") continue;; esac
  ID=$(hcloud server list -o noheader -o columns=id,name 2>/dev/null | awk -v h="$H" '$2==h {print $1}')
  [ -n "$ID" ] && hcloud server reboot "$ID" >> $LOG 2>&1 && log "$H REBOOTED via hcloud (strike 1)" && ssh -o BatchMode=yes $S1 "rm -f $A"
done
ALERTS=$(ssh -o BatchMode=yes -o ConnectTimeout=10 $S1 'ls ~/binance-sandbox/data/daily_chain/ALERT_HOST_*.txt 2>/dev/null' 2>/dev/null)
[ -z "$ALERTS" ] && exit 0
for A in $ALERTS; do
  H=$(basename "$A" .txt | sed 's/ALERT_HOST_//')
  case "$H" in s1|niels|gateway|"") log "refusing $H (never deleted)"; continue;; esac
  COPY=$(ssh -o BatchMode=yes $S1 "ls -d /home/niels/stalled_${H}_* 2>/dev/null | tail -1"); SZ=$(ssh -o BatchMode=yes $S1 "du -s ${COPY:-/nonexistent} 2>/dev/null | cut -f1")
  if [ -z "$COPY" ] || [ "${SZ:-0}" -lt 100 ]; then log "$H alert present but no verified data copy ($COPY size=${SZ:-0}) -> alert stands, no action"; continue; fi
  log "$H: data copy $COPY (${SZ}KB) verified -> STALL ALERT (no-delete ruling 2026-10-13; user decides)"
  osascript -e "display notification \"$H stalled 2+ strikes, data in S1:$COPY — delete ONLY by user order\" with title \"v15 STALLED HOST ALERT\"" 2>/dev/null
  log "$H ALERTED, left running for user decision (hcloud delete disabled)"
done
