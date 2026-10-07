#!/bin/bash
cd /Users/niels/Documents/binance
mkdir -p /Users/niels/logs
echo "=== Starting Binance Desktop Background Service ===" >> /Users/niels/logs/desktop_background_startup.log
echo "Checking server connection..." >> /Users/niels/logs/desktop_background_startup.log
if ssh -o ConnectTimeout=5 -o BatchMode=yes s1-int "echo 'Connection successful'" 2>/dev/null; then
    echo "✅ Server connection successful" >> /Users/niels/logs/desktop_background_startup.log
else
    echo "⚠️  Warning: Cannot connect to server. Will use local files." >> /Users/niels/logs/desktop_background_startup.log
fi
echo "🚀 Starting watchdog for desktop backgrounds..." >> /Users/niels/logs/desktop_background_startup.log
pkill -f "watchdog_desktop_backgrounds.sh" 2>/dev/null
pkill -f "generate_working_composites.py --daemon" 2>/dev/null
sleep 2
PYTHON_PATH="/opt/anaconda3/envs/binance_env/bin/python3"
SCRIPT_PATH="/Users/niels/Documents/binance/generate_working_composites.py"
WATCHDOG_PATH="/Users/niels/Documents/binance/watchdog_desktop_backgrounds.sh"
nohup "$WATCHDOG_PATH" >> /Users/niels/logs/desktop_background_watchdog_startup.log 2>&1 &
WATCHDOG_PID=$!
sleep 3
if ps -p $WATCHDOG_PID > /dev/null; then
    echo "✅ Watchdog started (PID: $WATCHDOG_PID)" >> /Users/niels/logs/desktop_background_startup.log
else
    echo "❌ Failed to start watchdog" >> /Users/niels/logs/desktop_background_startup.log
fi
