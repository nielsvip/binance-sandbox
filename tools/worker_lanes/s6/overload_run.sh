#!/bin/bash
# usage: overload_run.sh SS TEMPLATE WORKERS [MAXSIDES]  (runs on worker, cwd anywhere)
SS=$1; T=$2; W=$3; MAX=${4:-4}
[[ -e /tmp/v15_preopen_7d.pause ]] && { echo PAUSED-"${SS}"; exit 0; }
rsync -az --timeout=15 niels@10.0.0.3:binance-sandbox/data/v15_best_first.json /tmp/v15_best_first.json >/dev/null 2>&1 || { echo NO-SNAPSHOT-"${SS}"; exit 0; }
/home/niels/.conda/envs/binance_env_mac/bin/python "$HOME/binance-sandbox/tools/v15_best_first.py" --snapshot /tmp/v15_best_first.json --allowed "$SS" || { echo NOT-TRADEABLE-"${SS}"; exit 0; }
# USER 2026-10-25 global launch lock: scheduler ssh-launches and self-dispatch share
# /tmp/v15_fleet_launch_v2.lock so the box never exceeds MAXSIDES even when both fire the
# same minute (4 symbols max — no 5th until a workbook finishes). Non-blocking: contention
# echoes BUSY and the caller retries next cycle/tick. Count is INSIDE the lock (no TOCTOU).
if [ "${OVL_LOCKED}" != 1 ]; then exec env OVL_LOCKED=1 flock -n "/tmp/v15_fleet_launch_v2.lock" "$0" "$@"; echo "BUSY-${SS}"; exit 0; fi
_nrun=$(pgrep -af "^[^ ]*python[^ ]* -u ([^ ]*/)?v15_pilot(_7d|_no_publish_resume)?[.]py --sym-side " 2>/dev/null | grep -oE -- '--sym-side [A-Z0-9_]+' | sort -u | wc -l)
[ "$_nrun" -ge "$MAX" ] && { echo FULL-"${SS}"; exit 0; }
_avail_mb=$(awk '/^MemAvailable:/ {print int($2/1024)}' /proc/meminfo)
if [ "$_avail_mb" -lt 14000 ]; then
  [ "$_avail_mb" -ge 7500 ] || { echo "MEM_WAIT-${SS}-${_avail_mb}MB"; exit 0; }
  W=1
  echo "MEM_THROTTLE-${SS}-1-worker-${_avail_mb}MB"
