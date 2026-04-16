#!/bin/bash
# V8 engine scalp sweep — runs top 4 configs from expanded V2 sweep
# Uses V8_OVERRIDE_FILE to inject scalp config into the real V8 engine
# Results go to data/v8_scalp_results/

set -e
cd /Users/niels/Documents/binance
PYTHON=/opt/anaconda3/envs/binance_env/bin/python
RESULTS_DIR=data/v8_scalp_results
mkdir -p "$RESULTS_DIR"

CONFIGS=(
    "data/v8_scalp_configs/scalp_v1_wt_15m.json"
    "data/v8_scalp_configs/scalp_v1_wt_15m_redzone.json"
    "data/v8_scalp_configs/scalp_v1_wt_15m_lhll.json"
    "data/v8_scalp_configs/scalp_v1_wt_15m_all_exits.json"
)

for cfg in "${CONFIGS[@]}"; do
    label=$(python3 -c "import json; print(json.load(open('$cfg'))['_SWEEP_LABEL'])")
    echo "═══════════════════════════════════════════════"
    echo "RUNNING: $label"
    echo "CONFIG:  $cfg"
    echo "═══════════════════════════════════════════════"
    V8_OVERRIDE_FILE="$cfg" $PYTHON backtest_v8_engine.py \
        --mode crypto \
        --account inf \
        --start 2026-02-24 \
        --capital 1000 \
        2>&1 | tee "$RESULTS_DIR/${label}.log" | tail -30
    echo ""
    echo "✓ $label complete — log at $RESULTS_DIR/${label}.log"
    echo ""
done

echo "All 4 configs complete. Logs in $RESULTS_DIR/"
