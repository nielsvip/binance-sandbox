#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════════
# ⚠️  RE-ENABLE CHECKLIST — USER 2026-07-22 (do not lose this)
#
# Lanes below are TEMPORARILY DISABLED so 100% of S1 goes to the MU_LONG grid
# ("All s1 has to do is calculate the fields for mu_long NOTHING ELSE").
#
# TURN THEM ALL BACK ON once BOTH conditions hold:
#   (1) the switch matrix is COMPLETE for ALL symbols — not just MU_LONG. Check:
#         python tools/matrix_focus.py status      # every key at 3574/3574
#   (2) per_sym settings exist for EVERY tradeable key (long AND short):
#         data/hourly_reconfig/trb/active_config.json  covers every <SYM>_<SIDE>
#         in symbols_trb_long.json + symbols_trb_short.json
#
# WHAT TO RESTORE (all still present, just commented out — nothing was deleted):
#   a) matrix_focus advance          — the `advance` call below (lets the fleet
#                                      move on from MU to the next focus key)
#   b) lab_matrix_daemon             — `launch stocks w1/w2` + `launch crypto w1/w2`
#   c) vec_screen_daemon             — `launch_vec v1` + `launch_vec v2`
#   d) combo_search --plan           — the COMBO block (the switch-combination hunt)
#   e) hourly export / central mirror — export_lab_matrix_db, ingest_lab_matrix_to_central,
#                                      export_mega_matrix, param_keep_drop_report
#
# Until then this script launches ONLY param_matrix_daemon on the focus key.
# ═══════════════════════════════════════════════════════════════════════════════
# watchdog_lab_matrix.sh — keep the 24/7/365 lab-matrix fillers alive on S1 (USER 2026-07-21:
# "should NEVER stop unless it is replaced by a more advanced system"). Cron: */10.
# Workers: 2x stocks + 2x crypto (vectorized numpy, modest RAM — respects S1 utilization floor
# without competing with Tier-2 sweeps for the big allocations).
# The MATRIX_* overrides are intentionally test-only seams. Production cron sets none of them,
# so its paths remain byte-for-byte equivalent; the focused launcher regression uses a private
# temporary tree and can never touch the real result DB, claims, logs, or reports.
SBX=${MATRIX_SBX:-/home/niels/binance-sandbox}
PY=${MATRIX_PY:-/home/niels/.conda/envs/binance_env/bin/python}
LOGDIR=${MATRIX_LOGDIR:-/home/niels/logs}
mkdir -p "$LOGDIR"

# Bible §16.22B: legacy lab/OFAT/combo launcher is fully retired. It must
# never compete with the canonical bounded VECTOR_LIFECYCLE campaign.
echo "$(date -u +%FT%TZ) RETIRED_IDLE — replaced by run_path_productivity_hotlist.py" \
  >> "$LOGDIR/lab_matrix_watchdog.log"
exit 0
PAUSE_FILE="$SBX/data/MATRIX_WORKERS_PAUSED"

# This legacy watchdog supervises the same repaired exact fleet as
# tools/param_matrix_watchdog.sh.  It must honor the same global pause before
# any pgrep/setsid path; otherwise the two cron entries can disagree and this
# one can silently relaunch a deliberately stopped fleet.
if [ -f "$PAUSE_FILE" ]; then
  if [ -f "$SBX/tools/matrix_live_progress.py" ]; then
    "$PY" "$SBX/tools/matrix_live_progress.py" \
      --path "$SBX/chart_static/matrix_live_progress.json" \
      --pause-all --pause-status PAUSED_UNIQUENESS_REPAIR \
      >> "$LOGDIR/lab_matrix_watchdog.log" 2>&1 || true
  fi
  echo "$(date -u +%FT%TZ) paused by $PAUSE_FILE — no matrix workers launched" \
    >> "$LOGDIR/lab_matrix_watchdog.log"
  exit 0
