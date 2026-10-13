#!/bin/bash
# 2026-10-13 (BIBLE §94): the S1 daily chain is NEVER paused — every process carrying V15_DAILY_CHAIN=1 in its environment
# (exported by tools/v15_daily_chain_s1.sh, inherited by all its children) is skipped by the STOP and resumed if stopped.
is_chain() { tr '\0' '\n' < /proc/$1/environ 2>/dev/null | grep -qx 'V15_DAILY_CHAIN=1'; }
stop_pat() { for p in $(pgrep -f "$1" 2>/dev/null); do if is_chain "$p"; then kill -CONT "$p" 2>/dev/null; echo "[$(date -u +%FT%TZ)] EXEMPT chain pid=$p ($1) never stopped" >> /home/niels/logs/oom_guard.log; else kill -STOP "$p" 2>/dev/null; fi; done; }
LOAD=$(awk "{print \$1}" /proc/loadavg 2>&1 | head -n1)
MEM_AVAIL=$(awk "/MemAvailable/{print int(\$2/1024)}" /proc/meminfo 2>&1 | head -n1)
if [ -z "$MEM_AVAIL" ]; then MEM_AVAIL=$(free -m 2>&1 | awk "/Mem:/{print \$7}" | head -n1); fi
# throttle if load > 12 or mem < 2048 - BUT NEVER KILL dc_simple sweeps (protected)
IS_HIGH=$(python3 -c "import sys; print(1 if float(sys.argv[1])>12 else 0)" "$LOAD" 2>&1 | head -n1)
if [ "$IS_HIGH" = "1" ] || [ "$MEM_AVAIL" -lt 2048 ]; then
  stop_pat baseline_backtest; stop_pat sweep_every
  # dc_simple is PROTECTED - never STOP, OOM-monitored separately via V12_NPZ_CACHE limit
  echo "[$(date -u +%FT%TZ)] THROTTLE load=$LOAD mem=${MEM_AVAIL}M STOP hogs (dc_simple PROTECTED)" >> /home/niels/logs/oom_guard.log
else
  pkill -CONT -f baseline_backtest 2>&1 | true; pkill -CONT -f sweep_every 2>&1 | true
  echo "[$(date -u +%FT%TZ)] OK load=$LOAD mem=${MEM_AVAIL}M RESUME hogs" >> /home/niels/logs/oom_guard.log
fi
# OOM monitor: log dc_simple RAM every 2min, ensure no OOM kill
for pid in $(pgrep -f dc_simple 2>/dev/null); do
  RSS=$(ps -o rss= -p $pid 2>/dev/null | awk '{print int($1/1024)}')
  echo "[$(date -u +%FT%TZ)] OOM_MON dc_simple pid=$pid rss=${RSS}M mem_avail=${MEM_AVAIL}M" >> /home/niels/logs/oom_guard.log
done
# resume any chain process that something else stopped (state T)
for p in $(ps -eo pid=,stat= 2>/dev/null | awk '$2 ~ /^T/ {print $1}'); do is_chain "$p" && kill -CONT "$p" 2>/dev/null && echo "[$(date -u +%FT%TZ)] RESUMED stopped chain pid=$p" >> /home/niels/logs/oom_guard.log; done
