#!/bin/bash
# Push the exact Mac live full-set snapshots used to start each new backtest.
# 2026-10-28 fleet throughput: the four hosts were pushed one after another (10-17 min per cycle, plus failures), so the
# manifest the pilot reads reached 1800-2200 s while the pilot refuses > 900 s ([BASELINE-PERSYM-SNAPSHOT-INVALID]): S2
# boards died at start. Parallel pushes still took 555-630 s on cycles where many sets changed (4 copies over the Mac
# uplink) and the S1 public path dropped. Now: ONE copy Mac -> s2, then s2 fans out to s1/s5/s6 over the private
# network (10.0.0.x); a host the fan-out cannot reach gets a direct Mac push as fallback. Sets first, manifest last.
export PATH=/opt/homebrew/bin:/usr/local/bin:$PATH  # cron PATH lacks homebrew `timeout` (memory: mac_cron_gnu_coreutils_gap)
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
rel="binance-sandbox/data/live_baseline_snapshots/"
ssh_cmd="ssh -o ControlPath=none -o BatchMode=yes -o ConnectTimeout=20 -o ServerAliveInterval=10 -o ServerAliveCountMax=3"
push_direct() {
  local host=$1 try
  for try in 1 2; do
    if rsync -az --timeout=240 -e "$ssh_cmd" --exclude=manifest.json "$src" "$host:~/$rel" \
         && rsync -az --timeout=60 -e "$ssh_cmd" "${src}manifest.json" "$host:~/$rel"; then
      echo "$(date -u +%FT%TZ) $host Mac live sets synced (direct try $try, $(( $(date +%s) - t0 ))s after export start)"
      return 0
    fi
  done
  echo "$(date -u +%FT%TZ) $host Mac live set sync FAILED" >&2
  return 1
}
push_direct s2 || { for host in s1-pub s5 s6; do push_direct "$host" & done; wait; exit 0; }
fan() {
  local host=$1 ip
  case "$host" in s1-pub) ip=10.0.0.3;; s5) ip=10.0.0.5;; s6) ip=10.0.0.6;; esac
  if timeout 600 $ssh_cmd s2 "rsync -az --timeout=120 -e 'ssh -o BatchMode=yes -o ConnectTimeout=10' --exclude=manifest.json ~/$rel niels@$ip:~/$rel && rsync -az --timeout=60 -e 'ssh -o BatchMode=yes -o ConnectTimeout=10' ~/${rel}manifest.json niels@$ip:~/$rel"; then
    echo "$(date -u +%FT%TZ) $host Mac live sets synced (fan-out via s2, $(( $(date +%s) - t0 ))s after export start)"
  else
    push_direct "$host"
  fi
}
for host in s1-pub s5 s6; do
  fan "$host" &
done
wait
