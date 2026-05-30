#!/bin/bash
# watchdog_persym_s1.sh — keep S1 at the utilization floor (USER 2026-05-30): continuously re-run the parallel
# 4yr baseline + per_sym optimizer over the real universe (tradeable_keys + symbols_trb). Refreshes baselines +
# per_sym configs each cycle so S1 never sits idle. Log to ~/logs, disown, < /dev/null per SWEEP-LIVENESS MANDATE.
set -u
PY=/home/niels/.conda/envs/binance_env/bin/python
SANDBOX=/home/niels/binance-sandbox
LOGS=/home/niels/logs
WORKERS=${1:-12}
cd "$SANDBOX" || exit 1
if pgrep -f "persym_optimize_par.py" >/dev/null; then
  echo "[persym-wd] already running"; exit 0
fi
ts=$(date +%Y%m%d_%H%M%S); log="$LOGS/persym_opt_par_${ts}.log"
echo "[persym-wd] launching parallel optimizer workers=$WORKERS log=$log"
nohup nice -n 5 "$PY" -u tools/persym_optimize_par.py --workers "$WORKERS" > "$log" 2>&1 < /dev/null &
disown
sleep 20
if pgrep -f "persym_optimize_par.py" >/dev/null; then
  echo "[persym-wd] alive: $(pgrep -fc persym_optimize_par.py) procs"; tail -3 "$log"
else
  echo "[persym-wd] FAILED to start"; tail -10 "$log"
fi
