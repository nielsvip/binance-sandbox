#!/usr/bin/env bash
# monitor_s1.sh — Log-monitor for S1 (10.0.0.3 via s1-int)
# Checks every 30s: tail logs + JSON progress
# If stall >3min or Traceback/ModuleNotFoundError/0/60 strand or JSON not updating -> pkill, clear pycache, relaunch
# Logs every check to /tmp/monitor_s1.log
# Never let hang exceed 5min

set -o pipefail

MONITOR_LOG="/tmp/monitor_s1.log"
S1="s1-int"
SSH="ssh -o ConnectTimeout=8 -o ServerAliveInterval=10"
STALL_SEC=180
COOLDOWN_SEC=300

# cooldown tracking to avoid relaunch loop
LAST_RELAUNCH_TARGETED=0
LAST_RELAUNCH_REDESIGN=0
LAST_RELAUNCH_NPZ=0
LAST_RELAUNCH_JSON=0

log() {
  echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] $*" >> "$MONITOR_LOG"
  # also echo to stdout so LaunchAgent capture works if enabled
  echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] $*"
}

ensure_tunnel() {
  if ! $SSH $S1 "echo ok" >/dev/null 2>&1; then
    log "WARN s1-int unreachable, restarting s1-sftp tunnel"
    # kill stale control masters
    ssh -O exit s1-int 2>/dev/null || true
    ssh -O exit s1-sftp 2>/dev/null || true
    ssh -fNT s1-sftp 2>&1 | tee -a "$MONITOR_LOG" || true
    sleep 3
    if $SSH $S1 "echo ok" >/dev/null 2>&1; then
      log "OK tunnel restored"
    else
      log "ERR tunnel still down, will retry next cycle"
    fi
  fi
}

# helper: seconds since epoch for file mtime, 0 if missing
# called via ssh

intervene_targeted() {
  local reason="$1"
  local now=$(date +%s)
  if (( now - LAST_RELAUNCH_TARGETED < COOLDOWN_SEC )); then
    log "SKIP targeted relaunch cooldown (${reason})"
    return
  fi
  LAST_RELAUNCH_TARGETED=$now
  log "INTERVENE aapl_targeted_365.log reason=${reason} -> pkill -f aapl, clear pycache, relaunch --workers 8 --window-days 365"
  $SSH $S1 bash <<'EOSSH' 2>&1 | tee -a /tmp/monitor_s1.log
pkill -f aapl 2>&1; sleep 1; pkill -9 -f aapl 2>&1 || true
find ~/binance-sandbox -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; echo "[pycache cleared]"
find ~/binance -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; echo "[pycache binance cleared]"
# clear old log header
cd ~/binance-sandbox
# use valid template — .new is invalid openpyxl format (was causing Traceback InvalidFileException)
if [ -f SPREADSHEETS/TEMPLATE.xlsx ]; then TPL="SPREADSHEETS/TEMPLATE.xlsx"; else TPL="SPREADSHEETS/TEMPLATE_UNIVERSAL_20260906.xlsx"; fi
PYTHONPATH=/home/niels/binance-sandbox:/home/niels/binance nohup python3 -u tools/opt/aapl_1yr_filter_targeted.py --workers 8 --window-days 365 --vector-only --template "$TPL" > /tmp/aapl_targeted_365.log 2>&1 &
echo "[relaunch targeted pid $! template $TPL]"
sleep 1
tail -n 5 /tmp/aapl_targeted_365.log 2>&1 || echo "no log yet"
ps aux | grep -E "aapl_1yr_filter_targeted" | grep -v grep | head -n 5
EOSSH
  log "DONE intervene targeted"
}

