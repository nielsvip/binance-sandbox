#!/bin/bash
# v15_daily_chain_s1 — the daily pre-market chain on S1 (BIBLE §68.3 steps 1+2, director design 2026-10-06).
# Replaces the Mac 05:00Z tools/v15_daily_selfimprove.sh (cron PATH had no `timeout` -> died every day since 10-04).
# Cron (S1, 12:15Z, director 2026-10-06; full run ~45 min):
#   15 12 * * * cd /home/niels/binance-sandbox && flock -n /tmp/v15_daily_chain.lock bash tools/v15_daily_chain_s1.sh >> /home/niels/logs/v15_daily_chain.log 2>&1 # V15_DAILY_CHAIN_S1
# Steps: 1 avg-delta rebuild (hosts as seen from S1) -> 2 the ONE template writer on SPREADSHEETS + TEMPLATE_FINAL_NORM
# (each cat_side's promotions pass the COMBINED trade-floor guard tools/v15_promotion_floor_guard.py before saving; refused keys keep the old bold)
# (code-surface sync DISABLED: S1 never edits config.py/config_tradier.py/v12_quick_engine.py; the Mac follow-up
# tools/v15_daily_chain_mac_apply.sh syncs report promoted_keys at 13:20Z) -> 3 cat_side_defaults_4 (+ sweep copy)
# -> 4 avg_delta_pos_sym.json -> 4c causal export -> 4e endgame fleet knowledge -> 5 zero-delta watchdog (non-fatal) -> 6 push to s2/s5 + kv sync + md5 verify
# -> 6b tradeable check (non-fatal; SYSTEM_ERROR rows for tradeable keys with <10 trades/30D or TIM<20%) -> 7 done-stamp json.
# 4b (2026-10-07): cell-evidence regen (zero-skip maps) over ALL local progress dirs (campaign dirs move; glob, don't pin)
# + push data/avg_delta_pos_sym_cell.json + data/cell_evidence/*.json to s2/s5 so every host's pilots skip condemned cells.
# 4c (2026-10-09): causal learner ingest-progress + export data/causal_condemned.json (runs BEFORE 4b for same-day merge;
# step5 watchdog + runtime cell merge consume it; live losers ingested on the Mac step5e).
# 2a (2026-10-13, BIBLE §94): persistent template row intake — tools/v15_switch_add.py --queue data/template_row_queue.json
# installs every queued switch missing from the 8 templates (idempotent; Mac --enqueue / Mac-apply rescue-lost feed the inbox).
# 4e (2026-10-13): invention loop mine + rows-check + promote-check over the fleet autopsy/progress/missed-trend dirs (ancillary).
# 8 (2026-10-13, POST-STAMP, ancillary, never changes today's stamp): 8a missed-trend round (BEST_EFFORT & TIM<20),
# 8b priority evidence regen (data/cat_avg/priority_evidence_*.json, pushed md5-verified), 8c WEEKLY (Sun) on the idlest
# host (S1/s2/s5/s6, nice 15, <=4 workers): switch flip study + causal rescue ablation (fix_outcomes) + causal DB cap.
# Hardening (2026-10-13): trap EXIT writes <date>.FAILED.json whenever the run ends without DONE (TERM/INT/HUP trapped; bash
# runs the trap when the current foreground step returns — GNU timeout keeps steps in their own process group);
# a run killed by SIGKILL/OOM/reboot leaves <date>.RUNNING with a dead pid -> the next start converts it to FAILED.
# V15_DAILY_CHAIN=1 is exported so tools/oom_guard.sh never SIGSTOPs chain processes.
# Any failed step stops the chain (except 5). DRYRUN=1: rebuild into /tmp + template writer WITHOUT --apply, nothing else written/pushed.
# Never relies on *latest* names: the dated aggregate is passed explicitly through every step.
set -u
set -o pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT" || exit 1
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH
PY=$ROOT/.venv/bin/python
DATE=$(date -u +%Y%m%d)
DRYRUN=${DRYRUN:-0}
TARGETS=${V15_CHAIN_TARGETS:-"niels@10.0.0.4 niels@10.0.0.5 niels@10.0.0.6"}
export V15_FLEET_HOSTS=${V15_FLEET_HOSTS:-tools/fleet_hosts_final.json}
export V15_TEMPLATE_SYNC_SURFACES=0
STAMP_DIR=$ROOT/data/daily_chain
WORK=$STAMP_DIR/work_$DATE
[ "$DRYRUN" = 1 ] && WORK=/tmp/v15_daily_chain_dry_$DATE
mkdir -p "$STAMP_DIR" "$WORK" || exit 1
SSH="ssh -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new"
T0=$(date -u +%FT%TZ)
STEPS_OK=""
CUR_STEP=0
CHAIN_STATUS=RUNNING
export V15_DAILY_CHAIN=1
log() { echo "[$(date -u +%FT%TZ)] [chain] $*"; }
fail() {
  log "FAILED at step $1: $2 — chain STOPPED (nothing after this step ran)"
  CHAIN_STATUS=FAILED
  [ "$DRYRUN" = 1 ] || printf '{"date":"%s","status":"FAILED","failed_step":"%s","why":"%s","steps_ok":"%s","started":"%s","ended":"%s"}\n' "$DATE" "$1" "$2" "$STEPS_OK" "$T0" "$(date -u +%FT%TZ)" > "$STAMP_DIR/$DATE.FAILED.json"
  rm -f "$STAMP_DIR/$DATE.RUNNING"
  exit 1
}
# 2026-10-13 hardening: never die silently. Any exit without DONE writes a FAILED stamp (the Mac apply stops waiting on it).
on_exit() {
  RC=$?
  if [ "$DRYRUN" != 1 ] && [ "$CHAIN_STATUS" = RUNNING ]; then
    log "chain ended without DONE (rc=$RC, step=$CUR_STEP) — writing FAILED stamp"
    printf '{"date":"%s","status":"FAILED","failed_step":"%s","why":"chain exited rc=%s without DONE (signal/kill/unexpected exit)","steps_ok":"%s","started":"%s","ended":"%s"}\n' "$DATE" "$CUR_STEP" "$RC" "$STEPS_OK" "$T0" "$(date -u +%FT%TZ)" > "$STAMP_DIR/$DATE.FAILED.json"
  fi
  [ "$DRYRUN" = 1 ] || rm -f "$STAMP_DIR/$DATE.RUNNING"
}
trap on_exit EXIT
trap 'log "caught SIGTERM"; exit 143' TERM
trap 'log "caught SIGINT"; exit 130' INT
trap 'log "caught SIGHUP"; exit 129' HUP
if [ "$DRYRUN" != 1 ]; then
  # a previous run killed without a trap (SIGKILL/OOM/reboot) left <date>.RUNNING with a dead pid -> FAILED stamp for that date
  for RF in "$STAMP_DIR"/*.RUNNING; do
    [ -f "$RF" ] || continue
    RD=$(basename "$RF" .RUNNING); RP=$(cut -d' ' -f1 "$RF" 2>/dev/null)
    if [ -n "$RP" ] && kill -0 "$RP" 2>/dev/null; then continue; fi
    if ! grep -q '"status": "DONE"' "$STAMP_DIR/$RD.json" 2>/dev/null && [ ! -f "$STAMP_DIR/$RD.FAILED.json" ]; then
      printf '{"date":"%s","status":"FAILED","failed_step":"unknown","why":"previous run (pid %s, started %s) died without trap: SIGKILL/OOM/reboot","detected":"%s"}\n' "$RD" "$RP" "$(cut -d' ' -f2 "$RF" 2>/dev/null)" "$(date -u +%FT%TZ)" > "$STAMP_DIR/$RD.FAILED.json"
      log "stale RUNNING marker $RF (pid $RP dead) -> $RD.FAILED.json"
    fi
    rm -f "$RF"
  done
  echo "$$ $T0" > "$STAMP_DIR/$DATE.RUNNING"
fi
log "=== start date=$DATE DRYRUN=$DRYRUN host=$(hostname) root=$ROOT hosts=$V15_FLEET_HOSTS targets=$TARGETS"
[ -x "$PY" ] || fail 0 "venv python missing: $PY"
[ -e SPREADSHEETS/TEMPLATES_FROZEN ] && fail 0 "SPREADSHEETS/TEMPLATES_FROZEN exists (user freeze)"

# 1. deltas
CUR_STEP=1
if [ "$DRYRUN" = 1 ]; then
  AGG=$WORK/v15_avg_delta_$DATE.xlsx
  log "step1 rebuild (DRYRUN -> $AGG, latest copies untouched)"
  V15_AVG_DRYRUN=1 timeout 1800 "$PY" -u tools/v15_avg_delta_rebuild.py --date "$DATE" --out "$AGG" || fail 1 "v15_avg_delta_rebuild rc=$?"
else
  AGG=SPREADSHEETS/v15_avg_delta/v15_avg_delta_$DATE.xlsx
  [ -f "$AGG" ] && cp -p "$AGG" "backups/before_daily_chain_${DATE}_$(date -u +%H%M)_$(basename "$AGG")"
  log "step1 rebuild -> $AGG"
  timeout 1800 "$PY" -u tools/v15_avg_delta_rebuild.py --date "$DATE" --out "$AGG" || fail 1 "v15_avg_delta_rebuild rc=$?"
fi
[ -s "$AGG" ] || fail 1 "aggregate not written: $AGG"
[ $(( $(date +%s) - $(stat -c %Y "$AGG") )) -lt 3600 ] || fail 1 "aggregate is not fresh: $AGG"
"$PY" -c "import openpyxl,sys; wb=openpyxl.load_workbook(sys.argv[1],read_only=True); print('[chain] agg sheets', {s: wb[s].max_row-1 for s in wb.sheetnames})" "$AGG" || fail 1 "aggregate unreadable"
STEPS_OK="1"

# 2. template defaults (ONE writer; main dir first: only the main run writes cat_side_promotions + round ledger)
APPLY_FLAG="--apply"
[ "$DRYRUN" = 1 ] && APPLY_FLAG=""
CUR_STEP=2
for KIND in main norm; do
  TD=SPREADSHEETS
  [ "$KIND" = norm ] && TD=SPREADSHEETS/TEMPLATE_FINAL_NORM
  OUT=$WORK/template_$KIND.out
  log "step2 template writer $KIND ($TD) ${APPLY_FLAG:-REPORT-ONLY} V15_TEMPLATE_SYNC_SURFACES=$V15_TEMPLATE_SYNC_SURFACES"
  timeout 1800 "$PY" -u tools/v15_daily_template_update.py --agg "$AGG" --template-dir "$TD" $APPLY_FLAG 2>&1 | tee "$OUT"
  RC=$?
  [ $RC -eq 0 ] || fail 2 "template writer $KIND rc=$RC"
  grep -q "NOT saved" "$OUT" && fail 2 "template writer $KIND: a cat_side failed layout verification (NOT saved)"
  grep -Eq "violations=[1-9]" "$OUT" && fail 2 "template writer $KIND: layout violations reported"
  grep -q "^Traceback" "$OUT" && fail 2 "template writer $KIND: traceback"
  REP=$(grep -o '^\[report\] .*' "$OUT" | tail -1 | cut -d' ' -f2)
  [ -n "$REP" ] && [ -f "$REP" ] || fail 2 "template writer $KIND: no report json"
  cp "$REP" "$WORK/template_$KIND.json"
  grep "FLOOR-GUARD" "$OUT" | sed "s/^/[chain] step2 $KIND /" || true
  log "step2 $KIND report $REP -> $WORK/template_$KIND.json"
done
STEPS_OK="$STEPS_OK,2"

# 2a. persistent template row intake (2026-10-13, BIBLE §71 amendment / §94). The S1 source host owns
#     data/template_row_queue.json; the ONE row writer (tools/v15_switch_add.py --queue) installs every queued switch that is
#     missing from a target template — same validation/insert/guards as a single add, one atomic save per file, flock shared
#     with the bookkeeper. Idempotent: rows anything dropped are re-installed here. Ancillary: a refusal never fails the chain.
CUR_STEP=2a
if [ -f data/template_row_queue.json ]; then
  log "step2a template row intake ${APPLY_FLAG:-REPORT-ONLY}"
  [ "$DRYRUN" = 1 ] || timeout 120 "$PY" -u tools/v15_template_row_intake.py merge-inbox || log "step2a merge-inbox rc=$? (non-fatal)"
  timeout 1800 nice -n 10 "$PY" -u tools/v15_switch_add.py --queue data/template_row_queue.json --templates-dirs SPREADSHEETS/TEMPLATE_FINAL_NORM,SPREADSHEETS $APPLY_FLAG --summary-out "$WORK/row_intake.json" 2>&1 | tee "$WORK/row_intake.out" | grep -E '^\[queue\] (installed|summary|REFUSED|dry-run)' | sed 's/^/[chain] step2a /'
  RC=${PIPESTATUS[0]}
  [ "$RC" -eq 0 ] || log "step2a row intake rc=$RC (non-fatal; templates untouched on refusal)"
else
  log "step2a no data/template_row_queue.json — skipped"
fi
STEPS_OK="$STEPS_OK,2a"

if [ "$DRYRUN" = 1 ]; then
  log "step3 cat_side_defaults_4 SKIPPED (DRYRUN)"
  log "step4 pos_sym json (DRYRUN -> $WORK/avg_delta_pos_sym.json)"
  timeout 300 "$PY" -u tools/v15_possym_json_from_agg.py "$AGG" "$WORK/avg_delta_pos_sym.json" || fail 4 "possym rc=$?"
  log "step4c causal learner (DRYRUN db+out under $WORK, prod untouched)"
  _cpdirs=$(ls -d /home/niels/v15_run*/ data/reports/lifecycle_pilot 2>/dev/null | tr '\n' ',' | sed 's/,$//')
  _pdirs=$(ls -d /home/niels/v15_*/ data/reports/lifecycle_pilot 2>/dev/null | tr '\n' ',' | sed 's/,$//')
  timeout 900 "$PY" -u tools/v15_causal_learner.py ingest-progress "$_cpdirs" --db "$WORK/causal_learner.db" || fail 4c "causal ingest-progress rc=$?"
  timeout 300 "$PY" -u tools/v15_causal_learner.py export --db "$WORK/causal_learner.db" --out "$WORK/causal_condemned.json" || fail 4c "causal export rc=$?"
  log "step4b cell evidence (DRYRUN -> $WORK/avg_delta_pos_sym_cell.json)"
  timeout 900 "$PY" -u tools/v15_cell_evidence.py "$_pdirs" "$WORK/avg_delta_pos_sym_cell.json" || fail 4b "cell_evidence rc=$?"
  log "step5 zero-delta watchdog (DRYRUN, report-only)"
  timeout 600 "$PY" -u tools/v15_zero_delta_watchdog.py --agg "$AGG" || log "step5 watchdog rc=$? (non-fatal)"
  log "step6 push SKIPPED (DRYRUN) targets=$TARGETS"
  log "step6b tradeable check (DRYRUN -> $WORK/tradeable_check.json)"
  timeout 1800 "$PY" -u tools/v15_tradeable_check.py --selection "${AGG%.xlsx}.selection.json" --out "$WORK/tradeable_check.json" --date "$DATE" || log "step6b tradeable check rc=$? (non-fatal)"
  log "=== DRYRUN done (work dir $WORK)"
  exit 0
