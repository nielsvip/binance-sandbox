#!/bin/bash
# Mac mesh watchdog (USER 2026-10-30): every tunnel port must answer with the expected host, 24/7.
# Restarts the tunnel supervisor when its log stops moving (hang) or any port stays dead.
LOG=/Users/niels/Documents/binance/logs/mesh_watchdog.log
SUP_LOG=/Users/niels/Documents/binance/logs/tunnel_supervisor.log
ts(){ date '+%Y-%m-%d %H:%M:%S'; }
now=$(date +%s); mt=$(stat -f%m "$SUP_LOG" 2>/dev/null || echo 0)
stale=0; [ $((now-mt)) -gt 300 ] && stale=1
dead=""
for pair in 2201:niels 2202:gateway 2203:niels 2204:s2 2205:s5 2206:s6; do
  p=${pair%%:*}; e=${pair##*:}
  got=$(timeout 25 ssh -p $p -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=accept-new -S none niels@127.0.0.1 hostname 2>/dev/null | tail -1)
  [ "$got" = "$e" ] || dead="$dead $p"
done
echo "$(ts) stale_log=$stale dead_ports=[${dead# }]" >> "$LOG"
STATE=/tmp/mesh_watchdog.fails
fails=$(cat "$STATE" 2>/dev/null || echo 0)
if [ "$stale" = 1 ] || [ -n "$dead" ]; then fails=$((fails+1)); else fails=0; fi
echo "$fails" > "$STATE"
if [ "$stale" = 1 ] || [ "$fails" -ge 3 ]; then
  launchctl kickstart -k gui/$(id -u)/com.niels.tunnel_supervisor
  echo "$(ts) restarted tunnel_supervisor (fails=$fails stale=$stale)" >> "$LOG"
  echo 0 > "$STATE"
fi
