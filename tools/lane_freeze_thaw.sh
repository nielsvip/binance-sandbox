#!/bin/bash
# USER 2026-10-22 lane freeze/thaw: while the H2 step cache is thin, loop-lane
# passes burn CPU for 100% EVAL_TIMEOUTs. Freeze them (SIGSTOP, procs persist so
# supervisors never respawn dupes); auto-thaw (SIGCONT) once >=10 H2 symbols.
cd /Users/niels/Documents/binance || exit 0
H2=$(/Users/niels/.local/bin/python3 -c "
import sys,pickle,glob
sys.path.insert(0,'.')
from vec_decisions.stock_live_frames import _code_version
v=_code_version()
print(sum(1 for f in glob.glob('data/pure_board/step_cache/*.pkl') if pickle.load(open(f,'rb')).get('version')==v))" 2>/dev/null)
[ -z "$H2" ] && exit 0
# Time fallback: never enter the RTH open (2026-10-23 13:30Z) frozen — thaw by
# 06:00Z unconditionally so vector coverage is trying at the open even if thin.
NOW=$(date -u +%s); OPEN_FALLBACK=$(date -u -d "2026-10-23 06:00:00" +%s 2>/dev/null || date -u -j -f "%Y-%m-%d %H:%M:%S" "2026-10-23 06:00:00" +%s)
if [ "$H2" -ge 10 ] || { [ -n "$OPEN_FALLBACK" ] && [ "$NOW" -ge "$OPEN_FALLBACK" ]; }; then
  for p in $(pgrep -f "pure_board_trader.py --loop"); do kill -CONT $p 2>/dev/null; done
  for p in $(pgrep -f "spawn_main"); do kill -CONT $p 2>/dev/null; done
  echo "$(date -u +%FT%TZ) THAW H2=$H2"
fi
