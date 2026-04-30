#!/bin/bash
# Test evaluate_reentry impact: 12 syms × 6mo crypto, with vs without EVAL_REENTRY_ENABLED.
# User: "huge functions so augments backtest times by up to 50% but if it works it works".
set -uo pipefail
OUTDIR=/tmp/eval_reentry_ab
mkdir -p "$OUTDIR"
PY=/opt/anaconda3/envs/binance_env/bin/python
SYMS="BTCUSDC,ETHUSDC,SOLUSDC,BNBUSDC,XRPUSDC,ADAUSDC,DOTUSDT,AVAXUSDC,MATICUSDT,LINKUSDC,UNIUSDC,LTCUSDC"
cd /Users/niels/Documents/binance

run() {
    local NAME=$1 OVR=$2
    local OF="$OUTDIR/override_${NAME}.json"
    local LOG="$OUTDIR/result_${NAME}.log"
    echo "$OVR" > "$OF"
    local T0=$(date +%s)
    echo "=== $(date -u '+%H:%M:%S') START $NAME ==="
    V8_OVERRIDE_FILE="$OF" V8_RATE_GUARD_DISABLED=1 V8_SKIP_PROCESS_POSITION=1 \
        timeout 1800 "$PY" -u backtest_v8_engine.py \
        --mode crypto --account inf --start 2025-10-01 --capital 1000 \
        --symbols "$SYMS" > "$LOG" 2>&1
    local T1=$(date +%s)
    local R=$(grep "V8_RESULT_LIVE\|V8_RESULT:" "$LOG" 2>/dev/null | tail -1)
    local REENTRY_FIRES=$(grep -c "EVAL_REENTRY_FIRED" "$LOG" 2>/dev/null || echo 0)
    echo "  $NAME: elapsed=$((T1-T0))s reentry_fires=$REENTRY_FIRES"
    echo "  $NAME: $R"
}

run "REENTRY_OFF" '{"EVAL_REENTRY_ENABLED":false,"HEDGE_CLOSE_MODE":"wt_3m_15m_htf1","HEDGE_WT_CLOSE_REQUIRE_NONNEG_GAIN":true}'
run "REENTRY_ON"  '{"EVAL_REENTRY_ENABLED":true, "HEDGE_CLOSE_MODE":"wt_3m_15m_htf1","HEDGE_WT_CLOSE_REQUIRE_NONNEG_GAIN":true}'

echo ""
echo "=== EVAL_REENTRY A/B FINAL ==="
for NAME in REENTRY_OFF REENTRY_ON; do
    R=$(grep "V8_RESULT_LIVE\|V8_RESULT:" "$OUTDIR/result_$NAME.log" 2>/dev/null | tail -1)
    F=$(grep -c "EVAL_REENTRY_FIRED" "$OUTDIR/result_$NAME.log" 2>/dev/null || echo 0)
    printf "%-15s | fires=%5d | %s\n" "$NAME" "$F" "$R"
done
