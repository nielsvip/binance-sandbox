#!/bin/bash
# sweep_watchdog_48h.sh — Autonomous 48h sweep runner
# Keeps S1 (crypto) and S2 (tradier) sweeps running continuously.
# No agent needed. Logs to ~/logs/watchdog_*.log
# Usage: bash sweep_watchdog_48h.sh &

S1="157.180.125.52"
S2="204.168.181.211"
PYTHON_S1="/home/niels/.conda/envs/binance_env/bin/python"
PYTHON_S2="/home/niels/miniconda3/envs/binance_env/bin/python"
SANDBOX="/home/niels/binance-sandbox"
LOGS="/home/niels/logs"
LOGFILE="$HOME/logs/watchdog_$(date +%Y%m%d_%H%M).log"
mkdir -p "$HOME/logs"
exec > >(tee -a "$LOGFILE") 2>&1

log() { echo "[$(date -u '+%Y-%m-%d %H:%M UTC')] $*"; }

# Push latest code to both servers
push_code() {
    log "Pushing updated code to S1 and S2..."
    rsync -qz /Users/niels/Documents/binance/backtest_v8_sweep.py niels@$S1:$SANDBOX/backtest_v8_sweep.py
    rsync -qz /Users/niels/Documents/binance/backtest_v8_engine.py niels@$S1:$SANDBOX/backtest_v8_engine.py
    rsync -qz /Users/niels/Documents/binance/backtest_v8_sweep.py niels@$S2:$SANDBOX/backtest_v8_sweep.py
    rsync -qz /Users/niels/Documents/binance/backtest_v8_engine.py niels@$S2:$SANDBOX/backtest_v8_engine.py
    log "Code pushed."
}

# Start S1 crypto sweep (4 symbols, 4 years, 4 workers)
start_s1_crypto() {
    log "Starting S1 crypto sweep..."
    ssh -o ConnectTimeout=10 niels@$S1 "
        screen -S sweep_crypto -X quit 2>/dev/null; sleep 1
        screen -dmS sweep_crypto bash -c \"
            cd $SANDBOX && $PYTHON_S1 -u backtest_v8_sweep.py \
              --mode crypto --account inf --start 2022-01-01 --capital 1000 \
              --workers 4 --tier 25 --symbols core \
              > $LOGS/v8_sweep_crypto_t25_core_\$(date +%Y%m%d_%H%M).log 2>&1
        \"
    " && log "S1 crypto sweep started (4 symbols, 4yr)" || log "WARN: S1 crypto start failed"
}

# Start S2 tradier sweep (fast symbols, 2 years, 6 workers)
start_s2_tradier() {
    log "Starting S2 tradier sweep..."
    ssh -o ConnectTimeout=10 niels@$S2 "
        screen -S sweep_tradier_new -X quit 2>/dev/null; sleep 1
        screen -dmS sweep_tradier_new bash -c \"
            cd $SANDBOX && $PYTHON_S2 -u backtest_v8_sweep.py \
              --mode tradier --account trc --start 2024-01-01 --capital 10000 \
              --workers 6 --tier 25 --symbols fast \
              > $LOGS/v8_sweep_tradier_t25_fast_\$(date +%Y%m%d_%H%M).log 2>&1
        \"
    " && log "S2 tradier sweep started (12 symbols, 2yr)" || log "WARN: S2 tradier start failed"
}

# Check if a screen session is running and has active engine processes
check_alive() {
    local HOST=$1 SESSION=$2 MODE=$3
    local RUNNING=$(ssh -o ConnectTimeout=5 niels@$HOST "screen -ls 2>/dev/null | grep $SESSION && pgrep -f 'backtest_v8_engine.*$MODE' | wc -l" 2>/dev/null | tail -1 || echo "0")
    echo "$RUNNING"
}

log "=== sweep_watchdog_48h.sh started. Running for 48 hours. ==="
push_code

