#!/bin/bash
# start_tradier_sweeps.sh — canonical launcher for S2 (tradier-only sweeps).
# Mirror of start_crypto_sweeps.sh — refuses crypto mode, logs to ~/logs/, disowns,
# verifies alive after launch.
#
# Usage:
#   bash start_tradier_sweeps.sh entry_gates    # launch a specific tier
#   bash start_tradier_sweeps.sh status          # show current sweep liveness
#   bash start_tradier_sweeps.sh kill_crypto     # kill any wrong-mode crypto procs

set -u
PY=/home/niels/miniconda3/envs/binance_env/bin/python
SANDBOX=/home/niels/binance-sandbox
LOGS=/home/niels/logs
mkdir -p "$LOGS"

KILL_CRYPTO() {
  local pids
  pids=$(pgrep -f -- "--mode crypto" || true)
  if [ -n "$pids" ]; then
    echo "[TRADIER] killing wrong-mode crypto processes: $pids"
    kill $pids 2>/dev/null || true
    sleep 2
  fi
}

STATUS() {
  echo "=== S2 tradier sweep liveness ==="
  local n
  n=$(pgrep -afc "v8_quick_sweep.*--mode tradier|autonomous_search.*--mode tradier|v8_test_queue.*--mode tradier" || echo 0)
  echo "Tradier sweep processes: $n"
  if [ "$n" -gt 0 ]; then
    pgrep -af "v8_quick_sweep.*--mode tradier|autonomous_search.*--mode tradier|v8_test_queue.*--mode tradier" | head -8
  fi
  local bad
  bad=$(pgrep -afc -- "--mode crypto" || echo 0)
  echo "WRONG-MODE crypto processes (must be 0): $bad"
  if [ "$bad" -gt 0 ]; then
    pgrep -af -- "--mode crypto" | head -5
  fi
  echo "--- newest tradier sweep CSV ---"
  ls -lt "$SANDBOX"/data/sweep_results/v8_quick_tradier_*.csv 2>/dev/null | head -1
}

LAUNCH_TIER() {
  local tier=$1
  KILL_CRYPTO
  # Concurrency cap: only count v8_quick_sweep (autonomous_search is managed by
  # canonical_tradier_100sym.sh supervisor independently — do not count it here).
  local _running
  _running=$(pgrep -fc "v8_quick_sweep.*--mode tradier" 2>/dev/null | head -1 || echo 0)
  _running=${_running:-0}
  if [ "$_running" -ge 2 ]; then
    echo "[TRADIER] CONCURRENCY_CAP: $_running v8_quick_sweep workers already running (cap=2). Refusing new launch — kill existing first."
    pgrep -af "v8_quick_sweep.*--mode tradier" | head -5
    exit 1
  fi
  local log="$LOGS/sweep_tradier_${tier}_$(date +%Y%m%d_%H%M%S).log"
  echo "[TRADIER] launching tier=$tier  log=$log  (nice=0 high-priority per user directive 2026-05-01)"
  cd "$SANDBOX" || exit 1
  nohup nice -n 0 "$PY" -u v8_quick_sweep.py --mode tradier --symbols all --start 2024-01-01 \
      --tier "$tier" --workers 3 --stream --shuffle \
      --kill-secs 99999 --kill-sharpe 0 --min-csv-sharpe 0.0 \
      > "$log" 2>&1 < /dev/null &
  disown
  local pid=$!
  echo "[TRADIER] launched pid=$pid"
  sleep 30
  if pgrep -af "v8_quick_sweep.*--mode tradier.*$tier" >/dev/null; then
    local nproc
    nproc=$(pgrep -afc "v8_quick_sweep.*--mode tradier")
    echo "[TRADIER] T+30s: $nproc processes alive ✓"
    tail -5 "$log"
  else
    echo "[TRADIER] FAILURE — sweep died within 30s. Log:"
    tail -30 "$log"
    exit 1
  fi
}

case "${1:-status}" in
  status) STATUS ;;
  kill_crypto) KILL_CRYPTO ; STATUS ;;
  *) LAUNCH_TIER "$1" ;;
esac
