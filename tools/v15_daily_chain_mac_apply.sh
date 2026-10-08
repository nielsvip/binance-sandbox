#!/bin/bash
# v15_daily_chain_mac_apply — Mac follow-up of the S1 daily chain (BIBLE §68.3 step 2 code surfaces, director design 2026-10-06).
# Cron (Mac, 13:00Z after S1 12:15Z tools/v15_daily_chain_s1.sh, retry 20:00Z; step 0 no-ops a DONE day):
#   0 13 * * * PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin /bin/bash /Users/niels/Documents/binance/tools/v15_daily_chain_mac_apply.sh >> /tmp/v15_daily_chain_mac_apply.log 2>&1 # V15_DAILY_CHAIN_MAC_APPLY
#   0 20 * * * PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin /bin/bash /Users/niels/Documents/binance/tools/v15_daily_chain_mac_apply.sh >> /tmp/v15_daily_chain_mac_apply.log 2>&1 # V15_DAILY_CHAIN_MAC_RETRY
# 1 require S1's done-stamp data/daily_chain/<date>.json -> 2 pull the chain's state (8 templates, cat_side_defaults_4 + sweep copy,
# avg_delta_pos_sym.json, agg files, cat_side_promotions, round ledger, reports), md5-verified against the stamp, backups first,
# then kv sync (SQL-primary readers) -> 3 switch_parity sync-defaults for the stamp's promoted_keys ON THE MAC (backup, compile, md5)
# -> 4 push changed config.py/config_tradier.py/v12_quick_engine.py to S1/s2/s5 (sandbox + ~/binance) md5 + import verified
# -> 5 switch_parity gate for the 4 cat_sides on Mac/S1/s2/s5 (logged) -> 5d inf universe refresh (BIBLE §68.3 step 3b,
# top-15 30D gainers per side -> symbols_inf_long/short.json, non-fatal) -> 6 stamp data/daily_chain/<date>_mac_apply.json.
# Live processes pick the config up at their next restart — this script restarts NOTHING.
# DRYRUN=1: pull into /tmp only, report md5 differences + the sync PLAN (no --apply), no Mac file written, no push; gates still run (read-only).
set -u
set -o pipefail
export PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH
ROOT=/Users/niels/Documents/binance
cd "$ROOT" || exit 1
PY=$ROOT/.venv/bin/python
DATE=${CHAIN_DATE:-$(date -u +%Y%m%d)}
DRYRUN=${DRYRUN:-0}
S1=${V15_CHAIN_S1:-s1-pub}
HOSTS=${V15_CHAIN_HOSTS:-"s1-pub s2 s5 s6"}
SSH="ssh -o BatchMode=yes -o ConnectTimeout=10"
# 2026-10-08 USER (QuickConfig tandem lane): a late S1 chain (landed after the 20:00Z retry, e.g. 2026-10-07 landed 00:04Z
# next day) must still be applied at the next slot -> without CHAIN_DATE pick the NEWEST S1 DONE stamp (today, else
# yesterday) that has no Mac DONE stamp yet. Today-without-stamp still waits as before.
if [ -z "${CHAIN_DATE:-}" ]; then
  for _D in $(date -u +%Y%m%d) $(date -u -v-1d +%Y%m%d 2>/dev/null || date -u -d yesterday +%Y%m%d); do
    if $SSH "$S1" "grep -Eq '\"status\": ?\"DONE\"' ~/binance-sandbox/data/daily_chain/$_D.json" 2>/dev/null; then
      if ! grep -q '"status":"DONE"' "$ROOT/data/daily_chain/${_D}_mac_apply.json" 2>/dev/null; then DATE=$_D; fi
      break
    fi
  done
