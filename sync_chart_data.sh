#!/bin/bash
# sync_chart_data.sh — pull latest hourly_reconfig + canonical_trades from S1 to MB.
# SIMPLE rsync (no fancy include/exclude — just mirror, MB has disk).
# 2026-05-21: S2 (204.168.181.211) DEAD since 2026-05-08 — all tradier sweeps moved to S1.
# All rsync targets now point at S1 only. Per CLAUDE.md: "Never SSH s2-int".

set -u
LOG="${HOME}/Documents/binance/logs/sync_chart_data.log"
LOCK="${HOME}/Documents/binance/logs/.sync_chart_data.lock"
mkdir -p "$(dirname "$LOG")"
exec >>"$LOG" 2>&1
# Lockfile: skip if previous run still active.
if [ -f "$LOCK" ]; then
  pid=$(cat "$LOCK" 2>/dev/null)
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] previous sync (pid=$pid) still running, skip"
    exit 0
  fi
fi
echo $$ > "$LOCK"
trap 'rm -f "$LOCK"' EXIT
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] start"

MB_BASE="/Users/niels/Documents/binance"
RSYNC="rsync -a --no-perms --no-times --inplace --partial"

mkdir -p "$MB_BASE/data/hourly_reconfig" "$MB_BASE/data/canonical_trades" "$MB_BASE/data/sweep_results"

# S1 crypto: flz, fin, inf — hourly_reconfig dir mirror (--exclude=runs/ 2026-05-21: runs/ is 22GB audit trail Mac live trading never reads; chart_server/build_symbol_configs/parity_diff are on-demand tools, run on S1 instead)
for acct in flz fin inf; do
  echo "[sync] s1:hourly_reconfig/$acct"
  $RSYNC --timeout=180 --exclude='runs/' \
    "niels@157.180.125.52:/home/niels/binance-sandbox/data/hourly_reconfig/${acct}/" \
    "$MB_BASE/data/hourly_reconfig/${acct}/" 2>&1 || echo "  [warn] rsync exit $?"
done

# S1 tradier: trc, trb (was S2 pre-2026-05-08 shutdown — S1 confirmed has trb/trc dirs 2026-05-21)
for acct in trc trb; do
  echo "[sync] s1:hourly_reconfig/$acct"
  $RSYNC --timeout=180 --exclude='runs/' \
    "niels@157.180.125.52:/home/niels/binance-sandbox/data/hourly_reconfig/${acct}/" \
    "$MB_BASE/data/hourly_reconfig/${acct}/" 2>&1 || echo "  [warn] rsync exit $?"
done

# btc_settings_search candidates
echo "[sync] s1:_candidates"
$RSYNC --timeout=180 \
  "niels@157.180.125.52:/home/niels/binance-sandbox/data/hourly_reconfig/_candidates/" \
  "$MB_BASE/data/hourly_reconfig/_candidates/" 2>&1 || echo "  [warn] rsync exit $?"

# Big-sweep canonical trade dirs
echo "[sync] s1:canonical_trades"
$RSYNC --timeout=300 \
  "niels@157.180.125.52:/home/niels/binance-sandbox/data/canonical_trades/" \
  "$MB_BASE/data/canonical_trades/" 2>&1 || echo "  [warn] rsync exit $?"

# Canonical CSVs
echo "[sync] csvs"
$RSYNC --timeout=60 --include='canonical_*.csv' --exclude='*' \
  "niels@157.180.125.52:/home/niels/binance-sandbox/data/sweep_results/" \
  "$MB_BASE/data/sweep_results/" 2>&1 || echo "  [warn] rsync exit $?"
# S2 sweep_results rsync removed — S2 dead 2026-05-08 (host key change warnings 3677/day).
# Historical S2 sweep CSVs already archived at /Volumes/TOSHIBA_EXT/binance_archive/data/sweep_results/.

# Prune older cycles to last 5 per account (keeps MB disk reasonable)
for acct in flz fin inf trc trb; do
  d="$MB_BASE/data/hourly_reconfig/${acct}/runs"
  if [ -d "$d" ]; then
    ls -1t "$d" 2>/dev/null | tail -n +6 | while read old; do
      rm -rf "${d}/${old}" 2>/dev/null
    done
  fi
done

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] done"
