#!/bin/bash
# v15_history_archive — USER 2026-10-14: "hold on to it infinitely (at least on ext drive) for stats and development and self learning".
# Append-only archive of S1's per_sym_history (data/hourly_reconfig/per_sym_store.db) + every board manifest on an external drive.
#   per_sym_history/<UTCdate>_<from>-<to>.jsonl.gz   new rows since the last archived id (all columns; LAST_ID advances only after verify)
#   db_snapshots/per_sym_store_<YYYYMM>.db.gz        one full DB snapshot per month (sqlite online backup on S1, md5-verified)
#   manifests/                                        rsync of all board manifests (never --delete)
# Nothing on the drive is ever deleted or overwritten. Drive missing -> exit 2 (the next run catches up: incremental by id).
DEST=${V15_HISTORY_ARCHIVE_DIR:-/Volumes/E3T/binance_history}
S1=${V15_S1:-s1-pub}
ROOT=/Users/niels/Documents/binance
SSH="ssh -o BatchMode=yes -o ConnectTimeout=15 -o ControlMaster=no -o ControlPath=none"
log() { echo "[$(date -u +%FT%TZ)] [history-archive] $*"; }
[ -d "$(dirname "$DEST")" ] && [ -w "$(dirname "$DEST")" ] || { log "FAILED: drive $(dirname "$DEST") not mounted/writable"; exit 2; }
mkdir -p "$DEST/per_sym_history" "$DEST/db_snapshots" "$DEST/manifests" || exit 2
LAST=$(cat "$DEST/per_sym_history/LAST_ID" 2>/dev/null || echo 0)
D=$(date -u +%Y%m%d)
TMP="$DEST/per_sym_history/.incoming_$D.jsonl.gz"
# 1. incremental rows (id > LAST)
$SSH "$S1" "cd ~/binance-sandbox && python3 -c \"
import sqlite3, json, sys
c = sqlite3.connect('file:data/hourly_reconfig/per_sym_store.db?mode=ro', uri=True)
c.row_factory = sqlite3.Row
for r in c.execute('SELECT * FROM per_sym_history WHERE id > ? ORDER BY id', ($LAST,)):
    sys.stdout.write(json.dumps(dict(r), default=str) + chr(10))
\" | gzip -c" > "$TMP" || { rm -f "$TMP"; log "FAILED: export from $S1"; exit 3; }
gzip -t "$TMP" || { rm -f "$TMP"; log "FAILED: gzip test"; exit 3; }
N=$(gzip -dc "$TMP" | wc -l | tr -d ' ')
EXP=$($SSH "$S1" "cd ~/binance-sandbox && python3 -c \"import sqlite3; c=sqlite3.connect('file:data/hourly_reconfig/per_sym_store.db?mode=ro', uri=True); print(c.execute('SELECT COUNT(*) FROM per_sym_history WHERE id > $LAST').fetchone()[0])\"")
if [ "$N" -gt 0 ]; then
  [ "$N" -ge "$EXP" ] || { rm -f "$TMP"; log "FAILED: exported $N rows < expected $EXP"; exit 3; }
  TO=$(gzip -dc "$TMP" | tail -1 | python3 -c "import json,sys; print(json.loads(sys.stdin.read())['id'])")
  OUT="$DEST/per_sym_history/${D}_$((LAST + 1))-${TO}.jsonl.gz"
  [ -e "$OUT" ] && OUT="${OUT%.jsonl.gz}_$(date -u +%H%M%S).jsonl.gz"
  mv "$TMP" "$OUT" && echo "$TO" > "$DEST/per_sym_history/LAST_ID"
  log "rows $((LAST + 1))..$TO ($N) -> $OUT"
else
  rm -f "$TMP"
  log "no new rows (LAST_ID $LAST)"
fi
# 2. monthly full snapshot
M=$(date -u +%Y%m)
SNAP="$DEST/db_snapshots/per_sym_store_$M.db.gz"
if [ ! -s "$SNAP" ]; then
  $SSH "$S1" "cd ~/binance-sandbox && python3 -c \"import sqlite3; s=sqlite3.connect('file:data/hourly_reconfig/per_sym_store.db?mode=ro', uri=True); d=sqlite3.connect('/tmp/psh_archive_$M.db'); s.backup(d); d.close()\" && gzip -c /tmp/psh_archive_$M.db > /tmp/psh_archive_$M.db.gz && rm -f /tmp/psh_archive_$M.db && md5sum /tmp/psh_archive_$M.db.gz" > "$DEST/db_snapshots/.md5_$M" || { log "FAILED: snapshot on $S1"; exit 4; }
  rsync -a -e "$SSH" "$S1:/tmp/psh_archive_$M.db.gz" "$SNAP.part" && [ "$(md5 -q "$SNAP.part")" = "$(cut -d' ' -f1 "$DEST/db_snapshots/.md5_$M")" ] && mv "$SNAP.part" "$SNAP" && $SSH "$S1" "rm -f /tmp/psh_archive_$M.db.gz" \
    && log "monthly snapshot -> $SNAP ($(du -h "$SNAP" | cut -f1))" || { rm -f "$SNAP.part"; log "FAILED: snapshot copy/verify"; exit 4; }
fi
# 3. manifests (append-only)
rsync -a "$ROOT/SPREADSHEETS/V15_V16_CELL_BY_CELL/"*_matrix_manifest.json "$DEST/manifests/" 2>/dev/null
rsync -a -e "$SSH" "$S1:binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL*/*_matrix_manifest.json" "$DEST/manifests/" 2>/dev/null
log "manifests on drive: $(ls "$DEST/manifests" | wc -l | tr -d ' ') | LAST_ID $(cat "$DEST/per_sym_history/LAST_ID" 2>/dev/null)"
