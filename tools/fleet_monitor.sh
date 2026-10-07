#!/bin/bash
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
TIMEOUT="/opt/homebrew/bin/timeout"
[ -x "$TIMEOUT" ] || TIMEOUT="timeout"
set -u
LOG="/tmp/fleet_monitor.log"
echo "=== $(date -u +%FT%TZ) FLEET_CHECK ===" >> "$LOG"
for h in s1-pub s2 s3 s5; do
  df=$($TIMEOUT 8 ssh -o ConnectTimeout=5 $h "df -h / | awk 'NR==2{print \$5}'" 2>&1 | tail -1 | tr -d ' ')
  pilots=$($TIMEOUT 8 ssh -o ConnectTimeout=5 $h "ps aux | grep v15_pilot | grep -v grep | wc -l" 2>&1 | tail -1 | tr -d ' ')
  herds=$($TIMEOUT 8 ssh -o ConnectTimeout=5 $h "ps aux | grep v15_local_herd | grep -v grep | wc -l" 2>&1 | tail -1 | tr -d ' ')
  xlsx=$($TIMEOUT 8 ssh -o ConnectTimeout=5 $h "find ~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL -name '*.xlsx' 2>/dev/null | wc -l" 2>&1 | tail -1 | tr -d ' ')
  last=$($TIMEOUT 8 ssh -o ConnectTimeout=5 $h "find ~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL -name '*.xlsx' -mmin -15 2>/dev/null | wc -l" 2>&1 | tail -1 | tr -d ' ')
  todo=$($TIMEOUT 8 ssh -o ConnectTimeout=5 $h "grep -o 'todo [0-9]*' /tmp/v15_local_herd.log 2>&1 | tail -1" 2>&1 | tail -1 | tr -d ' ')
  load=$($TIMEOUT 8 ssh -o ConnectTimeout=5 $h "cat /proc/loadavg | awk '{print \$1}'" 2>&1 | tail -1 | tr -d ' ')
  printf "%-6s disk:%-4s pilots:%-2s herds:%-2s xlsx:%-5s recent15m:%-3s load:%-4s %s\n" "$h" "$df" "$pilots" "$herds" "$xlsx" "$last" "$load" "$todo" >> "$LOG"
  if [ "$herds" = "0" ] || [ -z "$herds" ]; then echo "HEAL $h herd dead" >> "$LOG"; $TIMEOUT 10 ssh -o ConnectTimeout=5 $h "nohup /home/niels/binance-sandbox/.venv/bin/python -u /home/niels/binance-sandbox/tools/v15_local_herd.py > /tmp/v15_local_herd.log 2>&1 &" 2>&1 | head -3 >> "$LOG"; fi
done
mac_xlsx=$(find /Users/niels/Documents/binance/SPREADSHEETS/V15_V16_CELL_BY_CELL -name "*.xlsx" 2>/dev/null | wc -l | tr -d ' ')
s1_xlsx=$($TIMEOUT 8 ssh -o ConnectTimeout=5 s1-pub "find ~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL -name '*.xlsx' 2>/dev/null | wc -l" 2>&1 | tail -1 | tr -d ' ')
echo "Mac:$mac_xlsx S1:$s1_xlsx drift:$(( ${s1_xlsx:-0} - ${mac_xlsx:-0} ))" >> "$LOG"
tail -n 500 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG" 2>/dev/null || true
