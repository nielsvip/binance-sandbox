#!/bin/bash
# watchdog_lab_matrix.sh — keep the 24/7/365 lab-matrix fillers alive on S1 (USER 2026-07-21:
# "should NEVER stop unless it is replaced by a more advanced system"). Cron: */10.
# Workers: 2x stocks + 2x crypto (vectorized numpy, modest RAM — respects S1 utilization floor
# without competing with Tier-2 sweeps for the big allocations).
SBX=/home/niels/binance-sandbox
PY=/home/niels/.conda/envs/binance_env/bin/python
LOGDIR=/home/niels/logs
mkdir -p "$LOGDIR"
launch() {
  mode=$1; tag=$2
  if ! pgrep -f "lab_matrix_daemon.py --mode $mode --workers-tag $tag" >/dev/null; then
    cd "$SBX" && nohup "$PY" tools/lab_matrix_daemon.py --mode "$mode" --workers-tag "$tag" \
      >> "$LOGDIR/lab_matrix_${mode}_${tag}.log" 2>&1 < /dev/null &
    disown
    echo "$(date -u +%FT%TZ) relaunched lab_matrix $mode $tag" >> "$LOGDIR/lab_matrix_watchdog.log"
  fi
}
launch stocks w1
launch stocks w2
launch crypto w1
launch crypto w2
# hourly export (cheap) — only from the w1 slot minute window to avoid dupes
if [ "$(date +%M)" -lt 10 ]; then
  cd "$SBX" && timeout 300 "$PY" tools/export_lab_matrix_db.py >> "$LOGDIR/lab_matrix_export.log" 2>&1
fi
