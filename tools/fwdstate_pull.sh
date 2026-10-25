#!/bin/bash
# fwdstate_pull.sh — Mac pulls S1 forward open/closed states every minute.
# USER 2026-10-25: execute_now VEC backstop enforces these same-instant; a silent
# pull stall blinds enforcement. Heartbeat every run + STALL alert past 180 s.
set -u
ROOT=/Users/niels/Documents/binance
DEST=$ROOT/data/forward_state
LOG=/tmp/fwdstate_pull.log
NOW=$(date -u +%FT%TZ)
E=$SECONDS
rsync -az -u --timeout=50 -e "ssh -o BatchMode=yes -o ConnectTimeout=10" niels@157.180.125.52:binance-sandbox/data/forward_state/ "$DEST/" >>"$LOG" 2>&1
RC=$?
NEWEST=$(ls -t "$DEST" 2>/dev/null | head -n 1)
AGE="?"; [ -n "$NEWEST" ] && AGE=$(( $(date +%s) - $(stat -f %m "$DEST/$NEWEST" 2>/dev/null || echo 0) ))
echo "[$NOW] rc=$RC newest_age_s=$AGE elapsed_s=$((SECONDS-E))" >>"$LOG"
if [ "$AGE" != "?" ] && [ "$AGE" -gt 180 ]; then
  echo "[$NOW] STALL forward_state newest $AGE s old" >>"$LOG"
fi
