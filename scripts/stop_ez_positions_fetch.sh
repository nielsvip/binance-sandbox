#!/bin/bash

# EZ POSITIONS FETCH - Stop Real Position Fetching Service

echo "🛑 Stopping EZ Positions Fetch Service..."

# Check if PID file exists
if [ -f "pids/ez_positions_fetch.pid" ]; then
    PID=$(cat pids/ez_positions_fetch.pid)
    
    if ps -p $PID > /dev/null 2>&1; then
        echo "🔄 Stopping process $PID..."
        kill $PID
        
        # Wait for graceful shutdown
        sleep 2
        
        # Force kill if still running
        if ps -p $PID > /dev/null 2>&1; then
            echo "⚡ Force stopping process $PID..."
            kill -9 $PID
        fi
        
        echo "✅ EZ Positions Fetch stopped"
    else
        echo "❌ Process $PID not found"
    fi
    
    # Remove PID file
    rm -f pids/ez_positions_fetch.pid
else
    echo "❌ PID file not found"
fi

# Kill any remaining processes
pkill -f "ez_positions_fetch.py" 2>/dev/null

echo "🧹 Cleanup complete"
