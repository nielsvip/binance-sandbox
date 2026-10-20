#!/bin/bash
# npz_loop_keepalive.sh — keep tools/npz_bar_refresh.py --loop alive AND PROGRESSING (runs on S1 from cron */5).
#
# USER 2026-10-16 ("revise and fix so they are always up to date from now on"). The cron this replaces tested
# liveness with `pgrep` alone, so a loop that had stopped doing work still counted as healthy. MEASURED the same
# day, 21:24-21:28Z: a SIGTERMed loop hung in do_wait() (Pool.join() has no timeout, one worker was inside builder
# C code) and the keepalive logged "locked (previous daemon active)" and started nothing — every NPZ would have
# gone stale behind a live pid. Same trap that let the Mac position writers wedge for 4.5 h on 2026-10-15, fixed
# the same way: judge the HEARTBEAT (the loop's own log), not the pid.
set -u
ROOT=/home/niels/binance-sandbox
LOG=/tmp/npz_loop.log
MAX_LOG_AGE=${NPZ_LOOP_MAX_LOG_AGE:-900}   # a healthy cycle logs a line per installed symbol; 900 s = one bar of silence
export NPZ_LOOP_SETTLE_S=${NPZ_LOOP_SETTLE_S:-300}
export NPZ_LOOP_MAX_WORKERS=${NPZ_LOOP_MAX_WORKERS:-8}
WORKERS=${NPZ_LOOP_WORKERS:-6}
# 2026-10-20 MODE OVERRIDE (USER "every 15min ALL npz get a bar appended - NO EXCEPTIONS"). Default stays "both",
# so S1 behaviour is byte-identical. S2 needs "stocks": measured 2026-10-20, stocks are refreshed on NEITHER host —
# S1 refuses them by design (25,064 EXCHANGE_BAR_NOT_READY in one log) and S2 ran no loop and had no npz cron at
# all, so 314 of its 704 NPZs were over a day stale (CTRA 17,122 bars = 178 days). S2 is also the only host that
# may build stock NPZs. Crypto stays on S1 so S2 never adds a second Binance REST IP to the 418/429 problem.
MODE=${NPZ_LOOP_MODE:-both}

klog() { echo "$(date -u +%FT%TZ) $*"; }

pids=$(pgrep -f "npz_bar_refresh\.py.*--loop" || true)
parent=""
for p in $pids; do
  [ "$(ps -o ppid= -p "$p" 2>/dev/null | tr -d ' ')" = "1" ] && parent=$p
done
[ -z "$parent" ] && [ -n "$pids" ] && parent=$(echo "$pids" | head -1)

if [ -n "$parent" ]; then
  age=999999
  [ -f "$LOG" ] && age=$(( $(date +%s) - $(stat -c %Y "$LOG") ))
  if [ "$age" -le "$MAX_LOG_AGE" ]; then
    exit 0
  fi
  klog "loop pid $parent ALIVE but its log has been silent ${age}s (>${MAX_LOG_AGE}) — killing it and its workers"
  pkill -KILL -f "npz_bar_refresh\.py.*--loop" || true
  sleep 3
fi

cd "$ROOT" || exit 1
klog "starting loop (workers $WORKERS, cap $NPZ_LOOP_MAX_WORKERS, settle ${NPZ_LOOP_SETTLE_S}s)"
# USER 2026-10-17 "we will be using conda only for everything". This line was the reason the .venv kept coming
# back: binance-npz-refresh.service already had a conda drop-in (01:24Z) pointing at binance_env_mac, but the unit
# sat inactive while THIS keepalive relaunched the loop on the venv every 5 min, so the running loop (started
# Oct 16 22:13) predated the drop-in and never picked it up.
# MEASURED SAFE, not assumed: backtest_v8_precompute.compute_symbol('BNBUSDC','crypto', return_arrays=True) run
# under the sandbox venv (py3.12.3 / numpy 2.5.3 / pandas 3.0.5) and under conda binance_env_mac (py3.11.14 /
# numpy 2.4.6 / pandas 3.0.6) produced 946 numeric arrays over 42,451 rows that are BIT-IDENTICAL -- 0 moved
# values, 0 dtype differences. Conda is also strictly MORE capable here: the venv has no scipy, which is what made
# the stocks row recompute fail ("No module named 'scipy'", AAPL 0/25 bars) while conda does it 25/25.
PY_CONDA=/home/niels/.conda/envs/binance_env_mac/bin/python
if [ ! -x "$PY_CONDA" ] && [ -x "$ROOT/tools/pyinterp.sh" ]; then
    PY_CONDA=$("$ROOT/tools/pyinterp.sh" 2>/dev/null || echo "$PY_CONDA")
fi
exec setsid nice -n 15 "$PY_CONDA" -u tools/npz_bar_refresh.py --mode "$MODE" --workers "$WORKERS" --w15 2500 --loop >> "$LOG" 2>&1 < /dev/null
