#!/bin/bash
# start_everything_2_LINUX.sh - Manually Manage Binance bot ez_manage systemd user services

USER_ID=$(id -u)
export XDG_RUNTIME_DIR="/run/user/$USER_ID"
if [ ! -d "$XDG_RUNTIME_DIR" ]; then 
    mkdir -p "$XDG_RUNTIME_DIR"
    chmod 700 "$XDG_RUNTIME_DIR"
fi

SERVICE_BASENAMES=(
    "binance-ezmanage-ang"
    "binance-ezmanage-inf"
    "binance-ezmanage-men"
    "binance-ezmanage-flz"
)

ACTION="$1" 

if [[ "$ACTION" != "start" && "$ACTION" != "stop" && "$ACTION" != "restart" && "$ACTION" != "status" ]]; then
    echo "Usage: $0 <start|stop|restart|status>"
    echo "Example: $0 start"
    exit 1
fi

LOG_DIR="/home/niels/logs" # Log directory remains the same
LOG_FILE="${LOG_DIR}/start_everything_2_LINUX_manual.log" 
mkdir -p "$LOG_DIR"

log_message() {
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] [start_everything_2_LINUX] $1" | tee -a "$LOG_FILE"
}

log_message "Manual action '$ACTION' invoked for all ez_manage services..."
# ... (rest of the script remains the same as provided in the previous response) ...
for service_base in "${SERVICE_BASENAMES[@]}"; do
    SERVICE_NAME="${service_base}.service"
    log_message "Processing: $SERVICE_NAME, Action: $ACTION"
    
    if [[ "$ACTION" == "status" ]]; then
        echo "--- Status for $SERVICE_NAME ---" | tee -a "$LOG_FILE"
        systemctl --user status "$SERVICE_NAME" --no-pager -l | tee -a "$LOG_FILE"
        echo "------------------------------------" | tee -a "$LOG_FILE"
    else
        systemctl --user "$ACTION" "$SERVICE_NAME" >> "$LOG_FILE" 2>&1
        exit_status=$?
        
        if [ $exit_status -ne 0 ]; then
            log_message "WARNING: Command '$ACTION $SERVICE_NAME' FAILED with exit code $exit_status. Checking status..."
            sleep 1 
            systemctl --user status "$SERVICE_NAME" --no-pager -l >> "$LOG_FILE" 2>&1
        else
            log_message "Command '$ACTION $SERVICE_NAME' issued successfully."
            if [[ "$ACTION" == "start" || "$ACTION" == "restart" ]]; then
                sleep 2 
                if systemctl --user is-active "$SERVICE_NAME" >/dev/null 2>&1; then
                    log_message "$SERVICE_NAME is ACTIVE after $ACTION."
                else
                    log_message "WARNING: $SERVICE_NAME is NOT active after $ACTION. Check 'systemctl --user status $SERVICE_NAME' and journalctl."
                fi
            elif [[ "$ACTION" == "stop" ]]; then
                sleep 1
                 if systemctl --user is-active "$SERVICE_NAME" >/dev/null 2>&1; then
                    log_message "WARNING: $SERVICE_NAME is STILL ACTIVE after stop command."
                else
                    log_message "$SERVICE_NAME is INACTIVE after stop."
                fi
            fi
        fi
    fi
    
    if [[ "$ACTION" != "status" ]]; then
      sleep 1 
    fi
done

log_message "Manual action '$ACTION' completed for all listed ez_manage services."

if [[ "$ACTION" != "status" ]]; then
    log_message "--- Summary after action '$ACTION' ---"
    log_message "Active ez_manage services:"
    systemctl --user list-units --type=service --state=running 'binance-ezmanage-*.service' --no-pager >> "$LOG_FILE" 2>&1
    echo 
    
    log_message "Failed ez_manage services:"
    systemctl --user list-units --type=service --state=failed 'binance-ezmanage-*.service' --no-pager >> "$LOG_FILE" 2>&1
    echo
    log_message "-----------------------------------"
fi

echo "Operation complete. Check log: $LOG_FILE"