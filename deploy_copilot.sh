#!/bin/bash
# Deploy ez_copilot.py to server and start the service
# Scheduled for Sunday 2026-03-29

set -e
SERVER="s1-int"
REMOTE_DIR="/home/niels/binance"
LOCAL_DIR="/Users/niels/Documents/binance"
LOG="/Users/niels/logs/copilot_deploy.log"

echo "$(date) — Starting copilot deployment" | tee -a "$LOG"

# 1. Sync copilot files to server
echo "Syncing files..." | tee -a "$LOG"
rsync -av "$LOCAL_DIR/ez_copilot.py" "$SERVER:$REMOTE_DIR/" 2>&1 | tee -a "$LOG"
rsync -av "$LOCAL_DIR/scripts/binance-copilot.service" "$SERVER:$REMOTE_DIR/scripts/" 2>&1 | tee -a "$LOG"

# 2. Install systemd service
echo "Installing systemd service..." | tee -a "$LOG"
ssh "$SERVER" "sudo cp $REMOTE_DIR/scripts/binance-copilot.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable binance-copilot" 2>&1 | tee -a "$LOG"

# 3. Start the service
echo "Starting copilot service..." | tee -a "$LOG"
ssh "$SERVER" "sudo systemctl start binance-copilot" 2>&1 | tee -a "$LOG"

# 4. Wait and check status
sleep 5
echo "Checking status..." | tee -a "$LOG"
ssh "$SERVER" "sudo systemctl status binance-copilot --no-pager" 2>&1 | tee -a "$LOG"

# 5. Check logs
echo "Recent logs:" | tee -a "$LOG"
ssh "$SERVER" "tail -20 /home/niels/logs/ez_copilot.log 2>/dev/null || echo 'No logs yet'" 2>&1 | tee -a "$LOG"

echo "$(date) — Deployment complete" | tee -a "$LOG"
