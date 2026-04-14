#!/bin/bash
# Install sweep_daemon as a systemd service on a server.
#
# Usage:
#   ssh s1-int  # (server1)
#   cd /home/niels/binance
#   bash sweep_daemon_install.sh server1 tradier 6
#
# Args: $1=machine_name  $2=mode(tradier|crypto)  $3=workers(default 6)

set -euo pipefail

MACHINE="${1:-server1}"
MODE="${2:-tradier}"
WORKERS="${3:-6}"
START="${4:-2024-06-01}"
SYMBOLS="${5:-fast}"
BATCH="${6:-20}"

# Detect python
if [ -f /home/niels/.conda/envs/binance_env/bin/python3 ]; then
    PYTHON="/home/niels/.conda/envs/binance_env/bin/python3"
elif [ -f /home/niels/miniconda3/envs/binance_env/bin/python ]; then
    PYTHON="/home/niels/miniconda3/envs/binance_env/bin/python"
else
    echo "ERROR: Cannot find python in conda envs"
    exit 1
fi

echo "=== Sweep Daemon Install ==="
echo "  Machine: $MACHINE"
echo "  Mode: $MODE"
echo "  Workers: $WORKERS"
echo "  Python: $PYTHON"
echo ""

# Create logs dir
mkdir -p /home/niels/logs

# Write environment file
sudo tee /etc/default/sweep_daemon > /dev/null <<EOF
SWEEP_PYTHON=$PYTHON
SWEEP_MACHINE=$MACHINE
SWEEP_MODE=$MODE
SWEEP_WORKERS=$WORKERS
SWEEP_START=$START
SWEEP_SYMBOLS=$SYMBOLS
SWEEP_BATCH=$BATCH
EOF

echo "Written /etc/default/sweep_daemon"

# Copy service file
sudo cp /home/niels/binance/sweep_daemon.service /etc/systemd/system/sweep_daemon.service
sudo systemctl daemon-reload
sudo systemctl enable sweep_daemon
sudo systemctl restart sweep_daemon

echo ""
echo "=== Done ==="
echo "  Status: sudo systemctl status sweep_daemon"
echo "  Logs:   tail -f /home/niels/logs/sweep_daemon.log"
echo "  Stop:   sudo systemctl stop sweep_daemon"
echo "  Config: /etc/default/sweep_daemon (edit + restart)"
echo ""

# Show status
sleep 2
sudo systemctl status sweep_daemon --no-pager || true
