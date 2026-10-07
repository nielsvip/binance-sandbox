#!/bin/bash
# Reboot gateway server - multiple methods to ensure it works
GATEWAY_HOST="gateway-internal"

echo "Attempting to reboot gateway server..."
ssh $GATEWAY_HOST << 'EOF'
# Method 1: systemctl reboot (preferred)
echo "Method 1: Using systemctl reboot..."
sudo /usr/bin/systemctl reboot 2>&1 || {
    echo "Method 1 failed, trying Method 2..."
    # Method 2: Direct reboot command
    sudo /usr/sbin/reboot 2>&1 || {
        echo "Method 2 failed, trying Method 3..."
        # Method 3: shutdown -r now
        sudo /usr/sbin/shutdown -r now || {
            echo "All methods failed. Please reboot via Hetzner console."
            exit 1
        }
    }
}
EOF

echo "Reboot command sent. Server should reboot shortly."

