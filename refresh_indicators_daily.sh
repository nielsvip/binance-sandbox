#!/bin/bash
# Daily durable kline refresh + NPZ rebuild on S1.
set -euo pipefail
PY=/home/niels/.conda/envs/binance_env/bin/python
SANDBOX=/home/niels/binance-sandbox
LIVE_KLINES=/home/niels/binance/klines_cache
BT_KLINES=$SANDBOX/klines_cache_backtest
LOGS=/home/niels/logs
TS=$(date -u +%Y%m%d_%H%M)
RETENTION_LEDGER=$SANDBOX/data/tradier_5m_retention_ledger.json
COVERAGE_REPORT=$SANDBOX/data/reports/tradier_5m_coverage_latest.json

exec >> "$LOGS/refresh_indicators_daily.log" 2>&1
echo "=== [$(date -u +%FT%TZ)] refresh_indicators_daily START ==="

echo "[$(date -u +%T)] Step 1: crypto klines append"
$PY - << 'PYEOF'
import json, os
LIVE_DIR = "/home/niels/binance/klines_cache"
BT_DIR = "/home/niels/binance-sandbox/klines_cache_backtest"
updated = 0; skipped = 0; missing = 0
for fname in sorted(os.listdir(BT_DIR)):
    if not fname.endswith("_15m.json"):
        continue
    bt_path = os.path.join(BT_DIR, fname)
    live_path = os.path.join(LIVE_DIR, fname)
    if not os.path.exists(live_path):
        missing += 1; continue
    with open(bt_path) as f: bt_data = json.load(f)
    with open(live_path) as f: live_data = json.load(f)
    if not bt_data or not live_data:
        skipped += 1; continue
    bt_last_ts = bt_data[-1]["timestamp"]
    new_bars = [bar for bar in live_data if bar["timestamp"] > bt_last_ts]
    if not new_bars:
        skipped += 1; continue
    old_len = len(bt_data)
    old_first = bt_data[0]["timestamp"]
    bt_data.extend(new_bars)
    assert len(bt_data) >= old_len and bt_data[0]["timestamp"] == old_first
    tmp = bt_path + f".tmp.{os.getpid()}"
    with open(tmp, "w") as f: json.dump(bt_data, f, separators=(",",":"))
    os.replace(tmp, bt_path)
    updated += 1
print(f"Crypto klines: {updated} updated, {skipped} current, {missing} missing")
PYEOF

cd "$SANDBOX"
echo "[$(date -u +%T)] Step 2: preflight native Tradier 5m retention"
$PY tools/tradier_5m_retention_audit.py \
  --symbols-file symbols_tradier.json \
  --ledger "$RETENTION_LEDGER" \
  --report "$COVERAGE_REPORT" \
  --source-only \
  --allow-missing-native

echo "[$(date -u +%T)] Step 3a: Tradier 15m append (3 days back)"
APPEND_ERRORS=0
if $PY -u tradier_klines_append.py --symbols-file symbols_tradier.json --days-back 3 --interval 15min \
  >> "$LOGS/tradier_klines_append_15m_daily_${TS}.log" 2>&1; then
  echo "Tradier 15m append done"
else
  echo "Tradier 15m append ERROR (check log)"
  APPEND_ERRORS=1
fi

echo "[$(date -u +%T)] Step 3b: Tradier native 5m append (durable, append-only)"
if $PY -u tradier_klines_append.py --symbols-file symbols_tradier.json --days-back 3 --interval 5min \
  >> "$LOGS/tradier_klines_append_5m_daily_${TS}.log" 2>&1; then
  echo "Tradier native 5m append done"
else
  echo "Tradier native 5m append ERROR (check log)"
  APPEND_ERRORS=1
fi

echo "[$(date -u +%T)] Step 3c: commit native 5m retention ledger"
$PY tools/tradier_5m_retention_audit.py \
  --symbols-file symbols_tradier.json \
  --ledger "$RETENTION_LEDGER" \
  --report "$COVERAGE_REPORT" \
  --source-only \
  --allow-missing-native \
  --update-ledger

if [ "$APPEND_ERRORS" -ne 0 ]; then
  echo "One or more Tradier append jobs failed; retained data was audited but NPZ rebuild is blocked"
  exit 1
fi

echo "[$(date -u +%T)] Step 4: crypto precompute"
$PY -u "$SANDBOX/backtest_v8_precompute.py" --all --mode crypto --workers 3 \
  >> "$LOGS/precompute_crypto_daily_${TS}.log" 2>&1

echo "[$(date -u +%T)] Step 5: Tradier precompute"
$PY -u "$SANDBOX/backtest_v8_precompute.py" --all --mode tradier --workers 3 \
  >> "$LOGS/precompute_tradier_daily_${TS}.log" 2>&1

echo "[$(date -u +%T)] Step 6: publish final native/interpolated NPZ coverage"
$PY tools/tradier_5m_retention_audit.py \
  --symbols-file symbols_tradier.json \
  --ledger "$RETENTION_LEDGER" \
  --report "$COVERAGE_REPORT" \
  --allow-missing-native

echo "=== [$(date -u +%FT%TZ)] refresh_indicators_daily END ==="
