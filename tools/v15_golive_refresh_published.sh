#!/bin/bash
# V15_GOLIVE_REFRESH (Mac cron 05:20/11:20/19:20Z) — USER 2026-10-13/14: live trades EXACTLY the latest published board.
# Same go-live as chain step 5b (tools/v15_daily_chain_mac_apply.sh): S1 builds data/published_selection.json from the latest
# published boards -> pulled -> v15_persym_golive.py --published --pin-all. Replaces the old refresh that applied
# data/avg_delta_selection.json (--all-finished) and overwrote the published go-live 3x/day (10-14 12:03Z: 194 sets from the old
# selection over the 235 published boards). A failed published build applies NOTHING (never falls back to the old selection).
ROOT=/Users/niels/Documents/binance
cd "$ROOT" || exit 1
PY=$ROOT/.venv/bin/python
S1=${V15_S1:-s1-pub}
SSH="ssh -o BatchMode=yes -o ConnectTimeout=10"
D=$(date -u +%Y%m%d)
PSEL=$ROOT/data/published_selection.json
log() { echo "[$(date -u +%FT%TZ)] [golive-refresh] $*"; }
GAPPLY=""
grep -q "PERF_TIER_SIZING" ez_manage.py && grep -q "PERF_TIER_SIZING" tradier_manage.py && "$PY" -c "import config,config_tradier,sys; sys.exit(0 if config.Config().PERF_TIER_SIZING_ENABLED and config_tradier.TradierConfig().PERF_TIER_SIZING_ENABLED else 1)" 2>/dev/null && GAPPLY="--apply"
log "=== start ${GAPPLY:-DRY-RUN}"
if $SSH "$S1" "cd ~/binance-sandbox && rm -f data/published_selection.json && .venv/bin/python -u tools/v15_published_selection.py --out data/published_selection.json" 2>&1 | tail -1 \
  && rsync -az -e "$SSH" "$S1:binance-sandbox/data/published_selection.json" "$PSEL.s1" && [ -s "$PSEL.s1" ]; then
  mv "$PSEL.s1" "$PSEL"
  GOUT=$ROOT/data/daily_chain/persym_golive_${D}_refresh_$(date -u +%H%M).json
  log "published --pin-all ${GAPPLY:-DRY-RUN} -> $GOUT"
  /opt/homebrew/bin/timeout 5400 "$PY" -u tools/v15_persym_golive.py --published "$PSEL" --pin-all $GAPPLY --out "$GOUT" --date "$D" 2>&1 | grep -v "Warning\|Loaded 1 symbols\|  PASS"
  [ -s "$GOUT" ] && "$PY" -u tools/v15_persym_size_tiers.py --report "$GOUT" --out data/persym_size_tiers.json
else
  rm -f "$PSEL.s1"
  log "FAILED: published selection build/pull on $S1 — nothing applied (the old selection is never used)"
  exit 1
fi
log "=== done"
