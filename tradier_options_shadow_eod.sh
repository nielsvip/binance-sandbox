#!/bin/bash
# End-of-day wrapper: runs the shadow comparator + scorer + mutator + suggestions.
# launchd fires this once at 20:05 UTC Mon-Fri.
#
# Manual test:
#   ./tradier_options_shadow_eod.sh
#   ./tradier_options_shadow_eod.sh --date 20260425   (replay an older day)

set -u
BASE="/Users/niels/Documents/binance"
PY="/opt/anaconda3/envs/binance_env/bin/python"
LOG_DIR="$BASE/data/options_supervisor"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/shadow_eod_$(date -u +%Y%m%d).log"
TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)

cd "$BASE" || exit 1
echo "[$TS] shadow EOD start  args=$*" >> "$LOG"
"$PY" tradier_options_shadow_compare.py "$@" >> "$LOG" 2>&1
RC=$?
echo "[$TS] shadow EOD done rc=$RC" >> "$LOG"
exit 0