fi
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
# USER 2026-07-22: "The grid is not relevant if it is not calculating vs 100% in the market so
# make sure it is not wasting cpu and time once again." The OFAT grid measures against the
# non-trading baseline and is SUSPENDED; the exposure ladder owns the box. Remove
# data/GRID_SUSPENDED to bring it back — do NOT just kill the workers, this cron relaunches
# them every 10 minutes and the orphaned engines they leave behind ate 20GB and OOM-killed the
# ladder run twice.
refresh_switch_matrix_reports() (
  # Compute can be suspended while fresh reporting remains mandatory.  Serialize XLSX writes
  # so a manual export and the ten-minute watchdog cannot corrupt each other's output.
  exec 9>"$LOGDIR/switch_matrix_export.lock"
  flock -n 9 || exit 0
  cd "$SBX" || exit 1
  timeout 300 "$PY" tools/export_switch_matrix_xls.py --account trb --include-1yr \
    >> "$LOGDIR/switch_matrix_export.log" 2>&1
  timeout 120 "$PY" tools/switch_matrix_digest.py \
    >> "$LOGDIR/switch_matrix_digest.log" 2>&1
)

# C5 REPAIRED CONTRACT LANE (2026-07-30).
#
# `GRID_SUSPENDED` continues to block every historical worker below.  The separate
# MATRIX_REPAIRED_ENABLE marker authorizes only these side-isolated, B&H-seeded workers, under
# a new campaign namespace.  This prevents removing the old kill switch from accidentally
# resurrecting mixed-side/pre-HTF-fix daemons.  HAO_SHORT is deliberately absent until its NPZ
# passes tools/backtest_data_contract.py.
REPAIRED_CAMPAIGN=stocks_repaired_20260730_c5
# These values are loaded from the same immutable worker manifest immediately
# before launch.  Hardcoding the retired July NPZ here caused this older
# watchdog to fight param_matrix_watchdog.sh and repeatedly launch stale-data
# workers after a causal-data cutover.
REPAIRED_NPZ_DIR=""
REPAIRED_END_DATE=""
launch_repaired() {
  symbol=$1; side=$2; tag=$3; slot=$4
  # Match the stable argument prefix only: the two watchdogs historically
  # emitted --all-tiers/--safe-contract in different orders and therefore did
  # not recognize one another's healthy process.
  needle="param_matrix_daemon.py --tag $tag --only $symbol --side $side"
  if ! pgrep -f "$needle" >/dev/null; then
    # `disown` is ineffective when this script is started by non-interactive cron: bash can
    # remain the daemon's parent and wait indefinitely, which prevents a clean watchdog cycle
    # after a contract-fingerprint exit. `setsid -f` makes the worker an independent session
    # owned by init, so this ten-minute script can finish and later replace failed workers.
    cd "$SBX" && PSC_CAMPAIGN="$REPAIRED_CAMPAIGN" \
      PSC_MATRIX_NPZ_DIR="$REPAIRED_NPZ_DIR" \
      PSC_MATRIX_END_DATE="$REPAIRED_END_DATE" \
      V8_MATRIX_CONTRACT_VERSION="tradier-matrix-exec-c5-20260730" \
      setsid -f nohup nice -n 18 "$PY" \
      tools/param_matrix_daemon.py --tag "$tag" --only "$symbol" --side "$side" \
      --all-tiers --safe-contract --progress-slot "$slot" --min-avail 5000 \
      >> "$LOGDIR/param_matrix_${tag}.log" 2>&1 < /dev/null
    echo "$(date -u +%FT%TZ) relaunched repaired matrix $tag (${symbol}_${side})" \
      >> "$LOGDIR/lab_matrix_watchdog.log"
  fi
}
if [ -f "$SBX/data/MATRIX_REPAIRED_ENABLE" ]; then
  # c5 dependency-pack workers come from one fail-closed manifest.  The daemon
  # independently verifies campaign, contract version, symbol/side, exact
  # fingerprint and no-live-promotion before accepting a tag's priority roots.
  # STOP_PACK/broad OFAT workers are intentionally absent from this lane.
  REPAIRED_WORKER_MANIFEST="$SBX/data/matrix_worker_manifest.json"
  if ! "$PY" "$SBX/tools/matrix_resume_gate.py" check-launch \
    --root "$SBX" --manifest "$REPAIRED_WORKER_MANIFEST" \
    >> "$LOGDIR/lab_matrix_watchdog.log" 2>&1; then
    echo "$(date -u +%FT%TZ) repaired matrix resume gate BLOCKED" \
      >> "$LOGDIR/lab_matrix_watchdog.log"
    exit 0
  fi
  REPAIRED_NPZ_REL=$(jq -er '.npz_dir' "$REPAIRED_WORKER_MANIFEST") || exit 1
  REPAIRED_END_DATE=$(jq -er '.end_date' "$REPAIRED_WORKER_MANIFEST") || exit 1
  case "$REPAIRED_NPZ_REL" in
    /*) REPAIRED_NPZ_DIR="$REPAIRED_NPZ_REL" ;;
    *) REPAIRED_NPZ_DIR="$SBX/$REPAIRED_NPZ_REL" ;;
  esac
  REPAIRED_WORKERS=$(
    "$PY" - "$REPAIRED_WORKER_MANIFEST" "$REPAIRED_CAMPAIGN" <<'PY'
import json
import sys

path, campaign = sys.argv[1:]
try:
    payload = json.load(open(path))
except Exception as exc:
    raise SystemExit(f"invalid repaired worker manifest {path}: {exc}")
if payload.get("campaign") != campaign:
    raise SystemExit(
        f"worker manifest campaign {payload.get('campaign')!r} != {campaign!r}"
    )
if payload.get("matrix_contract_version") != "tradier-matrix-exec-c5-20260730":
    raise SystemExit("worker manifest is not exact c5")
if payload.get("no_live_promotion") is not True:
    raise SystemExit("worker manifest must set no_live_promotion=true")
workers = payload.get("workers") or []
if not workers:
    raise SystemExit("worker manifest has no workers")
seen = set()
for slot, row in enumerate(workers, 1):
    tag = str(row.get("tag") or "")
    sym = str(row.get("symbol") or "").upper()
    side = str(row.get("side") or "").upper()
    roots = row.get("priority_roots") or []
    if not tag or not sym or side not in {"LONG", "SHORT"} or not roots:
        raise SystemExit(f"invalid dependency-pack worker: {row!r}")
    if tag in seen:
        raise SystemExit(f"duplicate worker tag: {tag}")
    seen.add(tag)
    if slot > 6:
        raise SystemExit("worker manifest exceeds six live progress slots")
    print(f"{sym}\t{side}\t{tag}\t{slot}")
PY
  ) || {
    echo "$(date -u +%FT%TZ) repaired worker manifest rejected" \
      >> "$LOGDIR/lab_matrix_watchdog.log"
    exit 1
  }
  while IFS=$'\t' read -r symbol side tag slot; do
    [ -n "$tag" ] && launch_repaired "$symbol" "$side" "$tag" "$slot"
  done <<< "$REPAIRED_WORKERS"
  refresh_switch_matrix_reports
  exit 0
fi
if [ -f "$SBX/data/GRID_SUSPENDED" ]; then
  refresh_switch_matrix_reports
  exit 0
fi
if [ ! -f "$SBX/data/MATRIX_REBASELINE_HOLD" ]; then
# USER 2026-07-22: "make sure it keeps going with the other 3 symbols so tomorrow at market
# open we can start tuning them and get baselines" — advance RE-ENABLED. Order is
# MU_LONG -> HAO_SHORT -> NVDA_LONG -> VT_LONG (tools/matrix_focus.py FOCUS_ORDER); it only
# moves on when the current key's grid is >=99.5% complete, so it cannot drift early.
"$PY" "$SBX/tools/matrix_focus.py" advance >> "$LOGDIR/matrix_focus.log" 2>&1
FOCUS=$("$PY" "$SBX/tools/matrix_focus.py" symbol 2>/dev/null)
[ -z "$FOCUS" ] && FOCUS=MU
SIDE=$("$PY" "$SBX/tools/matrix_focus.py" side 2>/dev/null)
[ -z "$SIDE" ] && SIDE=LONG
for t in w1 w2 w3 w4 w5 w6 w7 w8 w9 w10 w11 w12; do
  # a worker pinned to a stale ticker is drift, not work — kill it so it relaunches on FOCUS
  if pgrep -f "param_matrix_daemon.py --tag $t " >/dev/null && \
     ! pgrep -f "param_matrix_daemon.py --tag $t --only $FOCUS --side $SIDE " >/dev/null; then
    pkill -f "param_matrix_daemon.py --tag $t "
    echo "$(date -u +%FT%TZ) $t was off-focus, killed (focus=$FOCUS)" >> "$LOGDIR/lab_matrix_watchdog.log"
  fi
  if ! pgrep -f "param_matrix_daemon.py --tag $t " >/dev/null; then
    cd "$SBX" && PSC_CAMPAIGN=stocks_baseline_v2_s4h nohup "$PY" tools/param_matrix_daemon.py \
      --tag "$t" --only "$FOCUS" --side "$SIDE" --all-tiers --min-avail 3000 \
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
# USER 2026-07-22 "we need 3000 different numbers for MU by tomorrow morning": the Tier-2
# engine physically cannot (2,780 units x ~12min / 14 workers = ~40h). The vec lane can
# (~1-2.6s/cell), so it runs SCOPED TO THE FOCUS KEY ONLY. Its rows are Tier-1 and land in
# SWITCH_MATRIX_TRB_VEC_DIAGNOSTIC — never mixed with the engine sheet (2026-07-21 tier fix).
launch_vec_focus() {
  tag=$1
  if ! pgrep -f "vec_screen_daemon.py --tag $tag --only $FOCUS .*--all-tiers" >/dev/null; then
    cd "$SBX" && PSC_CAMPAIGN=stocks_baseline_v2_s4h nohup "$PY" tools/vec_screen_daemon.py \
      --tag "$tag" --only "$FOCUS" --side "$SIDE" --all-tiers \
      >> "$LOGDIR/vec_screen_${tag}.log" 2>&1 < /dev/null &
    disown
    echo "$(date -u +%FT%TZ) relaunched vec_screen $tag (focus=${FOCUS}_${SIDE})" >> "$LOGDIR/lab_matrix_watchdog.log"
  fi
}
# TIER-1 SCREEN — runs CONTINUOUSLY across the whole universe (USER 2026-07-22: "make sure
# the v8_vec_sweep is set up and continues at once when the Tier1 finishes").
# vec_params() now filters to the 164 knobs SweepConfig actually declares, so every sweep it
# starts can produce cells instead of answering "unknown SweepConfig knob" and refusing
# (581 cells/key ceiling — the other 757 knobs need porting, BACKTEST_BIBLE §13.5).
# NO --only: it covers PRIORITY_SYMS first then the rest of the universe, so when it exhausts
# one key it moves straight to the next and never idles. ONE worker at nice 19 — this is a
# screen, and the Tier-2 engine must keep the box.
launch_vec_universe() {
  tag=$1
  if ! pgrep -f "vec_screen_daemon.py --tag $tag " >/dev/null; then
    cd "$SBX" && PSC_CAMPAIGN=stocks_baseline_v2_s4h nohup nice -n 19 "$PY" tools/vec_screen_daemon.py \
      --tag "$tag" --all-tiers \
      >> "$LOGDIR/vec_screen_${tag}.log" 2>&1 < /dev/null &
    disown
    echo "$(date -u +%FT%TZ) relaunched vec_screen $tag (universe screen)" >> "$LOGDIR/lab_matrix_watchdog.log"
  fi
}
launch_vec_universe v1
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
# SWITCH_MATRIX export — every cycle (USER 2026-07-22 "I dont see the switch_matrix grow").
# The DB fills continuously but the spreadsheet is a GENERATED artifact, and S1 had NO cron
# regenerating it (verified: crontab has 0 export_switch_matrix entries) — so the sheet only
# moved when someone ran the exporter by hand while param_cells grew underneath it. It is a
# read-only query + xlsx write (~15s), cheap enough to run every 10 min. Reporting deliberately
# runs even while GRID_SUSPENDED is present. The Mac pulls at :35.
refresh_switch_matrix_reports
# heavier lab/mega exports stay OFF while the fleet is MU-only (see the checklist at the top)
