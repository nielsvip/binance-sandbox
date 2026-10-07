#!/bin/bash
# quick test: single check with verbose
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="/opt/anaconda3/envs/binance_env/bin/python"
if [ ! -x "$PY" ]; then PY="/usr/bin/python3"; fi
echo "=== V15 warn daemon --once ==="
$PY -u "$ROOT/tools/v15_warn_daemon.py" --once 2>&1 | tail -n 50
echo ""
echo "=== last warnings ==="
cat /tmp/v15_warn.log 2>&1 | tail -n 20
echo ""
echo "=== ai alerts ==="
cat /tmp/v15_ai_alert.jsonl 2>&1 | tail -n 20
