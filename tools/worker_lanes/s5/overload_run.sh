#!/bin/bash
# usage: overload_run.sh SS TEMPLATE WORKERS [MAXSIDES]  (runs on worker, cwd anywhere)
SS=$1; T=$2; W=$3; MAX=${4:-4}
[[ -e /tmp/v15_preopen_7d.pause ]] && { echo PAUSED-"${SS}"; exit 0; }
# 2026-10-27: this used to exit on ANY rsync failure, so a slow or OOM-ing S1 blocked every launch on
# every worker -- observed live as NO-SNAPSHOT-<SYM> while S1 sat at load 28. Fall back to the local
# snapshot while it is fresh enough; only refuse when there is no usable ordering at all.
if ! rsync -az --timeout=15 niels@10.0.0.3:binance-sandbox/data/v15_best_first.json /tmp/v15_best_first.json >/dev/null 2>&1; then
  _snage=$(( $(date +%s) - $(stat -c %Y /tmp/v15_best_first.json 2>/dev/null || echo 0) ))
  if [ -s /tmp/v15_best_first.json ] && [ "$_snage" -lt "${V15_BEST_FIRST_MAX_AGE_S:-21600}" ]; then
    echo "SNAPSHOT-STALE-OK-${SS}-${_snage}s"
  else
    echo NO-SNAPSHOT-"${SS}"; exit 0
  fi
fi
/home/niels/.conda/envs/binance_env_mac/bin/python "$HOME/binance-sandbox/tools/v15_best_first.py" --snapshot /tmp/v15_best_first.json --allowed "$SS" || { echo NOT-TRADEABLE-"${SS}"; exit 0; }
# USER 2026-10-25 global launch lock: scheduler ssh-launches and self-dispatch share
# /tmp/v15_fleet_launch_v2.lock so the box never exceeds MAXSIDES even when both fire the
# same minute (4 symbols max — no 5th until a workbook finishes). Non-blocking: contention
# echoes BUSY and the caller retries next cycle/tick. Count is INSIDE the lock (no TOCTOU).
if [ "${OVL_LOCKED}" != 1 ]; then exec env OVL_LOCKED=1 flock -n "/tmp/v15_fleet_launch_v2.lock" "$0" "$@"; echo "BUSY-${SS}"; exit 0; fi
_nrun=$(pgrep -af "^[^ ]*python[^ ]* -u ([^ ]*/)?v15_pilot(_7d|_no_publish_resume)?[.]py --sym-side " 2>/dev/null | grep -oE -- '--sym-side [A-Z0-9_]+' | sort -u | wc -l)
[ "$_nrun" -ge "$MAX" ] && { echo FULL-"${SS}"; exit 0; }
_avail_mb=$(awk '/^MemAvailable:/ {print int($2/1024)}' /proc/meminfo)
# OPERATOR 2026-10-27: every server runs 28 workers for 4 symbols x 2 sides (4 sym_sides x 7 workers).
# The old gate was a cliff -- below 14000MB it forced W=1 -- which meant only the FIRST of four boards
# ever got 7 workers: every later launch sees memory already held by the boards before it, so boards 2-4
# ran SINGLE-THREADED and each host delivered about a third of its design throughput. Measured 2026-10-27:
# s2 ran 4 boards all at --workers 1, parent at 0% CPU with one child at 100%, 0.73 boards/h/host.
# Marginal cost of a 7-worker board is ~5.75GB (S1 held 28 workers in ~23GB of 31GB), so admit a full
# 7-worker board while >=9000MB remains, degrade to 3 between 6500 and 9000, refuse below 6500.
# The 2026-10-27 OOM storm was vec_bar_trigger leaking 25GB on s6 / 10GB on s5 (now capped by
# BAR_TRIGGER_RSS_CAP) plus orphaned fork-workers from OOM-killed parents (now reaped by
# V15_PILOT_ORPHAN_REAPER) -- not the worker count. Both guards run every 5 min.
if [ "$_avail_mb" -lt 6500 ]; then
  echo "MEM_WAIT-${SS}-${_avail_mb}MB"; exit 0
elif [ "$_avail_mb" -lt 9000 ]; then
  W=3
  echo "MEM_THROTTLE-${SS}-3-workers-${_avail_mb}MB"
