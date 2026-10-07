#!/bin/bash
# Switch from root gateway-broadcaster service to user gateway_data_broadcaster service
# This avoids permission issues when reading mark prices and klines

GATEWAY_IP="157.90.168.35"

echo "🔄 Switching gateway broadcaster from root service to user service..."
echo ""

ssh niels@${GATEWAY_IP} bash << 'ENDSSH'
echo "1️⃣ Stopping root gateway-broadcaster service..."
sudo systemctl stop gateway-broadcaster.service
sudo systemctl disable gateway-broadcaster.service

echo ""
echo "2️⃣ Waiting for process to fully stop..."
sleep 3

echo ""
echo "3️⃣ Starting user gateway_data_broadcaster service..."
systemctl --user daemon-reload
systemctl --user enable gateway_data_broadcaster.service
systemctl --user start gateway_data_broadcaster.service

echo ""
echo "4️⃣ Waiting for service to start..."
sleep 5

echo ""
echo "5️⃣ Checking service status..."
systemctl --user status gateway_data_broadcaster.service --no-pager -l || true

echo ""
echo "6️⃣ Checking running processes..."
ps aux | grep gateway_data_broadcaster | grep -v grep || echo "No gateway_data_broadcaster running"

echo ""
echo "7️⃣ Checking logs..."
tail -20 /home/niels/gateway_broadcaster/logs/gateway_error.log 2>/dev/null || echo "No error log yet"
ENDSSH

echo ""
echo "✅ Service switch complete!"
echo ""
echo "Monitor with:"
echo "  ssh niels@${GATEWAY_IP} 'journalctl --user -u gateway_data_broadcaster.service -f'"
echo "  ssh niels@${GATEWAY_IP} 'tail -f /home/niels/gateway_broadcaster/logs/gateway_broadcaster.log'"