intervene_redesign() {
  local reason="$1"
  local now=$(date +%s)
  if (( now - LAST_RELAUNCH_REDESIGN < COOLDOWN_SEC )); then
    log "SKIP redesign relaunch cooldown (${reason})"
    return
  fi
  LAST_RELAUNCH_REDESIGN=$now
  log "INTERVENE aapl_1yr_filter_redesign reason=${reason} -> pkill -f aapl, clear pycache, relaunch --workers 8 --window-days 365"
  $SSH $S1 bash <<'EOSSH' 2>&1 | tee -a /tmp/monitor_s1.log
pkill -f aapl 2>&1; sleep 1; pkill -9 -f aapl 2>&1 || true
# also kill redesign specifically
pkill -f aapl_1yr_filter_redesign 2>&1 || true
find ~/binance-sandbox -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; echo "[pycache cleared]"
cd ~/binance-sandbox
# try targeted redesign with correct template; fallback to sheet per-row if needed
PYTHONPATH=/home/niels/binance-sandbox:/home/niels/binance nohup python3 -u tools/opt/aapl_1yr_filter_redesign.py --workers 8 --vector-only > /tmp/aapl_1yr_filter_redesign_20260906.log 2>&1 &
echo "[relaunch redesign pid $!]"
sleep 1
tail -n 5 /tmp/aapl_1yr_filter_redesign_20260906.log 2>&1 || echo "no log yet"
ps aux | grep -E "aapl_1yr_filter_redesign" | grep -v grep | head -n 5
EOSSH
  log "DONE intervene redesign"
}

intervene_npz() {
  local reason="$1"
  local now=$(date +%s)
  if (( now - LAST_RELAUNCH_NPZ < COOLDOWN_SEC )); then
    log "SKIP npz relaunch cooldown (${reason})"
    return
  fi
  LAST_RELAUNCH_NPZ=$now
  log "INTERVENE npz_to_mega.log reason=${reason} -> pkill -f aapl/npz, clear pycache, fix known_hosts, relaunch"
  $SSH $S1 bash <<'EOSSH' 2>&1 | tee -a /tmp/monitor_s1.log
pkill -f npz 2>&1 || true
pkill -f mega 2>&1 || true
find ~/binance-sandbox -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; echo "[pycache cleared]"
# fix host key for 10.0.0.4 that was breaking rsync
ssh-keygen -f ~/.ssh/known_hosts -R 10.0.0.4 2>&1 | head -n 5 || true
ssh-keyscan -H 10.0.0.4 2>/dev/null >> ~/.ssh/known_hosts || true
echo "[known_hosts fixed]"
# if mega script exists, restart; else just clear log and note
if [ -f ~/binance-sandbox/mega_sweep.py ]; then
  cd ~/binance-sandbox && PYTHONPATH=/home/niels/binance-sandbox:/home/niels/binance nohup python3 -u mega_sweep.py > /tmp/npz_to_mega.log 2>&1 &
  echo "[relaunch mega_sweep pid $!]"
elif [ -f ~/binance-sandbox/vec_stock_mega.py ]; then
  cd ~/binance-sandbox && PYTHONPATH=/home/niels/binance-sandbox:/home/niels/binance nohup python3 -u vec_stock_mega.py > /tmp/npz_to_mega.log 2>&1 &
  echo "[relaunch vec_stock_mega pid $!]"
else
  echo "[npz_to_mega no target script found, just truncated log]"
  : > /tmp/npz_to_mega.log
  echo "[$(date -u)] npz_to_mega monitor reset after ${reason}" >> /tmp/npz_to_mega.log
fi
sleep 1; tail -n 10 /tmp/npz_to_mega.log 2>&1 | head -n 20
EOSSH
  log "DONE intervene npz"
}

