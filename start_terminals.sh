#!/bin/bash
# Start Binance Terminals Script
# Opens local, gateway, and S1 Terminal windows.
#
# Keep the SSH commands self-contained.  The MacBook cannot route directly to
# S1's private 10.0.0.3 address; use the public bridge first, then localhost's
# forwarded port when that bridge is available.
echo "Opening Binance terminals..."
osascript <<EOF
tell application "Terminal"
    activate
    try
        do script "cd /Users/niels/Documents/binance && source /opt/anaconda3/etc/profile.d/conda.sh && conda activate binance_env"
        set custom title of front window to "Local Binance"
    on error
        display notification "Failed to open Local Binance" with title "Binance Terminals"
    end try
    delay 0.5
    try
        do script "ssh gateway-internal -t \"source /home/niels/miniforge3/etc/profile.d/conda.sh && conda activate binance_env && cd binance && exec bash -i\""
        set custom title of front window to "Gateway 157.90.168.35"
    on error
        display notification "Failed to open Gateway SSH" with title "Binance Terminals"
    end try
    delay 0.5
    try
        -- The private 10.0.0.3 route is gateway-only.  Prefer the working
        -- public bridge; fall back to the local forwarded SSH port.
        do script "if ! ssh -i /Users/niels/.ssh/id_ed25519 -o IdentitiesOnly=yes -o ConnectTimeout=8 -o ConnectionAttempts=1 niels@157.180.125.52 -tt \"cd /home/niels && exec bash -i\"; then exec ssh -p 2201 -o ConnectTimeout=8 -o ConnectionAttempts=1 localhost -tt \"cd /home/niels && exec bash -i\"; fi"
        set custom title of front window to "S1 /home/niels"
    on error
        display notification "Failed to open S1 SSH" with title "Binance Terminals"
    end try
end tell
EOF
echo "Terminal setup complete!"
echo "Terminal status:"
echo "1. Local Binance (with conda env activated)"
echo "2. Gateway 157.90.168.35 (with binance dir and conda env)"
echo "3. S1 /home/niels (bridge with public-IP fallback)"
