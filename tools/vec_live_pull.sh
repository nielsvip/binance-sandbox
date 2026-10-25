#!/bin/bash
# X3 VEC-DRIVEN LIVE (2026-10-06): pull S1 vec executor output -> Mac data/vec_live/ for the ez_manage consumer.
#   S1 ~/binance-sandbox/data/vec_live/intents/*.json  -> Mac data/vec_live/intents/
#   S1 ~/binance-sandbox/data/vec_live/state/*.json    -> Mac data/vec_live/state/
#   S1 ~/binance-sandbox/data/vec_live/vec_driven.json -> only with VEC_LIVE_PULL_REGISTRY=1 (default: the registry, its expires_utc and
#                                                the KILL file are Mac-local authority and are NEVER pulled or overwritten).
# Single instance via lockf (macOS has no flock). One pass per run; --loop N repeats every N seconds (for a KeepAlive agent).
# Intents arrive via a temp dir + rename so the consumer never reads a half-written json.
set -u
BASE="${BASE_PATH:-/Users/niels/Documents/binance}"
HOST="${VEC_LIVE_S1_HOST:-s1-int}"
REMOTE="${VEC_LIVE_S1_DIR:-binance-sandbox/data/vec_live}"  # X1 tools/vec_live_executor.py writes ~/binance-sandbox/data/vec_live on S1 (verified 2026-10-06)
DEST="$BASE/data/vec_live"
LOG="$BASE/logs/vec_live_pull.log"
LOCK="$DEST/.pull.lock"
mkdir -p "$DEST/intents" "$DEST/state" "$BASE/logs"
if [ "${VEC_LIVE_PULL_LOCKED:-0}" != "1" ]; then
  export VEC_LIVE_PULL_LOCKED=1
  exec /usr/bin/lockf -s -t 0 "$LOCK" "$0" "$@"
fi
SSH="ssh -o BatchMode=yes -o ConnectTimeout=8 -o ServerAliveInterval=5"
# USER 2026-10-25 per-cat_side shards: one lane host per shard (S1 crypto_short, S2 crypto_long,
# S5 stock_long, S6 stock_short). VEC_LIVE_PULL_HOSTS="h1:dir1 h2:dir2 ..." pulls every shard into
# the same dirs (merge rsync, never --delete: disjoint shards, disjoint sym_sides). Default = today's
# single S1 source, byte-identical behavior. Registry stays Mac-local unless VEC_LIVE_PULL_REGISTRY=1.
pull_one() {
  local host="$1" remote="$2" ts rc
  ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  rsync -az --timeout=15 -e "$SSH" --partial-dir=.rsync-partial --delay-updates --include='*.json' --exclude='*' "$host:$remote/intents/" "$DEST/intents/" >>"$LOG" 2>&1
  rc=$?
  rsync -az --timeout=15 -e "$SSH" --partial-dir=.rsync-partial --delay-updates --include='*.json' --exclude='*' "$host:$remote/state/" "$DEST/state/" >>"$LOG" 2>&1
  rc=$((rc + $?))
  if [ "${VEC_LIVE_PULL_REGISTRY:-0}" = "1" ]; then
    rsync -az --timeout=15 -e "$SSH" "$host:$remote/vec_driven.json" "$DEST/vec_driven.json" >>"$LOG" 2>&1
    rc=$((rc + $?))
  fi
  if [ $rc -ne 0 ]; then
    echo "$ts PULL_FAIL rc=$rc host=$host" >>"$LOG"
    return 1
  fi
  return 0
}
pull_once() {
  local spec ok=0 fail=0
  for spec in ${VEC_LIVE_PULL_HOSTS:-"$HOST:$REMOTE"}; do
    if pull_one "${spec%%:*}" "${spec#*:}"; then ok=$((ok + 1)); else fail=$((fail + 1)); fi
  done
  if [ $fail -eq 0 ]; then date -u +%s >"$DEST/.last_pull_ok"; fi
}
if [ "${1:-}" = "--loop" ]; then
  every="${2:-20}"
  while true; do pull_once; sleep "$every"; done
else
  pull_once
fi
