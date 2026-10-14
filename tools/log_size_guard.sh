#!/bin/bash
# 2026-10-14: Mac disk filled (ENOSPC) -> live heartbeat stalled -> trading lease failed over to S1. Logs >500 MB are cut to their last 50 MB.
LIM=$((500*1024*1024)); KEEP=$((50*1024*1024))
for d in /Users/niels/logs /Users/niels/Documents/binance/logs; do
  find "$d" -maxdepth 1 -type f -name "*.log*" -size +500M 2>/dev/null | while read -r f; do
    sz=$(stat -f %z "$f"); tail -c $KEEP "$f" > "$f.guardtail" && cat "$f.guardtail" > "$f" && rm -f "$f.guardtail"
    echo "$(date -u +%FT%TZ) cut $f from $sz bytes" >> /Users/niels/logs/log_size_guard.log
  done
done
free=$(df -k /System/Volumes/Data | awk 'NR==2{print $4}')
[ "$free" -lt 10485760 ] && echo "$(date -u +%FT%TZ) LOW_DISK free_kb=$free" >> /Users/niels/logs/log_size_guard.log
exit 0
