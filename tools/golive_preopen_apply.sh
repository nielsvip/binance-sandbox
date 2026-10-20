#!/bin/bash
# GOLIVE PREOPEN APPLY — USER 2026-10-20: "the fleet scheduler has to run and always recalculate TEMPLATE_*
# and all defaults before market open ... if mac is off it just syncs the new templates and defaults as soon
# as it comes back on and before it starts trading old data".
#
# WHY THIS EXISTS. tools/v15_golive_refresh_published.sh (hourly :50) wraps tools/v15_persym_golive.py in
# `timeout 5400`. On 2026-10-20 all six runs before the open hit that timeout (00:50:22->02:20:33,
# 02:50:26->04:20:35, 04:50:20->06:20:36, 06:50:21->08:20:37, 08:50:19->10:20:33, 10:50:19->12:20:35, every
# one 5409-5416 s), so the applier was killed before writing --out: no persym_golive_20261020_* report, size
# tiers still stamped 10-19 22:34, and the live set only partly applied. Measured at 14:1xZ: of 474 published
# boards 41 resolved exactly in per_sym_store, 271 differed only by documented strippings, 159 differed
# MATERIALLY and 3 had no live row. The market opened on that.
#
# WHAT IT DOES DIFFERENTLY
#   1. same lock as the hourly refresh, so the two can never apply at once;
#   2. rebuilds + pulls data/published_selection.json from S1 (identical commands to the hourly refresh —
#      a failed build applies NOTHING and the old selection is never reused);
#   3. applies the DELTA only (tools/golive_delta_selection.py: the sym_sides whose live row does not match
#      their published board, newest first). 162 rows instead of 474 at ~11 s/row fits the window that 474
#      never did;
#   4. budgets the run to end before the open instead of a fixed 5400 s, and FAILS LOUDLY when the report
#      was not written — the signal 2026-10-20 did not have;
#   5. verifies with tools/preopen_readiness_gate.py and leaves the verdict in
#      data/reports/preopen_readiness.json for tools/live_failover_stability_pin.py to read.
# The hourly full apply keeps running unchanged as the backstop. Nothing here touches the applier itself.
set -u
ROOT=/Users/niels/Documents/binance
cd "$ROOT" || exit 1
LOCK=/tmp/v15_golive_refresh.lock
PY=$(bash "$ROOT/tools/pyinterp.sh")
S1=${V15_S1:-s1-pub}
SSH="ssh -o BatchMode=yes -o ConnectTimeout=10"
D=$(date -u +%Y%m%d)
PSEL=$ROOT/data/published_selection.json
DELTA=$ROOT/data/published_selection_delta.json
MAX_S=${GOLIVE_PREOPEN_MAX_S:-14400}
MIN_S=${GOLIVE_PREOPEN_MIN_S:-900}
MARGIN_S=${GOLIVE_PREOPEN_MARGIN_S:-1200}
log() { echo "[$(date -u +%FT%TZ)] [golive-preopen] $*"; }
if ! /usr/bin/shlock -f "$LOCK" -p $$; then
  log "another go-live run holds $LOCK (pid $(cat "$LOCK" 2>/dev/null)) — skipping this slot"
  exit 0
fi
trap 'rm -f "$LOCK"' EXIT INT TERM
# Budget: before the open, whatever is left until MARGIN_S before it (clamped to [MIN_S, MAX_S]) so the apply
# lands BEFORE the bell; market closed, MAX_S, because then the only goal is to finish; session already open
# (the Mac-came-back catch-up case), INTRADAY_MAX_S — a 162-row delta runs ~31 min at the measured ~11 s/row,
# and an unbounded apply rewriting the live store mid-session is its own risk.
BUDGET=$("$PY" - <<'EOF'
import os, sys
sys.path.insert(0, "/Users/niels/Documents/binance/tools")
import live_failover_common as C
max_s = float(os.environ.get("GOLIVE_PREOPEN_MAX_S", 14400))
min_s = float(os.environ.get("GOLIVE_PREOPEN_MIN_S", 900))
margin = float(os.environ.get("GOLIVE_PREOPEN_MARGIN_S", 1200))
intraday = float(os.environ.get("GOLIVE_PREOPEN_INTRADAY_MAX_S", 3600))
session = C.nyse_session()
if not session:
    print(int(max_s))
else:
    left = session[0] - margin - C.now_s()
    print(int(max(min_s, min(max_s, left)) if left > 0 else intraday))
EOF
)
GAPPLY=""
grep -q "PERF_TIER_SIZING" ez_manage.py && grep -q "PERF_TIER_SIZING" tradier_manage.py && "$PY" -c "import config,config_tradier,sys; sys.exit(0 if config.Config().PERF_TIER_SIZING_ENABLED and config_tradier.TradierConfig().PERF_TIER_SIZING_ENABLED else 1)" 2>/dev/null && GAPPLY="--apply"
log "=== start ${GAPPLY:-DRY-RUN} budget=${BUDGET}s"
if $SSH "$S1" "cd ~/binance-sandbox && rm -f data/published_selection.json && /home/niels/.conda/envs/binance_env_mac/bin/python -u tools/v15_published_selection.py --out data/published_selection.json" 2>&1 | tail -1 \
  && rsync -az -e "$SSH" "$S1:binance-sandbox/data/published_selection.json" "$PSEL.s1" && [ -s "$PSEL.s1" ]; then
  mv "$PSEL.s1" "$PSEL"
else
  rm -f "$PSEL.s1"
  log "FAILED: published selection build/pull on $S1 — nothing applied (the old selection is never used)"
  exit 1
fi
DMETA=$("$PY" -u tools/golive_delta_selection.py --selection "$PSEL" --out "$DELTA" 2>&1 | tail -1)
log "$DMETA"
NEED=$("$PY" -c "import json,sys; print(json.load(open('$DELTA'))['_meta']['n'])" 2>/dev/null || echo 0)
if [ "${NEED:-0}" = "0" ]; then
  log "live set already covers every published board — nothing to apply"
else
  GOUT=$ROOT/data/daily_chain/persym_golive_${D}_preopen_$(date -u +%H%M).json
  log "delta ${NEED} sym_sides --pin-all ${GAPPLY:-DRY-RUN} -> $GOUT"
  START=$(date +%s)
  /opt/homebrew/bin/timeout "$BUDGET" "$PY" -u tools/v15_persym_golive.py --published "$DELTA" --pin-all $GAPPLY --out "$GOUT" --date "$D" 2>&1 | grep -v "Warning\|Loaded 1 symbols\|  PASS"
  RC=${PIPESTATUS[0]}
  log "applier rc=$RC elapsed=$(( $(date +%s) - START ))s"
  if [ -s "$GOUT" ]; then
    "$PY" -u tools/v15_persym_size_tiers.py --report "$GOUT" --out data/persym_size_tiers.json 2>&1 | tail -2
  else
    log "ALERT APPLY_INCOMPLETE: no report at $GOUT (rc=$RC) — the live set is PARTIAL and the size tiers were NOT rebuilt"
  fi
fi
"$PY" -u tools/preopen_readiness_gate.py --host "${PREOPEN_S1_HOST:-$S1}" 2>&1 | tail -12
log "=== done verdict=$("$PY" -c "import json; print(json.load(open('$ROOT/data/reports/preopen_readiness.json'))['verdict'])" 2>/dev/null || echo UNKNOWN)"