# Kill old sweeps and start fresh
log "Stopping old crypto sweeps on S1..."
ssh -o ConnectTimeout=10 niels@$S1 "
    screen -S sweep_crypto -X quit 2>/dev/null
    pkill -f 'backtest_v8_engine.*crypto' 2>/dev/null
    sleep 2
" 2>/dev/null || true

log "Stopping old tradier sweeps on S2 (keeping existing running ones until new ones start)..."
# Don't kill existing tradier unless confirmed broken

start_s1_crypto
start_s2_tradier

DEADLINE=$(($(date +%s) + 172800))  # 48 hours from now
CYCLE=0

while [ $(date +%s) -lt $DEADLINE ]; do
    sleep 300  # Check every 5 minutes
    CYCLE=$((CYCLE + 1))
    log "--- Watchdog cycle $CYCLE ---"

    # Check S1 crypto
    S1_ENGINE_COUNT=$(ssh -o ConnectTimeout=5 niels@$S1 "pgrep -f 'backtest_v8_engine.*crypto' | wc -l" 2>/dev/null || echo "0")
    S1_SWEEP_COUNT=$(ssh -o ConnectTimeout=5 niels@$S1 "pgrep -f 'backtest_v8_sweep.*crypto' | wc -l" 2>/dev/null || echo "0")
    log "S1: crypto_engines=$S1_ENGINE_COUNT sweep_procs=$S1_SWEEP_COUNT"

    # Check S2 tradier
    S2_ENGINE_COUNT=$(ssh -o ConnectTimeout=5 niels@$S2 "pgrep -f 'backtest_v8_engine.*tradier' | wc -l" 2>/dev/null || echo "0")
    S2_SWEEP_COUNT=$(ssh -o ConnectTimeout=5 niels@$S2 "pgrep -f 'backtest_v8_sweep.*tradier' | wc -l" 2>/dev/null || echo "0")
    log "S2: tradier_engines=$S2_ENGINE_COUNT sweep_procs=$S2_SWEEP_COUNT"

    # Quick progress check
    S1_PROGRESS=$(ssh -o ConnectTimeout=5 niels@$S1 "python3 -c \"
import json, glob, os
files = sorted(glob.glob('$SANDBOX/backtest_v8/sweeps/v8_sweep_crypto*.json'), key=os.path.getmtime, reverse=True)[:1]
if files:
    d = json.load(open(files[0]))
    configs = d if isinstance(d,list) else list(d.values())
    ok = [c for c in configs if c.get('trades',0) > 0]
    print(f'S1_CRYPTO: {len(ok)}/{len(configs)} with_trades')
else:
    print('S1_CRYPTO: no_progress_file')
\" 2>/dev/null" 2>/dev/null || echo "S1_CRYPTO: unreachable")
    log "$S1_PROGRESS"

    S2_PROGRESS=$(ssh -o ConnectTimeout=5 niels@$S2 "python3 -c \"
import json, glob, os
files = sorted(glob.glob('$SANDBOX/backtest_v8/sweeps/v8_sweep_tradier*.json'), key=os.path.getmtime, reverse=True)[:1]
if files:
    d = json.load(open(files[0]))
    configs = d if isinstance(d,list) else list(d.values())
    ok = [c for c in configs if c.get('trades',0) > 0]
    print(f'S2_TRADIER: {len(ok)}/{len(configs)} with_trades latest={max((c.get(\"trades\",0) for c in configs),default=0)}')
else:
    print('S2_TRADIER: no_progress_file')
\" 2>/dev/null" 2>/dev/null || echo "S2_TRADIER: unreachable")
    log "$S2_PROGRESS"

    # Restart S1 if dead
    if [ "$S1_SWEEP_COUNT" -lt "1" ] 2>/dev/null; then
        log "ALERT: S1 crypto sweep died — restarting..."
        push_code
        start_s1_crypto
    fi

    # Restart S2 if dead
    if [ "$S2_SWEEP_COUNT" -lt "1" ] 2>/dev/null; then
        log "ALERT: S2 tradier sweep died — restarting..."
        push_code
        start_s2_tradier
    fi
done

log "=== 48h watchdog complete ==="
