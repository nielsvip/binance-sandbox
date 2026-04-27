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
# 2026-04-27 user directive: vec tests are too heavy on MacBook — refuse to launch here.
# Detection: only S1/S2 have /home/niels/binance-sandbox. MacBook is Darwin and lacks /home/niels.
HOST="$(hostname -s 2>/dev/null || hostname)"
if [ "$(uname -s)" = "Darwin" ] || [ ! -d /home/niels/binance-sandbox ]; then
    echo "[$(date -u '+%H:%M:%S UTC')] ABORT: vec_mass_loop is not allowed on MacBook (host=$HOST). Run on S1 or S2." >&2
    exit 2
fi
# S1: niels (157.180.125.52), conda env. S2: sweep-box (204.168.181.211), miniconda env.
if [ -x /home/niels/.conda/envs/binance_env/bin/python ]; then
    PY=/home/niels/.conda/envs/binance_env/bin/python   # S1
elif [ -x /home/niels/miniconda3/envs/binance_env/bin/python ]; then
    PY=/home/niels/miniconda3/envs/binance_env/bin/python   # S2
else
    echo "[$(date -u '+%H:%M:%S UTC')] ABORT: cannot locate python env on $HOST" >&2
    exit 3
fi
BASE=/home/niels/binance-sandbox
LOG="/tmp/vec_mass_${MODE}_${TAG}_$(date +%Y%m%d_%H%M%S).log"
cd "$BASE"
echo "[$(date -u '+%H:%M:%S UTC')] vec_mass loop start mode=$MODE pick=$PICK bars=$BARS tag=$TAG -> $LOG"
ITER=0
while true; do
    ITER=$((ITER+1))
    echo "[$(date -u '+%H:%M:%S UTC')] iter=$ITER" >> "$LOG"
    "$PY" "$BASE/vec_mass_scan.py" --mode "$MODE" --bars "$BARS" --pick "$PICK" --min-trades 30 --top-report 5 >> "$LOG" 2>&1 || echo "iter $ITER FAILED" >> "$LOG"
done
