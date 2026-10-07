#!/bin/bash
# Run all 5 realtime position updaters simultaneously

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Log directory
mkdir -p logs

# Start each account in background using individual scripts
echo "Starting realtime updater for: men"
nohup python3 ez_positions_realtime_men.py > "logs/realtime_men.out" 2>&1 &
echo "Started PID: $!"
sleep 1

echo "Starting realtime updater for: fin"
nohup python3 ez_positions_realtime_fin.py > "logs/realtime_fin.out" 2>&1 &
echo "Started PID: $!"
sleep 1

echo "Starting realtime updater for: inf"
nohup python3 ez_positions_realtime_inf.py > "logs/realtime_inf.out" 2>&1 &
echo "Started PID: $!"
sleep 1

echo "Starting realtime updater for: ang"
nohup python3 ez_positions_realtime_ang.py > "logs/realtime_ang.out" 2>&1 &
echo "Started PID: $!"
sleep 1

echo "Starting realtime updater for: flz"
nohup python3 ez_positions_realtime_flz.py > "logs/realtime_flz.out" 2>&1 &
echo "Started PID: $!"

echo ""
echo "All 5 realtime updaters started"
echo "Check logs/realtime_*.out for output"
echo "Check logs/ez_positions_realtime.log for detailed logs"
echo "To stop all: pkill -f ez_positions_realtime"

