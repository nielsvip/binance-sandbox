#!/bin/bash
# fleet_launch.sh — worker-side launch of ONE 30D board for tools/fleet_dispatch.py (USER 2026-10-27 rewrite).
# Fed over ssh stdin (`ssh host bash -s -- SS W MAX TAIL BY < fleet_launch.sh`) so new/cloned servers need no deploy.
# No S1 dependency: the dispatcher already decided tradeability + order; this script only re-checks, inside the
# shared /tmp/v15_fleet_launch_v2.lock (same lock as worker_lanes/*/overload_run.sh), the slot count and memory,
# then spawns the pilot with the SAME env recipe as worker_lanes/s5/overload_run.sh (FORCE_DC_RERUN=1 for stocks
# only, as s2 crypto never carried it). Prints exactly one of LAUNCHED-/ALREADY-/FULL-/MEM_WAIT-/BUSY-/ERROR-.
SS=${1:?symside}; W=${2:-7}; MAX=${3:-4}; TAIL=${4:-0}; BY=${5:-?}
MEM_FULL=${FLEET_MEM_FULL_MB:-9000}; MEM_MIN=${FLEET_MEM_MIN_MB:-6500}
PY=/home/niels/.conda/envs/binance_env_mac/bin/python
SB=$HOME/binance-sandbox
case "$SS" in *USDT_LONG|*USDC_LONG) T=TEMPLATE_CRYPTO_LONG; DC=0;; *USDT_SHORT|*USDC_SHORT) T=TEMPLATE_CRYPTO_SHORT; DC=0;; *_LONG) T=TEMPLATE_STOCKS_LONG; DC=1;; *_SHORT) T=TEMPLATE_STOCKS_SHORT; DC=1;; *) echo "ERROR-$SS-bad-symside"; exit 0;; esac
T=$SB/SPREADSHEETS/TEMPLATE_FINAL_NORM/$T.xlsx
[ -f "$T" ] || { echo "ERROR-$SS-no-template"; exit 0; }
[ -x "$PY" ] || { echo "ERROR-$SS-no-python"; exit 0; }
PDIR=$(head -n 1 "$HOME/v15_current_progress_dir.txt" 2>/dev/null)
[[ "$PDIR" == /home/niels/v15_run*/progress ]] || { echo "ERROR-$SS-round-pointer"; exit 0; }
exec 9>/tmp/v15_fleet_launch_v2.lock
flock -n 9 || { echo "BUSY-$SS"; exit 0; }
RE='^[^ ]*python[^ ]* -u ([^ ]*/)?v15_pilot(_7d|_no_publish_resume)?[.]py --sym-side '
pgrep -f "${RE}${SS} " >/dev/null && { echo "ALREADY-$SS"; exit 0; }
NRUN=$(pgrep -af "$RE" 2>/dev/null | grep -oE -- '--sym-side [A-Z0-9_]+' | sort -u | wc -l)
[ "$NRUN" -ge "$MAX" ] && { echo "FULL-$SS-$NRUN"; exit 0; }
AVAIL=$(awk '/^MemAvailable:/ {print int($2/1024)}' /proc/meminfo)
[ "$AVAIL" -lt "$MEM_MIN" ] && { echo "MEM_WAIT-$SS-${AVAIL}MB"; exit 0; }
[ "$AVAIL" -lt "$MEM_FULL" ] && [ "$W" -gt 3 ] && W=3
PROG=$PDIR/${SS}_v14_progress.json
DONE=$($PY -c "import json,sys;d=json.load(open(sys.argv[1]));print(int(bool(d.get('done'))))" "$PROG" 2>/dev/null || echo 0)
cd "$SB" || { echo "ERROR-$SS-no-sandbox"; exit 0; }
export V15_START_OVERRIDES=$( [ -f "$HOME/v15_autopsy_first/${SS}_autopsy_base.json" ] && [ "$DONE" = 0 ] && echo "$HOME/v15_autopsy_first/${SS}_autopsy_base.json")
export V15_FRESH_RUN=$( [ "$DONE" = 1 ] && echo 0 || echo 1)
export V15_PROGRESS_DIR=$PDIR
export V12_ALLOW_STALE_NPZ=$( [ -s "$PROG" ] && echo 1 || echo 0)
export V15_DEFAULTS_ROUND=$(cat "$HOME/v15_defaults_round.txt" 2>/dev/null)
export CAT_SIDE_DEFAULTS_PATH=$( [ -f "$SB/data/sweep_defaults/per_sym_settings.json" ] && echo "$SB/data/sweep_defaults/per_sym_settings.json")
[ "$DC" = 1 ] && export FORCE_DC_RERUN=1
export V15_PAIR_MODE=0 V15_TEMPLATE_DEFAULTS=1 V15_ADAPT_BASELINE=0 V15_SKIP_LIVE_AT_DONE=1 V15_POSSYM_SAMPLING=0 V15_INERT_SAMPLE_EVERY=100 V15_POSSYM_MIN_N=1 V15_FILL_SLICE_S=0 V15_DIAG_REPAIR_S=150 V15_ENDGAME_S=120 V15_FOCUS_S_WINNER=600 V15_FOCUS_S_MEDIOCRE=150 V15_FOCUS_S_LOSER=60 V15_TIER_BIG_MOVER=8 V15_FOCUS_S_BHCHASE=600 V15_FOCUS_TOP_K_BHCHASE=48 V15_POOL_BATCH_N=8 V15_UNWIRED_SKIP=0 V12_NPZ_CACHE=4 MALLOC_ARENA_MAX=2 V15_V17_ROUNDS=1 V15_PREV_BEST_START=0
export V15_BOARD_BUDGET=0 V15_EVAL_CACHE=1 V15_EVAL_CACHE_DB="$SB/data/eval_cache_v2.db" V15_EVAL_CACHE_MAX_ROWS=100000 V15_V17_COMBO_SHARE=0.35 V15_V17_COMBO_SHARE_WINNER=0.4 V15_V17_P3_MAX_S_LOSER=300.0 V15_V17_P3_MAX_S_LOST_CAUSE=120.0 V15_V17_P3_MAX_S_MEDIOCRE=648.0 V15_V17_P3_MAX_S_WINNER=1500.0 V15_V17_P_MIN=0.006 V15_V17_SHARE_ABLATION=0.48 V15_V17_SHARE_FOCUS=0.6 V15_V17_SHARE_RETEST=0.84 V15_V17_SHARE_SURGEON=0.6 V15_V17_SHARE_SURGEON_WINNER=0.9
mkdir -p "$HOME/fleet_dispatch/launches"
REC=$HOME/fleet_dispatch/launches/$SS.json
PREV_N=$($PY -c "import json,sys;d=json.load(open(sys.argv[1]));print(int(d.get('n',0)) if int(d.get('tail',0))==int(sys.argv[2]) else 0)" "$REC" "$TAIL" 2>/dev/null || echo 0)
# Spawned in its own session with every fd >2 closed: the pilot must never inherit the launch lock (fd 9).
setsid nohup "$PY" -u v15_pilot.py --sym-side "$SS" --template "$T" --seq-mode worst2best --window-days 30 --vector-only --workers "$W" >> "/tmp/sweep_${SS}_30D.log" 2>&1 < /dev/null 9>&- &
PID=$!
printf '{"ss":"%s","ts":%d,"tail":%d,"n":%d,"workers":%d,"pid":%d,"by":"%s","avail_mb":%d}\n' "$SS" "$(date +%s)" "$TAIL" "$((PREV_N + 1))" "$W" "$PID" "$BY" "$AVAIL" > "$REC.tmp" && mv -f "$REC.tmp" "$REC"
sleep 2
if kill -0 "$PID" 2>/dev/null; then echo "LAUNCHED-$SS-w$W-pid$PID"; else echo "ERROR-$SS-died-at-start: $(tail -c 300 /tmp/sweep_${SS}_30D.log | tr '\n' ' ')"; fi
