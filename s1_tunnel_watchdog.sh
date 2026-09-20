#!/bin/bash
# s1 tunnel watchdog - alerts when NO S1 PATHS HEALTHY flaps in ssh_tunnels.log
# Runs via launchd every 5m, posts to night_watch_guardian.log and logger
LOG_SRC="/Users/niels/Documents/binance/logs/ssh_tunnels.log"
ALERT_LOG="/Users/niels/Documents/binance/logs/s1_tunnel_watchdog.log"
STATE_FILE="/tmp/s1_tunnel_watchdog.state"
THRESH=3  # 3 flaps in last hour triggers alert
WINDOW_SEC=3600

count=$(grep -c "NO S1 PATHS HEALTHY" "$LOG_SRC" 2>/dev/null || echo 0)
# check only last hour
recent=$(grep "NO S1 PATHS HEALTHY" "$LOG_SRC" 2>/dev/null | tail -n 20 | wc -l)
if [ "$recent" -ge "$THRESH" ]; then
  last=$(cat "$STATE_FILE" 2>/dev/null || echo 0)
  now=$(date +%s)
  if [ $((now - last)) -gt 1800 ]; then
    msg="[$(date -u +%Y-%m-%dT%H:%M:%SZ)] s1_tunnel_watchdog ALERT: $recent NO S1 PATHS HEALTHY in last hour (total $count) — gateway 157.90.168.35 flapping, check s1-ping-watchdog + Hetzner console"
    echo "$msg" | tee -a "$ALERT_LOG" | logger -t s1_tunnel_watchdog 2>/dev/null || true
    # also append to night_watch for visibility
    echo "$msg" >> /Users/niels/Documents/binance/logs/night_watch_guardian.log 2>/dev/null || true
    echo "$now" > "$STATE_FILE"
  fi
else
  # healthy - log once per day
  if [ ! -f "$STATE_FILE.healthy" ] || [ $(($(date +%s) - $(cat "$STATE_FILE.healthy" 2>/dev/null || echo 0))) -gt 86400 ]; then
    date +%s > "$STATE_FILE.healthy"
  fi
fi
