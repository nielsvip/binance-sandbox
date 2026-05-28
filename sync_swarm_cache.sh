#!/bin/bash
# Sync autonomous swarm CSVs from S1 and S2 to local cache for the dashboard.
# Run periodically (e.g. every 10 min via cron).

BASE=/Users/niels/Documents/binance/data/swarm_cache

rsync -az --timeout=30 \
  "s1-int:/home/niels/binance-sandbox/data/autonomous/" "$BASE/s1/data/autonomous/" \
  --exclude="*.npz" --exclude="*.pkl" --exclude="*.log" 2>/dev/null

rsync -az --timeout=30 \
  "s1-int:/home/niels/binance-sandbox/data/funnel_validated/" "$BASE/s1/data/funnel_validated/" \
  --exclude="*.npz" 2>/dev/null

rsync -az --timeout=30 \
  "s1-int:/home/niels/binance-sandbox/data/stage2_validated/" "$BASE/s1/data/stage2_validated/" 2>/dev/null

# 2026-05-28 S2 DEAD permanently — all s2-int pulls removed (autonomous/funnel_validated/stage2_validated)

echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') sync done"
