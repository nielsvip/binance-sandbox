#!/bin/bash
# V5 Sweep Monitor — Check status of running sweeps
# Usage: ./v5_sweep_monitor.sh          # One-shot status
#        ./v5_sweep_monitor.sh --watch   # Live watch (updates every 30s)

MASTER_DIR="/Users/niels/Documents/binance/backtest_v5/sweep_master"
STATUS_FILE="$MASTER_DIR/STATUS.md"
LOG_FILE="$MASTER_DIR/master.log"
PID_FILE="$MASTER_DIR/master.pid"
CHECKPOINT="$MASTER_DIR/checkpoint.json"
RESULTS="$MASTER_DIR/results.jsonl"

show_status() {
    clear
    echo "═══════════════════════════════════════════════════════════════"
    echo "  V5 SWEEP MONITOR — $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
    echo "═══════════════════════════════════════════════════════════════"

    # Process status
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            echo "  Process: RUNNING (PID=$PID)"
        else
            echo "  Process: DEAD (PID=$PID was not found)"
        fi
    else
        echo "  Process: NOT STARTED"
    fi

    # Checkpoint info
    if [ -f "$CHECKPOINT" ]; then
        COMPLETED=$(python3 -c "import json; d=json.load(open('$CHECKPOINT')); print(len(d.get('completed',[])))" 2>/dev/null)
        TOTAL=$(python3 -c "import json; d=json.load(open('$CHECKPOINT')); print(d.get('total',0))" 2>/dev/null)
        RUNNING=$(python3 -c "import json; d=json.load(open('$CHECKPOINT')); print(d.get('running','none'))" 2>/dev/null)
        SYSTEM=$(python3 -c "import json; d=json.load(open('$CHECKPOINT')); print(d.get('system','?'))" 2>/dev/null)
        ERRORS=$(python3 -c "import json; d=json.load(open('$CHECKPOINT')); print(len(d.get('errors',[])))" 2>/dev/null)
        echo "  System:   $SYSTEM"
        echo "  Progress: $COMPLETED / $TOTAL"
        echo "  Errors:   $ERRORS"
        echo "  Current:  $RUNNING"
    else
        echo "  No checkpoint found. Sweep not started."
    fi

    # Results summary
    if [ -f "$RESULTS" ]; then
        N_RESULTS=$(wc -l < "$RESULTS" | tr -d ' ')
        BEST=$(python3 -c "
import json
results = [json.loads(l) for l in open('$RESULTS') if l.strip()]
ok = [r for r in results if r.get('status') == 'OK' and 'total_pnl' in r]
if ok:
    best = max(ok, key=lambda x: x['total_pnl'])
    print(f\"{best['name']}: \${best['total_pnl']:.2f} ({best.get('n_trades','?')} trades)\")
else:
    print('No results yet')
" 2>/dev/null)
        WORST=$(python3 -c "
import json
results = [json.loads(l) for l in open('$RESULTS') if l.strip()]
ok = [r for r in results if r.get('status') == 'OK' and 'total_pnl' in r]
if ok:
    worst = min(ok, key=lambda x: x['total_pnl'])
    print(f\"{worst['name']}: \${worst['total_pnl']:.2f} ({worst.get('n_trades','?')} trades)\")
else:
    print('No results yet')
" 2>/dev/null)
        echo ""
        echo "  Results:  $N_RESULTS completed"
        echo "  Best:     $BEST"
        echo "  Worst:    $WORST"
    fi

    # Last 10 log lines
    echo ""
    echo "─── RECENT LOG ───"
    if [ -f "$LOG_FILE" ]; then
        tail -10 "$LOG_FILE"
    else
        echo "  No log file"
    fi
    echo "═══════════════════════════════════════════════════════════════"
}

if [ "$1" == "--watch" ]; then
    while true; do
        show_status
        sleep 30
    done
else
    show_status
fi
