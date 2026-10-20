#!/bin/bash
# market_data_snapshot_archive.sh — archive hourly market_data_*.json snapshots older than KEEP_DAYS out of the live data dir.
# 2026-10-20: 1,153 snapshots (13 GB) in S1 ~/binance/data pushed the dir over rsync_helpers.sh's 15 GB remote gate, so the
# Mac->S1 klines push skipped every cycle from 15:00Z and 112 stock symbols outside the catch-up universe went stale
# (JNJ/TECK/TRGP). ez_disk_cleanup.py keeps one snapshot per 15-min bin forever, so it never thins them. Every reader
# (ez_positions_service, ez_manipulation_detector, backtest_v12_engine F1 fallback, scalp_v3_shadow) uses the NEWEST file only.
# Join -> prove -> reclaim: tar.zst into ARCHIVE_DIR, verify member count, only then remove originals. Never deletes without a
# verified archive. Usage: market_data_snapshot_archive.sh [DATA_DIR] [KEEP_DAYS]   (env ARCHIVE_DIR, default ~/archive_market_data)
set -euo pipefail
DATA_DIR="${1:-$HOME/binance/data}"
KEEP_DAYS="${2:-3}"
ARCHIVE_DIR="${ARCHIVE_DIR:-$HOME/archive_market_data}"
MIN_FILES="${MIN_FILES:-24}"
mkdir -p "$ARCHIVE_DIR"
cd "$DATA_DIR"
LIST="$(mktemp /tmp/md_archive_list.XXXXXX)"
trap 'rm -f "$LIST"' EXIT
find . -maxdepth 1 -name "market_data_*.json" -mtime +"$KEEP_DAYS" -printf "%f\n" | sort > "$LIST"
n=$(wc -l < "$LIST")
if [ "$n" -lt "$MIN_FILES" ]; then
    echo "$(date -u +%FT%TZ) nothing to archive ($n files older than ${KEEP_DAYS}d, min $MIN_FILES)"
    exit 0
fi
first=$(head -1 "$LIST" | grep -oE "[0-9]{8}" | head -1)
last=$(tail -1 "$LIST" | grep -oE "[0-9]{8}" | head -1)
out="$ARCHIVE_DIR/market_data_live_${first}_${last}_$(date -u +%Y%m%dT%H%M%SZ).tar.zst"
nice -n 10 tar -cf - -T "$LIST" | zstd -T2 -q -o "$out"
zstd -t -q "$out"
m=$(zstd -dc "$out" | tar -t | wc -l)
if [ "$m" != "$n" ]; then
    echo "$(date -u +%FT%TZ) MISMATCH archive members $m != $n — originals kept, archive left at $out"
    exit 2
fi
xargs -a "$LIST" rm -f
echo "$(date -u +%FT%TZ) archived $n snapshots -> $out ($(du -h "$out" | cut -f1)); remaining $(ls market_data_*.json 2>/dev/null | wc -l); data dir $(du -sh "$DATA_DIR" | cut -f1)"
