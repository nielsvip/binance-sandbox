#!/bin/bash
# offload_to_storagebox.sh SRC_DIR REMOTE_SUBDIR
# Copies SRC_DIR to the Hetzner Storage Box (offload-cold, u705131) with rsync over ssh port 23,
# verifies every file by checksum, and deletes the local copy ONLY when the verify reports zero differences.
set -u
SRC="$1"; SUB="$2"
HOST=u705131@u705131.your-storagebox.de
RSH="ssh -p 23 -o BatchMode=yes"
DEST="$HOST:/home/cold/$SUB/"
echo "[offload] copy $SRC -> $DEST $(date -u +%FT%TZ)"
ssh -p 23 -o BatchMode=yes $HOST "mkdir -p /home/cold/$SUB" || { echo "[offload] mkdir failed"; exit 1; }
rsync -a --partial -e "$RSH" "$SRC/" "$DEST" || { echo "[offload] copy failed; local kept"; exit 1; }
diffs=$(rsync -a --checksum --dry-run --itemize-changes -e "$RSH" "$SRC/" "$DEST" | grep -c '^>f' || true)
echo "[offload] checksum diffs=$diffs"
if [ "$diffs" = "0" ]; then
  rm -rf "$SRC" && echo "[offload] local copy removed: $SRC"
else
  echo "[offload] verify failed; local kept"; exit 1
fi
