#!/bin/bash
# Hourly log triage with Muse — invoked by com.niels.log-triage-hourly.plist
# Calls Muse (free tier) instead of Claude for AI-powered log analysis + fixing recommendations.
# Muse handles the 5-agent parallel analysis; reports to /Users/niels/logs/hourly_triage.log
# Created 2026-10-07, replaces claude-based version to save Claude tokens.

set -uo pipefail
cd /Users/niels/Documents/binance

PROMPT_FILE="/Users/niels/Documents/binance/scripts/hourly_log_triage_prompt.txt"
OUT_LOG="/Users/niels/logs/hourly_triage.log"
MUSE=$(which muse 2>/dev/null)

# Fallback: if Muse not available, use the pure Python analyzer
if [ -z "$MUSE" ]; then
  /opt/anaconda3/envs/binance_env/bin/python /Users/niels/Documents/binance/scripts/hourly_log_triage_pure.py >> "$OUT_LOG" 2>&1
  exit $?
fi

{
  echo ""
  echo "════════════════════════════════════════════════════════════════════"
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] hourly_log_triage starting (MUSE-POWERED)"
  echo "════════════════════════════════════════════════════════════════════"
} >> "$OUT_LOG"

"$MUSE" --no-session-persistence --output-format text "$(cat "$PROMPT_FILE")" >> "$OUT_LOG" 2>&1

RC=$?

{
  echo ""
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] hourly_log_triage finished (exit=$RC)"
} >> "$OUT_LOG"

exit $RC
