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
SCOPE="${VEC_LIVE_SCOPE:-30d}"
case "$SCOPE" in
  30d) LIVE_DIR=vec_live ;;
  7d) LIVE_DIR=vec_live_7d ;;
  *) echo "Invalid VEC_LIVE_SCOPE=$SCOPE" >&2; exit 2 ;;
esac
REMOTE="${VEC_LIVE_S1_DIR:-binance-sandbox/data/$LIVE_DIR}"
DEST="$BASE/data/$LIVE_DIR"
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
  local host="$1" remote="$2" ts rc stage shard cache_key hour previous_hour side
  ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  cache_key=$(echo "$host" | tr -c 'A-Za-z0-9._-' '_')
  stage="$DEST/.hostcache/$cache_key"
  case "$host" in
    s1*|*10.0.0.3|157.180.125.52) shard=crypto_short ;;
    s2|*10.0.0.4) shard=crypto_long ;;
    s5|*10.0.0.5) shard=stock_long ;;
    s6|*10.0.0.6) shard=stock_short ;;
    *) echo "$ts UNKNOWN_SHARD host=$host" >>"$LOG"; return 1 ;;
  esac
  if [ "$SCOPE" = "7d" ]; then shard=all; fi
  mkdir -p "$stage/intents" "$stage/state"
  hour=$(date -u +%Y%m%dT%H)
  previous_hour=$(date -u -v-1H +%Y%m%dT%H)
  case "$shard" in *_short) side=SHORT ;; *_long) side=LONG ;; *) side='*' ;; esac
  rsync -az --timeout=60 -e "$SSH" --partial-dir=.rsync-partial --delay-updates --include='/state/' --include="/state/*_${side}.json" --include='/intents/' --include="/intents/${hour}*.json" --include="/intents/${previous_hour}*.json" --exclude='*' "$host:$remote/" "$stage/" >>"$LOG" 2>&1
  rc=$?
  if [ "${VEC_LIVE_PULL_REGISTRY:-0}" = "1" ]; then
    rsync -az --timeout=60 -e "$SSH" "$host:$remote/vec_driven.json" "$stage/vec_driven.json" >>"$LOG" 2>&1
    rc=$((rc + $?))
  fi
  if [ $rc -ne 0 ]; then
    echo "$ts PULL_FAIL rc=$rc host=$host" >>"$LOG"
    return 1
  fi
  "${VEC_PULL_PYTHON:-/opt/anaconda3/envs/binance_env/bin/python}" "$BASE/tools/vec_state_merge.py" "$stage" "$DEST" --shard "$shard" >>"$LOG" 2>&1 || rc=$((rc + $?))
  if [ -f "$stage/vec_driven.json" ]; then mv -f "$stage/vec_driven.json" "$DEST/vec_driven.json" || rc=$((rc + 1)); fi
  if [ $rc -ne 0 ]; then
    echo "$ts PULL_MERGE_FAIL rc=$rc host=$host" >>"$LOG"
    return 1
  fi
  return 0
}
pull_once() {
  local spec host remote pid ok=0 fail=0
  local pids=()
  # 2026-10-25 cutover: S1 crypto_short + S2 crypto_long + S5 stock_long + S6 stock_short.
  for spec in ${VEC_LIVE_PULL_HOSTS:-$HOST:$REMOTE s2:binance-sandbox/data/$LIVE_DIR niels@10.0.0.5:binance-sandbox/data/$LIVE_DIR niels@10.0.0.6:binance-sandbox/data/$LIVE_DIR}; do
    host="${spec%%:*}"
    remote="${spec#*:}"
    pull_one "$host" "$remote" &
    pids+=("$!")
  done
  for pid in "${pids[@]}"; do
    if wait "$pid"; then ok=$((ok + 1)); else fail=$((fail + 1)); fi
  done
  # In the isolated 7D lane, removed stock boxes must not make a healthy S2
  # crypto pull look globally stale. Each intent/state still has its own age.
  if [ "$SCOPE" = "7d" ] && [ "$ok" -gt 0 ] || [ "$fail" -eq 0 ]; then date -u +%s >"$DEST/.last_pull_ok"; fi
}
if [ "${1:-}" = "--loop" ]; then
  every="${2:-20}"
  while true; do pull_once; sleep "$every"; done
else
  pull_once
fi
