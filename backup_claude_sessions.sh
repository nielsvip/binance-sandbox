#!/bin/bash
# backup_claude_sessions.sh — Backup all Claude JSONL sessions locally
# Runs via cron every 30 min. Keeps last 3 snapshots to save disk.
# Source JSONL files in ~/.claude/ can be lost on crash — this preserves them.

set -euo pipefail

# CONCURRENCY LOCK (2026-07-29): cron runs this every */5 min (the header's "30 min" is
# stale), but a full snapshot of the .claude session JSONLs plus export_conversations.py
# takes far longer than 5 minutes under load. With no lock the instances stacked: 28 live
# copies (oldest 2h21m) and 56 rsync children thrashing local disk, which helped drive the
# Mac to load average 322 / swap 22.5G-of-23.5G and starved the live tradier trb/trc
# position-sync loop dead for 2h04m. Overlapping runs are also unsafe on their own: each
# instance prunes to KEEP=3 while another is still hardlinking against a snapshot it is
# deleting. One instance at a time, and never longer than the cron period.
LOCKDIR="/tmp/backup_claude_sessions.lock"
if ! mkdir "$LOCKDIR" 2>/dev/null; then
    _pid=$(cat "$LOCKDIR/pid" 2>/dev/null || true)
    if [ -n "$_pid" ] && kill -0 "$_pid" 2>/dev/null; then
        echo "[$(date -u +%FT%TZ)] backup_claude_sessions SKIPPED — already running (pid $_pid)"
        exit 0
    fi
    rm -rf "$LOCKDIR" 2>/dev/null || true
    mkdir "$LOCKDIR" 2>/dev/null || { echo "[$(date -u +%FT%TZ)] backup_claude_sessions SKIPPED — cannot acquire lock"; exit 0; }
fi
echo "$$" > "$LOCKDIR/pid"
# Children are killed first: bash defers a TERM trap while a foreground child is hung, so
# killing the hung rsync/python is what actually unblocks the parent and lets the trap fire.
# The kill -9 backstop skips the EXIT trap, which is why the stale-lock reclaim above exists.
MAX_RUNTIME=240
( sleep "$MAX_RUNTIME"; pkill -TERM -P "$$" 2>/dev/null; sleep 10; pkill -KILL -P "$$" 2>/dev/null; sleep 2; kill -9 "$$" 2>/dev/null ) &
WATCHDOG_PID=$!
trap 'kill "$WATCHDOG_PID" 2>/dev/null; rm -rf "$LOCKDIR" 2>/dev/null' EXIT INT TERM
SRC="$HOME/.claude/projects/-Users-niels-Documents-binance"
BACKUP_DIR="/Users/niels/Documents/binance/backups/claude_sessions"
TIMESTAMP=$(date -u +%Y%m%d_%H%M)
SNAP="$BACKUP_DIR/snap_$TIMESTAMP"
KEEP=3  # number of snapshots to retain

# Only run if source exists
if [ ! -d "$SRC" ]; then
    echo "Source dir not found: $SRC"
    exit 1
fi

# rsync to new snapshot (hardlink to previous to save space)
LATEST=$(find "$BACKUP_DIR" -maxdepth 1 -name "snap_*" -type d 2>/dev/null | sort | tail -1)
if [ -n "$LATEST" ] && [ -d "$LATEST" ]; then
    rsync -a --link-dest="$LATEST" "$SRC/" "$SNAP/"
else
    rsync -a "$SRC/" "$SNAP/"
fi

# Count what we backed up
COUNT=$(find "$SNAP" -name "*.jsonl" 2>/dev/null | wc -l | tr -d ' ')
SIZE=$(du -sh "$SNAP" | cut -f1)
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) Backed up $COUNT sessions ($SIZE) to $SNAP"

# Also regenerate the markdown exports
cd /Users/niels/Documents/binance
/opt/anaconda3/envs/binance_env/bin/python export_conversations.py 2>/dev/null

# Prune old snapshots, keep last $KEEP
SNAPS=($(find "$BACKUP_DIR" -maxdepth 1 -name "snap_*" -type d 2>/dev/null | sort))
TOTAL=${#SNAPS[@]}
if [ "$TOTAL" -gt "$KEEP" ]; then
    DELETE=$((TOTAL - KEEP))
    for ((i=0; i<DELETE; i++)); do
        rm -rf "${SNAPS[$i]}"
        echo "Pruned old snapshot: ${SNAPS[$i]}"
    done
fi
