#!/bin/bash
# v17_daily_learn — DAILY LEARN LOOP (BIBLE §99, USER 2026-10-13). Mac cron, once a day after the S1 12:15Z chain + 13:00Z Mac apply:
#   40 13 * * * /bin/bash /Users/niels/Documents/binance/tools/v17_daily_learn.sh >> /tmp/v17_daily_learn.log 2>&1 # V17_DAILY_LEARN
# 1 S1 leg (ssh s1-int, nice): fleet progress JSONs S1 + s2 (10.0.0.4) + combo library + A/B reports -> bt_leg_<date>.json, rsync back
# 2 Mac leg: live ledgers + klines -> live_leg_<date>.json; packet (<=30 KB) + valid keys
# 3 ONE headless LLM call: claude -p, NO tools (--tools ""), budget-capped, cwd outside the repo (no CLAUDE.md/memory load),
#   reads only the packet + valid-key reference from stdin; output parsed + validated by tools/v17_daily_learn_prep.py (ingest):
#   invalid keys/values dropped (template candidates + tools/switch_types.coerce), cost -> data/learn/cost_log.jsonl
# 4 insights_<date>.json -> insights_latest.json -> S1 + s2 ~/binance-sandbox/data/learn/ (md5 verified)
# The LLM can write NOTHING (no tools); only this script writes, and only under data/learn/. Never touches code/config/positions.
# Env: V17_LEARN_BUDGET_USD (default 3), V17_LEARN_MODEL (default claude-sonnet-5-5), V17_LEARN_SKIP_LLM=1 (prep only),
#      V17_LEARN_FORCE=1 (rerun a day that already has insights).
set -u
set -o pipefail
export PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH
ROOT=/Users/niels/Documents/binance
cd "$ROOT" || exit 1
if [ "${V17_LEARN_LOCKED:-0}" != "1" ]; then
  export V17_LEARN_LOCKED=1
  exec /usr/bin/lockf -s -t 0 /tmp/v17_daily_learn.lock /bin/bash "$0" "$@"
fi
PY=$ROOT/.venv/bin/python
CLAUDE=${CLAUDE_BIN:-/Users/niels/.local/bin/claude}
D=$(date -u +%Y%m%d)
LD=$ROOT/data/learn
BUDGET=${V17_LEARN_BUDGET_USD:-3}
MODEL=${V17_LEARN_MODEL:-claude-sonnet-5-5}
SSH="ssh -S none -o BatchMode=yes -o ConnectTimeout=20"
mkdir -p "$LD"
log() { echo "$(date -u +%FT%TZ) [v17_daily_learn] $*"; }
log "start date=$D model=$MODEL budget=\$$BUDGET"
if [ -s "$LD/insights_$D.json" ] && [ "${V17_LEARN_FORCE:-0}" != "1" ]; then
  log "insights_$D.json exists — day done (V17_LEARN_FORCE=1 to rerun)"; exit 0
fi
# 1 S1 leg
if timeout 1800 $SSH s1-int "cd ~/binance-sandbox && nice -n 15 python3 tools/v17_daily_learn_prep.py --leg backtest --hosts local,10.0.0.4 --date $D"; then
  rsync -az -e "$SSH" "s1-int:binance-sandbox/data/learn/bt_leg_$D.json" "$LD/" && log "S1 leg pulled ($(wc -c < "$LD/bt_leg_$D.json") bytes)"
else
  log "WARN S1 leg failed — packet will carry live data only"
fi
# 2 Mac leg + packet
nice -n 15 $PY tools/v17_daily_learn_prep.py --leg live --date "$D" || { log "FAIL live leg"; exit 2; }
nice -n 15 $PY tools/v17_daily_learn_prep.py --leg packet --date "$D" || { log "FAIL packet (cap or build)"; exit 3; }
PK="$LD/daily_packet_$D.md"
KR="$LD/valid_keys_$D.txt"
PROMPT="$LD/prompt_$D.md"
{ cat tools/v17_daily_learn_prompt.md; echo; echo "=================== DAILY LEARN PACKET ==================="; cat "$PK"; echo; echo "=================== VALID KEYS ==================="; cat "$KR"; } > "$PROMPT"
log "packet $(wc -c < "$PK") B, keyref $(wc -c < "$KR") B, prompt $(wc -c < "$PROMPT") B"
if [ "${V17_LEARN_SKIP_LLM:-0}" = "1" ]; then log "V17_LEARN_SKIP_LLM=1 — stop after prep"; exit 0; fi
# 3 one headless LLM call (no tools, isolated cwd so no project CLAUDE.md / memory is loaded)
CWD=$HOME/.cache/v17_daily_learn_cwd
mkdir -p "$CWD"
OUT="$LD/claude_$D.json"
( cd "$CWD" && timeout 900 "$CLAUDE" -p --model "$MODEL" --max-budget-usd "$BUDGET" --output-format json --tools "" \
    --no-session-persistence --strict-mcp-config --disable-slash-commands < "$PROMPT" > "$OUT" 2> "$LD/claude_$D.err" )
RC=$?
log "claude rc=$RC out=$(wc -c < "$OUT" 2>/dev/null) B"
$PY tools/v17_daily_learn_prep.py --leg ingest --date "$D" --claude-json "$OUT" --model "$MODEL" --budget "$BUDGET"
IRC=$?
if [ $IRC -ne 0 ] || [ ! -s "$LD/insights_$D.json" ]; then log "FAIL ingest rc=$IRC — insights_latest.json NOT replaced"; exit 4; fi
# 4 publish
cp "$LD/insights_$D.json" "$LD/insights_latest.json.tmp" && mv "$LD/insights_latest.json.tmp" "$LD/insights_latest.json"
M=$(md5 -q "$LD/insights_latest.json")
for H in s1-int s2; do
  if rsync -az -e "$SSH" "$LD/insights_latest.json" "$LD/insights_$D.json" "$LD/insights_$D.md" "$H:binance-sandbox/data/learn/" && \
     [ "$($SSH $H "md5sum ~/binance-sandbox/data/learn/insights_latest.json" | awk '{print $1}')" = "$M" ]; then
    log "pushed insights_latest.json -> $H md5 $M OK"
  else
    log "WARN push/md5 to $H failed"
  fi
done
log "done cost=$(tail -n 1 "$LD/cost_log.jsonl" | $PY -c 'import json,sys; print(json.loads(sys.stdin.read()).get("total_cost_usd"))')"
