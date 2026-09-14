#!/bin/bash
set -e
echo "=== Smart Muse: S1 → Cloud → Mac (bundled, crash-safe) ==="
# Mac memory check
FREE=$(vm_stat | awk '/Pages free/ {print $3}' | tr -d '.')
FREE_MB=$((FREE * 16384 / 1024 / 1024))
echo "Mac free: ${FREE_MB}MB | S1 via tunnel 11437 | Router :11436"

# 1. Ensure Mac Ollama running but not forced to carry load
open -a Ollama > /dev/null 2>&1 || true
sleep 1

# 2. Start local proxy 11435 if needed (for Mac path)
if curl -s --max-time 2 http://localhost:11435/v1/models > /dev/null 2>&1; then
  echo "✓ Local proxy 11435 running"
else
  echo "→ Starting local proxy 11435..."
  /Users/niels/binance/.venv/bin/python /Users/niels/binance/muse_local_proxy.py --port 11435 > /tmp/muse_proxy.log 2>&1 &
  sleep 2
  curl -s http://localhost:11435/v1/models > /dev/null && echo "✓ Local proxy OK" || echo "✗ Local proxy failed"
fi

# 3. Start S1 tunnel 11437 if needed
if curl -s --max-time 3 http://localhost:11437/v1/models > /dev/null 2>&1; then
  echo "✓ S1 tunnel 11437 running"
else
  echo "→ Starting S1 tunnel 11437:localhost:11434..."
  ssh -fNT -L 11437:localhost:11434 s1-pub -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=6 2>&1 | head -5 || true
  sleep 2
  curl -s http://localhost:11437/v1/models > /dev/null && echo "✓ S1 tunnel OK (S1 models available)" || echo "⚠️  S1 tunnel failed — S1 path will be skipped, fallback to Mac/Cloud"
fi

# 4. Start smart router 11436 if needed
if curl -s --max-time 2 http://localhost:11436/v1/models > /dev/null 2>&1; then
  echo "✓ Smart router 11436 running (S1 → Cloud → Mac)"
else
  echo "→ Starting smart router 11436..."
  S1_OLLAMA=http://localhost:11437 /Users/niels/binance/.venv/bin/python /Users/niels/binance/muse_smart_proxy.py --port 11436 > /tmp/smart_proxy.log 2>&1 &
  sleep 2
  curl -s http://localhost:11436/v1/models > /dev/null && echo "✓ Smart router OK" || echo "✗ Smart router failed"
fi

echo ""
echo "Ready — bundling active:"
echo "  muse --base-url http://localhost:11436/v1 --model muse-glimmer:latest \"prompt\"  # S1 first, Cloud before Mac crash"
echo "  muse-smart  # alias (see ~/binance/muse_alias.sh)"
echo "Conversations stay alive: router is stateless, Muse keeps history; long prompts auto-route to cloud."