intervene_json() {
  local reason="$1"
  local now=$(date +%s)
  if (( now - LAST_RELAUNCH_JSON < COOLDOWN_SEC )); then
    log "SKIP json relaunch cooldown (${reason})"
    return
  fi
  LAST_RELAUNCH_JSON=$now
  log "INTERVENE progress JSON stalled reason=${reason} -> pkill -f aapl, clear pycache, relaunch targeted+redesign with --workers 8"
  # reuse targeted intervener but avoid double cooldown
  LAST_RELAUNCH_TARGETED=$now
  LAST_RELAUNCH_REDESIGN=$now
  $SSH $S1 bash <<'EOSSH' 2>&1 | tee -a /tmp/monitor_s1.log
pkill -f aapl 2>&1; sleep 1; pkill -9 -f aapl 2>&1 || true
find ~/binance-sandbox -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; echo "[pycache cleared]"
cd ~/binance-sandbox
PYTHONPATH=/home/niels/binance-sandbox:/home/niels/binance nohup python3 -u tools/opt/aapl_1yr_filter_targeted.py --workers 8 --window-days 365 --vector-only > /tmp/aapl_targeted_365.log 2>&1 &
echo "[relaunch targeted pid $! for json stall]"
PYTHONPATH=/home/niels/binance-sandbox:/home/niels/binance nohup python3 -u tools/opt/aapl_1yr_filter_redesign.py --workers 8 --vector-only > /tmp/aapl_1yr_filter_redesign_20260906.log 2>&1 &
echo "[relaunch redesign pid $! for json stall]"
sleep 1
ps aux | grep -E "aapl_1yr" | grep -v grep | head -n 10
EOSSH
  log "DONE intervene json"
}

log "=== monitor_s1 started pid $$ at $(date -u) ==="
log "watching: /tmp/aapl_targeted_365.log, /tmp/npz_to_mega.log, /tmp/aapl_1yr_filter_redesign*.log, data/reports/lifecycle_pilot/*.json every 30s, stall>180s, Traceback/ModuleNotFoundError/0/60"

