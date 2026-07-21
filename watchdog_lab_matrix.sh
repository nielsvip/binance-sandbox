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
  tag=$1; first=$2
  if ! pgrep -f "param_matrix_daemon.py --tag $tag" >/dev/null; then
    cd "$SBX" && PSC_CAMPAIGN=stocks_baseline_v2_s4h nohup "$PY" tools/param_matrix_daemon.py --tag "$tag" --first "$first" --all-tiers \
      >> "$LOGDIR/param_matrix_${tag}.log" 2>&1 < /dev/null &
    disown
    echo "$(date -u +%FT%TZ) relaunched param_matrix $tag" >> "$LOGDIR/lab_matrix_watchdog.log"
  fi
}
# USER 2026-07-21 13:30: ALL workers converge on MU until EVERY param is filled, then the
# next priority sym, etc. (claims table splits MU's cells across the workers).
# 6 workers during the MU push (RAM guard self-throttles each below 8GB free).
launch_pmx w1 "MU,ARM"
launch_pmx w2 "MU,NVDA"
launch_pmx w3 "MU,HAO"
launch_pmx w4 "MU,TTD"
launch_pmx w5 "MU,AXTI"
launch_pmx w6 "MU,MNTS"
# surge workers: claim the cores the tradeable_keys replay releases when it finishes
if ! pgrep -f "precompute_tradeable_keys_histor[y]" >/dev/null; then
  launch_pmx w7 "MU,ARM"
  launch_pmx w8 "MU,NVDA"
  launch_pmx w9 "MU,HAO"
  launch_pmx w10 "MU,TTD"
fi
# VEC_SCREEN lane (USER 2026-07-21 "vectorize everything"): 2 workers cover the
# manifest params the Tier-2 fleet skips (sweep_tier==VEC_SCREEN) via v8_vec_sweep.
# Rows land in param_cells with source_file 'vec_screen/' — Tier-1 screen, not proof.
launch_vec() {
  tag=$1
  if ! pgrep -f "vec_screen_daemon.py --tag $tag" >/dev/null; then
    cd "$SBX" && PSC_CAMPAIGN=stocks_baseline_v2_s4h nohup "$PY" tools/vec_screen_daemon.py --tag "$tag" \
      >> "$LOGDIR/vec_screen_${tag}.log" 2>&1 < /dev/null &
    disown
    echo "$(date -u +%FT%TZ) relaunched vec_screen $tag" >> "$LOGDIR/lab_matrix_watchdog.log"
  fi
}
launch_vec v1
launch_vec v2
# COMBO hunt (USER 2026-07-21): greedy best-combination, objective = gain vs b&h;
# probes every candidate on the stack (interaction data), leave-one-out + TF ablation.
# USER 2026-07-21: plan mode — MU_LONG until 10x b&h, then HAO_SHORT, then the rest.
# MU is LONG-only; shorts hunt on HAO. Continuous rounds (no hourly wait).
# HANG GUARD (2026-07-21): combo_search held the shared param_results_stocks.db for 5h06m
# with zero log progress, starving every matrix writer — workers crash-looped on "database is
# locked" and 9-minute engine units were silently discarded. If its log has not advanced in
# 60 min, it is hung: kill it so the writers get the lock back, then relaunch below.
if pgrep -f "combo_search.py --plan" >/dev/null && [ -f "$LOGDIR/combo_plan.log" ] \
   && [ -z "$(find "$LOGDIR/combo_plan.log" -mmin -60)" ]; then
  pkill -9 -f "combo_search.py --plan"
  echo "$(date -u +%FT%TZ) killed HUNG combo_search (log stale >60min, was holding the DB)" >> "$LOGDIR/lab_matrix_watchdog.log"
fi
if ! pgrep -f "combo_search.py --plan" >/dev/null; then
  cd "$SBX" && PSC_CAMPAIGN=stocks_baseline_v2_s4h nohup "$PY" tools/combo_search.py \
    --plan "MU:LONG,HAO:SHORT,ARM:LONG,NVDA:LONG,ROKU:LONG,AXTI:SHORT,MNTS:SHORT,TTD:SHORT" --target 10.0 \
    >> "$LOGDIR/combo_plan.log" 2>&1 < /dev/null &
  disown
  echo "$(date -u +%FT%TZ) relaunched combo_search plan" >> "$LOGDIR/lab_matrix_watchdog.log"
fi
# hourly export + central-DB mirror (cheap) — only from the first cron slot of the hour
if [ "$(date +%M)" -lt 10 ]; then
  cd "$SBX" && timeout 300 "$PY" tools/export_lab_matrix_db.py >> "$LOGDIR/lab_matrix_export.log" 2>&1
  cd "$SBX" && timeout 600 "$PY" tools/ingest_lab_matrix_to_central.py >> "$LOGDIR/lab_matrix_ingest.log" 2>&1
  cd "$SBX" && timeout 600 "$PY" tools/export_mega_matrix.py >> "$LOGDIR/mega_matrix_export.log" 2>&1
  cd "$SBX" && timeout 300 "$PY" tools/param_keep_drop_report.py --min-keys 3 >> "$LOGDIR/param_keep_drop.log" 2>&1
fi
