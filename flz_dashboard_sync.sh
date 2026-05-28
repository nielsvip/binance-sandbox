#!/bin/bash
# Hourly rsync of S1+S2 autonomous_*_winners.jsonl files to MB local mirror.
# Triggered by launchd / cron every 1h. Triggers dashboard refresh after.
set -e
LOG=/tmp/flz_dashboard_sync.log
TS=$(date -u "+%Y-%m-%dT%H:%M:%SZ")
echo "[$TS] sync start" >> "$LOG"

S1_MIRROR=/Users/niels/Documents/binance/data/autonomous/_s1
S2_MIRROR=/Users/niels/Documents/binance/data/autonomous/_s2
mkdir -p "$S1_MIRROR" "$S2_MIRROR"

# S1 — crypto autonomous winners
rsync -az --include="*/" --include="autonomous_*_winners.jsonl" --exclude="*" \
  -e "ssh -o ConnectTimeout=15 -o BatchMode=yes" \
  s1-int:/home/niels/binance-sandbox/data/autonomous/ "$S1_MIRROR/" \
  >> "$LOG" 2>&1 || echo "[$TS] s1 sync FAILED" >> "$LOG"

# 2026-05-28 S2 DEAD permanently — s2-int autonomous-winners pull removed

# Trigger dashboard cache refresh (best-effort)
curl -sS -X POST -m 5 http://127.0.0.1:5057/api/refresh >> "$LOG" 2>&1 || true

N1=$(find "$S1_MIRROR" -name "*.jsonl" 2>/dev/null | wc -l | tr -d ' ')
echo "[$TS] sync done. S1=$N1 files" >> "$LOG"
