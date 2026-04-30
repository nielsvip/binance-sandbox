#!/bin/bash
# sync_chart_data.sh — pull latest hourly_reconfig cycles + canonical_trades from
# S1+S2 to MB, so the chart server at :5077 can render them on-chart.
# Runs cheap (rsync only copies what changed). Suitable for a 5-min cron / launchd.

set -u
LOG="${HOME}/Documents/binance/logs/sync_chart_data.log"
mkdir -p "$(dirname "$LOG")"
exec >>"$LOG" 2>&1
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] start"

MB_BASE="/Users/niels/Documents/binance"

# Latest hourly cycles per account (S1 crypto: flz/fin/inf)
mkdir -p "$MB_BASE/data/hourly_reconfig/"{flz,fin,inf,trc,trb}"/runs"
rsync -az --include='runs/' --include='runs/**/' --include='*.jsonl' \
  --include='active_config.json' --include='opinions.json' \
  --exclude='*' --prune-empty-dirs \
  niels@157.180.125.52:/home/niels/binance-sandbox/data/hourly_reconfig/flz/ \
  "$MB_BASE/data/hourly_reconfig/flz/"
rsync -az --include='runs/' --include='runs/**/' --include='*.jsonl' \
  --include='active_config.json' --include='opinions.json' \
  --exclude='*' --prune-empty-dirs \
  niels@157.180.125.52:/home/niels/binance-sandbox/data/hourly_reconfig/fin/ \
  "$MB_BASE/data/hourly_reconfig/fin/"
rsync -az --include='runs/' --include='runs/**/' --include='*.jsonl' \
  --include='active_config.json' --include='opinions.json' \
  --exclude='*' --prune-empty-dirs \
  niels@157.180.125.52:/home/niels/binance-sandbox/data/hourly_reconfig/inf/ \
  "$MB_BASE/data/hourly_reconfig/inf/"

# S2 tradier: trc / trb
rsync -az --include='runs/' --include='runs/**/' --include='*.jsonl' \
  --include='active_config.json' --include='opinions.json' \
  --exclude='*' --prune-empty-dirs \
  niels@204.168.181.211:/home/niels/binance-sandbox/data/hourly_reconfig/trc/ \
  "$MB_BASE/data/hourly_reconfig/trc/"
rsync -az --include='runs/' --include='runs/**/' --include='*.jsonl' \
  --include='active_config.json' --include='opinions.json' \
  --exclude='*' --prune-empty-dirs \
  niels@204.168.181.211:/home/niels/binance-sandbox/data/hourly_reconfig/trb/ \
  "$MB_BASE/data/hourly_reconfig/trb/"

# Big-sweep canonical trade dirs (S1 only)
mkdir -p "$MB_BASE/data/canonical_trades"
rsync -az \
  niels@157.180.125.52:/home/niels/binance-sandbox/data/canonical_trades/ \
  "$MB_BASE/data/canonical_trades/"

# Canonical CSVs (Sharpe row outputs)
mkdir -p "$MB_BASE/data/sweep_results"
rsync -az --include='canonical_hourly_*.csv' --include='canonical_*.csv' \
  --exclude='*' \
  niels@157.180.125.52:/home/niels/binance-sandbox/data/sweep_results/ \
  "$MB_BASE/data/sweep_results/"
rsync -az --include='canonical_hourly_*.csv' --include='canonical_*.csv' \
  --exclude='*' \
  niels@204.168.181.211:/home/niels/binance-sandbox/data/sweep_results/ \
  "$MB_BASE/data/sweep_results/"

# btc_settings_search promoted candidates (so chart can show their _meta + winning tag)
mkdir -p "$MB_BASE/data/hourly_reconfig/_candidates"
rsync -az \
  niels@157.180.125.52:/home/niels/binance-sandbox/data/hourly_reconfig/_candidates/ \
  "$MB_BASE/data/hourly_reconfig/_candidates/"

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
