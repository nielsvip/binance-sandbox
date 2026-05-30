#!/bin/bash
# watchdog_persym_s1.sh — keep S1 at the utilization floor (USER 2026-05-30): run the 4yr baseline + per_sym
# optimizer over the real universe (tradeable_keys + symbols_trb) as N SHARDED single-process instances (reliable;
# multiprocessing.Pool hung on the heavy imports). Each shard handles keys j%N==i. Merges shard outputs when all
# done. Log to ~/logs, disown, < /dev/null per SWEEP-LIVENESS MANDATE. Re-launch via cron keeps S1 busy.
set -u
PY=/home/niels/.conda/envs/binance_env/bin/python
SANDBOX=/home/niels/binance-sandbox
LOGS=/home/niels/logs
N=${1:-10}
cd "$SANDBOX" || exit 1
if pgrep -f "persym_optimize.py --shard" >/dev/null; then
  echo "[persym-wd] $(pgrep -fc 'persym_optimize.py --config') shard(s) already running — skip"; exit 0
fi
ts=$(date +%Y%m%d_%H%M%S)
echo "[persym-wd] launching $N shards ts=$ts"
for i in $(seq 1 "$N"); do
  nohup nice -n 5 "$PY" -u tools/persym_optimize.py --shard "$i/$N" \
     --out "data/_diagnostic/persym_opt_shard_${i}of${N}.json" \
     > "$LOGS/persym_shard_${i}of${N}_${ts}.log" 2>&1 < /dev/null &
  disown
done
sleep 15
echo "[persym-wd] shards alive: $(pgrep -fc 'persym_optimize.py --shard')"
top -bn1 | head -3 | tail -1
