#!/bin/bash
# Refresh 101.xlsx from ALL sources — run anytime
cd /Users/niels/Documents/binance
echo "Pulling results from servers..."
scp -o ConnectTimeout=10 s2-int:/home/niels/tradier_48h_sweep/results.csv data/s2_tradier.csv 2>/dev/null
scp -o ConnectTimeout=10 s1-int:/home/niels/tradier_48h_sweep/results.csv data/s1_tradier.csv 2>/dev/null
echo "Building 101.xlsx..."
/opt/anaconda3/envs/binance_env/bin/python3 sweep_coordinator.py --xls 2>/dev/null || echo "Using v7_orchestrator results..."
/opt/anaconda3/envs/binance_env/bin/python3 v7_orchestrator.py --results 2>/dev/null
echo "101.xlsx updated at $(date)"
open 101.xlsx 2>/dev/null
