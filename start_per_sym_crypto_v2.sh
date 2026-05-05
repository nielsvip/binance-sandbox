#!/bin/bash
# start_per_sym_crypto_v2.sh — 24/7 per-symbol crypto profiler loop on S1.
# Cycles through ang/fin/men/flz tradeable universe. Daily full-grid sweep.
#
# Usage:
#   bash start_per_sym_crypto_v2.sh status       # show running workers + last cycle
#   bash start_per_sym_crypto_v2.sh start        # launch daemon (idempotent)
#   bash start_per_sym_crypto_v2.sh stop         # stop daemon
#   bash start_per_sym_crypto_v2.sh once         # run one cycle in foreground
#
# Refuses to launch if not on S1. Logs to ~/logs/per_sym_crypto_v2.log
# (NOT /tmp/, per CLAUDE.md launcher rules — /tmp/ is cleared on reboot).

set -u

PYTHON_S1=/home/niels/.conda/envs/binance_env/bin/python
PYTHON_MAC=/opt/anaconda3/envs/binance_env/bin/python
SCRIPT=per_sym_crypto_profiler.py
HOSTNAME=$(hostname -s 2>/dev/null || hostname)
LOG=~/logs/per_sym_crypto_v2.log
PIDFILE=~/logs/per_sym_crypto_v2.pid

# Detect machine
if [[ -d /home/niels/binance-sandbox ]]; then
    BASE=/home/niels/binance-sandbox
    PYTHON=$PYTHON_S1
elif [[ -d /Users/niels/Documents/binance ]]; then
    BASE=/Users/niels/Documents/binance
    PYTHON=$PYTHON_MAC
else
    echo "[start_per_sym_crypto_v2] cannot detect base path"; exit 1
fi

mkdir -p ~/logs
cd "$BASE" || exit 1

cmd_status() {
    echo "[per_sym_crypto_v2] host=$HOSTNAME base=$BASE"
    if [[ -f "$PIDFILE" ]]; then
        pid=$(cat "$PIDFILE")
        if ps -p "$pid" > /dev/null 2>&1; then
            echo "  RUNNING  pid=$pid  uptime=$(ps -o etime= -p $pid | tr -d ' ')"
        else
            echo "  STALE PIDFILE (pid=$pid not running)"
        fi
    else
        echo "  NOT RUNNING"
    fi
    echo "  log tail:"
    tail -10 "$LOG" 2>/dev/null | sed 's/^/    /'
    if [[ -f $BASE/data/hourly_reconfig/per_sym_active_config.json ]]; then
        n=$(grep -o '"verdict"' $BASE/data/hourly_reconfig/per_sym_active_config.json | wc -l)
        promoted=$(grep -o '"verdict": "PROMOTE"' $BASE/data/hourly_reconfig/per_sym_active_config.json | wc -l)
        echo "  active_config entries: $n   PROMOTE verdicts: $promoted"
    fi
}

cmd_start() {
    if [[ -f "$PIDFILE" ]]; then
        pid=$(cat "$PIDFILE")
        if ps -p "$pid" > /dev/null 2>&1; then
            echo "[per_sym_crypto_v2] already running pid=$pid (PID file: $PIDFILE)"
            return 0
        fi
        rm -f "$PIDFILE"
    fi
    # Refuse to launch on Mac unless explicitly forced (live trading machine).
    if [[ "$BASE" == "/Users/niels/Documents/binance" && "${1:-}" != "--force-mac" ]]; then
        echo "[per_sym_crypto_v2] REFUSING to launch on Mac (live trading machine). Use 'once' for foreground or pass --force-mac."; exit 2
    fi
    echo "[per_sym_crypto_v2] starting daemon at $(date -u +%FT%TZ) on $HOSTNAME" | tee -a "$LOG"
    # Daily full-grid: 24h interval. Workers 4 (S1 has 8 cores; leave headroom for sweeps).
    nohup "$PYTHON" -u "$SCRIPT" --daemon --accounts ang,fin,men,flz \
            --years 4 --workers 2 \
            --cycle-interval-s 86400 \
            >> "$LOG" 2>&1 < /dev/null &
    pid=$!
    disown $pid 2>/dev/null
    echo "$pid" > "$PIDFILE"
    sleep 5
    if ! ps -p "$pid" > /dev/null 2>&1; then
        echo "[per_sym_crypto_v2] FAILED — process died at startup. Log tail:"
        tail -20 "$LOG"
        rm -f "$PIDFILE"
        exit 3
    fi
    echo "[per_sym_crypto_v2] started pid=$pid logfile=$LOG"
    sleep 25
    if ps -p "$pid" > /dev/null 2>&1; then
        # Check for fatal errors at T+30s
        if grep -E "Traceback|MODE_CONFIG_MISMATCH|ImportError|FATAL" "$LOG" | tail -3 | grep -q .; then
            echo "[per_sym_crypto_v2] WARNING — fatal markers in log:"
            grep -E "Traceback|MODE_CONFIG_MISMATCH|ImportError|FATAL" "$LOG" | tail -3
        fi
        echo "[per_sym_crypto_v2] T+30s log tail:"
        tail -8 "$LOG"
    else
        echo "[per_sym_crypto_v2] FAILED at T+30s. Log tail:"
        tail -25 "$LOG"
        rm -f "$PIDFILE"
        exit 3
    fi
}

cmd_stop() {
    if [[ -f "$PIDFILE" ]]; then
        pid=$(cat "$PIDFILE")
        if ps -p "$pid" > /dev/null 2>&1; then
            echo "[per_sym_crypto_v2] stopping pid=$pid"
            kill "$pid"
            sleep 2
            if ps -p "$pid" > /dev/null 2>&1; then
                kill -9 "$pid" 2>/dev/null
            fi
        fi
        rm -f "$PIDFILE"
    fi
    echo "[per_sym_crypto_v2] stopped"
}

cmd_once() {
    echo "[per_sym_crypto_v2] running ONE cycle in foreground (host=$HOSTNAME)"
    "$PYTHON" -u "$SCRIPT" --accounts ang,fin,men,flz \
            --years 4 --workers 4
}

action="${1:-status}"
case "$action" in
    status)  cmd_status ;;
    start)   cmd_start  ;;
    stop)    cmd_stop   ;;
    once)    cmd_once   ;;
    --force-mac) cmd_start --force-mac ;;
    *) echo "usage: $0 {status|start|stop|once}"; exit 1 ;;
esac
