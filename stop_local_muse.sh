#!/bin/bash
echo "Stopping local Muse to free 16GB..."
curl -s http://localhost:11434/api/generate -d '{"model":"muse-glimmer:latest","keep_alive":0}' > /dev/null 2>&1 || true
ollama stop muse-glimmer:latest 2>&1 | head -5 || true
pkill -f muse_local_proxy.py 2>&1 | head -5 || true
launchctl unload ~/Library/LaunchAgents/com.muse.local-proxy.plist 2>&1 | head -5 || true
echo "Freed. Check: ollama ps ; vm_stat | head -5"
