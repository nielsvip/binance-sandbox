#!/bin/bash
# self_dispatch.sh — worker pulls its own work, no S1 dispatch needed.
# usage: self_dispatch.sh SIDES_FILE TEMPLATE WORKERS MAXSIDES
# 2026-10-25: whole-cycle flock — overlapping cron cycles (slow S1 rsync +
# 66 tail probes > 2min period) launched the same side 8x in one second.
[ "${SELF_DISPATCH_LOCKED}" = 1 ] || exec env SELF_DISPATCH_LOCKED=1 flock -n "/tmp/self_dispatch_$(basename "${1:?sides}" .txt).lock" "$0" "$@"
# SIDES_FILE: /tmp/npz_sides_*.txt (SYM_SIDE per line, cat_side order)
# Launches (up to MAXSIDES concurrent). USER 2026-10-25 48-bar backoff: no-trade verdicts re-arm after 48 new bars only pending sides locally via overload_run.sh.
# Pending = not running locally AND not done (S1 progress pull or local).
SB=$HOME/binance-sandbox
SIDES=${1:?sides}; T=${2:?template}; W=${3:-6}; MAX=${4:-6}
PDIR=/home/niels/v15_run36_20261024/progress
SP=/tmp/sched_progress
mkdir -p "$SP"
rsync -az --timeout=60 niels@10.0.0.3:v15_run36_20261024/progress/ "$SP/" >/dev/null 2>&1
nrun=$(pgrep -af "python[^ ]* -u v15_pilot.py --sym-side " 2>/dev/null | grep -oE -- '--sym-side [A-Z0-9_]+' | sort -u | wc -l)
for ss in $(cat "$SIDES" 2>/dev/null); do
  [ "$nrun" -ge "$MAX" ] && break
  pgrep -f "python[^ ]* -u v15_pilot.py --sym-side ${ss} " >/dev/null 2>&1 && continue
  _rec="/tmp/dispatched_${ss}"
  if [ -f "$_rec" ]; then
    _rage=$(( $(date +%s) - $(stat -c %Y "$_rec" 2>/dev/null || stat -f %m "$_rec" 2>/dev/null || echo 0) ))
    if [ "$_rage" -lt 480 ]; then continue; fi
  fi
  if [ -f "$_rec" ] && grep -q ' ' "$_rec" 2>/dev/null; then
    _rectail=$(cut -d' ' -f2 "$_rec" 2>/dev/null || echo 0)
    _sym=${ss%_*}
    _curtail=$(/home/niels/.conda/envs/binance_env_mac/bin/python -c "import numpy as np;print(int(np.load('$SB/backtest_v8/indicators/${_sym}.npz',mmap_mode='r')['timestamps'][-1]))" 2>/dev/null || echo 0)
    if [ -n "$_curtail" ] && [ "$_curtail" = "$_rectail" ] && [ "$_rectail" != "0" ]; then
      continue
    fi
    if [ -n "$_curtail" ] && [ "$_rectail" != "0" ] && [ "$_curtail" -gt "$_rectail" ]; then
      _bars=$(( ($_curtail - $_rectail) / 900 ))
      if [ "$_bars" -lt 48 ]; then continue; fi
    fi
  fi
  done1=$(python3 -c "import json;d=json.load(open('$SP/${ss}_v14_progress.json'));print(int(d.get('final_gain') is not None))" 2>/dev/null || echo 0)
  done2=$(python3 -c "import json;d=json.load(open('$PDIR/${ss}_v14_progress.json'));print(int(d.get('final_gain') is not None))" 2>/dev/null || echo 0)
  if [ "$done1" = "1" ] || [ "$done2" = "1" ]; then continue; fi
  _out=$(bash /tmp/overload_run.sh "$ss" "$T" "$W" 2>&1 | grep -E "LAUNCHED|ALREADY" | head -n 1)
  echo "$_out"
  case "$_out" in LAUNCHED-*) _lt=$(/home/niels/.conda/envs/binance_env_mac/bin/python -c "import numpy as np;print(int(np.load('$SB/backtest_v8/indicators/${ss%_*}.npz',mmap_mode='r')['timestamps'][-1]))" 2>/dev/null || echo 0); echo "$(date +%s) ${_lt:-0}" > "/tmp/dispatched_${ss}";; esac
  case "$_out" in LAUNCHED-*|ALREADY-*) nrun=$((nrun + 1));; esac
done
echo "$(date -u +%FT%TZ) self-dispatch $(basename $SIDES) running=$nrun" >> /tmp/self_dispatch.log
