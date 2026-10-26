#!/bin/bash
# Pull into host-specific staging, then publish only SQL/engine/bar-verified states.
set -u
ROOT=/Users/niels/Documents/binance
INBOX="$ROOT/data/forward_state_inbox"
LOG=/tmp/fwdstate_pull.log
mkdir -p "$INBOX" "$ROOT/data/forward_state"

pull_one() {
  local label="$1" host="$2" dest="$INBOX/$1"
  mkdir -p "$dest"
  rsync -az -u --timeout=25 -e "ssh -S none -o BatchMode=yes -o ConnectTimeout=8 -J gateway-internal" "niels@$host:binance-sandbox/data/forward_state/" "$dest/" >>"$LOG" 2>&1
  echo "[$(date -u +%FT%TZ)] $label pull_rc=$?" >>"$LOG"
}

pull_one s1 10.0.0.3 & p1=$!
pull_one s2 10.0.0.4 & p2=$!
pull_one s5 10.0.0.5 & p3=$!
pull_one s6 10.0.0.6 & p4=$!
wait "$p1"; wait "$p2"; wait "$p3"; wait "$p4"
/usr/bin/python3 "$ROOT/tools/fwdstate_verified_publish.py" >>"$LOG" 2>&1