fi

# 3. cat_side_defaults_4 (+ sweep copy); the builder dual-writes kv
CUR_STEP=3
log "step3 build_cat_side_defaults_4"
cp -p data/per_sym_settings.json "backups/before_daily_chain_${DATE}_per_sym_settings.json" 2>/dev/null
timeout 600 "$PY" -u tools/build_cat_side_defaults_4.py || fail 3 "build_cat_side_defaults_4 rc=$?"
mkdir -p data/sweep_defaults && cp data/per_sym_settings.json data/sweep_defaults/per_sym_settings.json || fail 3 "sweep copy"
"$PY" -u tools/v15_state_kv_sync.py --apply || fail 3 "kv sync on S1"
STEPS_OK="$STEPS_OK,3"

# 4. pos_sym sampler input
CUR_STEP=4
log "step4 v15_possym_json_from_agg"
timeout 300 "$PY" -u tools/v15_possym_json_from_agg.py "$AGG" data/avg_delta_pos_sym.json || fail 4 "possym rc=$?"
cp "$AGG" SPREADSHEETS/v15_avg_delta_latest.xlsx && cp "$AGG" SPREADSHEETS/v15_vector_delta_latest.xlsx || fail 4 "latest copies"
STEPS_OK="$STEPS_OK,4"

# 4c. causal learner (runs BEFORE 4b so today's export merges same-day into the cell maps + step5 watchdog).
# Ingest is idempotent (content-hash); the export cross-checks the aggregate (pos_sym>0 veto) and auto-lifts
# condemned switches on later positive sweep evidence. Live losers are ingested on the Mac (step5e) where they live.
CUR_STEP=4c
log "step4c causal learner"
_cpdirs=$(ls -d /home/niels/v15_run*/ data/reports/lifecycle_pilot 2>/dev/null | tr '\n' ',' | sed 's/,$//')
[ -n "$_cpdirs" ] || fail 4c "no production progress dirs found"
[ -f data/causal_condemned.json ] && cp -p data/causal_condemned.json "backups/before_daily_chain_${DATE}_causal_condemned.json"
timeout 900 "$PY" -u tools/v15_causal_learner.py ingest-progress "$_cpdirs" || fail 4c "causal ingest-progress rc=$?"
timeout 600 "$PY" -u tools/v15_causal_learner.py export --out data/causal_condemned.json || fail 4c "causal export rc=$?"
[ -s data/causal_condemned.json ] || fail 4c "causal export not written"
STEPS_OK="$STEPS_OK,4c"

