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
# 2026-06-04 OOM PROTECTION: S1 RAM (31GB) is the binding constraint. 8 NPZ-decompressing shards on top
# of live ez_manage + sweeps caused global OOM that killed LIVE trading procs (men respawned 8x/2.5h).
# (1) free-memory gate: never add a shard spike when memory is already tight. (2) each shard gets a high
# oom_score_adj so the kernel sacrifices BACKTEST shards first, never live trading (which stays at 0).
AVAIL_MB=$(free -m | awk '/^Mem:/{print $7}')
MIN_FREE_MB=${MIN_FREE_MB:-5000}
if [ "${AVAIL_MB:-0}" -lt "$MIN_FREE_MB" ]; then
  echo "[persym-wd] available ${AVAIL_MB}MB < ${MIN_FREE_MB}MB — skip launch (protect live from OOM)"; exit 0
fi
echo "[persym-wd] launching $N shards ts=$ts (avail ${AVAIL_MB}MB)"
for i in $(seq 1 "$N"); do
  nohup nice -n 5 "$PY" -u tools/persym_optimize.py --shard "$i/$N" \
     --out "data/_diagnostic/persym_opt_shard_${i}of${N}.json" \
     > "$LOGS/persym_shard_${i}of${N}_${ts}.log" 2>&1 < /dev/null &
  _shard_pid=$!
  echo 900 > "/proc/${_shard_pid}/oom_score_adj" 2>/dev/null || true
  disown
done
sleep 15
echo "[persym-wd] shards alive: $(pgrep -fc 'persym_optimize.py --shard')"
top -bn1 | head -3 | tail -1