fi
CODE="config.py config_tradier.py v12_quick_engine.py"
CS4="CRYPTO_LONG CRYPTO_SHORT STOCKS_LONG STOCKS_SHORT"
TS=$(date -u +%Y%m%d%H%M)
STAMP=$ROOT/data/daily_chain/${DATE}_mac_apply.json
DEST=$ROOT
[ "$DRYRUN" = 1 ] && DEST=/tmp/v15_daily_chain_mac_dry_$DATE
mkdir -p "$ROOT/data/daily_chain" "$DEST" || exit 1
log() { echo "[$(date -u +%FT%TZ)] [mac-apply] $*"; }
fail() {
  log "FAILED at step $1: $2 — STOPPED"
  [ "$DRYRUN" = 1 ] || printf '{"date":"%s","status":"FAILED","failed_step":"%s","why":"%s","at":"%s"}\n' "$DATE" "$1" "$2" "$(date -u +%FT%TZ)" > "$STAMP"
  exit 1
}
md5f() { md5 -q "$1" 2>/dev/null || echo MISSING; }
log "=== start date=$DATE DRYRUN=$DRYRUN s1=$S1 hosts=$HOSTS dest=$DEST"
[ -x "$PY" ] || fail 0 "venv python missing"

# 0. idempotency (USER 2026-10-07: a 20:00Z retry slot re-runs this script; a DONE day is a no-op, a FAILED day retries)
if [ "$DRYRUN" != 1 ] && [ -s "$STAMP" ] && grep -q '"status":"DONE"' "$STAMP" 2>/dev/null; then
  log "already applied today ($STAMP DONE) — retry slot no-op, exit 0"
  exit 0
fi

# 1. S1 done-stamp
SJ=$DEST/data/daily_chain/$DATE.json
mkdir -p "$(dirname "$SJ")"
if [ "$DRYRUN" = 1 ] && [ -n "${CHAIN_TEST_REPORT:-}" ]; then
  # DRYRUN test only: no S1 --apply stamp exists yet -> build a TEST_FIXTURE stamp from S1's CURRENT file md5s + a template report's promoted keys
  log "step1 DRYRUN TEST_FIXTURE stamp from S1 current md5s + $CHAIN_TEST_REPORT"
  TAGG=SPREADSHEETS/v15_avg_delta/v15_avg_delta_$DATE.xlsx
  TF="SPREADSHEETS/TEMPLATE_CRYPTO_LONG.xlsx SPREADSHEETS/TEMPLATE_CRYPTO_SHORT.xlsx SPREADSHEETS/TEMPLATE_STOCKS_LONG.xlsx SPREADSHEETS/TEMPLATE_STOCKS_SHORT.xlsx SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_CRYPTO_LONG.xlsx SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_CRYPTO_SHORT.xlsx SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_STOCKS_LONG.xlsx SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_STOCKS_SHORT.xlsx data/per_sym_settings.json data/sweep_defaults/per_sym_settings.json data/avg_delta_pos_sym.json $TAGG SPREADSHEETS/v15_avg_delta_latest.xlsx SPREADSHEETS/v15_vector_delta_latest.xlsx data/cat_side_promotions.json data/avg_delta_round_ledger.json"
  $SSH "$S1" "cd ~/binance-sandbox && md5sum $TF" > "$DEST/s1_current.md5" || fail 1 "S1 md5sum (test fixture)"
  "$PY" - "$CHAIN_TEST_REPORT" "$DEST/s1_current.md5" "$DATE" "$TAGG" > "$SJ" <<'EOF' || fail 1 "test fixture stamp"
