#!/bin/bash
# Tier-2 full-sample validation on S2.
# Two arms run sequentially to avoid OOM on 121 stocks × 1.4yr:
#   A: reverted-live-config (Path A removed)
#   B: tradier_3p4361_genuine baseline overrides applied
# Per CLAUDE.md rule 4b: must report on ≥100 stocks × ≥1yr pool-averaged.
# Per user directive 2026-04-29: NO <1 sharpes acceptable anymore.
set -uo pipefail
export EZ_LOG_DIR="${EZ_LOG_DIR:-/tmp}"
OUTDIR=/home/niels/logs/tier2_full_tradier_$(date +%Y%m%d_%H%M)
mkdir -p "$OUTDIR"
PY=/home/niels/miniconda3/envs/binance_env/bin/python
NPZ_DIR=/home/niels/binance-sandbox/backtest_v5/indicators_5m_tradier
cd /home/niels/binance-sandbox

SYMS=$(ls "$NPZ_DIR"/*.npz 2>/dev/null | xargs -n1 basename | sed 's/.npz//' | tr '\n' ',' | sed 's/,$//')
NSYM=$(echo "$SYMS" | tr ',' '\n' | wc -l)
echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] FULL_SAMPLE n_syms=$NSYM start=2024-01-01" | tee -a "$OUTDIR/_meta.log"
echo "$SYMS" > "$OUTDIR/_symbols.txt"

run_arm() {
    local NAME=$1 OVR=$2
    local LOG="$OUTDIR/result_${NAME}.log"
    local T0=$(date +%s)
    echo "[$(date -u '+%H:%M:%S')] START $NAME" | tee -a "$OUTDIR/_meta.log"
    if [ -n "$OVR" ]; then
        V8_OVERRIDE_FILE="$OVR" V8_RATE_GUARD_DISABLED=1 \
            timeout 7200 "$PY" -u backtest_v8_engine.py \
            --mode tradier --account trb --start 2024-01-01 --capital 70000 \
            --symbols "$SYMS" > "$LOG" 2>&1
    else
        V8_RATE_GUARD_DISABLED=1 \
            timeout 7200 "$PY" -u backtest_v8_engine.py \
            --mode tradier --account trb --start 2024-01-01 --capital 70000 \
            --symbols "$SYMS" > "$LOG" 2>&1
    fi
    local T1=$(date +%s)
    local R=$(grep "V8_RESULT_LIVE\|V8_RESULT:" "$LOG" 2>/dev/null | tail -1)
    echo "[$(date -u '+%H:%M:%S')] END   $NAME elapsed=$((T1-T0))s" | tee -a "$OUTDIR/_meta.log"
    echo "  $NAME: $R" | tee -a "$OUTDIR/_meta.log"
}

run_arm "A_reverted_live"   ""
run_arm "B_3p4361_baseline" "/home/niels/binance-sandbox/data/baselines/tradier_3p4361_genuine.json"

echo "" | tee -a "$OUTDIR/_meta.log"
echo "================================================================" | tee -a "$OUTDIR/_meta.log"
echo "  TIER-2 FULL TRADIER SUMMARY (n_syms=$NSYM, start=2024-01-01)" | tee -a "$OUTDIR/_meta.log"
echo "================================================================" | tee -a "$OUTDIR/_meta.log"
for NAME in A_reverted_live B_3p4361_baseline; do
    R=$(grep "V8_RESULT_LIVE\|V8_RESULT:" "$OUTDIR/result_$NAME.log" 2>/dev/null | tail -1)
    printf "  %-22s | %s\n" "$NAME" "$R" | tee -a "$OUTDIR/_meta.log"
done
echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] DONE outdir=$OUTDIR" | tee -a "$OUTDIR/_meta.log"
