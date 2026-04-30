#!/bin/bash
# Tier-2 baseline on S1 — fixed engine, canonical pool_sharpe + sym_sharpe.
# 114 tradier symbols × from 2024-01-01 (1.4yr) clears CLAUDE.md rule 4b.
set -uo pipefail
OUTDIR=/home/niels/logs/tier2_s1_$(date +%Y%m%d_%H%M)
mkdir -p "$OUTDIR"
PY=/home/niels/.conda/envs/binance_env/bin/python
NPZ_DIR=/home/niels/binance-sandbox/backtest_v8/indicators
cd /home/niels/binance-sandbox

# Build tradier symbol list — 114 stocks (no USDC/USDT/BTC suffix → those are crypto)
SYMS=$(ls "$NPZ_DIR"/*.npz 2>/dev/null | xargs -n1 basename | sed 's/.npz//' | grep -vE 'USDC$|USDT$|BTC$' | head -114 | tr '\n' ',' | sed 's/,$//')
NSYM=$(echo "$SYMS" | tr ',' '\n' | wc -l)
echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] FULL_SAMPLE n_syms=$NSYM start=2024-01-01" | tee -a "$OUTDIR/_meta.log"
echo "$SYMS" > "$OUTDIR/_symbols.txt"

run_arm() {
    local NAME=$1 OVR=$2
    local LOG="$OUTDIR/result_${NAME}.log"
    local T0=$(date +%s)
    echo "[$(date -u '+%H:%M:%S')] START $NAME" | tee -a "$OUTDIR/_meta.log"
    if [ -n "$OVR" ]; then
        V8_OVERRIDE_FILE="$OVR" V8_RATE_GUARD_DISABLED=1 V8_SKIP_PROCESS_POSITION=1 \
            timeout 5400 "$PY" -u backtest_v8_engine.py \
            --mode tradier --account trb --start 2024-01-01 --capital 70000 \
            --symbols "$SYMS" > "$LOG" 2>&1
    else
        V8_RATE_GUARD_DISABLED=1 V8_SKIP_PROCESS_POSITION=1 \
            timeout 5400 "$PY" -u backtest_v8_engine.py \
            --mode tradier --account trb --start 2024-01-01 --capital 70000 \
            --symbols "$SYMS" > "$LOG" 2>&1
    fi
    local T1=$(date +%s)
    local R=$(grep "V8_RESULT:" "$LOG" 2>/dev/null | tail -1)
    echo "[$(date -u '+%H:%M:%S')] END   $NAME elapsed=$((T1-T0))s" | tee -a "$OUTDIR/_meta.log"
    echo "  $NAME: $R" | tee -a "$OUTDIR/_meta.log"
}

run_arm "BASELINE_reverted_live"  ""
run_arm "BASELINE_3p4361_baseline" "/home/niels/binance-sandbox/data/baselines/tradier_3p4361_genuine.json"

echo "" | tee -a "$OUTDIR/_meta.log"
echo "================================================================" | tee -a "$OUTDIR/_meta.log"
echo "  TIER-2 S1 SUMMARY (n_syms=$NSYM, start=2024-01-01, skip-PP=1)" | tee -a "$OUTDIR/_meta.log"
echo "================================================================" | tee -a "$OUTDIR/_meta.log"
for NAME in BASELINE_reverted_live BASELINE_3p4361_baseline; do
    R=$(grep "V8_RESULT:" "$OUTDIR/result_$NAME.log" 2>/dev/null | tail -1)
    printf "  %-28s | %s\n" "$NAME" "$R" | tee -a "$OUTDIR/_meta.log"
done
echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] DONE outdir=$OUTDIR" | tee -a "$OUTDIR/_meta.log"
