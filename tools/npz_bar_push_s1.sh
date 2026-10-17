#!/bin/bash
# npz_bar_push_s1.sh — ship the Mac-computed 15m NPZ bars to S1 (USER 2026-10-17).
# The Mac computes the row inside ez_indicators / tradier_indicators (npz_live_generator.export_bar_for_symbol)
# and stages it in data/npz_bar_outbox/. S1's tools/npz_bar_ingest.py appends it, so S1 never recomputes the bar.
# Files are removed locally only after they are on S1 (rsync --remove-source-files), so nothing is lost on failure.
set -u
REPO=/Users/niels/Documents/binance
OUT="$REPO/data/npz_bar_outbox"
LOG=/Users/niels/logs/npz_bar_push_s1.log
DST_DIR="binance-sandbox/data/npz_bar_inbox/"
SSH="ssh -o BatchMode=yes -o ConnectTimeout=10 -o ControlMaster=no -o ControlPath=none"
ts(){ date -u +%FT%TZ; }
[ -d "$OUT" ] || exit 0
N=$(ls -1 "$OUT"/*.json 2>/dev/null | wc -l | tr -d ' ')
[ "$N" = "0" ] && exit 0
for host in s1-int 157.180.125.52; do
  if rsync -az --timeout=30 --remove-source-files -e "$SSH" "$OUT"/*.json "niels@$host:$DST_DIR" >>"$LOG" 2>&1; then
    echo "$(ts) pushed $N bar(s) -> $host" >>"$LOG"
    exit 0
  fi
done
echo "$(ts) push FAILED for $N bar(s) — kept locally for the next run" >>"$LOG"
exit 1
