#!/bin/bash
# Run a medium-profile auto_promote cycle (48-sym × 1yr × 30min/candidate).
# Designed to surface decision-grade Tier-3 candidates before market open.
# Validates top 5 candidates with sharpe_pt > 0.3 from smoke results so far.
LOG="/Users/niels/logs/auto_promote_medium_$(date -u +%Y%m%d_%H%M).log"
PY=/opt/anaconda3/envs/binance_env/bin/python
cd /Users/niels/Documents/binance
echo "[$(date -u +'%Y-%m-%d %H:%M:%S UTC')] medium auto_promote start -> $LOG"
"$PY" auto_promote.py --once --validation-profile medium --max-validations-per-cycle 5 >> "$LOG" 2>&1
echo "[$(date -u +'%Y-%m-%d %H:%M:%S UTC')] medium auto_promote done"
