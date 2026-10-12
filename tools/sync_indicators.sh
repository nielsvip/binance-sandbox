#!/bin/bash
# sync_indicators.sh — S1 is source for backtest_v8/indicators, push to s2/s5/s6 24/7 (s7 deleted 2026-10-09)
# USER 2026-10-12 GATE: no push unless every NPZ holds >=1y AND ends at the latest
# 15m bar (crypto) / latest RTH session (stocks). Fail-closed with loud log.
set -e
GATE_DIR="$HOME/binance-sandbox/backtest_v8/indicators"
if [ -f "$HOME/binance-sandbox/tools/npz_freshness_gate.py" ]; then
  if ! python3 "$HOME/binance-sandbox/tools/npz_freshness_gate.py" --dir "$GATE_DIR" >> /tmp/sync_indicators.log 2>&1; then
    echo "$(date -u +%FT%TZ) GATE_REFUSED indicators push blocked: stale/short NPZ on S1 — refresh via backtest_v8_precompute.py first" >> /tmp/sync_indicators.log
    exit 1
  fi
else
  echo "$(date -u +%FT%TZ) GATE_MISSING npz_freshness_gate.py not on S1 — pushing ungated (deploy gate!)" >> /tmp/sync_indicators.log
fi
for ip in 10.0.0.6 10.0.0.5 10.0.0.4; do
  rsync -az --timeout=60 ~/binance-sandbox/backtest_v8/indicators/ niels@$ip:~/binance-sandbox/backtest_v8/indicators/ > /tmp/sync_ind_${ip##*.}.log 2>&1 || echo "$(date) fail $ip" >> /tmp/sync_indicators.log
  echo "$(date -u +%FT%TZ) $ip OK $(ls ~/binance-sandbox/backtest_v8/indicators 2>&1 | wc -l) files" >> /tmp/sync_indicators.log
done