# 4b. cell-evidence regen (zero-skip runtime maps) + verify at least one cat_side map exists
CUR_STEP=4b
log "step4b v15_cell_evidence"
timeout 900 "$PY" -u tools/v15_cell_evidence.py "$_cpdirs" data/avg_delta_pos_sym_cell.json || fail 4b "cell_evidence rc=$?"
ls data/cell_evidence/*.json >/dev/null 2>&1 || fail 4b "no data/cell_evidence/*.json written"
STEPS_OK="$STEPS_OK,4b"

# 4d. USER 2026-10-12: causal switch profile (pilot PRIOR POLICY priors, bible §87) + sym_side cadence ledger
# (scheduler DAILY/5d/10d tiers). Ancillary: never fails the chain; the pilot/scheduler fail-open without the files.
CUR_STEP=4d
log "step4d switch causal profile + sym_side ledger"
[ -f data/causal/switch_causal_profile.json ] && cp -p data/causal/switch_causal_profile.json "backups/before_daily_chain_${DATE}_switch_causal_profile.json"
timeout 1200 nice -n 10 python3 -u tools/v15_switch_causal_profile.py || log "step4d causal profile rc=$? (non-fatal, previous profile kept)"
timeout 600 nice -n 10 python3 -u tools/v15_symside_ledger.py || log "step4d symside ledger rc=$? (non-fatal)"
STEPS_OK="$STEPS_OK,4d"

# 4e. invention loop (USER 2026-10-13, bible §84/§94): autopsy rules (missing_entry_filters / missing_exit_confirmations,
#     distinct-sym scored) + gaps + coverage + missed-trend specs -> data/invention/backlog.json; rows-check reads the
#     templates read-only; promote-check moves TESTED->PROMOTED/RETIRED from done-row evidence. Ancillary, never fails the chain.
CUR_STEP=4e
log "step4e invention loop mine + rows-check + promote-check"
[ -f data/invention/backlog.json ] && cp -p data/invention/backlog.json "backups/before_daily_chain_${DATE}_invention_backlog.json"
timeout 900 nice -n 15 "$PY" -u tools/v15_invention_loop.py mine --write 2>&1 | tail -3 | sed 's/^/[chain] step4e /' || log "step4e invention mine rc=$? (non-fatal)"
timeout 600 nice -n 15 "$PY" -u tools/v15_invention_loop.py promote-check 2>&1 | tail -2 | sed 's/^/[chain] step4e /' || log "step4e invention promote-check rc=$? (non-fatal)"
STEPS_OK="$STEPS_OK,4e"

# 4e. USER 2026-10-13: fleet-wide ENDGAME knowledge (bible §69) — merges every host x campaign endgame_ledger.jsonl into
# data/endgame/endgame_knowledge_fleet.json and pushes it to the workers (md5-verified). Non-fatal; same lock as the hourly cron.
log "step4e endgame knowledge merge"
flock -w 600 /tmp/v15_endgame_merge.lock timeout 900 nice -n 10 python3 -u tools/v15_endgame_knowledge_merge.py --pull --push || log "step4e endgame knowledge merge rc=$? (non-fatal, previous fleet file kept)"
STEPS_OK="$STEPS_OK,4e"

# 5. zero-delta watchdog (non-fatal)
CUR_STEP=5
log "step5 zero-delta watchdog"
timeout 600 "$PY" -u tools/v15_zero_delta_watchdog.py --agg "$AGG" --apply || log "step5 watchdog rc=$? (non-fatal, chain continues)"
STEPS_OK="$STEPS_OK,5"

# 6. push to s2/s5 + kv sync + md5 verify
FILES="SPREADSHEETS/TEMPLATE_CRYPTO_LONG.xlsx SPREADSHEETS/TEMPLATE_CRYPTO_SHORT.xlsx SPREADSHEETS/TEMPLATE_STOCKS_LONG.xlsx SPREADSHEETS/TEMPLATE_STOCKS_SHORT.xlsx"
FILES="$FILES SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_CRYPTO_LONG.xlsx SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_CRYPTO_SHORT.xlsx SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_STOCKS_LONG.xlsx SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_STOCKS_SHORT.xlsx"
FILES="$FILES data/per_sym_settings.json data/sweep_defaults/per_sym_settings.json data/avg_delta_pos_sym.json"
FILES="$FILES data/avg_delta_pos_sym_cell.json data/cell_evidence/*.json data/causal_condemned.json"
[ -f data/causal/switch_causal_profile.json ] && FILES="$FILES data/causal/switch_causal_profile.json"  # USER 2026-10-12 pilot priors (step4d)
for _pe in data/cat_avg/priority_evidence_CRYPTO_LONG.json data/cat_avg/priority_evidence_CRYPTO_SHORT.json data/cat_avg/priority_evidence_STOCKS_LONG.json data/cat_avg/priority_evidence_STOCKS_SHORT.json data/causal/switch_flip_study.json; do
  [ -f "$_pe" ] && FILES="$FILES $_pe"  # 2026-10-13 §94: pilot NONE-priority evidence (step 8b) + weekly flip study (step 8c)
done
FILES="$FILES $AGG SPREADSHEETS/v15_avg_delta_latest.xlsx SPREADSHEETS/v15_vector_delta_latest.xlsx"
FILES="$FILES data/reports/lifecycle_pilot/disabled_switches_never_pos_per_category.json data/cat_side_promotions.json data/avg_delta_round_ledger.json"
for F in $FILES; do [ -f "$F" ] || fail 6 "push file missing: $F"; done
md5sum $FILES | sort -k2 > "$WORK/push_local.md5"
CUR_STEP=6
for H in $TARGETS; do
  log "step6 push -> $H"
  $SSH "$H" "cd ~/binance-sandbox && mkdir -p backups/daily_chain_$DATE && for f in $FILES; do [ -f \$f ] && cp -p \$f backups/daily_chain_$DATE/\$(echo \$f | tr / _); done; true" || fail 6 "remote backup on $H"
  rsync -azR -e "$SSH" $FILES "$H:binance-sandbox/" || fail 6 "rsync to $H"
  $SSH "$H" "cd ~/binance-sandbox && md5sum $FILES | sort -k2" > "$WORK/push_$H.md5" || fail 6 "remote md5 on $H"
  diff -q "$WORK/push_local.md5" "$WORK/push_$H.md5" >/dev/null || { diff "$WORK/push_local.md5" "$WORK/push_$H.md5"; fail 6 "md5 mismatch on $H"; }
  rsync -az -e "$SSH" tools/v15_state_kv_sync.py "$H:binance-sandbox/tools/v15_state_kv_sync.py" || fail 6 "kv helper to $H"
  $SSH "$H" "cd ~/binance-sandbox && .venv/bin/python -u tools/v15_state_kv_sync.py --apply" || fail 6 "kv sync on $H"
  log "step6 $H md5 OK ($(wc -l < "$WORK/push_local.md5") files) + kv synced"
done
STEPS_OK="$STEPS_OK,6"

# 6b. tradeable check (USER 2026-10-06: every tradeable key must trade; <10 trades/30D or TIM<20% = SYSTEM_ERROR row) — non-fatal
CUR_STEP=6b
log "step6b tradeable check"
timeout 1800 "$PY" -u tools/v15_tradeable_check.py --out "$WORK/tradeable_check.json" --date "$DATE" && cp "$WORK/tradeable_check.json" "$STAMP_DIR/${DATE}_tradeable_check.json" || log "step6b tradeable check rc=$? (non-fatal, chain continues)"
STEPS_OK="$STEPS_OK,6b"

# 7. done-stamp + summary
CUR_STEP=7
"$PY" - "$DATE" "$AGG" "$WORK" "$T0" "$STEPS_OK" "$TARGETS" > "$STAMP_DIR/$DATE.json" <<'EOF' || fail 7 "summary json"
import json, sys, datetime, pathlib
date, agg, work, t0, steps, targets = sys.argv[1:7]
w = pathlib.Path(work)
main = json.loads((w / "template_main.json").read_text())
norm = json.loads((w / "template_norm.json").read_text())
cs = ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT")
md5 = dict(reversed(l.split(None, 1)) for l in (w / "push_local.md5").read_text().splitlines())
print(json.dumps({"date": date, "status": "DONE", "started": t0, "ended": datetime.datetime.utcnow().isoformat() + "Z", "steps_ok": steps, "agg": agg,
                  "promoted_keys": main.get("promoted_keys", []), "saved_promos": main.get("saved_promos", {}),
                  "promoted": {c: {"switch": main.get(c, {}).get("promoted_switch", []), "filter": main.get(c, {}).get("promoted_filter", []), "violations": main.get(c, {}).get("violations")} for c in cs},
                  "norm_promoted": {c: {"switch": len(norm.get(c, {}).get("promoted_switch", [])), "filter": len(norm.get(c, {}).get("promoted_filter", [])), "violations": norm.get(c, {}).get("violations")} for c in cs},
                  "floor_guard": {c: main.get(c, {}).get("floor_guard") for c in cs},
                  "floor_guard_refused": {c: (main.get(c, {}).get("floor_guard") or {}).get("refused_keys") for c in cs if (main.get(c, {}).get("floor_guard") or {}).get("refused_keys")},
                  "parity_sync": main.get("parity_sync"), "reports": {"main": str(w / "template_main.json"), "norm": str(w / "template_norm.json")},
                  "pushed_to": targets.split(), "pushed_md5": {k.strip(): v for k, v in md5.items()},
                  "tradeable_check": ({"report": str(w / "tradeable_check.json"), "summary": json.loads((w / "tradeable_check.json").read_text()).get("summary")} if (w / "tradeable_check.json").exists() else None)}, indent=1, default=str))
EOF
log "step7 done-stamp $STAMP_DIR/$DATE.json promoted_keys=$("$PY" -c "import json,sys;print(len(json.load(open(sys.argv[1]))['promoted_keys']))" "$STAMP_DIR/$DATE.json")"
rm -f "$STAMP_DIR/$DATE.FAILED.json"
CHAIN_STATUS=DONE
rm -f "$STAMP_DIR/$DATE.RUNNING"
log "=== done"

# 8. POST-STAMP ancillary self-improvement steps (2026-10-13, BIBLE §94). Today's stamp is final; nothing here can fail or
#    change it. Heavy engine work runs niced, single-flight, and the weekly part runs on the idlest host only.
CUR_STEP=8
pick_idle_worker() {  # prints the weekly host (V15_WEEKLY_HOSTS, default S1 itself + s2/s5/s6; small jobs only
  # (nice 15, <=4 workers)) with the lowest load1/nproc below V15_IDLE_MAX_RATIO (0.85), or nothing
  local best="" bestr=99 H R
  for H in ${V15_WEEKLY_HOSTS:-local niels@10.0.0.4 niels@10.0.0.5 niels@10.0.0.6}; do
    if [ "$H" = local ]; then R=$(awk -v n="$(nproc)" '{printf "%.3f", $1/n}' /proc/loadavg)
    else R=$($SSH "$H" "awk -v n=\$(nproc) '{printf \"%.3f\", \$1/n}' /proc/loadavg" 2>/dev/null) || continue; fi
    [ -n "$R" ] || continue
    if awk -v r="$R" -v b="$bestr" 'BEGIN{exit !(r<b)}'; then best=$H; bestr=$R; fi
  done
  awk -v r="$bestr" -v m="${V15_IDLE_MAX_RATIO:-0.85}" 'BEGIN{exit !(r<m)}' && echo "$best"
}
on_host() {  # on_host HOST CMD — run CMD in ~/binance-sandbox on S1 itself (local) or over ssh
  if [ "$1" = local ]; then (cd "$ROOT" && bash -c "$2"); else $SSH "$1" "cd ~/binance-sandbox && $2"; fi
}
# 8a. missed-trend revision round (§80/§81): BEST_EFFORT boards with TIM<20 -> real engine screen -> verified base installed
#     for the side's next cadence round (published sheets untouched). Max V15_MT_MAX (2) boards per day.
log "step8a missed-trend round"
timeout 3600 nice -n 15 "$PY" -u tools/v15_missed_trend_round.py 2>&1 | grep '^\[mt-round\]' | sed 's/^/[chain] step8a /' || log "step8a missed-trend round rc=$? (non-fatal)"
# 8b. priority evidence regen (pilot none_priority_evidence, stale since 10-07): finalized sheets -> data/cat_avg/priority_evidence_*.json,
#     then pushed md5-verified to the workers (tomorrow's step 6 pushes it again with the stamp).
log "step8b priority evidence regen (v15_cat_avg_matrix)"
mkdir -p data/cat_avg && for _f in data/cat_avg/priority_evidence_*.json; do [ -f "$_f" ] && cp -p "$_f" "backups/before_daily_chain_${DATE}_$(basename "$_f")"; done
if timeout 3600 nice -n 15 "$PY" -u tools/v15_cat_avg_matrix.py --workers "${V15_CATAVG_WORKERS:-4}" 2>&1 | tail -3 | sed 's/^/[chain] step8b /'; then
  _PE=$(ls data/cat_avg/priority_evidence_*.json 2>/dev/null | tr '\n' ' ')
  for H in $TARGETS; do
    [ -n "$_PE" ] || break
    rsync -azR -e "$SSH" $_PE "$H:binance-sandbox/" && L=$(md5sum $_PE | sort -k2 | md5sum | cut -c1-12) && R=$($SSH "$H" "cd ~/binance-sandbox && md5sum $_PE | sort -k2 | md5sum | cut -c1-12") && [ "$L" = "$R" ] && log "step8b $H priority evidence md5 OK ($L)" || log "step8b push $H failed/mismatch (non-fatal)"
  done
else
  log "step8b v15_cat_avg_matrix rc=$? (non-fatal; previous evidence kept)"
fi
# 8c. WEEKLY (Sunday UTC, or V15_CHAIN_WEEKLY=1): switch flip study (§87) + causal rescue ablation (§85, writes fix_outcomes)
#     on the idlest of S1/s2/s5/s6 (V15_WEEKLY_HOSTS; nice 15, <=4 workers), then the learner DB cap (V15_CAUSAL_DB_MAX_GB 2.0) on S1.
if [ "$(date -u +%u)" = 7 ] || [ "${V15_CHAIN_WEEKLY:-0}" = 1 ]; then
  W=$(pick_idle_worker)
  if [ -z "$W" ]; then
    log "step8c weekly: no idle worker (all >= ${V15_IDLE_MAX_RATIO:-0.85} load/core) — skipped this week"
  else
    log "step8c weekly on idle host $W: flip study + causal rescue (tools/v15_causal_rescue.py run -> ingest-outcomes)"
    if [ "$W" != local ]; then
      rsync -azR -e "$SSH" tools/v15_switch_flip_study.py tools/v15_causal_rescue.py data/causal_condemned.json data/causal/switch_causal_profile.json "$W:binance-sandbox/" 2>/dev/null || log "step8c code/data push to $W failed (continuing)"
    fi
    [ -f data/causal/switch_flip_study.json ] && cp -p data/causal/switch_flip_study.json "backups/before_daily_chain_${DATE}_switch_flip_study.json"
    on_host "$W" "V12_NPZ_CACHE=4 V12_ALLOW_STALE_NPZ=1 timeout 3000 nice -n 15 .venv/bin/python -u tools/v15_switch_flip_study.py --workers ${V15_FLIP_WORKERS:-4} --per-cat 15" > "$WORK/flip_study.log" 2>&1 || log "step8c flip study rc=$? on $W (non-fatal)"
    tail -2 "$WORK/flip_study.log" | sed "s/^/[chain] step8c flip /"
    [ "$W" = local ] || rsync -az -e "$SSH" "$W:binance-sandbox/data/causal/switch_flip_study.json" "$W:binance-sandbox/data/causal/switch_flip_study.md" data/causal/ 2>/dev/null || log "step8c flip pull failed (non-fatal)"
    on_host "$W" "nice -n 15 timeout 2700 .venv/bin/python -u tools/v15_causal_rescue.py run --workers 2 --bases-per-cat 4 --budget-s 2400 --out data/causal/rescue_outcomes.json" > "$WORK/rescue_run.log" 2>&1 || log "step8c rescue run rc=$? on $W (non-fatal)"
    tail -3 "$WORK/rescue_run.log" | sed "s/^/[chain] step8c rescue /"
    if [ "$W" = local ] || rsync -az -e "$SSH" "$W:binance-sandbox/data/causal/rescue_outcomes.json" "$W:binance-sandbox/data/causal/rescue_outcomes.md" data/causal/; then
      nice -n 15 "$PY" -u tools/v15_causal_rescue.py ingest-outcomes data/causal/rescue_outcomes.json 2>&1 | tail -2 | sed 's/^/[chain] step8c ingest /' || log "step8c rescue ingest failed (non-fatal)"
    fi
  fi
  nice -n 15 ionice -c3 timeout 1800 "$PY" -u tools/v15_causal_rescue.py cap --apply 2>&1 | tail -3 | sed 's/^/[chain] step8c dbcap /' || log "step8c learner DB cap rc=$? (non-fatal)"
fi
log "=== post-stamp steps done"
