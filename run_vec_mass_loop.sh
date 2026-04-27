#!/bin/bash
# Continuous vec_mass_scan loop. Args: <mode> <pick> <bars> <log_tag>
# Each iteration writes a CSV. Loops until killed.
# Memory footprint: tiny (~few MB per iter, then released).
# Output goes to data/sweep_results/vec_mass_${mode}_<unix_ts>.csv (script default).
set -uo pipefail
MODE="${1:-crypto}"
PICK="${2:-3}"
BARS="${3:-5000}"
TAG="${4:-default}"
case "$(hostname -s 2>/dev/null || hostname)" in
    s1*|*157*)  PY=/home/niels/.conda/envs/binance_env/bin/python; BASE=/home/niels/binance-sandbox ;;
    s2*|*204*)  PY=/home/niels/miniconda3/envs/binance_env/bin/python; BASE=/home/niels/binance-sandbox ;;
    *)          PY=/opt/anaconda3/envs/binance_env/bin/python; BASE=/Users/niels/Documents/binance ;;
esac
LOG="/tmp/vec_mass_${MODE}_${TAG}_$(date +%Y%m%d_%H%M%S).log"
cd "$BASE"
echo "[$(date -u '+%H:%M:%S UTC')] vec_mass loop start mode=$MODE pick=$PICK bars=$BARS tag=$TAG -> $LOG"
ITER=0
while true; do
    ITER=$((ITER+1))
    echo "[$(date -u '+%H:%M:%S UTC')] iter=$ITER" >> "$LOG"
    "$PY" "$BASE/vec_mass_scan.py" --mode "$MODE" --bars "$BARS" --pick "$PICK" --min-trades 30 --top-report 5 >> "$LOG" 2>&1 || echo "iter $ITER FAILED" >> "$LOG"
done
