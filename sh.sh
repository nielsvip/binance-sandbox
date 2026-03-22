#!/bin/bash
# sh.sh - Start all Binance services in correct order after reboot
# Order: 1) Data providers (ez_positions.py), 2) Watchdog (starts ez_positions_quick), 3) Indicators, 4) Trading (ez_manage)

USER_ID=$(id -u)
export XDG_RUNTIME_DIR="/run/user/$USER_ID"
if [ ! -d "$XDG_RUNTIME_DIR" ]; then 
    mkdir -p "$XDG_RUNTIME_DIR"
    chmod 700 "$XDG_RUNTIME_DIR"
fi

LOG_DIR="/home/niels/logs"
LOG_FILE="${LOG_DIR}/start_all_services.log"
BINANCE_DIR="/home/niels/binance"
CONDA_SH="/home/niels/miniforge3/etc/profile.d/conda.sh"
SYSTEMD_DIR="${HOME}/.config/systemd/user"
LOCK_FILE="${LOG_DIR}/sh_sh.lock"

mkdir -p "$LOG_DIR"
mkdir -p "$SYSTEMD_DIR"

# Prevent multiple instances of sh.sh from running simultaneously
if [ -f "$LOCK_FILE" ]; then
    LOCK_PID=$(cat "$LOCK_FILE" 2>/dev/null)
    if [ -n "$LOCK_PID" ] && kill -0 "$LOCK_PID" 2>/dev/null; then
        echo "[$(date +'%Y-%m-%d %H:%M:%S')] [start_all_services] Another sh.sh is already running (PID $LOCK_PID). Exiting."
        exit 0
    fi
fi
echo $$ > "$LOCK_FILE"
trap "rm -f '$LOCK_FILE'" EXIT

log_message() {
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] [start_all_services] $1" | tee -a "$LOG_FILE"
}

log_message "🚀 Starting all Binance services in correct order..."

# PRE-STEP: Nuke all running scripts to ensure a clean slate and avoid duplicates
log_message "PRE-STEP: Nuking existing processes to ensure clean slate..."
if command -v systemctl >/dev/null 2>&1; then
    systemctl --user stop "binance-*" 2>/dev/null || true
fi

# Kill all background Python/bash processes for Binance/Tradier
log_message "Killing all Binance/Tradier processes..."
pkill -9 -f "python.*ez_" 2>/dev/null || true
pkill -9 -f "python.*tradier" 2>/dev/null || true
pkill -9 -f "python.*pa.py" 2>/dev/null || true
pkill -9 -f "run_with_watchdog_LINUX" 2>/dev/null || true
pkill -9 -f "rsync_market_data_to_server" 2>/dev/null || true

# Wait for processes to actually die (max 10s)
for i in $(seq 1 10); do
    P_COUNT=$(pgrep -f "python.*(ez_|tradier_|pa\.py)" | wc -l)
    W_COUNT=$(pgrep -f "run_with_watchdog_LINUX" | wc -l)
    REMAINING=$((P_COUNT + W_COUNT))
    if [ "$REMAINING" -eq 0 ]; then break; fi
    log_message "Waiting for $REMAINING processes to die ($i/10)..."
    sleep 1
done
# Clean up zombie processes
ZOMBIES=$(ps -eo pid,stat | awk '$2 ~ /Z/ {print $1}')
if [ -n "$ZOMBIES" ]; then
    log_message "Cleaning up zombie processes..."
    for zpid in $ZOMBIES; do kill -9 "$zpid" 2>/dev/null || true; done
fi
# Final verify — force kill any survivors
P_COUNT=$(pgrep -f "python.*(ez_|tradier_|pa\.py)" | wc -l)
if [ "$P_COUNT" -gt 0 ]; then
    log_message "Force killing $P_COUNT survivors..."
    pkill -9 -f "python.*(ez_|tradier_|pa\.py)" 2>/dev/null || true
    sleep 2
fi
log_message "✅ All old processes confirmed dead"

# STEP 0: Install/Update systemd service files
log_message "STEP 0: Installing/Updating systemd service files..."
cp "$BINANCE_DIR"/binance-*.service "$SYSTEMD_DIR/" 2>/dev/null || true
if command -v systemctl >/dev/null 2>&1; then
    systemctl --user daemon-reload
    log_message "✅ Systemd files updated and reloaded"
fi

# STEP 1: Start data providers (ez_positions.py) - one per account
log_message "STEP 1: Starting data providers (ez_positions.py)..."
ACCOUNTS=("ang" "inf" "men" "flz" "fin")
WATCHDOG_BIN="./run_with_watchdog_LINUX.sh"

