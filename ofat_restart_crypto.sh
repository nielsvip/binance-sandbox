#!/bin/bash
# Clean restart of the CRYPTO OFAT runner to apply the 2024-01-01 window + new parallelism.
# Patterns live in this file, so this script's own cmdline ("bash ofat_restart_crypto.sh")
# cannot self-match pgrep -f. Tradier is left untouched.
for pid in $(pgrep -f "engine_ofat_screen.py --mode crypto"); do kill -9 "$pid" 2>/dev/null; done
for pid in $(pgrep -f "backtest_v8_engine.py --mode crypto --account ang"); do kill -9 "$pid" 2>/dev/null; done
sleep 3
# CSV + trades-root are header-only / in-flight (no committed cells) -> safe to reset so the
# new baseline isn't poisoned by skipped old-window jsonl.
rm -rf /home/niels/logs/ofat_crypto_trades
rm -f /home/niels/logs/ofat_crypto_screen.csv
echo "crypto reset done: runners_left=$(pgrep -fc 'engine_ofat_screen.py --mode crypto' 2>/dev/null) engines_left=$(pgrep -fc 'backtest_v8_engine.py --mode crypto' 2>/dev/null)"
