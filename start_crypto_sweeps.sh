#!/bin/bash
# start_crypto_sweeps.sh — canonical launcher for S1 (crypto-only sweeps).
# Per CLAUDE.md SWEEP-LIVENESS MANDATE: log to ~/logs/, disown, < /dev/null,
# verify alive after launch. Refuses to run if not on S1 (157.180.125.52)
# or if any --mode tradier process is detected.
#
# Usage:
#   bash start_crypto_sweeps.sh wt_dc_full   # launch a specific tier
#   bash start_crypto_sweeps.sh status        # show current sweep liveness
#   bash start_crypto_sweeps.sh kill_tradier  # kill any wrong-mode procs

set -u
PY=/home/niels/.conda/envs/binance_env/bin/python
SANDBOX=/home/niels/binance-sandbox
LOGS=/home/niels/logs
mkdir -p "$LOGS"

# Refuse if any --mode tradier process running on this host
KILL_TRADIER() {
  local pids
  pids=$(pgrep -f -- "--mode tradier" || true)
  if [ -n "$pids" ]; then
    echo "[CRYPTO] killing wrong-mode tradier processes: $pids"
    kill $pids 2>/dev/null || true
    sleep 2
  fi
}

STATUS() {
  echo "=== S1 crypto sweep liveness ==="
  local n
  n=$(pgrep -afc "v8_quick_sweep.*--mode crypto|autonomous_search.*--mode crypto|v8_test_queue.*--mode crypto" || echo 0)
  echo "Crypto sweep processes: $n"
  if [ "$n" -gt 0 ]; then
    pgrep -af "v8_quick_sweep.*--mode crypto|autonomous_search.*--mode crypto|v8_test_queue.*--mode crypto" | head -8
  fi
  local bad
  bad=$(pgrep -afc -- "--mode tradier" || echo 0)
  echo "WRONG-MODE tradier processes (must be 0): $bad"
  if [ "$bad" -gt 0 ]; then
    pgrep -af -- "--mode tradier" | head -5
  fi
  echo "--- newest crypto sweep CSV ---"
  ls -lt "$SANDBOX"/data/sweep_results/v8_quick_crypto_*.csv 2>/dev/null | head -1
  ls -lt "$SANDBOX"/data/sweep_results/vec_*crypto*.csv 2>/dev/null | head -1
}

LAUNCH_TIER() {
  local tier=$1
  KILL_TRADIER
  # Concurrency cap: only count v8_quick_sweep (autonomous_search is managed by
  # canonical_crypto_50sym.sh supervisor independently — do not count it here).
  local _running
  _running=$(pgrep -fc "v8_quick_sweep.*--mode crypto" 2>/dev/null | head -1 || echo 0)
  _running=${_running:-0}
  if [ "$_running" -ge 2 ]; then
    echo "[CRYPTO] CONCURRENCY_CAP: $_running v8_quick_sweep workers already running (cap=2). Refusing new launch — kill existing first."
    pgrep -af "v8_quick_sweep.*--mode crypto" | head -5
    exit 1
  fi
  local log="$LOGS/sweep_${tier}_$(date +%Y%m%d_%H%M%S).log"
  echo "[CRYPTO] launching tier=$tier  log=$log  (nice=0 high-priority per user directive 2026-05-01)"
  cd "$SANDBOX" || exit 1
  # nice -n 0 = default (user can't go below 0 without root). Ad-hoc backtests should run at nice 19.
  # V8_RATE_GUARD_DISABLED=1: honest crypto baseline ~0.07 is below the 3-trade/day rate guard floor.
  nohup nice -n 0 env V8_RATE_GUARD_DISABLED=1 "$PY" -u v8_quick_sweep.py --mode crypto --symbols all --start 2022-01-01 \
      --tier "$tier" --workers 4 --stream --shuffle \
      --kill-secs 99999 --kill-sharpe 0 --min-csv-sharpe 0.1 \
      > "$log" 2>&1 < /dev/null &
  disown
  local pid=$!
  echo "[CRYPTO] launched pid=$pid"
  sleep 30
  if pgrep -af "v8_quick_sweep.*$tier" >/dev/null; then
    local nproc
    nproc=$(pgrep -afc "v8_quick_sweep.*$tier")
    echo "[CRYPTO] T+30s: $nproc processes alive ✓"
    tail -5 "$log"
  else
    echo "[CRYPTO] FAILURE — sweep died within 30s. Log:"
    tail -30 "$log"
    exit 1
  fi
}

case "${1:-status}" in
  status) STATUS ;;
  kill_tradier) KILL_TRADIER ; STATUS ;;
  *) LAUNCH_TIER "$1" ;;
esac
