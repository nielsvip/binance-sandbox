#!/bin/bash
# v15_must_fleet_refresh.sh — USER 2026-10-21: keep catch-up MUST fed from
# ALL hosts' slow positives ("always revise"). Steps:
#   1. rebuild the per-worker index on each worker over its LOCAL delta logs
#      (small JSON out; HOME set explicitly — ssh env leaks HOME=/Users/niels
#      to s2 which breaks ~ expansion)
#   2. pull the small JSONs to s1 (never bulk-copy logs; s1 disk is tight)
#   3. merge with s1's own index (backup first) into data/learn (synced to
#      workers by SYNC_FLEET_STATE within ~2 min)
#   4. incremental s1-local rebuild (watermarks) + re-merge
# Cron on s1 (example): */30 * * * * cd ... && flock -n /tmp/v15_must_fleet.lock timeout 1700 bash tools/v15_must_fleet_refresh.sh >> /tmp/v15_must_index.log 2>&1
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 1
PY=/home/niels/.conda/envs/binance_env_mac/bin/python
HO="ssh -o BatchMode=yes -o ConnectTimeout=8"
echo "=== must-fleet-refresh $(date -u +%FT%TZ) ==="
for ip in 10.0.0.4 10.0.0.5 10.0.0.6; do
  $HO niels@$ip 'cd ~/binance-sandbox && mkdir -p /tmp/must_local && HOME=/home/niels timeout 1500 /home/niels/.conda/envs/binance_env_mac/bin/python -u tools/v15_v18_must_index.py --out-dir /tmp/must_local >>/tmp/must_local/build.log 2>&1' || echo "WARN worker build failed: $ip"
done
mkdir -p /tmp/must_pull/s2 /tmp/must_pull/s5 /tmp/must_pull/s6
$HO niels@10.0.0.4 true || echo "WARN s2 unreachable"
for ip in 10.0.0.4 10.0.0.5 10.0.0.6; do
  case $ip in 10.0.0.4) h=s2;; 10.0.0.5) h=s5;; *) h=s6;; esac
  rsync -az --timeout=120 -e "$HO" "niels@$ip:/tmp/must_local/must_pos_CRYPTO_LONG.json" "niels@$ip:/tmp/must_local/must_pos_CRYPTO_SHORT.json" "niels@$ip:/tmp/must_local/must_pos_STOCKS_LONG.json" "niels@$ip:/tmp/must_local/must_pos_STOCKS_SHORT.json" /tmp/must_pull/$h/ || echo "WARN pull failed: $h"
done
$PY tools/v15_must_merge.py --inputs s1=data/learn s2=/tmp/must_pull/s2 s5=/tmp/must_pull/s5 s6=/tmp/must_pull/s6 --out-dir data/learn --backup-dir backups/must_merge_fleet || exit 3
$PY tools/v15_v18_must_index.py --logs '~/v15_run*/progress/v15_delta_log/*.jsonl' --out-dir data/learn || echo "WARN s1 incremental failed"
$PY tools/v15_must_merge.py --inputs s1=data/learn s2=/tmp/must_pull/s2 s5=/tmp/must_pull/s5 s6=/tmp/must_pull/s6 --out-dir data/learn --backup-dir backups/must_merge_fleet || exit 4
echo "=== must-fleet-refresh done $(date -u +%FT%TZ) ==="
