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
# s1-int (gateway tunnel 127.0.0.1:2201) first, s1-pub direct as fallback.
RC=255
for H in "s1-int:binance-sandbox" "157.180.125.52:binance-sandbox"; do
  rsync -az -u --timeout=40 -e "ssh -o BatchMode=yes -o ConnectTimeout=8" "niels@$H/data/forward_state/" "$DEST/" >>"$LOG" 2>&1
  RC=$?
  [ "$RC" = "0" ] && break
done
# S5 (stocks; disjoint universe from S1 — always attempt, never fallback-gated).
# USER 2026-10-25: S5 computes per-sym_side stock STATE (250 promoted incl. SHORTs,
# which exist nowhere else); Mac must carry them for Monday open.
rsync -az -u --timeout=60 -e "ssh -J niels@157.90.168.35 -o BatchMode=yes -o ConnectTimeout=10" "niels@10.0.0.5:binance-sandbox/data/forward_state/" "$DEST/" >>"$LOG" 2>&1
RC5=$?
echo "[$NOW] s5_rc=$RC5" >>"$LOG"
NEWEST=$(ls -t "$DEST" 2>/dev/null | head -n 1)
AGE="?"; [ -n "$NEWEST" ] && AGE=$(( $(date +%s) - $(stat -f %m "$DEST/$NEWEST" 2>/dev/null || echo 0) ))
echo "[$NOW] rc=$RC newest_age_s=$AGE elapsed_s=$((SECONDS-E))" >>"$LOG"
# STALL = a full 15m state cycle missed (writes land in quarter bursts by
# design; newest_age < 120 s most minutes, never steadily < 60 s).
if [ "$AGE" != "?" ] && [ "$AGE" -gt 900 ]; then
  echo "[$NOW] STALL forward_state newest $AGE s old" >>"$LOG"
fi
