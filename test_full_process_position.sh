#!/bin/bash
# Re-run loose_filters with FULL process_position (NO V8_SKIP_PROCESS_POSITION).
# User: "process_position is probably where the rest of live entries come from".
# Slower (~50% per user warning) but covers all live entry paths.
set -uo pipefail
OUTDIR=/tmp/full_pp
mkdir -p "$OUTDIR"
PY=/opt/anaconda3/envs/binance_env/bin/python
CRYPTO_SYMS="BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,ADAUSDT,DOTUSDT,AVAXUSDT,MATICUSDT,LINKUSDT,UNIUSDT,LTCUSDT"
TRADIER_SYMS="AAPL,MSFT,NVDA,AMZN,SPY,QQQ,XOM,GLD,TSLA,GOOGL,META,JPM"
cd /Users/niels/Documents/binance

run() {
    local NAME=$1 MODE=$2 OVR=$3 SYMS=$4 ACCT=$5 CAP=$6
    local OF="$OUTDIR/override_${NAME}.json"
    local LOG="$OUTDIR/result_${NAME}.log"
    echo "$OVR" > "$OF"
    local T0=$(date +%s)
    echo "=== $(date -u '+%H:%M:%S') START $NAME (FULL process_position) ==="
    # NOTE: V8_SKIP_PROCESS_POSITION NOT SET — full live path
    V8_OVERRIDE_FILE="$OF" V8_RATE_GUARD_DISABLED=1 \
        timeout 2400 "$PY" -u backtest_v8_engine.py \
        --mode "$MODE" --account "$ACCT" --start 2025-10-01 --capital "$CAP" \
        --symbols "$SYMS" > "$LOG" 2>&1
    local T1=$(date +%s)
    local R=$(grep "V8_RESULT_LIVE\|V8_RESULT:" "$LOG" 2>/dev/null | tail -1)
    local FIRES=$(grep -c "EVAL_REENTRY_FIRED" "$LOG" 2>/dev/null | head -1)
    local AUG=$(grep -c "AUGMENT.*Status:" "$LOG" 2>/dev/null | head -1)
    echo "  $NAME: elapsed=$((T1-T0))s eval_reentry_fires=$FIRES augments=$AUG"
    echo "  $NAME: $R"
}

# Crypto BASELINE with process_position ON
run "crypto_FULL"  "crypto"  '{"EVAL_REENTRY_ENABLED":true}' "$CRYPTO_SYMS" inf 1000

# Tradier deployed config with process_position ON
run "tradier_FULL" "tradier" '{}' "$TRADIER_SYMS" trb 70000

echo ""
echo "================================================================"
echo "  FULL process_position SUMMARY"
echo "================================================================"
for NAME in crypto_FULL tradier_FULL; do
    R=$(grep "V8_RESULT_LIVE\|V8_RESULT:" "$OUTDIR/result_$NAME.log" 2>/dev/null | tail -1)
    FIRES=$(grep -c "EVAL_REENTRY_FIRED" "$OUTDIR/result_$NAME.log" 2>/dev/null | head -1)
    AUG=$(grep -c "AUGMENT" "$OUTDIR/result_$NAME.log" 2>/dev/null | head -1)
    printf "%-15s | reentry_fires=%5d augments=%5d | %s\n" "$NAME" "$FIRES" "$AUG" "$R"
done
