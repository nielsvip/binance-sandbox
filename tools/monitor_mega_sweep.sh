#!/bin/bash
# monitor_mega_sweep.sh — Monitor s2-mega-ccx33 (62.238.125.113) for hangs/0.00/dead ends, pull SPREADSHEETS every 60s
set -e
LOG="/tmp/monitor_mega_sweep.log"
MEGA="niels@62.238.125.113"
S1="niels@127.0.0.1"
SSH_MEGA="ssh -i /Users/niels/.ssh/id_ed25519 -o StrictHostKeyChecking=no -o BatchMode=yes -o ConnectTimeout=5"
SSH_S1="ssh -4 -i /Users/niels/.ssh/id_ed25519 -o StrictHostKeyChecking=no -o BatchMode=yes -p 2201"
MAC_SPREAD="/Users/niels/Documents/binance/SPREADSHEETS"

mkdir -p "$MAC_SPREAD" /tmp

# NEW-ONLY 2026-10-06 (USER: ONLY new files may sync S1/MEGA->Mac, never ancient):
# -u on every pull + remote find -mmin recency gate (V15_SYNC_MAX_AGE_MIN,
# default 4320 = 72h) feeding --files-from. ssh failure yields an empty list
# (fail-closed: that pull transfers nothing). --checksum kept: integrity intent
# (same-mtime different-content still syncs), but never older-over-newer.
MAX_AGE_MIN="${V15_SYNC_MAX_AGE_MIN:-4320}"
LIST_MEGA="$(mktemp /tmp/mega_recent.XXXXXX)"
LIST_S1M="$(mktemp /tmp/mega_s1_recent.XXXXXX)"
trap 'rm -f "$LIST_MEGA" "$LIST_S1M"' EXIT
mega_recent() {
  local host="$1" list="$2"
  : > "$list"
  if [ "$host" = "$MEGA" ]; then
    ssh -i /Users/niels/.ssh/id_ed25519 -o StrictHostKeyChecking=no -o BatchMode=yes -o ConnectTimeout=5 "$host" "find /home/niels/binance-sandbox/SPREADSHEETS -maxdepth 1 -type f -mmin -$MAX_AGE_MIN -printf '%f\n'" >> "$list" 2>/dev/null || true
  else
    ssh -4 -i /Users/niels/.ssh/id_ed25519 -o StrictHostKeyChecking=no -o BatchMode=yes -p 2201 -o ConnectTimeout=5 "$host" "find /home/niels/binance-sandbox/SPREADSHEETS -maxdepth 1 -type f -mmin -$MAX_AGE_MIN -printf '%f\n'" >> "$list" 2>/dev/null || true
  fi
  return 0
}

