#!/bin/bash
# sync_mac_to_s1_30min.sh — RETIRED 2026-10-07 (refuser below). Mac -> S1 template push ended 2026-10-06.
echo "REFUSED: Mac->S1 template push retired 2026-10-07 (USER: S1 daily chain is the template writer). Templates flow S1->Mac ONLY via tools/v15_daily_chain_mac_apply.sh. Never re-enable." >&2
exit 1
# Pushes TEMPLATE_CRYPTO_LONG.xlsx (and all TEMPLATE_*.xlsx) from Mac to s1 (10.0.0.3 via gateway, s1-pub 157.180.125.52)
# Request: "now reverse: Mac->S1 every 30min via scp"
set -e
BASE="/Users/niels/Documents/binance"
cd "$BASE"
LOG="/tmp/sync_mac_to_s1_30min.log"
TEMPLATES=(
    "SPREADSHEETS/TEMPLATE_CRYPTO_LONG.xlsx"
    "SPREADSHEETS/TEMPLATE_CRYPTO_SHORT.xlsx"
    "SPREADSHEETS/TEMPLATE_STOCKS_LONG.xlsx"
    "SPREADSHEETS/TEMPLATE_STOCKS_SHORT.xlsx"
    "SPREADSHEETS/TEMPLATE.xlsx"
)
echo "[$(date -u +%FT%TZ)] sync Mac->S1 30min scp start" >>"$LOG"
for f in "${TEMPLATES[@]}"; do
    [ -f "$f" ] || continue
    # via s1-int (127.0.0.1:2201 gateway bridge) — primary
    scp -o ConnectTimeout=5 -o BatchMode=yes -o StrictHostKeyChecking=no "$f" s1-int:~/binance-sandbox/SPREADSHEETS/$(basename $f) >>"$LOG" 2>&1 || echo "scp s1-int $f failed" >>"$LOG"
    # via s1-pub (157.180.125.52) — fallback/dedup
    scp -o ConnectTimeout=8 -o BatchMode=yes -o StrictHostKeyChecking=no "$f" s1-pub:~/binance-sandbox/SPREADSHEETS/$(basename $f) >>"$LOG" 2>&1 || true
    ssh -o ConnectTimeout=5 -o BatchMode=yes s1-int "chmod 444 ~/binance-sandbox/SPREADSHEETS/$(basename $f) 2>&1 | head -n 1" >>"$LOG" 2>&1 || true
done
# Also push queue files if updated
for q in SPREADSHEETS/V15_SERVER_QUEUE_S1.txt SPREADSHEETS/CRYPTO_LONG_MEGA_QUEUE_S1_62.txt; do
    [ -f "$q" ] || continue
    scp -o ConnectTimeout=5 -o BatchMode=yes "$q" s1-int:~/binance-sandbox/SPREADSHEETS/ >>"$LOG" 2>&1 || true
done
echo "[$(date -u +%FT%TZ)] sync Mac->S1 done" >>"$LOG"
tail -n 30 "$LOG" 2>&1 | tail -n 20
