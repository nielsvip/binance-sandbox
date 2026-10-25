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
PDIR=$(head -n 1 "$HOME/v15_current_progress_dir.txt" 2>/dev/null)
[[ "$PDIR" == /home/niels/v15_run*/progress ]] || { echo "INVALID ROUND POINTER: $PDIR" >&2; exit 2; }
SP=/tmp/sched_progress_$(basename "$(dirname "$PDIR")")
mkdir -p "$SP"
if [ $(( $(date +%s) - $(stat -c %Y "$SP" 2>/dev/null || stat -f %m "$SP" 2>/dev/null || echo 0) )) -gt 240 ]; then
  rsync -az --timeout=60 "niels@10.0.0.3:$PDIR/" "$SP/" >/dev/null 2>&1
  touch "$SP"
fi
nrun=$(pgrep -af "python[^ ]* -u v15_pilot.py --sym-side " 2>/dev/null | grep -oE -- '--sym-side [A-Z0-9_]+' | sort -u | wc -l)
_TB=/tmp/tails_$(basename $SIDES .txt).cache
_PB=/tmp/prog_$(basename $SIDES .txt).cache
/home/niels/.conda/envs/binance_env_mac/bin/python - "$SIDES" "$SB" "$SP" "$PDIR" "$_TB" "$_PB" <<'PYEOF' 2>/dev/null
import sys,json,os
import numpy as np
sidesf,sb,sp,pdir,tb,pb=sys.argv[1:7]
sides=[l.strip() for l in open(sidesf) if l.strip()]
sides=[s for s in sides if s.endswith('_LONG')]
cache_path=pb+'.state.json'
try: cache=json.load(open(cache_path))
except (OSError,ValueError): cache={}
next_cache={}
def progress_state(path):
    try: st=os.stat(path)
    except OSError: return 0,0
    key=str(st.st_mtime_ns)+':'+str(st.st_size)
    old=cache.get(path)
    if isinstance(old,dict) and old.get('key')==key:
        next_cache[path]=old
        return int(old.get('done') or 0),int(old.get('ts') or 0)
    try:
        j=json.load(open(path))
        done=int(j.get('final_gain') is not None)
        ts=int(j.get('npz_last_bar_ts') or (j.get('npz_id') or {}).get('ts_last') or 0)
    except (OSError,ValueError,TypeError,OverflowError): return 0,0
    next_cache[path]={'key':key,'done':done,'ts':ts}
    return done,ts
syms=sorted({s.rsplit('_',1)[0] for s in sides})
with open(tb,'w') as f:
    for sym in syms:
        try: t=int(np.load(os.path.join(sb,'backtest_v8/indicators',sym+'.npz'),mmap_mode='r')['timestamps'][-1])
        except Exception: t=0
        f.write('%s %d\n'%(sym,t))
with open(pb,'w') as f:
    for s in sides:
        fg=0;ts=0
        for d in (sp,pdir):
            done,bar=progress_state(os.path.join(d,s+'_v14_progress.json'))
            fg=max(fg,done);ts=max(ts,bar)
        f.write('%s %d %d\n'%(s,fg,ts))
tmp=cache_path+'.tmp'+str(os.getpid())
with open(tmp,'w') as f: json.dump(next_cache,f)
os.replace(tmp,cache_path)
PYEOF
for ss in $(cat "$SIDES" 2>/dev/null); do
  case "$ss" in *_LONG) ;; *) continue ;; esac
  [ "$nrun" -ge "$MAX" ] && break
  pgrep -f "python[^ ]* -u v15_pilot.py --sym-side ${ss} " >/dev/null 2>&1 && continue
  _rec="/tmp/dispatched_${ss}"
  if [ -f "$_rec" ]; then
    _rage=$(( $(date +%s) - $(stat -c %Y "$_rec" 2>/dev/null || stat -f %m "$_rec" 2>/dev/null || echo 0) ))
    if [ "$_rage" -lt 480 ]; then continue; fi
  fi
  _qd=$(ls -dt "$SB/SPREADSHEETS/V15_V16_IMPOSSIBLE/${ss}_"* 2>/dev/null | head -n 1)
  if [ -n "$_qd" ]; then
    _qs=$(stat -c %Y "$_qd" 2>/dev/null || stat -f %m "$_qd" 2>/dev/null || echo 0)
    _sym=${ss%_*}
    _qtail=$(awk -v s="$_sym" '$1==s{print $2}' "$_TB" 2>/dev/null)
    if [ "$(( $(date +%s) - _qs ))" -lt 86400 ] && [ -n "$_qtail" ] && [ "$_qtail" != "0" ] && [ "$_qtail" -le "$_qs" ]; then
      continue
    fi
  fi
  if [ -f "$_rec" ] && grep -q ' ' "$_rec" 2>/dev/null; then
    _rectail=$(cut -d' ' -f2 "$_rec" 2>/dev/null || echo 0)
    _sym=${ss%_*}
    _curtail=$(awk -v s="$_sym" '$1==s{print $2}' "$_TB" 2>/dev/null)
    if [ -n "$_curtail" ] && [ "$_curtail" = "$_rectail" ] && [ "$_rectail" != "0" ]; then
      continue
    fi
    if [ -n "$_curtail" ] && [ "$_rectail" != "0" ] && [ "$_curtail" -gt "$_rectail" ]; then
      _bars=$(( ($_curtail - $_rectail) / 900 ))
      if [ "$_bars" -lt 48 ]; then continue; fi
    fi
  fi
  _prow=$(awk -v s="$ss" '$1==s{print $2, $3}' "$_PB" 2>/dev/null)
  done1=$(echo "$_prow" | cut -d' ' -f1); done2=$done1
  # USER 2026-10-25 stocks_short-10h: a board on a STALE NPZ is not done — relaunch unless
  # ANY progress file proves a board within 48 bars of the current tail (same 48-bar rule).
  _fresh_board=0
  _rects=$(echo "$_prow" | cut -d' ' -f2)
  if [ -n "$_rects" ] && [ "$_rects" != "0" ]; then
    _sym=${ss%_*}
    _curt=$(awk -v s="$_sym" '$1==s{print $2}' "$_TB" 2>/dev/null)
    if [ -n "$_curt" ] && [ "$_curt" != "0" ] && [ $(( ($_curt - $_rects) / 900 )) -lt 48 ]; then _fresh_board=1; fi
  fi
  if { [ "$done1" = "1" ] || [ "$done2" = "1" ]; } && [ "$_fresh_board" = "1" ]; then continue; fi
  _out=$(bash "$SB/tools/worker_lanes/s5/overload_run.sh" "$ss" "$T" "$W" "$MAX" 2>&1 | grep -E "LAUNCHED|ALREADY" | head -n 1)
  echo "$_out"
  case "$_out" in LAUNCHED-*) _lt=$(awk -v s="${ss%_*}" '$1==s{print $2}' "$_TB" 2>/dev/null); echo "$(date +%s) ${_lt:-0}" > "/tmp/dispatched_${ss}";; esac
  case "$_out" in LAUNCHED-*|ALREADY-*) nrun=$((nrun + 1));; esac
done
echo "$(date -u +%FT%TZ) self-dispatch $(basename $SIDES) running=$nrun" >> /tmp/self_dispatch.log