check() {
  local ts=$(date -u +%FT%TZ)
  # 1. Pull SPREADSHEETS from mega and S1 every minute (TEMPLATE* excluded - Mac is source of truth, never S1/MEGA->Mac)
  mega_recent "$MEGA" "$LIST_MEGA"
  mega_recent "$S1" "$LIST_S1M"
  rsync -auvz --checksum --files-from="$LIST_MEGA" --exclude='*TEMPLATE*' --exclude='*_20*.xlsx' --exclude='*pilot*.xlsx' --include='*.xlsx' --exclude='*' -e "ssh -i /Users/niels/.ssh/id_ed25519 -o StrictHostKeyChecking=no -o BatchMode=yes" "$MEGA:/home/niels/binance-sandbox/SPREADSHEETS/" "$MAC_SPREAD/" >>"$LOG" 2>&1 || echo "$ts MEGA pull failed" >>"$LOG"
  rsync -auvz --checksum --files-from="$LIST_MEGA" --exclude='*TEMPLATE*' --include='*COMPLETE_chart.html' --exclude='*' -e "ssh -i /Users/niels/.ssh/id_ed25519 -o StrictHostKeyChecking=no -o BatchMode=yes" "$MEGA:/home/niels/binance-sandbox/SPREADSHEETS/" "$MAC_SPREAD/" >>"$LOG" 2>&1 || true
  rsync -auvz --checksum --files-from="$LIST_S1M" --exclude='*TEMPLATE*' --exclude='*_20*.xlsx' --exclude='*pilot*.xlsx' --include='*.xlsx' --exclude='*' -e "ssh -4 -i /Users/niels/.ssh/id_ed25519 -o StrictHostKeyChecking=no -o BatchMode=yes -p 2201" "$S1:/home/niels/binance-sandbox/SPREADSHEETS/" "$MAC_SPREAD/" >>"$LOG" 2>&1 || true

  # 2. Check mega activity
  local ps_out=$($SSH_MEGA "$MEGA" "ps aux | grep -E 'python.*v12|python.*aapl|python.*crypto|python.*sweep' | grep -v grep | head -n 20" 2>&1 | head -n 20)
  local running=$(echo "$ps_out" | grep -c "python" || true)
  echo "$ts MEGA running: $running python jobs" >>"$LOG"
  echo "$ps_out" >>"$LOG"

  # 3. Check logs for 0.00 results / hangs / no deltas
  local mega_log=$($SSH_MEGA "$MEGA" "tail -n 100 /tmp/crypto_30d.log /tmp/aapl_redesign_vector.log 2>&1 | head -n 100" 2>&1 | tail -n 100)
  local zero_count=$(echo "$mega_log" | grep -c "gain.*0\.00\|delta.*0\.00" || true)
  local hang_check=$($SSH_MEGA "$MEGA" "find /tmp -name '*.log' -mmin +10 2>&1 | head -n 10" 2>&1 | head -n 10)
  echo "$ts zero_count=$zero_count hang_files=$hang_check" >>"$LOG"

  # 4. Check deltas / positive baselines in latest xlsx Delta column
  local xlsx_count=$(ls "$MAC_SPREAD"/*.xlsx 2>&1 | wc -l)
  echo "$ts Mac xlsx: $xlsx_count" >>"$LOG"

  # 5. Dead end detection: no deltas produced in last 5 minutes AND no positive new baselines
  # Simple heuristic: if last 5 pulls had same xlsx count and zero_count high, warn
  local last_counts=$(tail -n 5 "$LOG" 2>&1 | grep "Mac xlsx:" | awk '{print $NF}' | tr '\n' ' ')
  local unique_counts=$(echo "$last_counts" | tr ' ' '\n' | sort -u | wc -l)
  if [ "$unique_counts" -eq 1 ] && [ "$running" -eq 0 ]; then
    echo "$ts DEAD END: no new xlsx, no python running" >>"$LOG"
    # Warning + 5min wait + backup + delete
    echo "WARNING: s2-mega dead end - no deltas, no baselines, no activity. Backing up and deleting in 5min unless you intervene here." | tee -a "$LOG"
    # Backup via hcloud snapshot
    hcloud server create-image --type snapshot --description "mega-deadend-backup-$(date +%Y%m%d%H%M)" s2-mega-ccx33 >>"$LOG" 2>&1 || echo "backup failed" >>"$LOG"
    sleep 300
    # Check if still dead after 5min
    local ps2=$($SSH_MEGA "$MEGA" "ps aux | grep python | grep -v grep | wc -l" 2>&1 | tail -n 1)
    if [ "$ps2" -eq 0 ]; then
      echo "$ts DEAD END CONFIRMED after 5min wait - deleting s2-mega" >>"$LOG"
      hcloud server delete s2-mega-ccx33 >>"$LOG" 2>&1 || echo "delete failed" >>"$LOG"
    else
      echo "$ts Activity resumed during 5min wait - not deleting" >>"$LOG"
    fi
  fi

  # Also check for 0.00 spam
  if [ "$zero_count" -gt 20 ]; then
    echo "$ts WARNING: many 0.00 results ($zero_count) - possible hang or bad template" >>"$LOG"
  fi
}

# Loop every 60s if run as daemon, else single check
if [ "$1" = "--loop" ]; then
  while true; do check; sleep 60; done
else
  check
fi
