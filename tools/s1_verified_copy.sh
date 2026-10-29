#!/bin/bash
# s1_verified_copy.sh <remote path under ~/binance-sandbox> <dest dir on a mounted ext drive>
# Copies a tree from S1 through the Mac, then verifies every file's md5 on both ends. NEVER deletes anything.
# Exit 0 only when every remote file exists locally with an identical md5.
set -u
SRC="$1"; DEST="$2"
mkdir -p "$DEST" || exit 2
RS_OK=0
for attempt in $(seq 1 25); do
  rsync -a --partial --append-verify --timeout=300 -e "ssh -o ConnectTimeout=20 -o ServerAliveInterval=15 -o ServerAliveCountMax=4" "s1:binance-sandbox/$SRC" "$DEST/" && { RS_OK=1; break; }
  echo "rsync attempt $attempt failed for $SRC; retrying in 30s"; sleep 30
done
[ "$RS_OK" = 1 ] || { echo "RSYNC FAILED after retries $SRC"; exit 3; }
TOP=$(basename "$SRC")
if [ -f "$DEST/$TOP" ] && [ ! -d "$DEST/$TOP" ]; then
  RH=$(ssh -o ConnectTimeout=20 s1 "md5sum \$HOME/binance-sandbox/$SRC" | awk '{print $1}')
  LH=$(md5 -q "$DEST/$TOP")
  echo "verify single file $TOP remote=$RH local=$LH"
  [ -n "$RH" ] && [ "$RH" = "$LH" ] || { echo "SINGLE FILE MISMATCH $SRC"; exit 5; }
  echo "copied+verified $SRC -> $DEST rc=0 $(date -u +%FT%TZ)"; exit 0
else
  if [ -d "$DEST/$TOP" ]; then LOCAL="$DEST/$TOP"; REMOTE_CD="binance-sandbox/$SRC"; else LOCAL="$DEST"; REMOTE_CD="binance-sandbox/$(dirname "$SRC")"; fi
  ssh -o ConnectTimeout=20 s1 "cd \$HOME/$REMOTE_CD && find . -type f -print0 | xargs -0 md5sum" > "$DEST/.verify_remote_$TOP.md5" || { echo "REMOTE MD5 FAILED $SRC"; exit 4; }
  ( cd "$LOCAL" && find . -type f -print0 | xargs -0 md5 -r ) | sed -E 's/^([0-9a-f]+) \*?(.*)$/\1  \2/' > "$DEST/.verify_local_$TOP.md5"
fi
python3 -I - "$DEST/.verify_remote_$TOP.md5" "$DEST/.verify_local_$TOP.md5" <<'PY'
import sys
def load(p):
    d = {}
    for line in open(p, errors="replace"):
        parts = line.rstrip("\n").split(None, 1)
        if len(parts) == 2:
            h, f = parts
            d[f.lstrip("*").lstrip("./")] = h
    return d
r = load(sys.argv[1]); l = load(sys.argv[2])
miss = [f for f in r if f not in l]
bad = [f for f in r if f in l and r[f] != l[f]]
print(f"verify files={len(r)} missing={len(miss)} mismatch={len(bad)}")
for f in (miss + bad)[:20]:
    print("  ", "MISSING" if f in miss else "MISMATCH", f)
sys.exit(0 if (not miss and not bad and len(r) > 0) else 5)
PY
rc=$?
echo "copied+verified $SRC -> $DEST rc=$rc $(date -u +%FT%TZ)"
exit $rc
