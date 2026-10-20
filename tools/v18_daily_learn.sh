#!/bin/bash
# v18_daily_learn.sh — the V18 learning layer's daily chain (doc 17 §3.2/§3.5/§5 ABL-0/§3.6), Mac side.
# Runs 2 -> 3 -> 4 -> 5 of the L1 handover (docs/v15_pending/19_L1_LEARNING_LAYER_HANDOVER_20261020.md):
#   pull   the S2 evidence (tools/v18_evidence_mine.py output) + trade ledgers + measurements      (read-only rsync)
#   2      tools/v18_exit_loss_rank.py build    exit-loss rank + repair menu per cat_side
#   3      tools/v18_winners_pack.py            winners pack + xpoll per cat_side
#   4      tools/v18_filter_veto_map.py build   filter-veto map (+ additive data/v17_filter_exemptions.json, backup first)
#   5      tools/v18_digest.py                  the four digests (contract v18_digest_v1) + md5-verified push to s1-int / s2
# and prints the coverage line per cat_side. NO cron is installed by this script (USER decides scheduling).
# Dry-run by default: every tool runs without --apply and nothing is written or pushed. --apply writes + pushes.
# --mine first re-runs the miner on S2 (nice 10, 2 workers; ~6 min) before pulling; --no-pull skips the rsync.
set -u
set -o pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 1
PY="$(bash tools/pyinterp.sh 2>/dev/null || echo python3)"
APPLY=""
MINE=0
PULL=1
for a in "$@"; do
    case "$a" in
        --apply) APPLY="--apply" ;;
        --mine) MINE=1 ;;
        --no-pull) PULL=0 ;;
    esac
done
echo "[daily-learn] $(date -u +%FT%TZ) apply=${APPLY:-dry-run} mine=$MINE pull=$PULL"
if [ "$MINE" = 1 ]; then
    # fresh read-only copy of S1's hot delta logs onto S2 (S1: nice 15 / ionice idle; internal network)
    ssh -o ConnectTimeout=15 s1-int 'mkdir -p ~/logs; for d in ~/v15_run*/progress/v15_delta_log; do [ -d "$d" ] && nice -n 15 ionice -c3 rsync -a --bwlimit=60000 "$d/" s2:v18_scratch/s1_run33_delta_log/; done' || echo "[daily-learn] WARN S1 log copy failed"
    ssh -o ConnectTimeout=15 s2 'cd ~/binance-sandbox && nice -n 10 python3 tools/v18_evidence_mine.py --src s2="~/v15_run*/progress/v15_delta_log/*.jsonl" --src s1="~/v18_scratch/s1_run33_delta_log/*.jsonl" --workers 2 --scratch ~/v18_scratch/mine --out-dir ~/v18_learn/evidence > ~/logs/v18_evidence_mine.log 2>&1 < /dev/null; tail -1 ~/logs/v18_evidence_mine.log' || echo "[daily-learn] WARN miner on s2 failed — using the last pulled evidence"
fi
if [ "$PULL" = 1 ]; then
    mkdir -p data/learn/evidence data/learn/trade_ledgers data/learn/menu_measure data/learn/veto_measure
    rsync -a --timeout=120 s2:v18_learn/evidence/ data/learn/evidence/ || echo "[daily-learn] WARN evidence pull failed"
    rsync -a --timeout=300 s2:v18_learn/trade_ledgers/ data/learn/trade_ledgers/ || echo "[daily-learn] WARN ledger pull failed"
    rsync -a --timeout=120 s2:v18_learn/menu_measure/ data/learn/menu_measure/ 2>/dev/null || true
    rsync -a --timeout=120 s2:v18_learn/veto_measure/ data/learn/veto_measure/ 2>/dev/null || true
fi
$PY tools/v18_exit_loss_rank.py build $APPLY > /tmp/v18_daily_rank.out 2>&1 || { echo "[daily-learn] step 2 FAILED"; tail -5 /tmp/v18_daily_rank.out; exit 2; }
echo "[daily-learn] step 2 exit-loss rank OK"
$PY tools/v18_winners_pack.py $APPLY 2>&1 | grep '^\[winners\]' | cut -c1-200 || { echo "[daily-learn] step 3 FAILED"; exit 3; }
$PY tools/v18_filter_veto_map.py build $APPLY 2>&1 | grep '^\[veto\]' | cut -c1-200 || { echo "[daily-learn] step 4 FAILED"; exit 4; }
$PY tools/v18_digest.py $APPLY 2>&1 | grep '^\[digest\]' || { echo "[daily-learn] step 5 FAILED"; exit 5; }
if [ -n "$APPLY" ]; then
    bash tools/v18_digest_push.sh || { echo "[daily-learn] push FAILED (md5)"; exit 6; }
else
    bash tools/v18_digest_push.sh --dry-run
fi
echo "[daily-learn] done $(date -u +%FT%TZ)"
