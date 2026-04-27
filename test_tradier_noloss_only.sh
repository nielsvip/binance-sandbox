#!/bin/bash
# Re-run only the tradier NOLOSS tests with longer timeout (3600s).
set -uo pipefail
OUTDIR=/tmp/sell_vs_hold
PY=/opt/anaconda3/envs/binance_env/bin/python
TRADIER_SYMS="AAPL,MSFT,NVDA,AMZN,SPY,QQQ,XOM,GLD,TSLA,GOOGL,META,JPM"
cd /Users/niels/Documents/binance

run() {
    local NAME=$1 OVR=$2
    local OF="$OUTDIR/override_${NAME}.json"
    local LOG="$OUTDIR/result_${NAME}.log"
    echo "$OVR" > "$OF"
    echo "=== $(date -u '+%H:%M:%S') START $NAME (timeout 3600s) ==="
    V8_OVERRIDE_FILE="$OF" V8_RATE_GUARD_DISABLED=1 V8_SKIP_PROCESS_POSITION=1 \
        timeout 3600 "$PY" -u backtest_v8_engine.py \
        --mode tradier --account trb --start 2025-10-01 --capital 70000 \
        --symbols "$TRADIER_SYMS" > "$LOG" 2>&1
    local R=$(grep "V8_RESULT_LIVE\|V8_RESULT:" "$LOG" 2>/dev/null | tail -1)
    echo "  $NAME: $R"
}

run "tradier_NOLOSS_0_v2"  '{"NOLOSS_MIN_PROFIT_PCT_TRADIER":0.0}'
run "tradier_NOLOSS_n2_v2" '{"NOLOSS_MIN_PROFIT_PCT_TRADIER":-2.0}'

echo ""
echo "=== TRADIER FINAL ==="
for NAME in tradier_NOLOSS_0_v2 tradier_NOLOSS_n2_v2; do
    R=$(grep "V8_RESULT_LIVE\|V8_RESULT:" "$OUTDIR/result_$NAME.log" 2>/dev/null | tail -1)
    printf "%-25s | %s\n" "$NAME" "$R"
done
