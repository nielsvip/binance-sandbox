#!/bin/bash
# ensure_tests_running.sh — keep pytest running via cron, not a daemon (cron is the keeper).
# Called every 30min via crontab; runs a quick pytest -q and logs.
# If pytest not scheduled, daily_parity_fix will flag it.
set -euo pipefail
ROOT="/Users/niels/Documents/binance"
PY="/opt/anaconda3/envs/binance_env/bin/python"
if [ ! -x "$PY" ]; then PY="/usr/bin/python3"; fi
LOG="/tmp/tests_keep_running.log"
STAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
echo "[$STAMP] ensure_tests_running: pytest -q (quick parity + per_sym)" | tee -a "$LOG"
cd "$ROOT"
# quick subset that covers parity — full suite is heavy (30s), keep this light (<10s)
$PY -m pytest tests/test_per_sym_parity.py tests/test_live_gate_and.py tests/test_ez_positions_quick_parity_fix.py -q >> "$LOG" 2>&1 || echo "[$STAMP] pytest had failures (see log)" | tee -a "$LOG"
# also run the honest switch parity guard (light, ~5s)
$PY tools/verify_switch_parity.py >> "$LOG" 2>&1 || echo "[$STAMP] verify_switch_parity had failures" | tee -a "$LOG"
echo "[$STAMP] done" | tee -a "$LOG"
# keep log under 5M
/usr/bin/tail -c 5000000 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG" 2>/dev/null || true