fi
PDIR=$(head -n 1 "$HOME/v15_current_progress_dir.txt" 2>/dev/null)
[[ "$PDIR" == /home/niels/v15_run*/progress ]] || { echo "INVALID ROUND POINTER: $PDIR" >&2; exit 2; }
# OPERATOR 2026-10-27: "a server is not allowed to take a symbol out of ram until both sides (or
# whatever is tradeable) is COMPLETELY FINISHED AND PUBLISHED". V15_FILL_SLICE_S is therefore 0 (off)
# in both places below. It was 4200s, and v15_pilot.py:5385/:14722 used it to exit a board mid-sweep
# with "[SLICE] fill hit cap - clean exit, scheduler relaunches into holes-only resume". Measured
# 2026-10-27: that cap landed every board at ~2700 of 3804 template rows, and the resume re-swept
# instead of filling holes (NLR_SHORT went 3803 -> 1411 -> 2681 filled) so no board ever passed the
# same ceiling. 43 of 44 boards on s6 and 51 of 55 on s5 were short, ALB_SHORT and NLR_SHORT both
# stuck at exactly 2681/3804 with 1123 template rows never visited once. The harvester needs 100% of
# the template rows, so a sliced board can never be published: that is why there was never a result.
GATE="V15_FILL_SLICE_S=0 V15_ADAPT_BASELINE=0 V15_BOARD_BUDGET=0 V15_DIAG_REPAIR_S=150 V15_ENDGAME_S=120 V15_EVAL_CACHE=0 V15_FOCUS_S_BHCHASE=600 V15_FOCUS_S_LOSER=60 V15_FOCUS_S_MEDIOCRE=150 V15_FOCUS_S_WINNER=600 V15_INERT_SAMPLE_EVERY=100 V15_POOL_BATCH_N=8 V15_POSSYM_MIN_N=1 V15_POSSYM_SAMPLING=0 V15_TEMPLATE_DEFAULTS=1 V15_TIER_BIG_MOVER=8 V15_UNWIRED_SKIP=0 V15_V17_COMBO_SHARE=0.35 V15_V17_COMBO_SHARE_WINNER=0.4 V15_V17_P3_MAX_S_LOSER=300.0 V15_V17_P3_MAX_S_LOST_CAUSE=120.0 V15_V17_P3_MAX_S_MEDIOCRE=648.0 V15_V17_P3_MAX_S_WINNER=1500.0 V15_V17_P_MIN=0.006 V15_V17_ROUNDS=1 V15_V17_SHARE_ABLATION=0.48 V15_V17_SHARE_FOCUS=0.6 V15_V17_SHARE_RETEST=0.84 V15_V17_SHARE_SURGEON=0.6 V15_V17_SHARE_SURGEON_WINNER=0.9"
flock -n /tmp/v15_launch_${SS}_30D.lock bash -c '
pgrep -f "^[^ ]*python[^ ]* -u ([^ ]*/)?v15_pilot(_7d|_no_publish_resume)?[.]py --sym-side '"${SS}"' " >/dev/null && { echo ALREADY-'"${SS}"'; exit 0; }
cd ~/binance-sandbox || exit 3
export V15_START_OVERRIDES=$(test -f ~/v15_autopsy_first/'"${SS}"'_autopsy_base.json -a "$(python3 -c "import json;d=json.load(open(\"'"${PDIR}"'/'"${SS}"'_v14_progress.json\"));print(int(bool(d.get('"'"'done'"'"'))))" 2>/dev/null||echo 0)" = 0 && echo ~/v15_autopsy_first/'"${SS}"'_autopsy_base.json)
export V15_FRESH_RUN=$(python3 -c "import json;d=json.load(open(\"'"${PDIR}"'/'"${SS}"'_v14_progress.json\"));print(int(not d.get('"'"'done'"'"')))" 2>/dev/null||echo 1)
export V15_PROGRESS_DIR='"${PDIR}"'
export V12_ALLOW_STALE_NPZ=$(test -s '"${PDIR}"'/'"${SS}"'_v14_progress.json && echo 1 || echo 0)
export V15_DEFAULTS_ROUND=$(cat ~/v15_defaults_round.txt 2>/dev/null)
export CAT_SIDE_DEFAULTS_PATH=$(test -f ~/binance-sandbox/data/sweep_defaults/per_sym_settings.json && echo ~/binance-sandbox/data/sweep_defaults/per_sym_settings.json)
export V15_PAIR_MODE=0 V15_TEMPLATE_DEFAULTS=1 V15_ADAPT_BASELINE=0 V15_SKIP_LIVE_AT_DONE=1 FORCE_DC_RERUN=1 V15_POSSYM_SAMPLING=0 V15_INERT_SAMPLE_EVERY=100 V15_POSSYM_MIN_N=1 V15_FILL_SLICE_S=0 V15_DIAG_REPAIR_S=150 V15_ENDGAME_S=120 V15_FOCUS_S_WINNER=600 V15_FOCUS_S_MEDIOCRE=150 V15_FOCUS_S_LOSER=60 V15_TIER_BIG_MOVER=8 V15_FOCUS_S_BHCHASE=600 V15_FOCUS_TOP_K_BHCHASE=48 V15_POOL_BATCH_N=8 V15_UNWIRED_SKIP=0 V12_NPZ_CACHE=4 MALLOC_ARENA_MAX=2 V15_V17_ROUNDS=1 V15_PREV_BEST_START=0 '"${GATE}"' V15_BOARD_BUDGET=0
# USER 2026-10-25 lock-inheritance fix: pilots must not inherit dispatch/launch
# flock fds (an inheriting pilot holds the lock for its whole lifetime and the
# */1 dispatcher can then only run when zero pilots run — lanes serialize to 1).
for _fd in /proc/$$/fd/*; do _fn=${_fd##*/}; [ "$_fn" -gt 2 ] 2>/dev/null && eval "exec $_fn>&-" 2>/dev/null; done
nohup /home/niels/.conda/envs/binance_env_mac/bin/python -u v15_pilot.py --sym-side '"${SS}"' --template '"${T}"' --seq-mode worst2best --window-days 30 --vector-only --workers '"${W}"' >> /tmp/sweep_'"${SS}"'_30D.log 2>&1 </dev/null &
echo LAUNCHED-'"${SS}"'
'