fi
PDIR=$(head -n 1 "$HOME/v15_current_progress_dir.txt" 2>/dev/null)
[[ "$PDIR" == /home/niels/v15_run*/progress ]] || { echo "INVALID ROUND POINTER: $PDIR" >&2; exit 2; }
GATE="V15_ADAPT_BASELINE=0 V15_BOARD_BUDGET=0 V15_DIAG_REPAIR_S=150 V15_ENDGAME_S=120 V15_EVAL_CACHE=0 V15_FOCUS_S_BHCHASE=600 V15_FOCUS_S_LOSER=60 V15_FOCUS_S_MEDIOCRE=150 V15_FOCUS_S_WINNER=600 V15_INERT_SAMPLE_EVERY=100 V15_POOL_BATCH_N=8 V15_POSSYM_MIN_N=1 V15_POSSYM_SAMPLING=0 V15_TEMPLATE_DEFAULTS=1 V15_TIER_BIG_MOVER=8 V15_UNWIRED_SKIP=0 V15_V17_COMBO_SHARE=0.35 V15_V17_COMBO_SHARE_WINNER=0.4 V15_V17_P3_MAX_S_LOSER=300.0 V15_V17_P3_MAX_S_LOST_CAUSE=120.0 V15_V17_P3_MAX_S_MEDIOCRE=648.0 V15_V17_P3_MAX_S_WINNER=1500.0 V15_V17_P_MIN=0.006 V15_V17_ROUNDS=1 V15_V17_SHARE_ABLATION=0.48 V15_V17_SHARE_FOCUS=0.6 V15_V17_SHARE_RETEST=0.84 V15_V17_SHARE_SURGEON=0.6 V15_V17_SHARE_SURGEON_WINNER=0.9"
flock -n /tmp/v15_launch_${SS}_30D.lock bash -c '
pgrep -f "^[^ ]*python[^ ]* -u ([^ ]*/)?v15_pilot(_7d|_no_publish_resume)?[.]py --sym-side '"${SS}"' " >/dev/null && { echo ALREADY-'"${SS}"'; exit 0; }
cd ~/binance-sandbox || exit 3
export V15_START_OVERRIDES=$(test -f ~/v15_autopsy_first/'"${SS}"'_autopsy_base.json -a "$(python3 -c "import json;d=json.load(open(\"'"${PDIR}"'/'"${SS}"'_v14_progress.json\"));print(int(bool(d.get('"'"'done'"'"'))))" 2>/dev/null||echo 0)" = 0 && echo ~/v15_autopsy_first/'"${SS}"'_autopsy_base.json)
export V15_FRESH_RUN=$(python3 -c "import json;d=json.load(open(\"'"${PDIR}"'/'"${SS}"'_v14_progress.json\"));print(int(not d.get('"'"'done'"'"')))" 2>/dev/null||echo 1)
export V15_PROGRESS_DIR='"${PDIR}"'
export V12_ALLOW_STALE_NPZ=$(test -s '"${PDIR}"'/'"${SS}"'_v14_progress.json && echo 1 || echo 0)
export V15_DEFAULTS_ROUND=$(cat ~/v15_defaults_round.txt 2>/dev/null)
export CAT_SIDE_DEFAULTS_PATH=$(test -f ~/binance-sandbox/data/sweep_defaults/per_sym_settings.json && echo ~/binance-sandbox/data/sweep_defaults/per_sym_settings.json)
export V15_PAIR_MODE=0 V15_TEMPLATE_DEFAULTS=1 V15_ADAPT_BASELINE=0 V15_SKIP_LIVE_AT_DONE=1 FORCE_DC_RERUN=1 V15_POSSYM_SAMPLING=0 V15_INERT_SAMPLE_EVERY=100 V15_POSSYM_MIN_N=1 V15_FILL_SLICE_S=4200 V15_DIAG_REPAIR_S=150 V15_ENDGAME_S=120 V15_FOCUS_S_WINNER=600 V15_FOCUS_S_MEDIOCRE=150 V15_FOCUS_S_LOSER=60 V15_TIER_BIG_MOVER=8 V15_FOCUS_S_BHCHASE=600 V15_FOCUS_TOP_K_BHCHASE=48 V15_POOL_BATCH_N=8 V15_UNWIRED_SKIP=0 V12_NPZ_CACHE=4 MALLOC_ARENA_MAX=2 V15_V17_ROUNDS=1 V15_PREV_BEST_START=0 '"${GATE}"' V15_BOARD_BUDGET=0
# USER 2026-10-25 lock-inheritance fix: pilots must not inherit dispatch/launch
# flock fds (an inheriting pilot holds the lock for its whole lifetime and the
# */1 dispatcher can then only run when zero pilots run — lanes serialize to 1).
for _fd in /proc/$$/fd/*; do _fn=${_fd##*/}; [ "$_fn" -gt 2 ] 2>/dev/null && eval "exec $_fn>&-" 2>/dev/null; done
nohup /home/niels/.conda/envs/binance_env_mac/bin/python -u v15_pilot.py --sym-side '"${SS}"' --template '"${T}"' --seq-mode worst2best --window-days 30 --vector-only --workers '"${W}"' >> /tmp/sweep_'"${SS}"'_30D.log 2>&1 </dev/null &
echo LAUNCHED-'"${SS}"'
'
