#!/bin/bash
# ext_to_wd25_verified.sh <source mount> <folder> — copy one folder to WD25 with checksums, then re-check. NEVER deletes.
set -u
SRCV="$1"; F="$2"; DEST=/Volumes/WD25/archive_from_$(basename "$SRCV")
mkdir -p "$DEST" || exit 2
rsync -a --checksum --partial --progress "$SRCV/$F" "$DEST/" || { echo "COPY FAILED $F"; exit 3; }
# second pass: must transfer nothing and report no differences
OUT=$(rsync -a --checksum --dry-run --itemize-changes "$SRCV/$F" "$DEST/" | grep -v '^sending\|^sent\|^total\|^$' | head -5)
if [ -n "$OUT" ]; then echo "VERIFY DIFFERENCES $F:"; echo "$OUT"; exit 4; fi
echo "verified $F -> $DEST $(date -u +%FT%TZ)"