for account in "${ACCOUNTS[@]}"; do
    if ! pgrep -f "ez_positions.py.*--account.*${account}" >/dev/null 2>&1; then
        log_message "Starting ez_positions.py for ${account} via watchdog..."
        (cd "$BINANCE_DIR" && source "$CONDA_SH" && conda activate binance_env && \
        nohup bash "$WATCHDOG_BIN" ez_positions.py --account "$account" >> "${LOG_DIR}/watchdog_ez_positions_${account}.log" 2>&1 &)
        sleep 2
    else
        log_message "ez_positions.py for ${account} already running"
    fi
done

if ! pgrep -f "tradier_positions.py" >/dev/null 2>&1; then
    log_message "Starting tradier_positions.py for all accounts via watchdog..."
    (cd "$BINANCE_DIR" && source "$CONDA_SH" && conda activate binance_env && \
    nohup bash "$WATCHDOG_BIN" tradier_positions.py --accounts tra trb trc >> "${LOG_DIR}/watchdog_tradier_positions.log" 2>&1 &)
    sleep 2
else
    log_message "tradier_positions.py already running"
fi

sleep 5
log_message "✅ Data providers started (waiting 15s for them to initialize)..."
sleep 15

# STEP 1.5: Start DATA services via systemd (Rankings, Prices, etc.)
log_message "STEP 1.5: Starting DATA services (rankings, prices, crosses)..."
DATA_SERVICES=(
    "binance-ezmarketdata.service"
    "binance-ezprices.service"
    "binance-ezprices-ws.service"
    "binance-ezmarkprices.service"
    "binance-ezklines.service"
    "binance-ezrankings.service"
    "binance-ezcrosses.service"
)

if command -v systemctl >/dev/null 2>&1; then
    for service in "${DATA_SERVICES[@]}"; do
        log_message "Starting $service..."
        systemctl --user restart "$service" 2>&1 | tee -a "$LOG_FILE"
        sleep 1
    done
fi

if ! pgrep -f "tradier_prices.py" >/dev/null 2>&1; then
    log_message "Starting tradier_prices.py via watchdog..."
    (cd "$BINANCE_DIR" && source "$CONDA_SH" && conda activate binance_env && \
    nohup bash "$WATCHDOG_BIN" tradier_prices.py >> "${LOG_DIR}/watchdog_tradier_prices.log" 2>&1 &)
    sleep 2
fi

if ! pgrep -f "tradier_rankings.py" >/dev/null 2>&1; then
    log_message "Starting tradier_rankings.py via watchdog..."
    (cd "$BINANCE_DIR" && source "$CONDA_SH" && conda activate binance_env && \
    nohup bash "$WATCHDOG_BIN" tradier_rankings.py >> "${LOG_DIR}/watchdog_tradier_rankings.log" 2>&1 &)
    sleep 2
fi

sleep 2

# STEP 2: Start watchdog (which will start ez_positions_quick.py once data providers are ready)
log_message "STEP 2: Starting watchdog (will start ez_positions_quick.py)..."
if ! pgrep -f "ez_positions_watchdog.py" >/dev/null 2>&1; then
    (cd "$BINANCE_DIR" && source "$CONDA_SH" && conda activate binance_env && \
    nohup python3 -u ez_positions_watchdog.py >> "${LOG_DIR}/ez_positions_watchdog.log" 2>&1 &)
    log_message "✅ Watchdog started"
else
    log_message "Watchdog already running"
fi
sleep 5

# STEP 3: Start indicator workers
log_message "STEP 3: Starting ez_share_ind and indicator workers..."

log_message "Restarting ez_share_ind.py via systemd (kills port 50005 first)..."
fuser -k 50005/tcp 2>/dev/null || true
sleep 1
systemctl --user restart binance-ezshareind.service
sleep 3
WORKER_TOTAL=${WORKER_TOTAL_INSTANCES:-3}
if [ "$WORKER_TOTAL" -gt 1 ]; then
    for i in $(seq 0 $((WORKER_TOTAL-1))); do
        if ! pgrep -f "ez_indicators.py --worker $i" >/dev/null 2>&1; then
            log_message "Starting indicator worker ${i} via watchdog..."
            (cd "$BINANCE_DIR" && source "$CONDA_SH" && conda activate binance_env && \
            export WORKER_INSTANCE_ID=$i && export WORKER_TOTAL_INSTANCES=$WORKER_TOTAL && \
            nohup bash "$WATCHDOG_BIN" ez_indicators.py --worker $i >> "${LOG_DIR}/watchdog_ez_indicators_worker${i}.log" 2>&1 &)
            sleep 3
        fi
    done
    if ! pgrep -f "ez_indicators_merger.py" >/dev/null 2>&1; then
        log_message "Starting indicator merger via watchdog..."
        (cd "$BINANCE_DIR" && source "$CONDA_SH" && conda activate binance_env && \
        nohup bash "$WATCHDOG_BIN" ez_indicators_merger.py >> "${LOG_DIR}/watchdog_ez_indicators_merger.log" 2>&1 &)
    fi
    
    if ! pgrep -f "tradier_indicators.py" >/dev/null 2>&1; then
        log_message "Starting tradier_indicators.py via watchdog..."
        (cd "$BINANCE_DIR" && source "$CONDA_SH" && conda activate binance_env && \
        nohup bash "$WATCHDOG_BIN" tradier_indicators.py >> "${LOG_DIR}/watchdog_tradier_indicators.log" 2>&1 &)
    fi

    log_message "✅ Indicator workers started"
