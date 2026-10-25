#!/bin/bash
# Five-minute fleet monitor; the scheduler's completion ledger is the source of truth.
cd /Users/niels/Documents/binance || exit 1
exec /usr/bin/python3 tools/fleet_throughput_monitor.py