import json, sys
rep, m, date, agg = sys.argv[1:5]
r = json.load(open(rep))
keys = r.get("promoted_keys") or sorted({x[1] for c in ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT") for k in ("promoted_switch", "promoted_filter") for x in (r.get(c) or {}).get(k, [])})
md5 = {l.split(None, 1)[1].strip(): l.split()[0] for l in open(m) if l.strip()}
print(json.dumps({"date": date, "status": "DONE", "TEST_FIXTURE": "built by DRYRUN from S1 current md5s + " + rep, "agg": agg, "promoted_keys": keys, "pushed_md5": md5}, indent=1))
EOF
elif rm -f "$SJ"; W=0; until rsync -az -e "$SSH" "$S1:binance-sandbox/data/daily_chain/$DATE.json" "$SJ" 2>/dev/null; do
       $SSH "$S1" "test -f ~/binance-sandbox/data/daily_chain/$DATE.FAILED.json" 2>/dev/null && break
       [ $W -ge "${CHAIN_WAIT_MIN:-55}" ] && break
       [ $W -eq 0 ] && log "step1 S1 stamp not there yet — waiting up to ${CHAIN_WAIT_MIN:-55} min"
       sleep 60; W=$((W+1))
     done; [ -s "$SJ" ]; then
  log "step1 S1 stamp present (waited ${W} min)"
else
  $SSH "$S1" "cat ~/binance-sandbox/data/daily_chain/$DATE.FAILED.json" 2>/dev/null
  fail 1 "no S1 done-stamp data/daily_chain/$DATE.json (S1 chain did not finish)"
fi
"$PY" -c "import json,sys; d=json.load(open(sys.argv[1])); assert d.get('status')=='DONE', d.get('status'); print('[mac-apply] S1 stamp', d['date'], d['status'], 'agg', d['agg'], 'promoted_keys', len(d.get('promoted_keys') or []))" "$SJ" || fail 1 "S1 stamp not DONE"
AGG=$("$PY" -c "import json,sys; print(json.load(open(sys.argv[1]))['agg'])" "$SJ")
KEYS=$("$PY" -c "import json,sys; print(','.join(json.load(open(sys.argv[1])).get('promoted_keys') or []))" "$SJ")

# 2. pull state (md5 must equal what S1 pushed)
PULL="SPREADSHEETS/TEMPLATE_CRYPTO_LONG.xlsx SPREADSHEETS/TEMPLATE_CRYPTO_SHORT.xlsx SPREADSHEETS/TEMPLATE_STOCKS_LONG.xlsx SPREADSHEETS/TEMPLATE_STOCKS_SHORT.xlsx"
PULL="$PULL SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_CRYPTO_LONG.xlsx SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_CRYPTO_SHORT.xlsx SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_STOCKS_LONG.xlsx SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_STOCKS_SHORT.xlsx"
PULL="$PULL data/per_sym_settings.json data/sweep_defaults/per_sym_settings.json data/avg_delta_pos_sym.json"
PULL="$PULL data/avg_delta_pos_sym_cell.json"
PULL="$PULL $AGG SPREADSHEETS/v15_avg_delta_latest.xlsx SPREADSHEETS/v15_vector_delta_latest.xlsx data/cat_side_promotions.json data/avg_delta_round_ledger.json"
if [ "$DRYRUN" != 1 ]; then
  mkdir -p "backups/daily_chain_mac_$TS"
  for F in $PULL; do [ -f "$F" ] && cp -p "$F" "backups/daily_chain_mac_$TS/$(echo "$F" | tr / _)"; done
  log "step2 backups -> backups/daily_chain_mac_$TS"
fi
log "step2 pull $(echo $PULL | wc -w | tr -d ' ') files from $S1 -> $DEST"
for F in $PULL; do
  mkdir -p "$(dirname "$DEST/$F")" && rsync -az -e "$SSH" "$S1:binance-sandbox/$F" "$DEST/$F" || fail 2 "pull $F from $S1"
done
# 2b. cell-evidence runtime maps (zero-skip): whole dir, each file md5-verified against the stamp (2026-10-07)
if [ "$DRYRUN" != 1 ]; then
  mkdir -p "$DEST/data/cell_evidence" "backups/daily_chain_mac_$TS/cell_evidence"
  for CF in "$DEST"/data/cell_evidence/*.json; do [ -f "$CF" ] && cp -p "$CF" "backups/daily_chain_mac_$TS/cell_evidence/"; done
fi
rsync -az --delete -e "$SSH" "$S1:binance-sandbox/data/cell_evidence/" "$DEST/data/cell_evidence/" || fail 2 "pull cell_evidence"
for CF in "$DEST"/data/cell_evidence/*.json; do
  [ -f "$CF" ] || continue
  F="data/cell_evidence/$(basename "$CF")"
  WANT=$("$PY" -c "import json,sys; print(json.load(open(sys.argv[1]))['pushed_md5'].get(sys.argv[2],'NOT_IN_STAMP'))" "$SJ" "$F")
  GOT=$(md5f "$CF")
  [ "$WANT" = "$GOT" ] || fail 2 "md5 mismatch $F stamp=$WANT pulled=$GOT"
  log "step2b cell_evidence $F s1=$GOT"
done
if [ "$DRYRUN" = 1 ] && [ -n "${CHAIN_TEST_REPORT:-}" ]; then
  log "step2 TEST_FIXTURE: no S1 work_$DATE reports to pull (skipped)"
else
  rsync -az -e "$SSH" "$S1:binance-sandbox/data/daily_chain/work_$DATE/" "$DEST/data/daily_chain/work_$DATE/" || fail 2 "pull reports"
fi
NCHG=0
for F in $PULL; do
  WANT=$("$PY" -c "import json,sys; print(json.load(open(sys.argv[1]))['pushed_md5'].get(sys.argv[2],'NOT_IN_STAMP'))" "$SJ" "$F")
  GOT=$(md5f "$DEST/$F")
  [ "$WANT" = "$GOT" ] || fail 2 "md5 mismatch $F stamp=$WANT pulled=$GOT"
  OLD=$(md5f "$ROOT/backups/daily_chain_mac_$TS/$(echo "$F" | tr / _)")
  [ "$DRYRUN" = 1 ] && OLD=$(md5f "$ROOT/$F")
  [ "$OLD" = "$GOT" ] && S=same || { S=CHANGED; NCHG=$((NCHG+1)); }
  log "step2 $S $F mac_before=$OLD s1=$GOT"
done
if [ "$DRYRUN" = 1 ]; then
  log "step2 DRYRUN: $NCHG of the pulled files differ from the Mac copies (Mac files NOT replaced); kv sync check only"
  "$PY" -u tools/v15_state_kv_sync.py
else
  "$PY" -u tools/v15_state_kv_sync.py --apply || fail 2 "kv sync on Mac"
fi

# 3. code-surface sync on the Mac for the promoted keys
for F in $CODE; do eval "BEFORE_${F//[^A-Za-z0-9]/_}=$(md5f "$F")"; done
if [ -z "$KEYS" ]; then
  log "step3 no promoted keys in the S1 stamp -> no code-surface sync"
elif [ "$DRYRUN" = 1 ]; then
  log "step3 DRYRUN sync-defaults PLAN for $(echo "$KEYS" | tr ',' '\n' | wc -l | tr -d ' ') keys (against the Mac templates)"
  "$PY" -u switch_parity.py sync-defaults --keys "$KEYS" > "$DEST/sync_plan.json"; RC=$?
  "$PY" -c "import json,sys; d=json.load(open(sys.argv[1])); print('[mac-apply] plan: planned', len(d.get('planned',[])), 'needs_manual', len(d.get('needs_manual',[])), 'error', repr(d.get('error'))); [print('   PLAN', p) for p in d.get('planned',[])[:20]]; [print('   MANUAL', p) for p in d.get('needs_manual',[])[:10]]" "$DEST/sync_plan.json"
  log "step3 plan rc=$RC (full plan $DEST/sync_plan.json)"
else
  for F in $CODE; do cp -p "$F" "backups/before_daily_chain_sync_${TS}_$F"; done
  log "step3 sync-defaults --apply for keys: $KEYS"
  "$PY" -u switch_parity.py sync-defaults --keys "$KEYS" --apply --confirm-unlocked > "data/daily_chain/${DATE}_sync_defaults.json"; RC=$?
  "$PY" -c "import json,sys; d=json.load(open(sys.argv[1])); print('[mac-apply] sync: applied', d.get('applied'), 'planned', len(d.get('planned',[])), 'needs_manual', len(d.get('needs_manual',[])), 'error', repr(d.get('error')))" "data/daily_chain/${DATE}_sync_defaults.json"
  [ $RC -eq 0 ] || fail 3 "sync-defaults rc=$RC (backups backups/before_daily_chain_sync_${TS}_*)"
  for F in $CODE; do "$PY" -c "import py_compile,sys; py_compile.compile(sys.argv[1], doraise=True)" "$F" || fail 3 "compile $F after sync (backup backups/before_daily_chain_sync_${TS}_$F)"; done
  "$PY" -c "import v12_quick_engine" || fail 3 "import v12_quick_engine after sync"
fi
CHANGED=""
for F in $CODE; do
  V="BEFORE_${F//[^A-Za-z0-9]/_}"
  NOW=$(md5f "$F")
  [ "${!V}" = "$NOW" ] || CHANGED="$CHANGED $F"
  log "step3 $F md5 before=${!V} now=$NOW"
done

# 4. push changed code to the fleet
if [ -z "$CHANGED" ]; then
  log "step4 no code file changed -> no push"
elif [ "$DRYRUN" = 1 ]; then
  log "step4 DRYRUN: would push$CHANGED"
else
  for H in $HOSTS; do
    for D in binance-sandbox binance; do
      $SSH "$H" "test -d ~/$D" || continue
      $SSH "$H" "cd ~/$D && mkdir -p backups && for f in $CHANGED; do [ -f \$f ] && cp -p \$f backups/before_daily_chain_sync_${TS}_\$f; done; true" || fail 4 "remote backup $H:$D"
      rsync -az -e "$SSH" $CHANGED "$H:$D/" || fail 4 "rsync code to $H:$D"
      for F in $CHANGED; do
        R=$($SSH "$H" "md5sum ~/$D/$F" | cut -d' ' -f1)
        [ "$R" = "$(md5f "$F")" ] || fail 4 "md5 mismatch $H:$D/$F"
      done
      log "step4 $H:~/$D md5 OK:$CHANGED"
    done
    $SSH "$H" "cd ~/binance-sandbox && .venv/bin/python -c 'import v12_quick_engine, config, config_tradier'" || fail 4 "import check on $H"
  done
fi

# 5. gates (read-only)
GATES=""
for H in mac $HOSTS; do
  for C in $CS4; do
    if [ "$H" = mac ]; then OUT=$("$PY" switch_parity.py gate --cat-side "$C" 2>&1); RC=$?
    else OUT=$($SSH "$H" "cd ~/binance-sandbox && .venv/bin/python switch_parity.py gate --cat-side $C" 2>&1); RC=$?; fi
    SUM=$(printf '%s' "$OUT" | "$PY" -c "import json,sys
t=sys.stdin.read()
try:
    d=json.loads(t[t.index('{'):])
    print('ok=%s hard=%d warn=%d' % (d.get('ok'), len(d.get('hard') or []), len(d.get('warnings') or [])), ('first_hard=%s' % str((d.get('hard') or [''])[0])[:160]) if d.get('hard') else '')
except Exception as e:
    print('unparsed:', t[-200:].replace(chr(10),' '))")
    log "step5 gate $H $C rc=$RC $SUM"
    GATES="$GATES $H:$C:$RC"
  done
done

# 5b. STEP 3 per-sym go-live (USER 2026-10-06: register EVERY finished set, diff-only; risk carried by SIZE) + 5c size tiers.
#     --apply only when the live files carry PERF_TIER_SIZING (tools/apply_perf_tier_patch.py) with the switch True: without it
#     ez/tradier NEG-block every acc_gain<=0 book entry, so registering negative sets would stop them trading. Non-fatal.
SEL=$DEST/data/avg_delta_selection.json
if rsync -az -e "$SSH" "$S1:binance-sandbox/data/avg_delta_selection.json" "$SEL.s1" 2>/dev/null; then
  [ "$DRYRUN" = 1 ] || { [ -f "$SEL" ] && cp -p "$SEL" "backups/daily_chain_mac_$TS/data_avg_delta_selection.json"; }
  mv "$SEL.s1" "$SEL"
else
  log "step5b WARN: could not pull S1 avg_delta_selection.json -> using $SEL as is"
  [ -f "$SEL" ] || SEL=$ROOT/data/avg_delta_selection.json
fi
GOUT=$ROOT/data/daily_chain/persym_golive_$DATE.json
[ "$DRYRUN" = 1 ] && GOUT=$DEST/persym_golive_$DATE.json
GAPPLY=""
if [ "$DRYRUN" != 1 ] && grep -q "PERF_TIER_SIZING" ez_manage.py && grep -q "PERF_TIER_SIZING" tradier_manage.py && "$PY" -c "import config,config_tradier,sys; sys.exit(0 if config.Config().PERF_TIER_SIZING_ENABLED and config_tradier.TradierConfig().PERF_TIER_SIZING_ENABLED else 1)" 2>/dev/null; then
  GAPPLY="--apply"
else
  log "step5b go-live stays DRY-RUN (DRYRUN=$DRYRUN or PERF_TIER_SIZING not live in ez_manage/tradier_manage/config)"
fi
log "step5b per-sym go-live --all-finished ${GAPPLY:-DRY-RUN} (selection $SEL -> $GOUT)"
timeout 2400 "$PY" -u tools/v15_persym_golive.py --all-finished $GAPPLY --selection "$SEL" --out "$GOUT" --date "$DATE" 2>&1 | grep -v "Warning\|Loaded 1 symbols\|  PASS" || log "step5b per-sym go-live rc=$? (non-fatal)"
TOUT=$ROOT/data/persym_size_tiers.json
[ "$DRYRUN" = 1 ] && TOUT=$DEST/persym_size_tiers.json
if [ -s "$GOUT" ]; then
  log "step5c size tiers -> $TOUT"
  "$PY" -u tools/v15_persym_size_tiers.py --report "$GOUT" --out "$TOUT" || log "step5c size tiers rc=$? (non-fatal; live keeps the previous tiers file)"
else
  log "step5c size tiers SKIPPED (no go-live report)"
fi

# 5d. STEP 3b inf universe (USER 2026-10-08): refresh symbols_inf_long/short.json with the top-15 30D gainers
#     per side from this day's backtest, right after the per-sym go-live + size tiers. Non-fatal: a refused
#     refresh keeps yesterday's books (still tradeable via PERSIST_INF); the 15:05Z V15_INF_UNIVERSE_APPLY
#     cron is the backstop. DRYRUN: skipped (fetch health is covered by the cron's own runs).
if [ "$DRYRUN" = 1 ]; then
  INF_RES="skipped-dryrun"
  INF_APPLIED="dryrun"
  log "step5d inf universe SKIPPED (DRYRUN)"
else
  ILOG=$ROOT/data/daily_chain/inf_universe_$DATE.log
  log "step5d inf universe --apply --top 15 (log $ILOG)"
  if timeout 1500 "$PY" -u tools/v15_daily_inf_universe.py --apply --top 15 --report-tag chain_$DATE >"$ILOG" 2>&1; then
    INF_RES="rc=0"
  else
    INF_RES="rc=$? (non-fatal)"
  fi
  INF_APPLIED=$("$PY" -c "import json,glob; fs=sorted(glob.glob('data/inf_universe/*_chain_$DATE.json')); print(json.load(open(fs[-1])).get('applied') if fs else 'no-report')" 2>/dev/null || echo unknown)
  tail -n 3 "$ILOG" | sed 's/^/[mac-apply] step5d /'
  log "step5d inf universe $INF_RES applied=$INF_APPLIED"
fi

# 6. stamp
if [ "$DRYRUN" != 1 ]; then
  printf '{"date":"%s","status":"DONE","at":"%s","promoted_keys":"%s","code_changed":"%s","gates":"%s","inf_universe":"%s applied=%s","backups":"backups/daily_chain_mac_%s"}\n' "$DATE" "$(date -u +%FT%TZ)" "$KEYS" "${CHANGED# }" "${GATES# }" "$INF_RES" "$INF_APPLIED" "$TS" > "$STAMP"
  log "step6 stamp $STAMP"
fi
log "=== done (DRYRUN=$DRYRUN)"
