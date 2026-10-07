#!/bin/bash
# Hourly live-trading log triage wrapper — invoked by com.niels.log-triage-hourly.plist
# Runs claude CLI with the triage prompt, appends output to /Users/niels/logs/hourly_triage.log
# Created 2026-05-21 22:00 UTC.

set -uo pipefail
cd /Users/niels/Documents/binance

PROMPT_FILE="/Users/niels/Documents/binance/scripts/hourly_log_triage_prompt.txt"
OUT_LOG="/Users/niels/logs/hourly_triage.log"
CLAUDE="/Users/niels/.local/bin/claude"

if [ ! -x "$CLAUDE" ]; then
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] ERROR: $CLAUDE not executable" >> "$OUT_LOG"
  exit 1
fi
if [ ! -f "$PROMPT_FILE" ]; then
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] ERROR: prompt file missing: $PROMPT_FILE" >> "$OUT_LOG"
  exit 1
fi

{
  echo ""
  echo "════════════════════════════════════════════════════════════════════"
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] hourly_log_triage starting"
  echo "════════════════════════════════════════════════════════════════════"
} >> "$OUT_LOG"

"$CLAUDE" -p \
  --model opus \
  --output-format text \
  --dangerously-skip-permissions \
  --no-session-persistence \
  "$(cat "$PROMPT_FILE")" \
  >> "$OUT_LOG" 2>&1

RC=$?

{
  echo ""
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] hourly_log_triage finished (exit=$RC)"
} >> "$OUT_LOG"

exit $RC
