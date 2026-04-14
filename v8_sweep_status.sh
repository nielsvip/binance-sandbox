#!/bin/bash
# V8 Sweep Hourly Status — SSH to both servers, pull progress
# Usage: bash v8_sweep_status.sh
# Or loop: watch -n 3600 bash v8_sweep_status.sh

S1="s1-int"
S2="s2-int"
SWEEP_DIR="/home/niels/binance-sandbox/backtest_v8/sweeps"

echo "════════════════════════════════════════════════════════════"
echo "  V8 SWEEP STATUS — $(date -u '+%Y-%m-%d %H:%M UTC')"
echo "════════════════════════════════════════════════════════════"

for SERVER in "$S1" "$S2"; do
    LABEL=$(echo $SERVER | grep -o '157\|204' | head -1)
    echo ""
    echo "▶ SERVER $LABEL ($SERVER):"

    # NPZ counts
    echo -n "  NPZ crypto (v8): "
    ssh -o ConnectTimeout=5 $SERVER "ls /home/niels/binance-sandbox/backtest_v8/indicators/*.npz 2>/dev/null | wc -l"
    echo -n "  NPZ tradier (v4): "
    ssh -o ConnectTimeout=5 $SERVER "ls /home/niels/binance-sandbox/backtest_v4_tradier/indicators/*.npz 2>/dev/null | wc -l"

    # Active screens
    echo "  Screens:"
    ssh -o ConnectTimeout=5 $SERVER "screen -ls 2>/dev/null | grep -v 'Sockets\|There are\|No Sockets'" | sed 's/^/    /'

    # Sweep progress from latest progress JSON
    PROGRESS=$(ssh -o ConnectTimeout=5 $SERVER "ls -t ${SWEEP_DIR}/v8_sweep_*_progress.json 2>/dev/null | head -3")
    if [ -n "$PROGRESS" ]; then
        echo "  Sweep progress:"
        for F in $PROGRESS; do
            COUNT=$(ssh -o ConnectTimeout=5 $SERVER "python3 -c \"import json; d=json.load(open('$F')); ok=[r for r in d if r.get('status')=='ok' and r.get('trades',0)>0]; best=sorted(ok,key=lambda x:-x.get('sharpe',0))[:3] if ok else []; print(f'  {len(d)} done, {len(ok)} with trades | best sharpe: {[r[\\\"sharpe\\\"] for r in best]}')\" 2>/dev/null")
            echo "    $(basename $F): $COUNT"
        done
    else
        echo "  No sweep progress files yet"
    fi

    # CPU/memory load
    echo -n "  Load: "
    ssh -o ConnectTimeout=5 $SERVER "uptime | awk -F'load average:' '{print \$2}'" 2>/dev/null

    # Lock status
    echo -n "  Sweep lock: "
    ssh -o ConnectTimeout=5 $SERVER "cat /home/niels/SWEEP_RUNNING 2>/dev/null || echo 'none'"
done

echo ""
echo "════════════════════════════════════════════════════════════"
