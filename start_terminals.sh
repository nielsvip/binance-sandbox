#!/bin/bash

# Start Binance Terminals Script
# Opens 3 Terminal windows: Local, Server 1, Server 2

echo "Opening Binance terminals..."

osascript <<EOF
tell application "Terminal"
    activate
    
    -- Terminal 1: Local binance directory with conda env
    try
        do script "cd /Users/niels/Documents/binance && source /opt/anaconda3/etc/profile.d/conda.sh && conda activate binance_env"
        set custom title of front window to "Local Binance"
    on error
        display notification "Failed to open Local Binance" with title "Binance Terminals"
    end try
    delay 0.5
    
    -- Terminal 2: SSH to gateway server with binance setup (force conda activation)
    try
        do script "ssh gateway-internal -t \"source /home/niels/miniforge3/etc/profile.d/conda.sh && conda activate binance_env && cd binance && exec bash -i\""
        set custom title of front window to "Gateway 157.90.168.35"
    on error
        display notification "Failed to open Gateway SSH" with title "Binance Terminals"
    end try
    delay 0.5
    
    -- Terminal 3: SSH to second server with binance setup (force conda activation)
    try
        do script "ssh s1-int -t \"source /home/niels/miniforge3/etc/profile.d/conda.sh && conda activate binance_env && cd binance && exec bash -i\""
        set custom title of front window to "Server 157.180.125.52"
    on error
        display notification "Failed to open Server SSH" with title "Binance Terminals"
    end try
    
end tell
EOF

echo "Terminal setup complete!"
echo "Check that all 3 terminals opened successfully:"
echo "1. Local Binance (with conda env activated)"
echo "2. Gateway 157.90.168.35 (with binance dir and conda env)"
echo "3. Server 157.180.125.52 (with binance dir and conda env)"
