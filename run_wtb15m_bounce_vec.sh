#!/usr/bin/env bash
# run_wtb15m_bounce_vec.sh — runs all 8 WT_15M_BOUNCE_OPEN variants through v8_vec_sweep.py
# ROOT-CAUSE FIX TEST: 15m bounce was REENTRY-only; validates it as an OPEN trigger.
# Run on S1: bash run_wtb15m_bounce_vec.sh  (or pass --dry-run to just print commands)
set -euo pipefail

PYTHON=/home/niels/.conda/envs/binance_env/bin/python
SCRIPT=/home/niels/binance-sandbox/v8_vec_sweep.py
LOG_DIR=/home/niels/logs

CRYPTO_SYMS="BTC,ETH,SOL,XRP"
TRADIER_SYMS="AAPL,MSFT,NVDA,TSLA,META,AMD,AMZN,GOOG"
CRYPTO_START="2022-01-01"
TRADIER_START="2024-01-01"

DRY_RUN=0
if [[ "${1:-}" == "--dry-run" ]]; then DRY_RUN=1; fi

run_variant() {
    local mode="$1"; local label="$2"; shift 2
    local syms; local start
    if [[ "$mode" == "crypto" ]]; then syms="$CRYPTO_SYMS"; start="$CRYPTO_START"
    else syms="$TRADIER_SYMS"; start="$TRADIER_START"; fi
    local logfile="$LOG_DIR/wtb15m_${label}_$(date +%Y%m%d_%H%M%S).log"
    local cmd="$PYTHON $SCRIPT --mode $mode --symbols $syms --start $start --workers 1 --no-history $*"
    echo "[wtb15m] $label → $logfile"
    echo "         $cmd"
    if [[ $DRY_RUN -eq 0 ]]; then
        nohup bash -c "$cmd > $logfile 2>&1" < /dev/null &
        local pid=$!
        echo "         PID $pid"
        sleep 2  # stagger launches to avoid NPZ read contention
    fi
}

echo "=== WT_15M_BOUNCE_OPEN vec sweep — $(date -u) ==="
echo "Crypto syms: $CRYPTO_SYMS  start: $CRYPTO_START"
echo "Tradier syms: $TRADIER_SYMS  start: $TRADIER_START"
echo ""

# ── CRYPTO ─────────────────────────────────────────────────────────────────────
run_variant crypto WTB_15M_O_C1 \
    --override WT_15M_BOUNCE_OPEN_ENABLED=True \
    --override WT_15M_BOUNCE_MAX_BARS_AGO=1 \
    --override WT_15M_BOUNCE_REQUIRE_BOTH_HTF=False

run_variant crypto WTB_15M_O_C2 \
    --override WT_15M_BOUNCE_OPEN_ENABLED=True \
    --override WT_15M_BOUNCE_MAX_BARS_AGO=2 \
    --override WT_15M_BOUNCE_REQUIRE_BOTH_HTF=False

run_variant crypto WTB_15M_O_C3 \
    --override WT_15M_BOUNCE_OPEN_ENABLED=True \
    --override WT_15M_BOUNCE_MAX_BARS_AGO=3 \
    --override WT_15M_BOUNCE_REQUIRE_BOTH_HTF=False

run_variant crypto WTB_15M_O_CA \
    --override WT_15M_BOUNCE_OPEN_ENABLED=True \
    --override WT_15M_BOUNCE_MAX_BARS_AGO=2 \
    --override WT_15M_BOUNCE_REQUIRE_BOTH_HTF=True

# ── TRADIER ─────────────────────────────────────────────────────────────────────
run_variant tradier WTB_15M_O_T1 \
    --override WT_15M_BOUNCE_OPEN_ENABLED=True \
    --override WT_15M_BOUNCE_MAX_BARS_AGO=1 \
    --override WT_15M_BOUNCE_REQUIRE_BOTH_HTF=False

run_variant tradier WTB_15M_O_T2 \
    --override WT_15M_BOUNCE_OPEN_ENABLED=True \
    --override WT_15M_BOUNCE_MAX_BARS_AGO=2 \
    --override WT_15M_BOUNCE_REQUIRE_BOTH_HTF=False

run_variant tradier WTB_15M_O_T3 \
    --override WT_15M_BOUNCE_OPEN_ENABLED=True \
    --override WT_15M_BOUNCE_MAX_BARS_AGO=3 \
    --override WT_15M_BOUNCE_REQUIRE_BOTH_HTF=False

run_variant tradier WTB_15M_O_TA \
    --override WT_15M_BOUNCE_OPEN_ENABLED=True \
    --override WT_15M_BOUNCE_MAX_BARS_AGO=2 \
    --override WT_15M_BOUNCE_REQUIRE_BOTH_HTF=True

echo ""
echo "=== Launched. Monitor: tail -f $LOG_DIR/wtb15m_*.log ==="
echo "=== Results: ls -lt data/sweep_results/v8_vec_sweep_*.jsonl | head ==="
