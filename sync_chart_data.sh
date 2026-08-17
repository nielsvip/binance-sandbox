#!/usr/bin/env bash
# launchd entry point for Bible §16.22 always-connected synchronization.
# Mac source only moves Mac->S1; S1 evidence only moves S1->Mac. Live config
# is deliberately outside this job and remains receipt/promotion controlled.
set -euo pipefail

BASE=/Users/niels/Documents/binance
PY=/opt/anaconda3/envs/binance_env/bin/python
LOG="$BASE/logs/sync_chart_data.log"
LOCK="$BASE/logs/.sync_chart_data.lock"
mkdir -p "$BASE/logs" "$BASE/data/sync"
exec >>"$LOG" 2>&1

if ! mkdir "$LOCK" 2>/dev/null; then
  echo "$(date -u +%FT%TZ) SKIP prior sync transaction still active"
  exit 0
fi
trap 'rmdir "$LOCK" 2>/dev/null || true' EXIT

# Ensure 24/7 lab daemons survive reboot without new LaunchAgents (sandbox cannot write ~/Library/LaunchAgents)
# This runs every 300s via com.niels.chart-data-sync — KeepAlive for vector + gateway
if ! pgrep -f "vector_lab_streamer.py" >/dev/null 2>&1; then
  echo "$(date -u +%FT%TZ) watchdog: vector_lab_streamer not running — restarting via run_vector_streamer.sh"
  bash "$BASE/tools/run_vector_streamer.sh" >>"$BASE/logs/vector_streamer_launchd.log" 2>&1 || true
fi
if ! pgrep -f "switch_lab_gateway.py" >/dev/null 2>&1; then
  echo "$(date -u +%FT%TZ) watchdog: switch_lab_gateway not running — restarting on 5082"
  nohup /opt/anaconda3/envs/binance_env/bin/python -u "$BASE/tools/switch_lab_gateway.py" >>"$BASE/logs/switch_lab_gateway.log" 2>&1 &
fi
# Chart server is KeepAlive via launchd, but also guard here if launchd stalls
if ! curl -s --max-time 3 http://127.0.0.1:5077/api/switch_lab/status >/dev/null 2>&1; then
  echo "$(date -u +%FT%TZ) watchdog: chart_server not responding"
fi
echo "$(date -u +%FT%TZ) always-connected sync start"
run_started=$(date +%s)
matrix_surface_rc=0
"$BASE/tools/pull_current_matrix_surface_s1.sh" || matrix_surface_rc=$?
if (( matrix_surface_rc == 0 )); then
  echo "$(date -u +%FT%TZ) current matrix surface PASS"
else
  echo "$(date -u +%FT%TZ) current matrix surface SYNC_PENDING rc=$matrix_surface_rc"
fi
helper_rc=0
"$BASE/tools/always_connected_sync.sh" || helper_rc=$?
if "$PY" - "$BASE/data/sync/ALWAYS_CONNECTED_SYNC_STATUS.json" "$run_started" <<'PY'
import json
import sys

path, started = sys.argv[1], float(sys.argv[2])
try:
    payload = json.load(open(path, encoding="utf-8"))
    completed = float(
        payload.get("completed_at_epoch", payload.get("verified_at_epoch", 0))
    )
except (OSError, ValueError, TypeError, json.JSONDecodeError):
    raise SystemExit(1)
raise SystemExit(
    0
    if payload.get("status") == "PASS" and completed >= started
    else 1
)
PY
then
  echo "$(date -u +%FT%TZ) always-connected sync PASS"
else
  echo "$(date -u +%FT%TZ) always-connected sync SYNC_PENDING helper_rc=$helper_rc"
fi
