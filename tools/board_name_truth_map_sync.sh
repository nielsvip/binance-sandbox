#!/bin/bash
# board_name_truth_map_sync.sh — exchange board_name_truth_map.json between Mac, S1, S2 so every host's
# tools/board_name_truth_guard.py knows old->true names whose chart page lives only on another host.
# Mac is the hub (it reaches both). Only the small map files move; no board file is ever copied.
set -u
R=/Users/niels/Documents/binance/data/reports
P=$R/board_name_truth_peers
SSHO="ssh -o BatchMode=yes -o ConnectTimeout=15"
mkdir -p "$P"
rsync -az -e "$SSHO" s1-pub:binance-sandbox/data/reports/board_name_truth_map.json "$P/s1.json" 2>/dev/null
rsync -az -e "$SSHO" s2:binance-sandbox/data/reports/board_name_truth_map.json "$P/s2.json" 2>/dev/null
for H in s1-pub s2; do
  $SSHO $H "mkdir -p ~/binance-sandbox/data/reports/board_name_truth_peers" 2>/dev/null
  rsync -az -e "$SSHO" "$R/board_name_truth_map.json" $H:binance-sandbox/data/reports/board_name_truth_peers/mac.json 2>/dev/null
done
rsync -az -e "$SSHO" "$P/s2.json" s1-pub:binance-sandbox/data/reports/board_name_truth_peers/s2.json 2>/dev/null
rsync -az -e "$SSHO" "$P/s1.json" s2:binance-sandbox/data/reports/board_name_truth_peers/s1.json 2>/dev/null
echo "$(date -u +%FT%TZ) map sync done: $(ls "$P" | tr '\n' ' ')"
