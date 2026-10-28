#!/bin/bash
# Push the exact Mac live full-set snapshots used to start each new backtest.
# 2026-10-28 fleet throughput: the four hosts were pushed one after another (10-17 min per cycle, plus failures), so the
# manifest the pilot reads reached 1800-2200 s while the pilot refuses > 900 s ([BASELINE-PERSYM-SNAPSHOT-INVALID]): S2
# boards died at start. Hosts are now pushed in parallel; ssh without the shared ControlMaster so one wedged mux cannot
# stall the others.
set -u
cd /Users/niels/Documents/binance || exit 1
lock=/tmp/mac_live_baseline_push_fleet.lock
if ! mkdir "$lock" 2>/dev/null; then
  # a lock older than 20 min belongs to a dead run: take it over
  if [ -n "$(find "$lock" -maxdepth 0 -mmin +20 2>/dev/null)" ]; then rmdir "$lock" 2>/dev/null; mkdir "$lock" 2>/dev/null || exit 0; else exit 0; fi
fi
trap 'rmdir "$lock" 2>/dev/null' EXIT INT TERM
py=/opt/anaconda3/envs/binance_env/bin/python
t0=$(date +%s)
"$py" tools/mac_live_baseline_export.py || exit 1
src=data/live_baseline_snapshots/
ssh_cmd="ssh -o ControlPath=none -o BatchMode=yes -o ConnectTimeout=20 -o ServerAliveInterval=10 -o ServerAliveCountMax=3"
push_one() {
  local host=$1 dest="$1:~/binance-sandbox/data/live_baseline_snapshots/" try
  for try in 1 2; do
    if rsync -az --timeout=240 -e "$ssh_cmd" --exclude=manifest.json "$src" "$dest" \
         && rsync -az --timeout=60 -e "$ssh_cmd" "${src}manifest.json" "$dest"; then
      echo "$(date -u +%FT%TZ) $host Mac live sets synced (try $try, $(( $(date +%s) - t0 ))s after export start)"
      return 0
    fi
  done
  echo "$(date -u +%FT%TZ) $host Mac live set sync FAILED" >&2
}
for host in s1-pub s2 s5 s6; do
  push_one "$host" &
done
wait
