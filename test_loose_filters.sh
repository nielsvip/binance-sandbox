#!/bin/bash
# Test loose-filter overrides in SANDBOX (not live) to get 20+ trades/day for meaningful test results.
# 12 syms × 6mo crypto + 12 syms × 6mo tradier sequential.
set -uo pipefail
OUTDIR=/tmp/loose_filters
mkdir -p "$OUTDIR"
PY=/opt/anaconda3/envs/binance_env/bin/python
CRYPTO_SYMS="BTCUSDC,ETHUSDC,SOLUSDC,BNBUSDC,XRPUSDC,ADAUSDC,DOTUSDT,AVAXUSDC,MATICUSDT,LINKUSDC,UNIUSDC,LTCUSDC"
TRADIER_SYMS="AAPL,MSFT,NVDA,AMZN,SPY,QQQ,XOM,GLD,TSLA,GOOGL,META,JPM"
cd /Users/niels/Documents/binance

run() {
    local NAME=$1 MODE=$2 OVR=$3 SYMS=$4 ACCT=$5 CAP=$6
    local OF="$OUTDIR/override_${NAME}.json"
    local LOG="$OUTDIR/result_${NAME}.log"
    echo "$OVR" > "$OF"
    local T0=$(date +%s)
    echo "=== $(date -u '+%H:%M:%S') START $NAME ==="
    V8_OVERRIDE_FILE="$OF" V8_RATE_GUARD_DISABLED=1 V8_SKIP_PROCESS_POSITION=1 \
        timeout 1200 "$PY" -u backtest_v8_engine.py \
        --mode "$MODE" --account "$ACCT" --start 2025-10-01 --capital "$CAP" \
        --symbols "$SYMS" > "$LOG" 2>&1
    local T1=$(date +%s)
    local R=$(grep "V8_RESULT_LIVE\|V8_RESULT:" "$LOG" 2>/dev/null | tail -1)
    local OPENS=$(grep -c "OPEN.*Status: SUBMITTED\|OPEN.*Status: filled" "$LOG" 2>/dev/null || echo 0)
    local BLOCKS=$(grep -c "BLOCKED_" "$LOG" 2>/dev/null || echo 0)
    echo "  $NAME: elapsed=$((T1-T0))s opens=$OPENS blocks=$BLOCKS"
    echo "  $NAME: $R"
}

# CRYPTO loose filters
LOOSE_CRYPTO='{
"ALIGNMENT_GATE_TOTAL":0,
"WT_DC_ENTRY_THRESHOLD":30,
"K_ZONE_LONG_THRESHOLD":80,
"K_ZONE_SHORT_THRESHOLD":20,
"MIN_HOLD_BARS_BEFORE_EXIT":4,
"COOLDOWN_BARS":1,
"EVAL_REENTRY_ENABLED":true
}'
# CRYPTO baseline (current live-equivalent)
BASELINE_CRYPTO='{}'

# TRADIER loose filters
LOOSE_TRADIER='{
"ALIGNMENT_GATE_TOTAL":0,
"WT_DC_ENTRY_THRESHOLD":30,
"TRADIER_STOCH_ENTRY_LONG_TRADIER":100,
"TRADIER_STOCH_ENTRY_SHORT_TRADIER":0,
"K_ZONE_LONG_THRESHOLD_TRADIER":80,
"K_ZONE_SHORT_THRESHOLD_TRADIER":20,
"MIN_HOLD_BARS_TRADIER":2,
"COOLDOWN_BARS_TRADIER":1,
"WT_DC_HTF_GATE":"none",
"LIVE_ENTRY_ENGINE_ENABLED":false,
"NOLOSS_MIN_PROFIT_PCT_TRADIER":0.0
}'
# TRADIER baseline (current deployed live config)
BASELINE_TRADIER='{}'

run "crypto_BASELINE" "crypto" "$BASELINE_CRYPTO" "$CRYPTO_SYMS" inf 1000
run "crypto_LOOSE"    "crypto" "$LOOSE_CRYPTO"    "$CRYPTO_SYMS" inf 1000
run "tradier_BASELINE" "tradier" "$BASELINE_TRADIER" "$TRADIER_SYMS" trb 70000
run "tradier_LOOSE"    "tradier" "$LOOSE_TRADIER"    "$TRADIER_SYMS" trb 70000

echo ""
echo "================================================================"
echo "  LOOSE-FILTER TEST SUMMARY"
echo "================================================================"
for NAME in crypto_BASELINE crypto_LOOSE tradier_BASELINE tradier_LOOSE; do
    R=$(grep "V8_RESULT_LIVE\|V8_RESULT:" "$OUTDIR/result_$NAME.log" 2>/dev/null | tail -1)
    OPENS=$(grep -c "OPEN.*Status: SUBMITTED\|OPEN.*Status: filled" "$OUTDIR/result_$NAME.log" 2>/dev/null || echo 0)
    BLOCKS=$(grep -c "BLOCKED_" "$OUTDIR/result_$NAME.log" 2>/dev/null || echo 0)
    printf "%-20s | opens=%5d blocks=%6d | %s\n" "$NAME" "$OPENS" "$BLOCKS" "$R"
done
