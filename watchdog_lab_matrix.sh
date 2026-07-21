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
# MEGA SWEEP split (USER 2026-07-21): lab masks = cheap idea screen (2+2 workers),
# param_matrix_daemon = REAL manifest params via Tier-2 engine (4 workers, the priority).
launch stocks w1
launch stocks w2
launch crypto w1
launch crypto w2
launch_pmx() {
  tag=$1
  if ! pgrep -f "param_matrix_daemon.py --tag $tag" >/dev/null; then
    cd "$SBX" && PSC_CAMPAIGN=stocks_baseline_v2_s4h nohup "$PY" tools/param_matrix_daemon.py --tag "$tag" \
      >> "$LOGDIR/param_matrix_${tag}.log" 2>&1 < /dev/null &
    disown
    echo "$(date -u +%FT%TZ) relaunched param_matrix $tag" >> "$LOGDIR/lab_matrix_watchdog.log"
  fi
}
launch_pmx w1
launch_pmx w2
launch_pmx w3
launch_pmx w4
# hourly export + central-DB mirror (cheap) — only from the first cron slot of the hour
if [ "$(date +%M)" -lt 10 ]; then
  cd "$SBX" && timeout 300 "$PY" tools/export_lab_matrix_db.py >> "$LOGDIR/lab_matrix_export.log" 2>&1
  cd "$SBX" && timeout 600 "$PY" tools/ingest_lab_matrix_to_central.py >> "$LOGDIR/lab_matrix_ingest.log" 2>&1
  cd "$SBX" && timeout 600 "$PY" tools/export_mega_matrix.py >> "$LOGDIR/mega_matrix_export.log" 2>&1
  cd "$SBX" && timeout 300 "$PY" tools/param_keep_drop_report.py >> "$LOGDIR/param_keep_drop.log" 2>&1
fi
