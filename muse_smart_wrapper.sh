#!/bin/bash
# Simple fallback wrapper — no daemon needed
# Usage: ./muse_smart_wrapper.sh "your prompt"
PROMPT="$*"
if [ -z "$PROMPT" ]; then echo "Usage: $0 \"prompt\""; exit 1; fi
echo "→ Trying local Mac (11435)..."
if timeout 90 muse exec --base-url http://localhost:11435/v1 --model muse-glimmer:latest "$PROMPT" 2>&1; then
  exit 0
fi
echo "→ Local failed or timed out, falling back to S1..."
if ssh -o ConnectTimeout=5 s1-pub "muse exec --base-url http://localhost:11435/v1 --model muse-glimmer:latest \"$PROMPT\" " 2>&1; then
  exit 0
fi
echo "→ S1 also failed, falling back to Cloud..."
muse exec "$PROMPT"
