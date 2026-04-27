#!/bin/bash
# Test 1: re-enable crypto breakouts (BB + MULTI_LUNG) on top of current exit config.
# Test 2: same as 1 but with --HEDGE_CLOSE_MODE=wt_3m_15m_htf1 (the winning hedge close from earlier A/B).
# 12 syms × 6mo, ~10min each. Sequential.
set -uo pipefail
OUTDIR=/tmp/crypto_breakouts
mkdir -p "$OUTDIR"
PY=/opt/anaconda3/envs/binance_env/bin/python
SYMS="BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,ADAUSDT,DOTUSDT,AVAXUSDT,MATICUSDT,LINKUSDT,UNIUSDT,LTCUSDT"
cd /Users/niels/Documents/binance

run() {
    local NAME=$1 OVR=$2
    local OF="$OUTDIR/override_${NAME}.json"
    local LOG="$OUTDIR/result_${NAME}.log"
    echo "$OVR" > "$OF"
    echo "=== $(date -u '+%H:%M:%S') START $NAME ==="
    V8_OVERRIDE_FILE="$OF" V8_RATE_GUARD_DISABLED=1 V8_SKIP_PROCESS_POSITION=1 \
        timeout 900 "$PY" -u backtest_v8_engine.py \
        --mode crypto --account inf --start 2025-10-01 --capital 1000 \
        --symbols "$SYMS" > "$LOG" 2>&1
    local R=$(grep "V8_RESULT_LIVE" "$LOG" 2>/dev/null | tail -1)
    echo "  $NAME: $R"
}

# Baseline: current config (no override) — gives reference
run "BASELINE_no_breakouts" '{}'

# Test A: BB+MULTI_LUNG re-enabled, existing exits
run "BREAKOUTS_existing_exits" '{"BB_BREAKOUT_ENABLED":true,"BREAKOUT_MULTI_LUNG_ENABLED":true}'

# Test B: BB+MULTI_LUNG re-enabled + winning hedge close mode (htf1)
run "BREAKOUTS_htf1_hedgeclose" '{"BB_BREAKOUT_ENABLED":true,"BREAKOUT_MULTI_LUNG_ENABLED":true,"HEDGE_CLOSE_MODE":"wt_3m_15m_htf1","HEDGE_WT_CLOSE_REQUIRE_NONNEG_GAIN":true}'

# Test C: BB+MULTI_LUNG re-enabled + DC_BREAKOUT_FAILED_STOP enabled (existing stop)
run "BREAKOUTS_dc_failed_stop" '{"BB_BREAKOUT_ENABLED":true,"BREAKOUT_MULTI_LUNG_ENABLED":true,"DC_BREAKOUT_FAILED_STOP_ENABLED":true}'

echo ""
echo "================================================================"
echo "  CRYPTO BREAKOUT TEST SUMMARY"
echo "================================================================"
for NAME in BASELINE_no_breakouts BREAKOUTS_existing_exits BREAKOUTS_htf1_hedgeclose BREAKOUTS_dc_failed_stop; do
    R=$(grep "V8_RESULT_LIVE" "$OUTDIR/result_$NAME.log" 2>/dev/null | tail -1)
    printf "%-32s | %s\n" "$NAME" "$R"
done
