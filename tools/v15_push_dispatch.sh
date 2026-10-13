#!/bin/bash
# v15_push_dispatch — USER 2026-10-13: "the npz goes WITH THE PUSH of the sym_side, it gets calculated on s1 before
# dispatching" + "get 6 syms per server tested in the next hour". Runs ON S1 (the NPZ source).
#
# Per sym_side: 1) make sure s1's symbol NPZ is at the latest closed 15m bar (STOCKS: tools/v15_npz_prep.py, the same
# Tradier klines gate the scheduler uses; CRYPTO: the Binance npz refresher owns the tail, so we verify the tail age
# instead of running the Tradier gate, which has no cache for crypto and would falsely hold it), 2) rsync that NPZ to
# the target worker and VERIFY md5 (the push carries the data — no reliance on a background sync), 3) launch v15_pilot
# with the push launch env (prior policy + tiers + FOCUS, sampling OFF, capped post-stages).
# A side already running on the target is skipped. Stale beyond MAX_LAG_MIN is reported and held.
# Usage: bash tools/v15_push_dispatch.sh <host_ip|127.0.0.1> <workers> SYM_SIDE [SYM_SIDE ...]
set -u
ROOT="$HOME/binance-sandbox"
H="$1"; shift
W="$1"; shift
MAX_LAG_MIN="${V15_NPZ_MAX_LAG_MIN:-75}"
PDIR="$(ls -dt $HOME/v15_run3*/progress 2>/dev/null | head -1)"
[ -n "$PDIR" ] || { echo "no round progress dir"; exit 1; }
SSH="ssh -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new"
IS_LOCAL=0
case "$H" in 127.0.0.1|localhost) IS_LOCAL=1 ;; esac
cd "$ROOT" || exit 1
for SS in "$@"; do
  SYM="${SS%_*}"; SIDE="${SS##*_}"
  RUN=0
  if [ "$IS_LOCAL" = 1 ]; then RUN=$(pgrep -fc "v15_pilot.py --sym-side $SS( |\$)" 2>/dev/null | head -1)
  else RUN=$($SSH "niels@$H" "pgrep -fc 'v15_pilot.py --sym-side $SS( |\$)' 2>/dev/null | head -1" 2>/dev/null | head -1); fi
  RUN="${RUN:-0}"
  if [ "$RUN" -gt 0 ] 2>/dev/null; then echo "[skip] $SS already running on $H"; continue; fi
  case "$SYM" in *USDT|*USDC|*BUSD|*FDUSD|*TUSD) CAT=CRYPTO ;; *) CAT=STOCKS ;; esac
  NPZ="$ROOT/backtest_v8/indicators/$SYM.npz"
  # 1) s1-side freshness
  if [ "$CAT" = STOCKS ]; then
    PREP=$(timeout 600 python3 tools/v15_npz_prep.py --symbol "$SYM" 2>&1 | tail -1)
    echo "[npz-prep] $SS $(echo "$PREP" | cut -c1-150)"
  fi
  LAG=$(python3 - "$NPZ" <<'EOF'
import sys, os, time
try:
    import numpy as np
    z = np.load(sys.argv[1], allow_pickle=True)
    ts = z["timestamps"].astype("int64")
    last = int(ts[-1] // 1000 if ts[-1] > 1e11 else ts[-1])
    print(int((time.time() - last) / 60))
except Exception as e:
    print(-1)
EOF
)
  if [ "${LAG:--1}" -lt 0 ] 2>/dev/null; then echo "[hold] $SS s1 NPZ unreadable ($NPZ) — not dispatched"; continue; fi
  # The wall-clock tail gate applies to CRYPTO only (24/7 bars). A STOCK tail is hours "behind" whenever the US market
  # is closed, which is normal, not stale — stock freshness is judged by tools/v15_npz_prep.py above, which compares the
  # NPZ against the Tradier klines cache and understands market hours. Gating stocks on wall clock blocks every
  # overnight stock board (observed 2026-10-13 02:10Z: ZCSH 142min, AXON 262min, MSTR 157min all falsely held).
  if [ "$CAT" = CRYPTO ] && [ "$LAG" -gt "$MAX_LAG_MIN" ] 2>/dev/null; then echo "[hold] $SS s1 crypto NPZ tail ${LAG}min behind (> ${MAX_LAG_MIN}) — not dispatched, refresher is behind"; continue; fi
  if [ "$CAT" = STOCKS ]; then
    POK=$(echo "${PREP:-}" | python3 -c "
import json,sys
try: print(json.loads(sys.stdin.read().strip()).get('ok'))
except Exception: print('unknown')" 2>/dev/null)
    if [ "$POK" = "False" ]; then echo "[hold] $SS stock NPZ gate says not usable — not dispatched"; continue; fi
    echo "[npz-stocks] $SS prep_ok=$POK tail=${LAG}min (market-hours aware gate)"
  fi
  # 2) the push: NPZ travels with the dispatch, md5 verified
  if [ "$IS_LOCAL" != 1 ]; then
    rsync -az -e "$SSH" "$NPZ" "niels@$H:~/binance-sandbox/backtest_v8/indicators/" || { echo "[hold] $SS npz rsync to $H failed"; continue; }
    L5=$(md5sum "$NPZ" | awk '{print $1}')
    R5=$($SSH "niels@$H" "md5sum ~/binance-sandbox/backtest_v8/indicators/$SYM.npz 2>/dev/null | awk '{print \$1}'")
    [ "$L5" = "$R5" ] || { echo "[hold] $SS npz md5 mismatch after push (local $L5 remote $R5)"; continue; }
    echo "[npz-push] $SS -> $H tail_lag=${LAG}min md5 ${L5:0:8} VERIFIED"
  else
    echo "[npz-local] $SS tail_lag=${LAG}min (s1 is the source, no push needed)"
  fi
  # 3) OOM floor on the target right before launching (USER 2026-10-13): a host under its floor gets no new board,
  # whatever the caller asked for. Cheap and fail-closed — an OOM kill costs a whole board plus an attempt.
  OOM_FLOOR="${V15_PUSH_OOM_FLOOR_MB:-2500}"
  if [ "$IS_LOCAL" = 1 ]; then MEMAV=$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo)
  else MEMAV=$($SSH "niels@$H" "awk '/MemAvailable/{print int(\$2/1024)}' /proc/meminfo" 2>/dev/null); fi
  MEMAV="${MEMAV:-0}"
  if [ "$MEMAV" -lt "$OOM_FLOOR" ] 2>/dev/null; then
    echo "[oom-hold] $SS -> $H MemAvailable=${MEMAV}MB < floor ${OOM_FLOOR}MB — not launched"
    continue
  fi
  ENVV="V15_START_OVERRIDES=\$(test -f ~/v15_autopsy_first/${SS}_autopsy_base.json && echo ~/v15_autopsy_first/${SS}_autopsy_base.json) V15_FRESH_RUN=1 V15_TEMPLATE_DEFAULTS=1 V15_ADAPT_BASELINE=0 V15_SKIP_LIVE_AT_DONE=1 V15_POSSYM_SAMPLING=0 V15_INERT_SAMPLE_EVERY=100 V15_POSSYM_MIN_N=1 V15_FILL_SLICE_S=4200 V15_DIAG_REPAIR_S=300 V15_ENDGAME_S=240 V15_FOCUS_S_WINNER=300 V15_FOCUS_S_MEDIOCRE=150 V15_POOL_BATCH_N=8 V15_UNWIRED_SKIP=0 V12_NPZ_CACHE=8 V15_PROGRESS_DIR=$PDIR V15_DEFAULTS_ROUND=\$(cat ~/v15_defaults_round.txt 2>/dev/null) CAT_SIDE_DEFAULTS_PATH=\$(test -f ~/binance-sandbox/data/sweep_defaults/per_sym_settings.json && echo ~/binance-sandbox/data/sweep_defaults/per_sym_settings.json)"
  TPL="SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_${CAT}_${SIDE}.xlsx"
  BODY="cd ~/binance-sandbox && mkdir -p logs && $ENVV nice -n 5 .venv/bin/python -u v15_pilot.py --sym-side $SS --template $TPL --seq-mode worst2best --window-days 30 --vector-only --workers $W"
  if [ "$IS_LOCAL" = 1 ]; then
    (setsid nohup bash -c "$BODY" >> "/tmp/sweep_${SS}_30D.log" 2>&1 < /dev/null &) > /dev/null 2>&1
  else
    $SSH "niels@$H" "(setsid nohup bash -c $(printf '%q' "$BODY") >> /tmp/sweep_${SS}_30D.log 2>&1 < /dev/null &) > /dev/null 2>&1"
  fi
  echo "[launch] $SS -> $H workers=$W (log /tmp/sweep_${SS}_30D.log)"
done
