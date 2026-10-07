#!/bin/bash
# DESTROY if repeat already done — run on s1/s2/s3/s5 before any launch
# If a server tries to run a sym_side already done final, it gets destroyed (herd killed, queue deleted, server flagged)
ROOT="$HOME/binance-sandbox"
ALREADY="$ROOT/data/reports/lifecycle_pilot"
QUEUE_DIR="$ROOT/SPREADSHEETS"
HOST=$(hostname)
for q in "$QUEUE_DIR"/V15_SERVER_QUEUE_S*.txt; do
  srv=$(basename "$q" .txt | sed 's/V15_SERVER_QUEUE_S/S/')
  while read -r sym; do
    [ -z "$sym" ] && continue
    # Check if already done final
    if ls "$ALREADY/${sym}_v14_progress.json" >/dev/null 2>&1; then
      if grep -q '"final_gain"' "$ALREADY/${sym}_v14_progress.json" 2>/dev/null; then
        # Check if this queue contains a done sym — violation
        echo "[DESTROY] $HOST queue $srv contains already done $sym — DESTROYING $srv"
        pkill -f "v15_local_herd" || true
        pkill -f "v15_pilot.*$sym" || true
        mv "$q" "$q.DESTROYED_$(date -u +%FT%TZ)" 2>/dev/null || true
        # Flag server destroyed
        touch "$QUEUE_DIR/.DESTROYED_S${srv}_$(date -u +%FT%TZ)"
        echo "DESTROYED $srv for repeating $sym" | wall 2>/dev/null || true
      fi
    fi
  done < "$q"
done
# Also check running pilots vs already done
for pid in $(pgrep -f "v15_pilot.*--sym-side" 2>/dev/null); do
  cmd=$(ps -o args= -p "$pid" 2>/dev/null | grep -o "BTCUSDC[^ ]*\|[A-Z]*_LONG\|[A-Z]*_SHORT" | head -n1)
  if [ -n "$cmd" ]; then
    sym=$(echo "$cmd" | grep -o "[A-Z0-9]*_\(LONG\|SHORT\)" | head -n1)
    if [ -n "$sym" ] && grep -q '"final_gain"' "$ALREADY/${sym}_v14_progress.json" 2>/dev/null; then
      echo "[DESTROY] running $sym already done — killing $pid and destroying host $HOST"
      kill -9 "$pid" 2>/dev/null || true
      pkill -f "v15_local_herd" || true
      touch "$QUEUE_DIR/.DESTROYED_${HOST}_REPEAT_${sym}_$(date -u +%FT%TZ)"
    fi
  fi
done
echo "[guard] no repeat done syms found — OK $HOST"
