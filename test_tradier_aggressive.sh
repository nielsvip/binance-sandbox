#!/bin/bash
# Aggressive tradier test — find config that fires REAL gains.
# User: "5X B&H return on any stock" — current 12 trades / +0.01% = useless.
# Goal: 500+ trades, sharpe_pt positive, gain > 5x SPY/AAPL B&H
set -uo pipefail
export EZ_LOG_DIR="${EZ_LOG_DIR:-/tmp}"
OUTDIR=/tmp/tradier_aggressive
mkdir -p "$OUTDIR"
PY=/opt/anaconda3/envs/binance_env/bin/python
SYMS="AAPL,MSFT,NVDA,AMZN,SPY,QQQ,XOM,GLD,TSLA,GOOGL,META,JPM"
cd /Users/niels/Documents/binance

run() {
    local NAME=$1 OVR=$2
    local OF="$OUTDIR/override_${NAME}.json"
    local LOG="$OUTDIR/result_${NAME}.log"
    echo "$OVR" > "$OF"
    local T0=$(date +%s)
    echo "=== $(date -u '+%H:%M:%S') START $NAME ==="
    V8_OVERRIDE_FILE="$OF" V8_RATE_GUARD_DISABLED=1 \
        timeout 2700 "$PY" -u backtest_v8_engine.py \
        --mode tradier --account trb --start 2025-10-01 --capital 70000 \
        --symbols "$SYMS" > "$LOG" 2>&1
    local T1=$(date +%s)
    local R=$(grep "V8_RESULT_LIVE\|V8_RESULT:" "$LOG" 2>/dev/null | tail -1)
    local AUGS=$(grep -c "AUGMENT.*Status: filled\|AUGMENT.*Status: SUBMITTED\|🚀AUGMENT" "$LOG" 2>/dev/null | head -1)
    local TRADES=$(echo "$R" | grep -oE "trades=[0-9]+" | head -1 | sed 's/trades=//')
    [ -z "$TRADES" ] && TRADES=$(echo "$R" | grep -oE "closes=[0-9]+" | head -1 | sed 's/closes=//')
    echo "  $NAME: elapsed=$((T1-T0))s trades=$TRADES augs=$AUGS"
    echo "  $NAME: $R"
}

# Baseline (current deployed = nuclear-conservative)
run "baseline_DEPLOYED" '{}'

# Loosen entry threshold + re-enable engines + keep HTF gate + keep NOLOSS
run "looser_threshold" '{"WT_DC_ENTRY_THRESHOLD":55,"LIVE_ENTRY_ENGINE_ENABLED":true,"WT_DC_HTF_GATE":"4h","NOLOSS_MIN_PROFIT_PCT_TRADIER":0.0}'

# More aggressive — lower alignment gate too
run "alignment_loose" '{"WT_DC_ENTRY_THRESHOLD":55,"LIVE_ENTRY_ENGINE_ENABLED":true,"WT_DC_HTF_GATE":"4h","NOLOSS_MIN_PROFIT_PCT_TRADIER":0.0,"ALIGNMENT_GATE_TOTAL":24}'

# Most aggressive — shorter hold, allow rapid cycling
run "aggressive_full" '{"WT_DC_ENTRY_THRESHOLD":50,"LIVE_ENTRY_ENGINE_ENABLED":true,"WT_DC_HTF_GATE":"4h","NOLOSS_MIN_PROFIT_PCT_TRADIER":0.0,"ALIGNMENT_GATE_TOTAL":20,"MIN_HOLD_BARS_TRADIER":4,"COOLDOWN_BARS_TRADIER":2}'

echo ""
echo "================================================================"
echo "  TRADIER AGGRESSIVE SUMMARY"
echo "================================================================"
for NAME in baseline_DEPLOYED looser_threshold alignment_loose aggressive_full; do
    R=$(grep "V8_RESULT_LIVE\|V8_RESULT:" "$OUTDIR/result_$NAME.log" 2>/dev/null | tail -1)
    AUGS=$(grep -c "🚀AUGMENT" "$OUTDIR/result_$NAME.log" 2>/dev/null | head -1)
    printf "%-25s | augs=%5d | %s\n" "$NAME" "$AUGS" "$R"
done
