#!/bin/bash
# Restart BOTH OFAT runners to pick up the regenerated manifest + long/short-first priority.
# Patterns live in this file so "bash ofat_restart_all.sh" cannot self-match pgrep.
# CSVs are KEPT (done cells stay done; only the remaining-work ORDER changes).
for pid in $(pgrep -f "engine_ofat_screen.py --mode"); do kill -9 "$pid" 2>/dev/null; done
for pid in $(pgrep -f "backtest_v8_engine.py --mode"); do kill -9 "$pid" 2>/dev/null; done
sleep 3
echo "killed: runners_left=$(pgrep -fc 'engine_ofat_screen.py --mode' 2>/dev/null) engines_left=$(pgrep -fc 'backtest_v8_engine.py --mode' 2>/dev/null)"
