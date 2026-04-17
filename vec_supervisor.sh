#!/bin/bash
# Vec-Backlog supervisor: keeps N workers alive, auto-restarts on exit,
# monitors CPU/memory every 30s and throttles by killing workers if >cap.
#
# Each worker is a vec_backlog.py process with its own --worker-id shard.
# When phase 1 completes (process exits 0), launches vec_phase2.py once.
#
# Usage: ./vec_supervisor.sh <crypto|tradier> <num_workers>

set -u
MODE="${1:-crypto}"
WORKERS="${2:-4}"
CPU_CAP="${CPU_CAP:-95}"
MEM_CAP="${MEM_CAP:-90}"
CPU_FLOOR="${CPU_FLOOR:-30}"

if [ "$(uname)" = "Darwin" ]; then
    PY="/opt/anaconda3/envs/binance_env/bin/python"
    BASE="/Users/niels/Documents/binance"
elif [ -d /home/niels/.conda/envs/binance_env ]; then
    PY="/home/niels/.conda/envs/binance_env/bin/python"
    BASE="/home/niels/binance-sandbox"
else
    PY="/home/niels/miniconda3/envs/binance_env/bin/python"
    BASE="/home/niels/binance-sandbox"
fi

cd "$BASE" || exit 1
LOGDIR="$BASE/data/sweep_results"
mkdir -p "$LOGDIR"

echo "[$(date -u +%H:%M:%S) supervisor] mode=$MODE workers=$WORKERS cpu_cap=$CPU_CAP mem_cap=$MEM_CAP"

PIDS=()
STARTUP_STAGGER="${STARTUP_STAGGER:-15}"  # seconds between worker starts (prevents OOM on concurrent NPZ load)
for i in $(seq 0 $((WORKERS-1))); do
    LOG="$LOGDIR/vec_backlog_${MODE}_w${i}.log"
    $PY -u vec_backlog.py --mode "$MODE" --worker-id "$i" --total-workers "$WORKERS" \
        --cpu-cap "$CPU_CAP" --mem-cap "$MEM_CAP" > "$LOG" 2>&1 &
    PIDS+=($!)
    echo "[supervisor] Started worker $i PID=${PIDS[-1]} → $LOG"
    [ "$i" -lt "$((WORKERS-1))" ] && sleep "$STARTUP_STAGGER"
done

cleanup() {
    echo "[supervisor] SIGTERM — killing workers"
    kill "${PIDS[@]}" 2>/dev/null
    sleep 2
    kill -9 "${PIDS[@]}" 2>/dev/null
    exit 0
}
trap cleanup TERM INT

# Monitor loop
while true; do
    sleep 30
    if command -v top > /dev/null; then
        if [ "$(uname)" = "Darwin" ]; then
            CPU_USED=$(top -l 1 -n 0 | awk '/CPU usage/ {gsub("%",""); print 100-$7}' | head -1)
            MEM_USED=$(top -l 1 -n 0 -s 0 | awk '/PhysMem/ {print $2}' | tr -d 'GMK' | head -1)
        else
            CPU_USED=$(top -bn1 | grep '%Cpu' | awk '{print 100-$8}')
            MEM_USED=$(free | awk '/Mem:/ {printf "%.0f", $3*100/$2}')
        fi
    fi
    # Check workers alive
    ALIVE=0
    NEW_PIDS=()
    for i in "${!PIDS[@]}"; do
        if kill -0 "${PIDS[$i]}" 2>/dev/null; then
            ALIVE=$((ALIVE+1))
            NEW_PIDS+=("${PIDS[$i]}")
        else
            echo "[$(date -u +%H:%M:%S) supervisor] worker $i DEAD — respawning"
            LOG="$LOGDIR/vec_backlog_${MODE}_w${i}.log"
            $PY -u vec_backlog.py --mode "$MODE" --worker-id "$i" --total-workers "$WORKERS" \
                --cpu-cap "$CPU_CAP" --mem-cap "$MEM_CAP" >> "$LOG" 2>&1 &
            NEW_PIDS+=($!)
        fi
    done
    PIDS=("${NEW_PIDS[@]}")
    echo "[$(date -u +%H:%M:%S) supervisor] alive=$ALIVE/$WORKERS cpu=${CPU_USED:-?}% mem=${MEM_USED:-?}%"
done
