#!/bin/bash
# Deploy COORDINATOR to Server 1 after reboot — runs sweep + monitors S2
set -e
echo "Deploying coordinator to Server 1 (157.180.125.52)..."
scp /Users/niels/Documents/binance/sweep_coordinator.py s1-int:/home/niels/binance/
scp /Users/niels/Documents/binance/101.csv s1-int:/home/niels/binance/
scp /Users/niels/Documents/binance/SERVER_LOCKS.md s1-int:/home/niels/binance/

echo "Starting COORDINATOR in screen (runs worker + monitors S2)..."
ssh s1-int 'mkdir -p /home/niels/binance/sweep_logs && screen -dmS sweep48h /home/niels/.conda/envs/binance_env/bin/python3 /home/niels/binance/sweep_coordinator.py && echo "LAUNCHED" && screen -ls'

echo "Verifying..."
sleep 15
ssh s1-int 'cat /home/niels/SWEEP_RUNNING 2>/dev/null; echo "==="; ps aux | grep sweep_coordinator | grep -v grep'
echo "Server 1 = COORDINATOR + WORKER. Monitors S2, syncs 101.csv every 5 min."
