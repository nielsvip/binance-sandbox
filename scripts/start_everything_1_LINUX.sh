#!/bin/bash
# start_everything_1_LINUX.sh - Manages (restarts/starts) DATA systemd user services on Linux

USER_ID=$(id -u)
export XDG_RUNTIME_DIR="/run/user/$USER_ID"
if [ ! -d "$XDG_RUNTIME_DIR" ]; then
    mkdir -p "$XDG_RUNTIME_DIR"; chmod 700 "$XDG_RUNTIME_DIR"
fi

LOG_DIR="/home/niels/logs"
LOG_FILE="${LOG_DIR}/start_everything_1_LINUX.log"
mkdir -p "$LOG_DIR"

RUN_SOURCE_ARG="${1:-SYSTEM}" # Default to SYSTEM if no source passed
REASON_ARG="${2:-Automated check by SE_Linux_Main}" # Default reason

log_msg() {
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] [SE1_Linux] (Invoked by: $RUN_SOURCE_ARG) $1" | tee -a "$LOG_FILE"
}

DATA_SERVICE_BASENAMES=(
    "binance-ezprices-ws"
    "binance_ezmarkprices"
    "binance-ezprices"
    "binance-ezcrosses"
    "binance-ezindicators"
    "binance-ezrankings"
)
ACTION_TO_PERFORM="restart"

# If a Mac leadership flag is present (pulled via lightweight SSH), stop ez_manage on server
MAC_HOST="s1-int" # same host for coordination; adjust if different
MAC_FLAG_PATH="/Users/niels/Documents/binance/mac_leads_manage.flag"
if ssh -o BatchMode=yes -o ConnectTimeout=5 "$MAC_HOST" "test -f '$MAC_FLAG_PATH'" 2>/dev/null; then
    log_msg "Mac leadership flag detected on Mac. Stopping server ez_manage services to avoid interference."
    /home/niels/binance/start_everything_2_LINUX.sh stop "SE1_Linux" "Mac leads manage"
fi

log_msg "Script called. Reason: '$REASON_ARG'. Performing '$ACTION_TO_PERFORM' on all DATA services..."

for service_base in "${DATA_SERVICE_BASENAMES[@]}"; do
    SERVICE_NAME="${service_base}.service"
    log_msg "Processing: $SERVICE_NAME, Action: $ACTION_TO_PERFORM"
    # ... (rest of the service handling logic remains the same) ...
    systemctl --user "$ACTION_TO_PERFORM" "$SERVICE_NAME" >> "$LOG_FILE" 2>&1
    exit_status=$?
    
    if [ $exit_status -ne 0 ]; then
        log_msg "WARNING: Command '$ACTION_TO_PERFORM $SERVICE_NAME' FAILED with exit code $exit_status."
    else
        log_msg "Command '$ACTION_TO_PERFORM $SERVICE_NAME' issued successfully."
        sleep 1 # Allow service to change state
        if systemctl --user is-active "$SERVICE_NAME" >/dev/null 2>&1; then
            log_msg "$SERVICE_NAME is ACTIVE after $ACTION_TO_PERFORM."
        else
            log_msg "WARNING: $SERVICE_NAME is NOT active after $ACTION_TO_PERFORM. Check logs for $SERVICE_NAME."
        fi
    fi
    sleep 0.5 # Stagger actions
done

log_msg "Action '$ACTION_TO_PERFORM' completed for all listed DATA services."
# ... (summary logging can remain)
echo "Operation complete. Log: $LOG_FILE"