#!/bin/bash
# sync_chart_data.sh — pull latest hourly_reconfig + canonical_trades from S1+S2 to MB.
# SIMPLE rsync (no fancy include/exclude — just mirror, MB has disk).

set -u
LOG="${HOME}/Documents/binance/logs/sync_chart_data.log"
mkdir -p "$(dirname "$LOG")"
exec >>"$LOG" 2>&1
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] start"

MB_BASE="/Users/niels/Documents/binance"
RSYNC="rsync -a --no-perms --no-times --inplace --partial"

mkdir -p "$MB_BASE/data/hourly_reconfig" "$MB_BASE/data/canonical_trades" "$MB_BASE/data/sweep_results"

# S1 crypto: flz, fin, inf — full hourly_reconfig dir mirror
for acct in flz fin inf; do
  echo "[sync] s1:hourly_reconfig/$acct"
  $RSYNC --timeout=180 \
    "niels@157.180.125.52:/home/niels/binance-sandbox/data/hourly_reconfig/${acct}/" \
    "$MB_BASE/data/hourly_reconfig/${acct}/" 2>&1 || echo "  [warn] rsync exit $?"
done

# S2 tradier: trc, trb
for acct in trc trb; do
  echo "[sync] s2:hourly_reconfig/$acct"
  $RSYNC --timeout=180 \
    "niels@204.168.181.211:/home/niels/binance-sandbox/data/hourly_reconfig/${acct}/" \
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
$RSYNC --timeout=60 --include='canonical_*.csv' --exclude='*' \
  "niels@204.168.181.211:/home/niels/binance-sandbox/data/sweep_results/" \
  "$MB_BASE/data/sweep_results/" 2>&1 || echo "  [warn] rsync exit $?"

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
