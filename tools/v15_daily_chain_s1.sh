#!/bin/bash
# v15_daily_chain_s1 — the daily pre-market chain on S1 (BIBLE §68.3 steps 1+2, director design 2026-10-06).
# Replaces the Mac 05:00Z tools/v15_daily_selfimprove.sh (cron PATH had no `timeout` -> died every day since 10-04).
# Cron (S1, 12:15Z, director 2026-10-06; full run ~45 min):
#   15 12 * * * cd /home/niels/binance-sandbox && flock -n /tmp/v15_daily_chain.lock bash tools/v15_daily_chain_s1.sh >> /home/niels/logs/v15_daily_chain.log 2>&1 # V15_DAILY_CHAIN_S1
# Steps: 1 avg-delta rebuild (hosts as seen from S1) -> 2 the ONE template writer on SPREADSHEETS + TEMPLATE_FINAL_NORM
# (each cat_side's promotions pass the COMBINED trade-floor guard tools/v15_promotion_floor_guard.py before saving; refused keys keep the old bold)
# (code-surface sync DISABLED: S1 never edits config.py/config_tradier.py/v12_quick_engine.py; the Mac follow-up
# tools/v15_daily_chain_mac_apply.sh syncs report promoted_keys at 13:20Z) -> 3 cat_side_defaults_4 (+ sweep copy)
# -> 4 avg_delta_pos_sym.json -> 5 zero-delta watchdog (non-fatal) -> 6 push to s2/s5 + kv sync + md5 verify
# -> 6b tradeable check (non-fatal; SYSTEM_ERROR rows for tradeable keys with <10 trades/30D or TIM<20%) -> 7 done-stamp json.
# 4b (2026-10-07): cell-evidence regen (zero-skip maps) over ALL local progress dirs (campaign dirs move; glob, don't pin)
# + push data/avg_delta_pos_sym_cell.json + data/cell_evidence/*.json to s2/s5 so every host's pilots skip condemned cells.
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
log() { echo "[$(date -u +%FT%TZ)] [chain] $*"; }
fail() {
  log "FAILED at step $1: $2 — chain STOPPED (nothing after this step ran)"
  [ "$DRYRUN" = 1 ] || printf '{"date":"%s","status":"FAILED","failed_step":"%s","why":"%s","steps_ok":"%s","started":"%s","ended":"%s"}\n' "$DATE" "$1" "$2" "$STEPS_OK" "$T0" "$(date -u +%FT%TZ)" > "$STAMP_DIR/$DATE.FAILED.json"
  exit 1
}
log "=== start date=$DATE DRYRUN=$DRYRUN host=$(hostname) root=$ROOT hosts=$V15_FLEET_HOSTS targets=$TARGETS"
[ -x "$PY" ] || fail 0 "venv python missing: $PY"
[ -e SPREADSHEETS/TEMPLATES_FROZEN ] && fail 0 "SPREADSHEETS/TEMPLATES_FROZEN exists (user freeze)"

# 1. deltas
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

if [ "$DRYRUN" = 1 ]; then
  log "step3 cat_side_defaults_4 SKIPPED (DRYRUN)"
  log "step4 pos_sym json (DRYRUN -> $WORK/avg_delta_pos_sym.json)"
  timeout 300 "$PY" -u tools/v15_possym_json_from_agg.py "$AGG" "$WORK/avg_delta_pos_sym.json" || fail 4 "possym rc=$?"
  log "step4b cell evidence (DRYRUN -> $WORK/avg_delta_pos_sym_cell.json)"
  _pdirs=$(ls -d /home/niels/v15_*/ data/reports/lifecycle_pilot 2>/dev/null | tr '\n' ',' | sed 's/,$//')
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
log "step3 build_cat_side_defaults_4"
cp -p data/per_sym_settings.json "backups/before_daily_chain_${DATE}_per_sym_settings.json" 2>/dev/null
timeout 600 "$PY" -u tools/build_cat_side_defaults_4.py || fail 3 "build_cat_side_defaults_4 rc=$?"
mkdir -p data/sweep_defaults && cp data/per_sym_settings.json data/sweep_defaults/per_sym_settings.json || fail 3 "sweep copy"
"$PY" -u tools/v15_state_kv_sync.py --apply || fail 3 "kv sync on S1"
STEPS_OK="$STEPS_OK,3"

# 4. pos_sym sampler input
log "step4 v15_possym_json_from_agg"
timeout 300 "$PY" -u tools/v15_possym_json_from_agg.py "$AGG" data/avg_delta_pos_sym.json || fail 4 "possym rc=$?"
cp "$AGG" SPREADSHEETS/v15_avg_delta_latest.xlsx && cp "$AGG" SPREADSHEETS/v15_vector_delta_latest.xlsx || fail 4 "latest copies"
STEPS_OK="$STEPS_OK,4"

# 4b. cell-evidence regen (zero-skip runtime maps) + verify at least one cat_side map exists
log "step4b v15_cell_evidence"
_pdirs=$(ls -d /home/niels/v15_*/ data/reports/lifecycle_pilot 2>/dev/null | tr '\n' ',' | sed 's/,$//')
[ -n "$_pdirs" ] || fail 4b "no progress dirs found"
timeout 900 "$PY" -u tools/v15_cell_evidence.py "$_pdirs" data/avg_delta_pos_sym_cell.json || fail 4b "cell_evidence rc=$?"
ls data/cell_evidence/*.json >/dev/null 2>&1 || fail 4b "no data/cell_evidence/*.json written"
STEPS_OK="$STEPS_OK,4b"

# 5. zero-delta watchdog (non-fatal)
log "step5 zero-delta watchdog"
timeout 600 "$PY" -u tools/v15_zero_delta_watchdog.py --agg "$AGG" --apply || log "step5 watchdog rc=$? (non-fatal, chain continues)"
STEPS_OK="$STEPS_OK,5"

# 6. push to s2/s5 + kv sync + md5 verify
FILES="SPREADSHEETS/TEMPLATE_CRYPTO_LONG.xlsx SPREADSHEETS/TEMPLATE_CRYPTO_SHORT.xlsx SPREADSHEETS/TEMPLATE_STOCKS_LONG.xlsx SPREADSHEETS/TEMPLATE_STOCKS_SHORT.xlsx"
FILES="$FILES SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_CRYPTO_LONG.xlsx SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_CRYPTO_SHORT.xlsx SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_STOCKS_LONG.xlsx SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_STOCKS_SHORT.xlsx"
FILES="$FILES data/per_sym_settings.json data/sweep_defaults/per_sym_settings.json data/avg_delta_pos_sym.json"
FILES="$FILES data/avg_delta_pos_sym_cell.json data/cell_evidence/*.json"
FILES="$FILES $AGG SPREADSHEETS/v15_avg_delta_latest.xlsx SPREADSHEETS/v15_vector_delta_latest.xlsx"
FILES="$FILES data/reports/lifecycle_pilot/disabled_switches_never_pos_per_category.json data/cat_side_promotions.json data/avg_delta_round_ledger.json"
for F in $FILES; do [ -f "$F" ] || fail 6 "push file missing: $F"; done
md5sum $FILES | sort -k2 > "$WORK/push_local.md5"
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
log "step6b tradeable check"
timeout 1800 "$PY" -u tools/v15_tradeable_check.py --out "$WORK/tradeable_check.json" --date "$DATE" && cp "$WORK/tradeable_check.json" "$STAMP_DIR/${DATE}_tradeable_check.json" || log "step6b tradeable check rc=$? (non-fatal, chain continues)"
STEPS_OK="$STEPS_OK,6b"

# 7. done-stamp + summary
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
log "=== done"
