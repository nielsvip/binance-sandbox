#!/usr/bin/env bash
# run_crypto_sector_sweeps.sh — Per-sector crypto backtest sweep launcher.
#
# Usage:
#   bash run_crypto_sector_sweeps.sh <sector_name>   — launch sweep for one sector
#   bash run_crypto_sector_sweeps.sh list            — list all sectors and symbol counts
#
# Must be run on S1 (s1-int) in /home/niels/binance-sandbox/
# Python: /home/niels/.conda/envs/binance_env/bin/python
# Tier: crypto_sector_baseline | Start: 2022-01-01 (bear + bull market)
# Workers: 1 per sector (safe on S1 RAM, ~10.8GB/worker peak)
#
# Created: 2026-05-14

PYTHON=/home/niels/.conda/envs/binance_env/bin/python
WORKDIR=/home/niels/binance-sandbox
SECTORS_FILE="$WORKDIR/sectors_crypto.json"
START_DATE="2022-01-01"
TIER="crypto_sector_baseline"
WORKERS=1
TIMEOUT=3600  # 60 min — baseline for 6-12 syms × 4yr takes ~20-40 min
LOG_DIR="$HOME/logs"

mkdir -p "$LOG_DIR"

# ---------------------------------------------------------------------------
# Helper: get comma-separated symbol list for a sector
# ---------------------------------------------------------------------------
get_symbols() {
    local sector="$1"
    "$PYTHON" -c "
import json, sys
with open('$SECTORS_FILE') as f:
    d = json.load(f)
sector = '$sector'
if sector not in d or sector.startswith('_'):
    print('', end='')
    sys.exit(0)
print(','.join(d[sector]), end='')
"
}

# ---------------------------------------------------------------------------
# list command
# ---------------------------------------------------------------------------
list_sectors() {
    echo ""
    echo "Crypto sector sweep definitions ($SECTORS_FILE)"
    echo "Start date: $START_DATE  |  Tier: $TIER  |  Workers: $WORKERS"
    echo "------------------------------------------------------------"
    "$PYTHON" -c "
import json
with open('$SECTORS_FILE') as f:
    d = json.load(f)
for k, v in d.items():
    if k.startswith('_'):
        continue
    print(f'  {k:<30} {len(v):>3} symbols')
"
    echo ""
    echo "Run: bash $0 <sector_name>"
    echo ""
}

# ---------------------------------------------------------------------------
# launch_sector: launch one sector sweep via nohup + disown
# ---------------------------------------------------------------------------
launch_sector() {
    local sector="$1"

    local syms
    syms=$(get_symbols "$sector")
    if [[ -z "$syms" ]]; then
        echo "ERROR: unknown sector '$sector'" >&2
        echo ""
        list_sectors
        exit 1
    fi

    local count
    count=$(echo "$syms" | tr ',' '\n' | grep -c .)
    local TS
    TS=$(date +%Y%m%d_%H%M%S)
    local logfile="$LOG_DIR/crypto_sector_sweep_${sector}_${TS}.log"

    echo ""
    echo "Launching crypto sector sweep: $sector"
    echo "  Symbols ($count): $syms"
    echo "  Start:   $START_DATE"
    echo "  Tier:    $TIER"
    echo "  Workers: $WORKERS"
    echo "  Log:     $logfile"

    cd "$WORKDIR" || { echo "ERROR: cannot cd to $WORKDIR" >&2; exit 1; }

    nohup "$PYTHON" backtest_v8_sweep.py \
        --mode crypto \
        --tier "$TIER" \
        --symbols "$syms" \
        --start "$START_DATE" \
        --workers "$WORKERS" \
        --timeout "$TIMEOUT" \
        > "$logfile" 2>&1 < /dev/null &

    local pid=$!
    disown "$pid"

    echo "  PID:     $pid"
    echo ""
    echo "Verify after 30s:"
    echo "  tail -f $logfile"
    echo "  pgrep -af 'backtest_v8_sweep.*crypto'"
    echo ""
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
CMD="${1:-}"

case "$CMD" in
    list)
        list_sectors
        ;;
    "")
        echo ""
        echo "Usage: bash $0 <sector_name>"
        echo "       bash $0 list"
        echo ""
        list_sectors
        exit 1
        ;;
    *)
        launch_sector "$CMD"
        ;;
esac
