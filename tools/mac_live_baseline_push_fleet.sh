#!/bin/bash
# Push the exact Mac live full-set snapshots used to start each new backtest.
set -u
cd /Users/niels/Documents/binance || exit 1
lock=/tmp/mac_live_baseline_push_fleet.lock
mkdir "$lock" 2>/dev/null || exit 0
trap 'rmdir "$lock" 2>/dev/null' EXIT INT TERM
py=/opt/anaconda3/envs/binance_env/bin/python
"$py" projects/template_parity_20261025/live_set_divergence_20261026/export_live_baseline.py || exit 1
src=data/live_baseline_snapshots/
for host in s1-pub s2 s5 s6; do
  dest="$host:~/binance-sandbox/data/live_baseline_snapshots/"
  if rsync -az --timeout=240 --exclude=manifest.json "$src" "$dest" \
       && rsync -az --timeout=60 "${src}manifest.json" "$dest"; then
    echo "$(date -u +%FT%TZ) $host Mac live sets synced"
  else
    echo "$(date -u +%FT%TZ) $host Mac live set sync FAILED" >&2
  fi
done
