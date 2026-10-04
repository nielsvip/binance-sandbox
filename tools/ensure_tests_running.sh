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
# === SINGLETON LOCK (2026-10-04): wedged pytest runs piled up every 30min.
_L=/tmp/ensure_tests_running.lockdir
if ! mkdir "$_L" 2>/dev/null; then
  _o_pid=$(cat "$_L/pid" 2>/dev/null)
  _dead=0; { [ -z "$_o_pid" ] || ! kill -0 "$_o_pid" 2>/dev/null; } && _dead=1
  _ver=0; [ -n "$_o_pid" ] && ps -o command= -p "$_o_pid" 2>/dev/null | grep -q "ensure_tests_running" && _ver=1
  if [ "$_dead" = 1 ] || { [ $(( $(date +%s) - $(stat -f %m "$_L" 2>/dev/null || echo 0) )) -gt 1200 ] && [ "$_ver" = 1 ]; }; then
    if [ "$_ver" = 1 ]; then
      if [ "$(ps -o pgid= -p "$_o_pid" 2>/dev/null | tr -d ' ')" = "$_o_pid" ]; then kill -9 -"$_o_pid" 2>/dev/null; else kill -9 "$_o_pid" 2>/dev/null; fi
      sleep 2
    fi
    rmdir "$_L" 2>/dev/null; mkdir "$_L" 2>/dev/null || exit 0
  else echo "[$STAMP] another run in flight, exiting" >> "$LOG"; exit 0; fi
fi
echo $$ > "$_L/pid"
trap 'rmdir "$_L" 2>/dev/null' EXIT INT TERM
echo "[$STAMP] ensure_tests_running: pytest -q (quick parity + per_sym)" | tee -a "$LOG"
cd "$ROOT"
# quick subset that covers parity — full suite is heavy (30s), keep this light (<10s)
$PY -m pytest tests/test_per_sym_parity.py tests/test_live_gate_and.py tests/test_ez_positions_quick_parity_fix.py -q >> "$LOG" 2>&1 || echo "[$STAMP] pytest had failures (see log)" | tee -a "$LOG"
# also run the honest switch parity guard (light, ~5s)
$PY tools/verify_switch_parity.py >> "$LOG" 2>&1 || echo "[$STAMP] verify_switch_parity had failures" | tee -a "$LOG"
echo "[$STAMP] done" | tee -a "$LOG"
# keep log under 5M
/usr/bin/tail -c 5000000 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG" 2>/dev/null || true
