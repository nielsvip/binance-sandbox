#!/bin/bash
# 48-sym × 1yr tradier matrix — runs on S2 (user said CPU available).
# 4 tests: NOLOSS_0 vs NOLOSS_n2, ALSO with/without HTF gate.
set -uo pipefail
export EZ_LOG_DIR="${EZ_LOG_DIR:-/tmp}"
OUTDIR=/home/niels/binance-sandbox/data/tradier_48sym_1yr
mkdir -p "$OUTDIR"
PY=/home/niels/miniconda3/envs/binance_env/bin/python
SYMS="AA,AAPL,ABBV,ABNB,ABT,ACN,ADBE,ADM,ADP,AEM,AG,AGCO,AGI,ALB,AM,AMD,AMZN,APA,APO,AR,ARM,ASC,ASML,ASTS,ATI,AU,AVGO,AXON,BA,BABA,BG,BHP,BIDU,BITO,BK,BKR,BLOK,BOIL,BTCL,BTG,BWXT,CALM,CAT,CCJ,CDE,CENX,CF,CHWY"
cd /home/niels/binance-sandbox

run() {
    local NAME=$1 OVR=$2
    local OF="$OUTDIR/override_${NAME}.json"
    local LOG="$OUTDIR/result_${NAME}.log"
    echo "$OVR" > "$OF"
    echo "=== $(date -u '+%H:%M:%S') START $NAME ==="
    V8_OVERRIDE_FILE="$OF" V8_RATE_GUARD_DISABLED=1 V8_SKIP_PROCESS_POSITION=1 \
        timeout 5400 "$PY" -u backtest_v8_engine.py \
        --mode tradier --account trb --start 2024-01-01 --capital 70000 \
        --symbols "$SYMS" > "$LOG" 2>&1
    local R=$(grep "V8_RESULT_LIVE\|V8_RESULT:" "$LOG" 2>/dev/null | tail -1)
    echo "  $NAME: $R"
}

run "tradier48_noloss0_htfNONE"  '{"NOLOSS_MIN_PROFIT_PCT_TRADIER":0.0,"WT_DC_HTF_GATE":"none","WT_DC_ENTRY_THRESHOLD":55,"LIVE_ENTRY_ENGINE_ENABLED":true}'
run "tradier48_noloss0_htf4h"    '{"NOLOSS_MIN_PROFIT_PCT_TRADIER":0.0,"WT_DC_HTF_GATE":"4h","WT_DC_ENTRY_THRESHOLD":75,"LIVE_ENTRY_ENGINE_ENABLED":false}'
run "tradier48_nolossn2_htf4h"   '{"NOLOSS_MIN_PROFIT_PCT_TRADIER":-2.0,"WT_DC_HTF_GATE":"4h","WT_DC_ENTRY_THRESHOLD":75,"LIVE_ENTRY_ENGINE_ENABLED":false}'

echo ""
echo "=== TRADIER 48-SYM 1YR FINAL ==="
for NAME in tradier48_noloss0_htfNONE tradier48_noloss0_htf4h tradier48_nolossn2_htf4h; do
    R=$(grep "V8_RESULT_LIVE\|V8_RESULT:" "$OUTDIR/result_$NAME.log" 2>/dev/null | tail -1)
    printf "%-32s | %s\n" "$NAME" "$R"
done
