#!/bin/bash
echo "Stopping smart bundling to free RAM..."
# Keep S1 running (server), only free Mac
curl -s http://localhost:11434/api/generate -d '{"model":"muse-glimmer:latest","keep_alive":0}' > /dev/null 2>&1 || true
ollama stop muse-glimmer:latest 2>&1 | head -3 || true
pkill -f muse_smart_proxy.py 2>&1 | head -3 || true
# keep tunnel but low priority — optional kill:
# pkill -f "ssh.*11437" || true
echo "Mac freed (S1 + Cloud remain). Check: ollama ps; vm_stat | head -3"
