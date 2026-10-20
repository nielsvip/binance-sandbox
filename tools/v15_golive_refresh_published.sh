#!/bin/bash
# V15_GOLIVE_REFRESH (Mac cron 05:20/11:20/19:20Z) — USER 2026-10-13/14: live trades EXACTLY the latest published board.
# Same go-live as chain step 5b (tools/v15_daily_chain_mac_apply.sh): S1 builds data/published_selection.json from the latest
# published boards -> pulled -> v15_persym_golive.py --published --pin-all. Replaces the old refresh that applied
# data/avg_delta_selection.json (--all-finished) and overwrote the published go-live 3x/day (10-14 12:03Z: 194 sets from the old
# selection over the 235 published boards). A failed published build applies NOTHING (never falls back to the old selection).
ROOT=/Users/niels/Documents/binance
cd "$ROOT" || exit 1
# 2026-10-15: single-flight. The schedule went hourly (:50) the same day, and a run that outlasts the hour met the next
# one inside the same store: two appliers were writing per_sym_active + the 56/59 MB JSON books at 04:50-05:11Z. flock
# makes a late run skip its slot instead of doubling up (the next hour re-applies whatever it missed).
# macOS has no flock(1); /usr/bin/shlock is the system's pid-aware lock (stale locks self-clear when the pid is gone).
LOCK=/tmp/v15_golive_refresh.lock
if ! /usr/bin/shlock -f "$LOCK" -p $$; then
  echo "[$(date -u +%FT%TZ)] [golive-refresh] another run holds $LOCK (pid $(cat "$LOCK" 2>/dev/null)) — skipping this slot"
  exit 0
fi
trap 'rm -f "$LOCK"' EXIT INT TERM
PY=$(bash "$ROOT/tools/pyinterp.sh")
S1=${V15_S1:-s1-pub}
SSH="ssh -o BatchMode=yes -o ConnectTimeout=10"
D=$(date -u +%Y%m%d)
PSEL=$ROOT/data/published_selection.json
log() { echo "[$(date -u +%FT%TZ)] [golive-refresh] $*"; }
GAPPLY=""
grep -q "PERF_TIER_SIZING" ez_manage.py && grep -q "PERF_TIER_SIZING" tradier_manage.py && "$PY" -c "import config,config_tradier,sys; sys.exit(0 if config.Config().PERF_TIER_SIZING_ENABLED and config_tradier.TradierConfig().PERF_TIER_SIZING_ENABLED else 1)" 2>/dev/null && GAPPLY="--apply"
log "=== start ${GAPPLY:-DRY-RUN}"
if $SSH "$S1" "cd ~/binance-sandbox && rm -f data/published_selection.json && /home/niels/.conda/envs/binance_env_mac/bin/python -u tools/v15_published_selection.py --out data/published_selection.json" 2>&1 | tail -1 \
  && rsync -az -e "$SSH" "$S1:binance-sandbox/data/published_selection.json" "$PSEL.s1" && [ -s "$PSEL.s1" ]; then
  mv "$PSEL.s1" "$PSEL"
  # USER 2026-10-20. Two things were wrong here and they compounded.
  # (1) `timeout 5400` on the WHOLE selection. v15_persym_golive has no resume, no ordering and no limit, so every
  #     hour re-applied the same 474 rows from the start at ~11 s/row and was SIGKILLed at 90 min before writing
  #     --out. All six runs before the 2026-10-20 open hit it (00:50:22->02:20:33, 02:50:26->04:20:35,
  #     04:50:20->06:20:36, 06:50:21->08:20:37, 08:50:19->10:20:33, 10:50:19->12:20:35, every one 5409-5416 s), so no
  #     persym_golive_20261020_* report existed, the size tiers stayed stamped 10-19 22:34, and the live set was left
  #     two-thirds applied: 41 of 474 boards resolved exactly in per_sym_store, 159 differed materially.
  # (2) NOTHING SAID SO. The only completion signal anyone read was this script's own "=== done", which a timeout
  #     also prints. A silent partial apply is the worst of both: live trades a mix of boards and nobody knows.
  # So: apply the DELTA (tools/golive_delta_selection.py — the sym_sides whose live row does not match their
  # published board, newest publish first), which converges instead of restarting, and budget it to stop before the
  # bell rather than after a fixed hour and a half. The delta IS the whole selection when nothing is live yet, so no
  # board is ever skipped; V15_GOLIVE_FULL=1 forces a full pass. A missing report is now an ALERT line.
  SRC=$PSEL
  if [ "${V15_GOLIVE_FULL:-0}" != "1" ]; then
    DELTA=$ROOT/data/published_selection_delta.json
    if "$PY" -u tools/golive_delta_selection.py --selection "$PSEL" --out "$DELTA" 2>&1 | tail -1; then
      SRC=$DELTA
    else
      log "delta build failed — falling back to the full selection"
    fi
  fi
  NEED=$("$PY" -c "import json,sys; print(json.load(open(sys.argv[1]))['_meta'].get('n') or len(json.load(open(sys.argv[1]))['sym_sides']))" "$SRC" 2>/dev/null || echo "?")
  if [ "$NEED" = "0" ]; then
    log "live set already covers every published board — nothing to apply"
  else
    BUDGET=$("$PY" - <<'PYEOF'
import os, sys
sys.path.insert(0, "/Users/niels/Documents/binance/tools")
cap = float(os.environ.get("V15_GOLIVE_TIMEOUT_S", 5400))
floor = float(os.environ.get("V15_GOLIVE_MIN_S", 1800))
margin = float(os.environ.get("V15_GOLIVE_MARGIN_S", 900))
try:
    import live_failover_common as C
    session = C.nyse_session()
    left = None if not session else session[0] - margin - C.now_s()
except Exception:
    left = None
print(int(cap if left is None else max(floor, min(cap, left)) if left > 0 else floor))
PYEOF
)
    GOUT=$ROOT/data/daily_chain/persym_golive_${D}_refresh_$(date -u +%H%M).json
    log "published($(basename "$SRC")) n=$NEED --pin-all ${GAPPLY:-DRY-RUN} budget=${BUDGET}s -> $GOUT"
    T0=$(date +%s)
    /opt/homebrew/bin/timeout "$BUDGET" "$PY" -u tools/v15_persym_golive.py --published "$SRC" --pin-all $GAPPLY --out "$GOUT" --date "$D" 2>&1 | grep -v "Warning\|Loaded 1 symbols\|  PASS"
    RC=${PIPESTATUS[0]}
    log "applier rc=$RC elapsed=$(( $(date +%s) - T0 ))s of ${BUDGET}s"
    if [ -s "$GOUT" ]; then
      "$PY" -u tools/v15_persym_size_tiers.py --report "$GOUT" --out data/persym_size_tiers.json
    else
      log "ALERT APPLY_INCOMPLETE: no report at $GOUT (rc=$RC) — the live set is PARTIAL and the size tiers were NOT rebuilt"
    fi
  fi
else
  rm -f "$PSEL.s1"
  log "FAILED: published selection build/pull on $S1 — nothing applied (the old selection is never used)"
  exit 1
fi
log "=== done"
