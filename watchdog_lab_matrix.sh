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
# USER 2026-07-22: "All s1 has to do is calculate the fields for mu_long NOTHING ELSE".
# Lab / vec / combo / hourly-export lanes are OFF — every core belongs to the MU_LONG grid.
# launch stocks w1
# launch stocks w2
# launch crypto w1
# launch crypto w2
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
# USER 2026-07-21 evening: "concentrate all instances on one tickr at a time". The whole
# Tier-2 fleet works ONE key until its grid is complete, then matrix_focus.py advances to the
# next of MU_LONG -> HAO_SHORT -> NVDA_LONG -> VT_LONG. Measured cost: one single-symbol run =
# 9m11s / 632MB RSS, so 14 workers ~= one core each on S1's 16 within the 30GB budget. Do not
# add workers past ~14: oversubscription measured at load 31 cut per-worker throughput ~2.4x.
# PAUSED 2026-07-21 late: the campaign baseline (overrides={}) has WT_3M_FORCE_OPEN_ENABLED
# =False (config_tradier:2319), so MU_LONG's baseline is 19 trades / 0.13% time-in-market vs
# b&h +632.98% — it barely trades. USER's actual baseline is "open every wt_5m crossover, close
# every wt_5m crossunder" (~7,940 trades, 1.84x b&h in the lab). Every OFAT delta measured
# against a non-trading baseline is noise, and 80% of cells came back inert because there were
# no trades for the knobs to act on. Do NOT burn days filling that grid: rebaseline first, then
# remove this guard.
if [ ! -f "$SBX/data/MATRIX_REBASELINE_HOLD" ]; then
# advance DISABLED (USER 2026-07-22: MU_LONG only — do not drift to HAO/NVDA/VT)
FOCUS=$("$PY" "$SBX/tools/matrix_focus.py" symbol 2>/dev/null)
[ -z "$FOCUS" ] && FOCUS=MU
for t in w1 w2 w3 w4 w5 w6 w7 w8 w9 w10 w11 w12 w13 w14; do
  # a worker pinned to a stale ticker is drift, not work — kill it so it relaunches on FOCUS
  if pgrep -f "param_matrix_daemon.py --tag $t " >/dev/null && \
     ! pgrep -f "param_matrix_daemon.py --tag $t --only $FOCUS " >/dev/null; then
    pkill -f "param_matrix_daemon.py --tag $t "
    echo "$(date -u +%FT%TZ) $t was off-focus, killed (focus=$FOCUS)" >> "$LOGDIR/lab_matrix_watchdog.log"
  fi
  if ! pgrep -f "param_matrix_daemon.py --tag $t " >/dev/null; then
    cd "$SBX" && PSC_CAMPAIGN=stocks_baseline_v2_s4h nohup "$PY" tools/param_matrix_daemon.py \
      --tag "$t" --only "$FOCUS" --all-tiers \
      >> "$LOGDIR/param_matrix_${t}.log" 2>&1 < /dev/null &
    disown
    echo "$(date -u +%FT%TZ) relaunched param_matrix $t (focus=$FOCUS)" >> "$LOGDIR/lab_matrix_watchdog.log"
  fi
done
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
# PARKED 2026-07-21 evening (USER "concentrate all instances on one tickr at a time"): the vec
# lane screens OTHER symbols and competes for the same 16 cores as the MU push, and it is the
# lane that produced the tier-lie (Tier-1 deltas differenced against a Tier-2 baseline) and
# silently no-ops knobs it does not implement. Its cells are diagnostic-only anyway. Re-enable
# by uncommenting once the four pilot keys are complete.
# launch_vec v1
# launch_vec v2
# COMBO hunt (USER 2026-07-21): greedy best-combination, objective = gain vs b&h;
# probes every candidate on the stack (interaction data), leave-one-out + TF ablation.
# USER 2026-07-21 evening: the first four keys are MU_LONG, HAO_SHORT, NVDA_LONG, VT_LONG.
# MU_LONG must be fully figured out — OFAT *and* combinations — before the fleet moves on.
# MU is LONG-only; shorts hunt on HAO. Continuous rounds (no hourly wait).
# HANG GUARD (2026-07-21): combo_search held the shared param_results_stocks.db for 5h06m
# with zero log progress, starving every matrix writer. But it only logs AFTER each probe, and
# one probe is a full engine run (9-15min, timeout 3600) — a stale log alone is NOT a hang and
# a 60min-stale test killed 5 legitimate runs. The real discriminator: a working combo_search
# has an engine CHILD; a hung one has none. Require both signals, and a 2h staleness floor.
# combo_search OFF (USER 2026-07-22: MU_LONG fields only)
# hourly exports OFF (USER 2026-07-22: MU_LONG fields only) — rerun by hand when the grid is done
