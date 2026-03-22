#!/bin/bash
# Start ALL SSH tunnels with auto-reconnect at Mac boot/login
# Includes: Redis tunnels (gateway + server) + VNC tunnel for IBKR
# Now with resilient monitoring and auto-reconnect

cd "$(dirname "$0")"

echo "🚀 Starting Resilient SSH Tunnel Manager..."
echo "=========================================="

# Check if Python script exists
if [ ! -f "setup_ssh_tunnels.py" ]; then
    echo "❌ setup_ssh_tunnels.py not found!"
    exit 1
fi

# Make sure Python script is executable
chmod +x setup_ssh_tunnels.py

echo "📡 Starting tunnel manager with auto-reconnect..."
echo "   This will run continuously and monitor tunnel health"
echo "   Press Ctrl+C to stop"
echo ""

# Run the resilient tunnel manager
python3 setup_ssh_tunnels.py

echo ""
echo "🛑 Tunnel manager stopped"