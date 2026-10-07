#!/bin/bash
# start_everything_linux.sh - Start or restart all Binance bot systemd user services

# Ensure we operate as the correct user for systemctl --user
if [ -z "$USER" ]; then
    USER=$(whoami)
    if [ -z "$USER" ]; then
        echo "ERROR: Could not determine current user."
        exit 1
    fi
fi
export XDG_RUNTIME_DIR="/run/user/$(id -u $USER)" # Often needed for systemctl --user in scripts

# List of your service basenames (without .service)
# Add ALL your script basenames here
SERVICE_BASENAMES=(
    "binance_ezmarkprices"
    "binance-ezprices_ws"
    "ginance_ezprices"
    "binance-ezcrosses"
    "binance-ezindicators"
    "binance-ezrankings"
    # "binance-ezlaggers"  # Added from your start_everything
    # "binance-ezbackup"   # Added from your start_everything
    "binance-ezmanage-ang"
    "binance-ezmanage-inf"
    "binance-ezmanage-men"
    "binance-ezmanage-flz"
)

ACTION="$1" # Expect "start" or "restart" or "status"

if [[ "$ACTION" != "start" && "$ACTION" != "restart" && "$ACTION" != "status" ]]; then
    echo "Usage: $0 <start|restart|status>"
    exit 1
fi

echo "Performing '$ACTION' on all Binance bot services..."

for service_base in "${SERVICE_BASENAMES[@]}"; do
    SERVICE_NAME="${service_base}.service"
    echo "Processing: $SERVICE_NAME"
    
    if [[ "$ACTION" == "status" ]]; then
        systemctl --user status "$SERVICE_NAME" --no-pager -l
    else
        systemctl --user "$ACTION" "$SERVICE_NAME"
        if [ $? -ne 0 ]; then
            echo "WARNING: Command '$ACTION $SERVICE_NAME' failed. Checking status..."
            sleep 1 # Give a moment for systemd to update
            systemctl --user status "$SERVICE_NAME" --no-pager -l
        else
            echo "Command '$ACTION $SERVICE_NAME' issued successfully."
            if [[ "$ACTION" == "start" || "$ACTION" == "restart" ]]; then
                sleep 2 # Brief pause for service to attempt start
                systemctl --user status "$SERVICE_NAME" --no-pager -l | grep "Active:"
            fi
        fi
    fi
    
    if [[ "$ACTION" != "status" ]]; then
      sleep 2 # Small delay between actions on different services
    fi
done

echo "Action '$ACTION' completed for all listed services."
echo "Summary of active services:"
systemctl --user list-units --type=service --state=running 'binance-*.service'
echo "Summary of failed services:"
systemctl --user list-units --type=service --state=failed 'binance-*.service'
