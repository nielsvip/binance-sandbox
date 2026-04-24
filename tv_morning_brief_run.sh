#!/bin/bash
# Runs at 13:25 UTC (9:25 AM ET) weekdays via launchd.
# Reads tradier_indicators_latest.json directly — no CDP, no Claude tokens.
# Writes data/tv_morning_brief.json for the newsletter at 13:35 UTC.

cd /Users/niels/Documents/binance || exit 1

LOGFILE="/tmp/tv_morning_brief.log"
PYTHON="/opt/anaconda3/envs/binance_env/bin/python"

echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') — tv_morning_brief starting" >> "$LOGFILE"

"$PYTHON" /Users/niels/Documents/binance/tv_morning_brief.py >> "$LOGFILE" 2>&1

EXIT_CODE=$?
echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') — tv_morning_brief done (exit $EXIT_CODE)" >> "$LOGFILE"
exit $EXIT_CODE