while true; do
  ensure_tunnel
  TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
  NOW=$(date +%s)

  # single ssh batch to collect all states
  STATE=$($SSH $S1 bash <<'EOSSH'
set -o pipefail
now=$(date +%s)
for f in /tmp/aapl_targeted_365.log /tmp/npz_to_mega.log /tmp/aapl_1yr_filter_redesign_20260906.log /tmp/aapl_1yr_filter_redesign.log; do
  if [ -f "$f" ]; then
    mtime=$(stat -c %Y "$f" 2>/dev/null || stat -f %m "$f" 2>/dev/null || echo 0)
    size=$(stat -c %s "$f" 2>/dev/null || stat -f %z "$f" 2>/dev/null || echo 0)
    age=$((now - mtime))
    echo "FILE|$f|$mtime|$size|$age"
    # check for error patterns in last 100 lines
    if tail -n 100 "$f" 2>/dev/null | grep -q "Traceback"; then echo "ERR|$f|Traceback"; fi
    if tail -n 100 "$f" 2>/dev/null | grep -q "ModuleNotFoundError"; then echo "ERR|$f|ModuleNotFoundError"; fi
    if tail -n 100 "$f" 2>/dev/null | grep -q "0/60"; then echo "ERR|$f|0/60 strand"; fi
    # also any 0/ without progress? generic 0/60 or 0/65 etc is strand stall
    if tail -n 100 "$f" 2>/dev/null | grep -qE "[0-9]+/60 strand| 0/60"; then echo "ERR|$f|0/60 strand2"; fi
  else
    echo "FILE|$f|0|0|9999"
    echo "ERR|$f|MISSING"
  fi
done
# wildcard for any redesign logs
for f in /tmp/aapl_1yr_filter_redesign*.log; do
  [ -e "$f" ] || continue
  # already covered above, avoid dup if exact match
  if [ "$f" = "/tmp/aapl_1yr_filter_redesign_20260906.log" ] || [ "$f" = "/tmp/aapl_1yr_filter_redesign.log" ]; then continue; fi
  mtime=$(stat -c %Y "$f" 2>/dev/null || echo 0); size=$(stat -c %s "$f" 2>/dev/null || echo 0); age=$((now - mtime))
  echo "FILE|$f|$mtime|$size|$age"
  if tail -n 100 "$f" 2>/dev/null | grep -q "Traceback"; then echo "ERR|$f|Traceback"; fi
  if tail -n 100 "$f" 2>/dev/null | grep -q "ModuleNotFoundError"; then echo "ERR|$f|ModuleNotFoundError"; fi
  if tail -n 100 "$f" 2>/dev/null | grep -q "0/60"; then echo "ERR|$f|0/60 strand"; fi
done
# progress JSONs
for f in ~/binance-sandbox/data/reports/lifecycle_pilot/*.json; do
  [ -e "$f" ] || continue
  mtime=$(stat -c %Y "$f" 2>/dev/null || echo 0); size=$(stat -c %s "$f" 2>/dev/null || echo 0); age=$((now - mtime))
  echo "JSON|$f|$mtime|$size|$age"
done
# processes
ps aux | grep -E "aapl_1yr|npz_to_mega|mega_sweep|vec_stock_mega" | grep -v grep | head -n 20 | while read line; do echo "PS|$line"; done
# tail snippets for logging
echo "TAIL_AAPL_TARGETED_START"
tail -n 3 /tmp/aapl_targeted_365.log 2>&1 | tr '\n' '|' | head -c 500; echo ""
echo "TAIL_AAPL_TARGETED_END"
echo "TAIL_REDESIGN_START"
tail -n 3 /tmp/aapl_1yr_filter_redesign_20260906.log 2>&1 | tr '\n' '|' | head -c 500; echo ""
echo "TAIL_REDESIGN_END"
echo "TAIL_NPZ_START"
tail -n 3 /tmp/npz_to_mega.log 2>&1 | tr '\n' '|' | head -c 500; echo ""
echo "TAIL_NPZ_END"
EOSSH
  )
  # handle ssh failure
  if [ -z "$STATE" ]; then
    log "WARN empty STATE from S1, ssh may have failed, will retry next cycle"
    sleep 30
    continue
  fi

  # parse FILE lines
  TARGETED_AGE=$(echo "$STATE" | awk -F'|' '$1=="FILE" && $2=="/tmp/aapl_targeted_365.log" {print $5}')
  REDESIGN_AGE=$(echo "$STATE" | awk -F'|' '$1=="FILE" && $2=="/tmp/aapl_1yr_filter_redesign_20260906.log" {print $5}')
  NPZ_AGE=$(echo "$STATE" | awk -F'|' '$1=="FILE" && $2=="/tmp/npz_to_mega.log" {print $5}')
  TARGETED_ERR=$(echo "$STATE" | grep "^ERR|/tmp/aapl_targeted_365.log" | cut -d'|' -f3 | tr '\n' ',' )
  REDESIGN_ERR=$(echo "$STATE" | grep "^ERR|/tmp/aapl_1yr_filter_redesign" | cut -d'|' -f3 | tr '\n' ',' )
  NPZ_ERR=$(echo "$STATE" | grep "^ERR|/tmp/npz_to_mega.log" | cut -d'|' -f3 | tr '\n' ',' )

  # JSON ages — find most recent AAPL progress vs oldest
  JSON_AAPL_TARGETED_AGE=$(echo "$STATE" | grep "AAPL_LONG_1yr_filter_targeted" | awk -F'|' '{print $5}' | head -n1)
  JSON_AAPL_REDESIGN_AGE=$(echo "$STATE" | grep "AAPL_LONG_1yr_filter_redesign" | awk -F'|' '{print $5}' | head -n1)
  JSON_BONK_AGE=$(echo "$STATE" | grep "1000BONKUSDC_LONG" | awk -F'|' '{print $5}' | head -n1)

  # default to 0 if missing
  TARGETED_AGE=${TARGETED_AGE:-9999}
  REDESIGN_AGE=${REDESIGN_AGE:-9999}
  NPZ_AGE=${NPZ_AGE:-9999}
  JSON_AAPL_TARGETED_AGE=${JSON_AAPL_TARGETED_AGE:-9999}
  JSON_AAPL_REDESIGN_AGE=${JSON_AAPL_REDESIGN_AGE:-9999}

  PS_LINES=$(echo "$STATE" | grep "^PS|" | head -n 5)

  # extract tails
  TAIL_TARGETED=$(echo "$STATE" | sed -n '/TAIL_AAPL_TARGETED_START/,/TAIL_AAPL_TARGETED_END/p' | head -n 2 | tail -n 1)
  TAIL_REDESIGN=$(echo "$STATE" | sed -n '/TAIL_REDESIGN_START/,/TAIL_REDESIGN_END/p' | head -n 2 | tail -n 1)
  TAIL_NPZ=$(echo "$STATE" | sed -n '/TAIL_NPZ_START/,/TAIL_NPZ_END/p' | head -n 2 | tail -n 1)

  ALERT=""

  # log every check
  log "CHECK targeted_age=${TARGETED_AGE}s redesign_age=${REDESIGN_AGE}s npz_age=${NPZ_AGE}s json_targeted_age=${JSON_AAPL_TARGETED_AGE}s json_redesign_age=${JSON_AAPL_REDESIGN_AGE}s bonk_age=${JSON_BONK_AGE}s err_targeted=[${TARGETED_ERR}] err_redesign=[${REDESIGN_ERR}] err_npz=[${NPZ_ERR}] ps=[$(echo "$PS_LINES" | tr '\n' ';' | head -c 300)]"

  # decision: intervene if stall >180 or error patterns
  NEED_TARGETED=""
  if [ "$TARGETED_AGE" -gt "$STALL_SEC" ] 2>/dev/null; then NEED_TARGETED="stall ${TARGETED_AGE}s"; fi
  if echo "$TARGETED_ERR" | grep -q "Traceback"; then NEED_TARGETED="${NEED_TARGETED} Traceback"; fi
  if echo "$TARGETED_ERR" | grep -q "ModuleNotFoundError"; then NEED_TARGETED="${NEED_TARGETED} ModuleNotFoundError"; fi
  if echo "$TARGETED_ERR" | grep -q "0/60"; then NEED_TARGETED="${NEED_TARGETED} 0/60 strand"; fi

  NEED_REDESIGN=""
  if [ "$REDESIGN_AGE" -gt "$STALL_SEC" ] 2>/dev/null; then NEED_REDESIGN="stall ${REDESIGN_AGE}s"; fi
  if echo "$REDESIGN_ERR" | grep -q "Traceback"; then NEED_REDESIGN="${NEED_REDESIGN} Traceback"; fi
  if echo "$REDESIGN_ERR" | grep -q "ModuleNotFoundError"; then NEED_REDESIGN="${NEED_REDESIGN} ModuleNotFoundError"; fi
  if echo "$REDESIGN_ERR" | grep -q "0/60"; then NEED_REDESIGN="${NEED_REDESIGN} 0/60 strand"; fi

  NEED_NPZ=""
  if [ "$NPZ_AGE" -gt "$STALL_SEC" ] 2>/dev/null; then NEED_NPZ="stall ${NPZ_AGE}s"; fi
  if echo "$NPZ_ERR" | grep -q "Traceback"; then NEED_NPZ="${NEED_NPZ} Traceback"; fi
  if echo "$NPZ_ERR" | grep -q "ModuleNotFoundError"; then NEED_NPZ="${NEED_NPZ} ModuleNotFoundError"; fi
  if echo "$NPZ_ERR" | grep -q "0/60"; then NEED_NPZ="${NEED_NPZ} 0/60 strand"; fi
  if echo "$NPZ_ERR" | grep -q "MISSING"; then NEED_NPZ="${NEED_NPZ} MISSING"; fi

  NEED_JSON=""
  # JSON stall: if AAPL progress not updating while process expected, or any pilot json stalled >300s while worker running
  if [ "$JSON_AAPL_TARGETED_AGE" -gt "$STALL_SEC" ] 2>/dev/null && [ "$TARGETED_AGE" -gt "$STALL_SEC" ] 2>/dev/null; then NEED_JSON="targeted_json_stall ${JSON_AAPL_TARGETED_AGE}s"; fi
  if [ "$JSON_AAPL_REDESIGN_AGE" -gt "$STALL_SEC" ] 2>/dev/null && [ "$REDESIGN_AGE" -gt "$STALL_SEC" ] 2>/dev/null; then NEED_JSON="${NEED_JSON} redesign_json_stall ${JSON_AAPL_REDESIGN_AGE}s"; fi

  # BATCH interventions to avoid cannibalizing relaunches within same cycle
  # Collect all needs first, then do single pkill and relaunch each without extra kills
  BATCH_NEEDS=""
  if [ -n "$NEED_TARGETED" ]; then BATCH_NEEDS="${BATCH_NEEDS} targeted"; fi
  if [ -n "$NEED_REDESIGN" ]; then BATCH_NEEDS="${BATCH_NEEDS} redesign"; fi
  if [ -n "$NEED_NPZ" ]; then
    if echo "$NEED_NPZ" | grep -q "Traceback\|ModuleNotFoundError\|MISSING" || [ "$NPZ_AGE" -gt 600 ] 2>/dev/null; then
      BATCH_NEEDS="${BATCH_NEEDS} npz"
    else
      log "NOTE npz stall ${NPZ_AGE}s but <10min and no traceback, deferring"
      NEED_NPZ=""  # clear so not treated as batch
    fi
  fi
  if [ -n "$NEED_JSON" ]; then BATCH_NEEDS="${BATCH_NEEDS} json"; fi

  if [ -n "$BATCH_NEEDS" ]; then
    log "BATCH intervene for:$BATCH_NEEDS"
    # If multiple aapl-related, do single pkill then launch each
    NEED_AAPL_BATCH=""
    if echo "$BATCH_NEEDS" | grep -q "targeted\|redesign\|json"; then NEED_AAPL_BATCH="yes"; fi
    if [ "$NEED_AAPL_BATCH" = "yes" ]; then
      # single pkill for all aapl, with cooldown check per-type still inside intervene funcs if called individually
      # but for batch we handle cooldown globally
      NOW_BATCH=$(date +%s)
      # check cooldowns: if all aapl types are in cooldown, skip batch pkill
      SKIP_BATCH=""
      if [ -n "$NEED_TARGETED" ] && (( NOW_BATCH - LAST_RELAUNCH_TARGETED < COOLDOWN_SEC )); then SKIP_TARGETED="yes"; else SKIP_TARGETED=""; fi
      if [ -n "$NEED_REDESIGN" ] && (( NOW_BATCH - LAST_RELAUNCH_REDESIGN < COOLDOWN_SEC )); then SKIP_REDESIGN="yes"; else SKIP_REDESIGN=""; fi
      # if both needed but both in cooldown, skip entire aapl batch
      if [ -n "$NEED_TARGETED" ] && [ -n "$NEED_REDESIGN" ] && [ "$SKIP_TARGETED" = "yes" ] && [ "$SKIP_REDESIGN" = "yes" ]; then
        log "SKIP batch aapl intervene — both in cooldown"
      else
        # at least one not in cooldown: do targeted pkill only for needed jobs to avoid collateral kill
        # do per-job kill + shared pycache clear once
        NEED_PYCACHE_CLEAR="no"
        if [ -n "$NEED_TARGETED" ] && [ "$SKIP_TARGETED" != "yes" ]; then NEED_PYCACHE_CLEAR="yes"; fi
        if [ -n "$NEED_REDESIGN" ] && [ "$SKIP_REDESIGN" != "yes" ]; then NEED_PYCACHE_CLEAR="yes"; fi
        if [ -n "$NEED_JSON" ]; then NEED_PYCACHE_CLEAR="yes"; fi
        if [ "$NEED_PYCACHE_CLEAR" = "yes" ]; then
          $SSH $S1 bash <<'EOSSH_BATCH' 2>&1 | tee -a /tmp/monitor_s1.log
find ~/binance-sandbox -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; echo "[batch pycache cleared]"
find ~/binance -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; echo "[batch pycache binance cleared]"
EOSSH_BATCH
          log "BATCH pycache cleared once"
        fi
        # per-job pkill (targeted kill only, not global)
        if [ -n "$NEED_TARGETED" ] && [ "$SKIP_TARGETED" != "yes" ]; then
          log "BATCH pkill -f aapl_1yr_filter_targeted"
          $SSH $S1 "pkill -f aapl_1yr_filter_targeted; sleep 1; pkill -9 -f aapl_1yr_filter_targeted 2>/dev/null || true; echo [pkill targeted done]" 2>&1 | tee -a /tmp/monitor_s1.log
        fi
        if [ -n "$NEED_REDESIGN" ] && [ "$SKIP_REDESIGN" != "yes" ]; then
          log "BATCH pkill -f aapl_1yr_filter_redesign"
          $SSH $S1 "pkill -f aapl_1yr_filter_redesign; sleep 1; pkill -9 -f aapl_1yr_filter_redesign 2>/dev/null || true; echo [pkill redesign done]" 2>&1 | tee -a /tmp/monitor_s1.log
        fi
        if [ -n "$NEED_JSON" ]; then
          # json stall may need both, but only kill those not already killed above
          if [ -z "$NEED_TARGETED" ] && (( NOW_BATCH - LAST_RELAUNCH_TARGETED >= COOLDOWN_SEC )); then
            $SSH $S1 "pkill -f aapl_1yr_filter_targeted; sleep 1; pkill -9 -f aapl_1yr_filter_targeted 2>/dev/null || true" 2>&1 | tee -a /tmp/monitor_s1.log
          fi
          if [ -z "$NEED_REDESIGN" ] && (( NOW_BATCH - LAST_RELAUNCH_REDESIGN >= COOLDOWN_SEC )); then
            $SSH $S1 "pkill -f aapl_1yr_filter_redesign; sleep 1; pkill -9 -f aapl_1yr_filter_redesign 2>/dev/null || true" 2>&1 | tee -a /tmp/monitor_s1.log
          fi
        fi
        if [ -n "$NEED_TARGETED" ] && [ "$SKIP_TARGETED" != "yes" ]; then
          log "ALERT targeted needs intervene: $NEED_TARGETED tail=${TAIL_TARGETED}"
          LAST_RELAUNCH_TARGETED=$NOW_BATCH
          $SSH $S1 bash <<'EOSSH_T' 2>&1 | tee -a /tmp/monitor_s1.log
cd ~/binance-sandbox
if [ -f SPREADSHEETS/TEMPLATE.xlsx ]; then TPL="SPREADSHEETS/TEMPLATE.xlsx"; else TPL="SPREADSHEETS/TEMPLATE_UNIVERSAL_20260906.xlsx"; fi
PYTHONPATH=/home/niels/binance-sandbox:/home/niels/binance nohup python3 -u tools/opt/aapl_1yr_filter_targeted.py --workers 8 --window-days 365 --vector-only --template "$TPL" > /tmp/aapl_targeted_365.log 2>&1 &
echo "[batch relaunch targeted pid $! template $TPL]"
sleep 1; tail -n 5 /tmp/aapl_targeted_365.log 2>&1 | head -n 5; ps aux | grep -E "aapl_1yr_filter_targeted" | grep -v grep | head -n 3
EOSSH_T
          log "DONE batch targeted"
        elif [ -n "$NEED_TARGETED" ]; then log "SKIP targeted batch — cooldown"; fi
        if [ -n "$NEED_REDESIGN" ] && [ "$SKIP_REDESIGN" != "yes" ]; then
          log "ALERT redesign needs intervene: $NEED_REDESIGN tail=${TAIL_REDESIGN}"
          LAST_RELAUNCH_REDESIGN=$NOW_BATCH
          $SSH $S1 bash <<'EOSSH_R' 2>&1 | tee -a /tmp/monitor_s1.log
cd ~/binance-sandbox
PYTHONPATH=/home/niels/binance-sandbox:/home/niels/binance nohup python3 -u tools/opt/aapl_1yr_filter_redesign.py --workers 8 --vector-only > /tmp/aapl_1yr_filter_redesign_20260906.log 2>&1 &
echo "[batch relaunch redesign pid $!]"
sleep 1; tail -n 5 /tmp/aapl_1yr_filter_redesign_20260906.log 2>&1 | head -n 5; ps aux | grep -E "aapl_1yr_filter_redesign" | grep -v grep | head -n 3
EOSSH_R
          log "DONE batch redesign"
        elif [ -n "$NEED_REDESIGN" ]; then log "SKIP redesign batch — cooldown"; fi
        if [ -n "$NEED_JSON" ]; then
          # json stall implies both logs stalled; if not already relaunched, do missing ones
          if [ -z "$NEED_TARGETED" ] && (( NOW_BATCH - LAST_RELAUNCH_TARGETED >= COOLDOWN_SEC )); then
            log "ALERT json-> relaunch targeted (not already)"
            LAST_RELAUNCH_TARGETED=$NOW_BATCH
            $SSH $S1 bash <<'EOSSH_JT' 2>&1 | tee -a /tmp/monitor_s1.log
cd ~/binance-sandbox
if [ -f SPREADSHEETS/TEMPLATE.xlsx ]; then TPL="SPREADSHEETS/TEMPLATE.xlsx"; else TPL="SPREADSHEETS/TEMPLATE_UNIVERSAL_20260906.xlsx"; fi
PYTHONPATH=/home/niels/binance-sandbox:/home/niels/binance nohup python3 -u tools/opt/aapl_1yr_filter_targeted.py --workers 8 --window-days 365 --vector-only --template "$TPL" > /tmp/aapl_targeted_365.log 2>&1 &
echo "[batch json relaunch targeted pid $!]"
EOSSH_JT
          fi
          if [ -z "$NEED_REDESIGN" ] && (( NOW_BATCH - LAST_RELAUNCH_REDESIGN >= COOLDOWN_SEC )); then
            log "ALERT json-> relaunch redesign (not already)"
            LAST_RELAUNCH_REDESIGN=$NOW_BATCH
            $SSH $S1 bash <<'EOSSH_JR' 2>&1 | tee -a /tmp/monitor_s1.log
cd ~/binance-sandbox
PYTHONPATH=/home/niels/binance-sandbox:/home/niels/binance nohup python3 -u tools/opt/aapl_1yr_filter_redesign.py --workers 8 --vector-only > /tmp/aapl_1yr_filter_redesign_20260906.log 2>&1 &
echo "[batch json relaunch redesign pid $!]"
EOSSH_JR
          fi
          LAST_RELAUNCH_JSON=$NOW_BATCH
          log "DONE batch json"
        fi
      fi
    fi
    if echo "$BATCH_NEEDS" | grep -q "npz"; then
      if (( $(date +%s) - LAST_RELAUNCH_NPZ >= COOLDOWN_SEC )); then
        log "ALERT npz needs intervene: $NEED_NPZ tail=${TAIL_NPZ}"
        intervene_npz "$NEED_NPZ"
      else
        log "SKIP npz batch — cooldown"
      fi
    fi
  fi
  # legacy single-path fallback removed; batch handles all

  if [ -z "$NEED_TARGETED" ] && [ -z "$NEED_REDESIGN" ] && [ -z "$NEED_NPZ" ] && [ -z "$NEED_JSON" ]; then
    log "OK all logs flowing"
  fi

  sleep 30
done
