#!/bin/bash
# Disk cleanup script - can be run locally or on server
# Usage: ./run_disk_cleanup.sh [server|gateway|local]

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ "$1" == "server" ]; then
    echo "Running cleanup on server..."
    ssh s1-int "cd /home/niels/Documents/binance && python3 ez_disk_cleanup.py"
elif [ "$1" == "gateway" ]; then
    echo "Running cleanup on gateway..."
    ssh gateway-internal "cd /home/niels/binance && python3 ez_disk_cleanup.py"
else
    echo "Running cleanup locally..."
    python3 ez_disk_cleanup.py
fi

