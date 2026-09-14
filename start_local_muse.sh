#!/bin/bash
set -e
echo "=== Safe Local Muse Start ==="
# Check memory
FREE=$(vm_stat | awk '/Pages free/ {print $3}' | tr -d '.')
FREE_MB=$((FREE * 16384 / 1024 / 1024))
echo "Free pages: $FREE (~${FREE_MB}MB)"
if [ "$FREE_MB" -lt 500 ]; then
  echo "⚠️  Low memory! Unloading Ollama models..."
  ollama ps 2>&1 | head -10
  curl -s http://localhost:11434/api/ps 2>&1 | python3 -c "import json,sys; print(sys.stdin.read()[:200])" 2>&1 | head -5
  pkill -f llama-server 2>&1 | head -3 || true
  sleep 2
fi
# Ensure Ollama running but not overloaded
open -a Ollama 2>&1 | head -3 || true
sleep 1
# Start proxy if not running
if curl -s http://localhost:11435/v1/models > /dev/null 2>&1; then
  echo "Proxy already running on 11435"
else
  echo "Starting proxy..."
  nohup /Users/niels/binance/.venv/bin/python /Users/niels/Documents/binance/muse_local_proxy.py --port 11435 > /tmp/muse_proxy.log 2>&1 &
  sleep 1
  curl -s http://localhost:11435/v1/models > /dev/null && echo "Proxy OK" || echo "Proxy FAILED - check /tmp/muse_proxy.log"
fi
echo "Ready. Use: muse exec --base-url http://localhost:11435/v1 --model muse-glimmer:latest \"prompt\""
echo "To save RAM, use smaller model: muse exec --base-url http://localhost:11435/v1 --model qwen2.5-coder:7b \"prompt\""
echo "To unload: ollama stop muse-glimmer:latest  OR  curl -s http://localhost:11434/api/generate -d '{\"model\":\"muse-glimmer:latest\",\"keep_alive\":0}'"
