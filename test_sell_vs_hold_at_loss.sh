#!/bin/bash
# Tier-3 medium tests for sell-vs-hold loss-tolerance.
# Crypto: 12 syms × 6mo (smoke; medium 48-sym × 1yr would take ~30 min/test).
# Tradier: 12 syms × 6mo. Each test ~10-15 min. Sequential to avoid OOM.
set -uo pipefail
OUTDIR=/tmp/sell_vs_hold
mkdir -p "$OUTDIR"
PY=/opt/anaconda3/envs/binance_env/bin/python
CRYPTO_SYMS="BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,ADAUSDT,DOTUSDT,AVAXUSDT,MATICUSDT,LINKUSDT,UNIUSDT,LTCUSDT"
TRADIER_SYMS="AAPL,MSFT,NVDA,AMZN,SPY,QQQ,XOM,GLD,TSLA,GOOGL,META,JPM"
cd /Users/niels/Documents/binance

run_test() {
    local NAME=$1 MODE=$2 OVERRIDES=$3 SYMS=$4 ACCT=$5 CAP=$6
    local OVR="$OUTDIR/override_${NAME}.json"
    local LOG="$OUTDIR/result_${NAME}.log"
    echo "$OVERRIDES" > "$OVR"
    echo "=== $(date -u '+%H:%M:%S') START $NAME ==="
    V8_OVERRIDE_FILE="$OVR" V8_RATE_GUARD_DISABLED=1 V8_SKIP_PROCESS_POSITION=1 \
        timeout 1200 "$PY" -u backtest_v8_engine.py \
        --mode "$MODE" --account "$ACCT" --start 2025-10-01 --capital "$CAP" \
        --symbols "$SYMS" > "$LOG" 2>&1
    local RESULT=$(grep "V8_RESULT_LIVE" "$LOG" 2>/dev/null | tail -1)
    echo "  $NAME: $RESULT"
}

# CRYPTO — using winner mode wt_3m_15m_htf1 + user-requested wt_dc_score
# Var A: hold at loss (REQUIRE_NONNEG_GAIN=true) — current behavior
# Var B: sell at loss when signal fires (REQUIRE_NONNEG_GAIN=false)
run_test "crypto_htf1_HOLD" "crypto" '{"HEDGE_CLOSE_MODE":"wt_3m_15m_htf1","HEDGE_WT_CLOSE_REQUIRE_NONNEG_GAIN":true}' "$CRYPTO_SYMS" inf 1000
run_test "crypto_htf1_SELL" "crypto" '{"HEDGE_CLOSE_MODE":"wt_3m_15m_htf1","HEDGE_WT_CLOSE_REQUIRE_NONNEG_GAIN":false}' "$CRYPTO_SYMS" inf 1000
run_test "crypto_wt_dc_HOLD" "crypto" '{"HEDGE_CLOSE_MODE":"wt_dc_score","HEDGE_WT_CLOSE_REQUIRE_NONNEG_GAIN":true,"HEDGE_CLOSE_WT_DC_THRESHOLD":25.0}' "$CRYPTO_SYMS" inf 1000
run_test "crypto_wt_dc_SELL" "crypto" '{"HEDGE_CLOSE_MODE":"wt_dc_score","HEDGE_WT_CLOSE_REQUIRE_NONNEG_GAIN":false,"HEDGE_CLOSE_WT_DC_THRESHOLD":25.0}' "$CRYPTO_SYMS" inf 1000

# TRADIER — wt_dc_exit_scorer is already live primary exit signal (uses 5m base TF correctly)
# The lever is NOLOSS_MIN_PROFIT_PCT_TRADIER threshold:
# Var A: 0.0 (hold any loss) — current behavior
# Var B: -2.0 (allow exit when wt_dc fires AND loss < 2%)
run_test "tradier_NOLOSS_0"   "tradier" '{"NOLOSS_MIN_PROFIT_PCT_TRADIER":0.0}'  "$TRADIER_SYMS" trb 70000
run_test "tradier_NOLOSS_n2"  "tradier" '{"NOLOSS_MIN_PROFIT_PCT_TRADIER":-2.0}' "$TRADIER_SYMS" trb 70000

echo ""
echo "================================================================"
echo "  SUMMARY"
echo "================================================================"
for NAME in crypto_htf1_HOLD crypto_htf1_SELL crypto_wt_dc_HOLD crypto_wt_dc_SELL tradier_NOLOSS_0 tradier_NOLOSS_n2; do
    LINE=$(grep "V8_RESULT_LIVE" "$OUTDIR/result_$NAME.log" 2>/dev/null | tail -1)
    printf "%-25s | %s\n" "$NAME" "$LINE"
done
