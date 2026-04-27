#!/bin/bash
# Tier-3 comparison of HEDGE_CLOSE_MODE variants on crypto.
# Each mode runs real backtest_v8_engine on 12 syms × 6mo (~5min each).
# Output: /tmp/hedge_close_modes/result_<mode>.log + summary.
set -uo pipefail
OUTDIR=/tmp/hedge_close_modes
mkdir -p "$OUTDIR"
PY=/opt/anaconda3/envs/binance_env/bin/python
SYMS="BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,ADAUSDT,DOTUSDT,AVAXUSDT,MATICUSDT,LINKUSDT,UNIUSDT,LTCUSDT"
MODES="wt_3m wt_3m_15m wt_3m_1h wt_3m_15m_1h wt_3m_15m_htf1 wt_3m_15m_htf2 wt_3m_15m_htf3 wt_dc_score"
cd /Users/niels/Documents/binance

for MODE in $MODES; do
    echo "=== $(date -u '+%H:%M:%S') START $MODE ==="
    OVR="$OUTDIR/override_$MODE.json"
    LOG="$OUTDIR/result_$MODE.log"
    echo "{\"HEDGE_CLOSE_MODE\":\"$MODE\",\"HEDGE_WT_CLOSE_REQUIRE_NONNEG_GAIN\":true,\"HEDGE_CLOSE_WT_DC_THRESHOLD\":25.0}" > "$OVR"
    V8_OVERRIDE_FILE="$OVR" V8_RATE_GUARD_DISABLED=1 V8_SKIP_PROCESS_POSITION=1 \
      timeout 600 "$PY" -u backtest_v8_engine.py \
      --mode crypto --account inf --start 2025-10-01 --capital 1000 \
      --symbols "$SYMS" > "$LOG" 2>&1
    RESULT=$(grep "V8_RESULT:" "$LOG" | tail -1)
    echo "  $MODE: $RESULT"
done

echo ""
echo "================================================================"
echo "  SUMMARY (sorted by sharpe)"
echo "================================================================"
for MODE in $MODES; do
    LINE=$(grep "V8_RESULT:" "$OUTDIR/result_$MODE.log" | tail -1)
    echo "$MODE | $LINE"
done | sort -t= -k2 -nr
