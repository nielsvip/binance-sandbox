#!/bin/bash
# LIVE FAILOVER S1 manual override (2026-10-13). Run on S1, or from the Mac:
#   ssh s1-pub /home/niels/binance/tools/live_failover_ctl.sh <cmd>
# status     lease + per-venue data-failover state + last alerts
# force-s1   pin the lease to S1: S1 takes over and starts its traders as soon as the Mac has
#            acknowledged the fence (its agent SIGTERMs every Mac trader) or is silent >=120 s
# force-mac  one-shot failback: stop S1 traders, verify none left, lease -> Mac, then auto again
# pin-mac    hold the lease on the Mac (disables automatic S1 takeover) until `auto`
# auto       clear any pin (automatic failover/failback rules apply)
# arm|disarm enable/disable the automatic FULL trading takeover (data failover is always on)
# data-test <crypto|stocks> print the current outbox manifest for a venue
set -u
BIN=/home/niels/binance
LF=$BIN/data/live_failover
PY=/home/niels/.conda/envs/binance_env_mac/bin/python
WD="$PY $BIN/tools/live_failover_s1.py"
log(){ echo "[$(date -u +%FT%TZ)] [CTL] $1" >> /home/niels/logs/live_failover.log; }
case "${1:-status}" in
  status)
    $WD --status | head -120
    echo "--- last alerts"; tail -n 8 "$LF/ALERTS.jsonl" 2>/dev/null
    echo "--- units"; for u in live-failover-watchdog live-failover-crypto-producers live-failover-stocks-producers live-failover-trading; do echo "$u: $(systemctl is-active $u)"; done ;;
  force-s1) log "force-s1 by $(whoami)"; $WD --pin s1 ;;
  force-mac) log "force-mac (one-shot) by $(whoami)"; $WD --pin mac_once ;;
  pin-mac) log "pin-mac by $(whoami)"; $WD --pin mac ;;
  auto) log "auto by $(whoami)"; $WD --pin auto ;;
  arm) touch "$LF/TRADING_FAILOVER_ENABLED"; log "ARMED full trading failover"; echo armed ;;
  disarm) rm -f "$LF/TRADING_FAILOVER_ENABLED"; log "DISARMED full trading failover"; echo disarmed ;;
  data-test) cat "$LF/outbox/${2:-crypto}/MANIFEST.json" 2>/dev/null | head -40 || echo "no outbox (venue not ACTIVE)" ;;
  *) sed -n '2,13p' "$0"; exit 1 ;;
esac
