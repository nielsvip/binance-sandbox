#!/bin/bash
# backup_claude_sessions.sh — Backup all Claude JSONL sessions locally
# Runs via cron every 30 min. Keeps last 3 snapshots to save disk.
# Source JSONL files in ~/.claude/ can be lost on crash — this preserves them.

set -euo pipefail

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
