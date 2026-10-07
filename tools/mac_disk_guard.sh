#!/bin/bash
# mac_disk_guard — keep Mac boot disk off ENOSPC (a full disk stalls v15sync,
# backlogs results, and fakes "new arrivals"). Every 15 min via cron (# V15_DISKGUARD):
# if Avail <2GB, clean REGENERABLE caches only (conda tarballs, pip cache) and log.
# Never touches workspace, backups, data, or live code. Installed 2026-10-04.
set -u
LOG="/tmp/mac_disk_guard.log"
AVAIL_KB=$(df -k / 2>/dev/null | tail -n 1 | awk '{print $4}')
[ -z "$AVAIL_KB" ] && exit 0
if [ "$AVAIL_KB" -lt 2097152 ]; then
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] LOW DISK ${AVAIL_KB}KB free — cleaning regenerable caches" >> "$LOG"
  /opt/anaconda3/bin/conda clean --tarballs --yes --quiet >> "$LOG" 2>&1 || true
  /opt/anaconda3/envs/binance_env/bin/pip cache purge >> "$LOG" 2>&1 || true
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] after clean: $(df -h / 2>/dev/null | tail -n 1 | awk '{print $4}') free" >> "$LOG"
fi
exit 0
