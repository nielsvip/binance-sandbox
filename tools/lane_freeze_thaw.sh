#!/bin/bash
# USER 2026-10-22 lane freeze/thaw: while the H2 step cache is thin, loop-lane
# passes burn CPU for 100% EVAL_TIMEOUTs. Freeze them (SIGSTOP, procs persist so
# supervisors never respawn dupes); auto-thaw (SIGCONT) once >=10 H2 symbols.
cd /Users/niels/Documents/binance || exit 0
H2=$(python3 -c "
import sys,pickle,glob
sys.path.insert(0,'.')
from vec_decisions.stock_live_frames import _code_version
v=_code_version()
print(sum(1 for f in glob.glob('data/pure_board/step_cache/*.pkl') if pickle.load(open(f,'rb')).get('version')==v))" 2>/dev/null)
[ -z "$H2" ] && exit 0
if [ "$H2" -ge 10 ]; then
  for p in $(pgrep -f "pure_board_trader.py --loop"); do kill -CONT $p 2>/dev/null; done
  for p in $(pgrep -f "spawn_main"); do kill -CONT $p 2>/dev/null; done
  echo "$(date -u +%FT%TZ) THAW H2=$H2"
fi
