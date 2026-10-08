#!/bin/bash
# v15_stalled_host_deleter — Mac side of USER 2026-10-08 "stalled server gets deleted (all data copied)".
# Every 15 min: read data/daily_chain/ALERT_HOST_<host>.txt on S1 (written by tools/v15_results_watchdog.sh after 2 strikes
# = >=4h of zero finished boards with pilots running). For a WORKER only (never niels/S1, never gateway): verify the stalled
# data copy exists on S1 and is non-empty, then `hcloud server delete <host>` and record DELETED_<host>.txt on S1.
# INFRA law: workers are ephemeral images of S1 — re-provision by cloning S1, never from scratch.
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
  if [ -z "$COPY" ] || [ "${SZ:-0}" -lt 100 ]; then log "$H alert present but no verified data copy ($COPY size=${SZ:-0}) -> NOT deleting"; continue; fi
  ID=$(hcloud server list -o noheader -o columns=id,name 2>/dev/null | awk -v h="$H" '$2==h {print $1}')
  if [ -z "$ID" ]; then log "$H no hcloud server by that name -> nothing to delete"; continue; fi
  log "$H: data copy $COPY (${SZ}KB) verified -> hcloud server delete $ID"
  if hcloud server delete "$ID" >> $LOG 2>&1; then
    ssh -o BatchMode=yes $S1 "mv ~/binance-sandbox/data/daily_chain/ALERT_HOST_${H}.txt ~/binance-sandbox/data/daily_chain/DELETED_${H}_$(date -u +%Y%m%d%H%M).txt"
    osascript -e "display notification \"$H deleted after 2 stall strikes; data in S1:$COPY — re-image from S1\" with title \"v15 STALLED HOST DELETED\"" 2>/dev/null
    log "$H DELETED"
  else
    log "$H delete FAILED"
  fi
done