else
    log_message "Skipping indicator workers (WORKER_TOTAL_INSTANCES=${WORKER_TOTAL})"
fi
sleep 3

# STEP 4: Start trading services (ez_manage) via systemd
log_message "STEP 4: Starting trading services (ez_manage)..."
SERVER_LEADERSHIP_FLAG="${BINANCE_DIR}/server_leads_manage.flag"
touch "$SERVER_LEADERSHIP_FLAG"
log_message "✅ Server leadership flag created: $SERVER_LEADERSHIP_FLAG"

if command -v systemctl >/dev/null 2>&1; then
    MANAGE_SERVICES=(
        "binance-ezmanage-ang.service"
        "binance-ezmanage-inf.service"
        "binance-ezmanage-men.service"
        "binance-ezmanage-flz.service"
        "binance-ezmanage-fin.service"
    )
    for service in "${MANAGE_SERVICES[@]}"; do
        log_message "Starting $service..."
        systemctl --user restart "$service" 2>&1 | tee -a "$LOG_FILE"
        sleep 2
    done

    # Loss mitigator (ang gain guard)
    log_message "Starting binance-loss-mitigator.service..."
    systemctl --user restart "binance-loss-mitigator.service" 2>&1 | tee -a "$LOG_FILE"

    log_message "✅ Trading services started"
else
    log_message "⚠️ systemctl not available - skipping trading services"
fi

TRADIER_MANAGE_ACCOUNTS=("trb" "trc")
for account in "${TRADIER_MANAGE_ACCOUNTS[@]}"; do
    if ! pgrep -f "tradier_manage.py.*--accounts.*${account}" >/dev/null 2>&1; then
        log_message "Starting tradier_manage.py for ${account} via watchdog..."
        (cd "$BINANCE_DIR" && source "$CONDA_SH" && conda activate binance_env && \
        nohup bash "$WATCHDOG_BIN" tradier_manage.py --accounts "$account" >> "${LOG_DIR}/watchdog_tradier_manage_${account}.log" 2>&1 &)
        sleep 2
    else
        log_message "tradier_manage.py for ${account} already running"
    fi
done

# STEP 5: Start analytics dashboard (trade_analytics.py on port 5050)
log_message "STEP 5: Starting trade_analytics.py on port 5050..."
fuser -k 5050/tcp 2>/dev/null || true
sleep 1
(cd "$BINANCE_DIR" && source "$CONDA_SH" && conda activate binance_env && \
nohup python3 -u trade_analytics.py >> "${LOG_DIR}/trade_analytics.log" 2>&1 &)
log_message "✅ trade_analytics.py started"

# STEP 6: Start disk space watchdog
log_message "STEP 6: Starting ez_disk_watchdog.py..."
if ! pgrep -f "ez_disk_watchdog.py" >/dev/null 2>&1; then
    (cd "$BINANCE_DIR" && nohup /home/niels/.conda/envs/binance_env/bin/python -u ez_disk_watchdog.py >> "${LOG_DIR}/ez_disk_watchdog.log" 2>&1 &)
    log_message "✅ ez_disk_watchdog.py started"
else
    log_message "ez_disk_watchdog.py already running"
fi

# Final status check
log_message "📊 Final status check..."
log_message "Data providers: $(pgrep -f "ez_positions.py" | wc -l) processes running"
log_message "Watchdog: $(pgrep -f "ez_positions_watchdog.py" | wc -l) processes running"
log_message "ez_positions_quick: $(pgrep -f "ez_positions_quick.py" | wc -l) processes running"
log_message "Indicators: $(pgrep -f "ez_indicators" | wc -l) processes running"
log_message "Tradier processes: $(pgrep -f "python.*tradier" | wc -l) processes running"
if command -v systemctl >/dev/null 2>&1; then
    log_message "Trading/Data services status:"
    systemctl --user list-units --type=service --state=running 'binance-*.service' --no-pager 2>&1 | tee -a "$LOG_FILE"
fi

log_message "✅✅✅ All services startup complete!"
echo "Check logs: $LOG_FILE"
exit 0
