#!/bin/bash
# mac_ext_ingest.sh — Mac side of S1 ext offload. Pulls ~/ext_staging batches
# from S1 to an ext drive (E3T first, WD25 fallback), verifies count+bytes vs
# manifest, then removes the verified batch on S1. Never deletes unverified.
# Cron: */15 * * * * flock -n /tmp/mac_ext_ingest.lock bash tools/mac_ext_ingest.sh
set -u
LOG=/tmp/mac_ext_ingest.log
say() { echo "$(date -u +%FT%TZ) [ingest] $*" >> "$LOG"; }
# USER 2026-10-16: a detached drive leaves its /Volumes/<name> DIRECTORY behind, and `[ -d ]` is true for it —
# so this wrote "backups" onto the BOOT DISK while believing it had an external drive. Measured: /Volumes/WD25 held
# 1.2 GB written 2026-10-14 19:34 on the internal volume (same st_dev as /). A mount, not a directory, is the test.
is_ext_mount() { [ -d "$1" ] || return 1; [ "$(stat -f %d "$1" 2>/dev/null)" != "$(stat -f %d / 2>/dev/null)" ]; }
TARGET=""
# USER 2026-10-16: SSD2T added. E3T is unplugged and /Volumes/WD25 is a PHANTOM — a leftover directory on the boot
# disk (st_dev 16777232 == /), which is_ext_mount correctly refuses — so this list had NO valid target and the
# ingester logged "no MOUNTED ext target" every 15 min while S1's ~/ext_staging held 12 GB of cold archives it
# could not release and S1 filled to 100% (2.5 GB free). SSD2T is a real mount (st_dev 16777237) with 87 GB free.
for d in /Volumes/E3T /Volumes/SSD2T /Volumes/WD25; do
  if is_ext_mount "$d"; then
    FREE_MB=$(df -m "$d" 2>/dev/null | awk 'NR==2{print $4}')
    if [ "${FREE_MB:-0}" -gt 30720 ]; then TARGET="$d"; break; fi
  fi
done
[ -z "$TARGET" ] && { say "no MOUNTED ext target with >30G free — skip (this step deletes the S1 copy once bytes match, so a phantom mountpoint would move data onto the boot disk and drop the only other copy)"; exit 0; }
DEST="$TARGET/s1_offload"
mkdir -p "$DEST" 2>/dev/null || { say "$TARGET not writable — skip"; exit 0; }
BATCHES=$(ssh -S none -o ConnectTimeout=20 -o BatchMode=yes s1-int "ls ~/ext_staging/ 2>/dev/null" | grep -v manifest || true)
[ -z "$BATCHES" ] && exit 0
for b in $BATCHES; do
  [ "$b" = "manifest.json" ] && continue
  say "pull batch $b -> $TARGET"
  rsync -az -e "ssh -S none -o StrictHostKeyChecking=accept-new" "s1-int:ext_staging/$b/" "$DEST/$b/" >> "$LOG" 2>&1 || { say "rsync failed $b"; continue; }
  MANIFEST_BYTES=$(ssh -S none -o ConnectTimeout=20 -o BatchMode=yes s1-int "python3 -c \"import json;print(json.load(open('/home/niels/ext_staging/$b/manifest.json'))['total_bytes'])\"" 2>/dev/null || echo 0)
  GOT_BYTES=$(find "$DEST/$b" -type f -exec stat -f%z {} + 2>/dev/null | awk '{s+=$1} END {print s+0}')
  [ -z "$GOT_BYTES" ] && GOT_BYTES=0
  [ -z "$MANIFEST_BYTES" ] && MANIFEST_BYTES=0
  MANIFEST_N=$(ssh -S none -o ConnectTimeout=20 -o BatchMode=yes s1-int "python3 -c \"import json;print(len(json.load(open('/home/niels/ext_staging/$b/manifest.json'))['files']))\"" 2>/dev/null || echo 0)
  GOT_N=$(ssh -S none -o ConnectTimeout=20 -o BatchMode=yes s1-int "ls /home/niels/ext_staging/$b/ | grep -cv manifest" 2>/dev/null || echo 0)
  LOCAL_N=$(find "$DEST/$b" -mindepth 1 -maxdepth 1 ! -name manifest.json | wc -l | tr -d ' ')
  if [ "$GOT_N" = "$LOCAL_N" ] && [ "$MANIFEST_BYTES" != "0" ] && [ "$GOT_BYTES" -ge "$MANIFEST_BYTES" ]; then
    ssh -S none -o ConnectTimeout=20 -o BatchMode=yes s1-int "rm -rf /home/niels/ext_staging/$b" && say "verified $b (${GOT_N} dirs, ${GOT_BYTES}B) — S1 copy removed"
  else
    say "VERIFY FAIL $b manifest_n=$MANIFEST_N s1_n=$GOT_N local_n=$LOCAL_N bytes=$GOT_BYTES/$MANIFEST_BYTES — kept on S1"
  fi
done
