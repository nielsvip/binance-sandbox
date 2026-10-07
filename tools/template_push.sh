#!/bin/bash
# template_push.sh — Mac is SOURCE OF TRUTH for TEMPLATE files, push to ALL servers every minute
# Fixes waste: S1/servers were overwriting Mac TEMPLATE.xlsx via pulls, losing days of work
# This script is run via crontab * * * * * — pushes the 5 canonical templates Mac->all servers
# Pull scripts (sync_s1_to_mac.sh, mac_pull_sheets_charts.sh, monitor_mega_sweep.sh) now
# use --exclude='*TEMPLATE*' so server copies NEVER flow back to Mac
set -e
BASE="/Users/niels/Documents/binance"
cd "$BASE"
LOG="/tmp/template_push.log"
TEMPLATES=(
    "SPREADSHEETS/TEMPLATE_STOCKS_LONG.xlsx"
    "SPREADSHEETS/TEMPLATE_STOCKS_SHORT.xlsx"
    "SPREADSHEETS/TEMPLATE_CRYPTO_LONG.xlsx"
    "SPREADSHEETS/TEMPLATE_CRYPTO_SHORT.xlsx"
)
# Canonical aliases (via ~/.ssh/config ControlMaster) — covers S1 via s1-int (127.0.0.1:2201 tunnel)
# plus S2/S3/S5/mega/fresh. Raw IPs are fallback deduped — s1-int already is 157.180.125.52, s2-mega is 62.238.125.113
TARGETS=(
    "s1-int:/home/niels/binance-sandbox/SPREADSHEETS/"
    "s1-pub:/home/niels/binance-sandbox/SPREADSHEETS/"
    # "s2:/home/niels/binance-sandbox/SPREADSHEETS/"  # USER 2026-09-30: s2/s5 being deleted — off-limits
    # "s2-pub:/home/niels/binance-sandbox/SPREADSHEETS/"  # USER 2026-09-30: s2/s5 being deleted — off-limits
    # "s3-pub:/home/niels/binance-sandbox/SPREADSHEETS/"  # USER 2026-09-30: s2/s5 being deleted — off-limits
    # "s5-pub:/home/niels/binance-sandbox/SPREADSHEETS/"  # USER 2026-09-30: s2/s5 being deleted — off-limits
    # "s2-mega:/home/niels/binance-sandbox/SPREADSHEETS/"  # USER 2026-09-30: only s1 remains
    # "s2-fresh:/home/niels/binance-sandbox/SPREADSHEETS/"  # USER 2026-09-30: only s1 remains
)
if [ ! -f "${TEMPLATES[0]}" ]; then
    echo "$(date -u +%FT%TZ) SKIP no ${TEMPLATES[0]} on Mac" >>"$LOG"
    exit 0
fi
for f in "${TEMPLATES[@]}"; do
    [ -f "$f" ] || { echo "$(date -u +%FT%TZ) MISSING $f" >>"$LOG"; continue; }
    for dst in "${TARGETS[@]}"; do
        host="${dst%%:*}"
        rsync -az --timeout=10 -e "ssh -o ConnectTimeout=5 -o BatchMode=yes -o StrictHostKeyChecking=no" "$f" "$dst" >>"$LOG" 2>&1 || true
        # Harden S1 copy as read-only so S1 can never be the writer (Mac is source of truth, never S1->Mac)
        ssh -o ConnectTimeout=5 -o BatchMode=yes -o StrictHostKeyChecking=no "$host" "chmod 444 ~/binance-sandbox/SPREADSHEETS/$(basename $f) 2>&1 | head -n 1" >>"$LOG" 2>&1 || true
    done
done
if [ -f "SPREADSHEETS/.template_defaults_verified.json" ]; then
    for dst in "${TARGETS[@]}"; do
        rsync -az --timeout=10 -e "ssh -o ConnectTimeout=5 -o BatchMode=yes -o StrictHostKeyChecking=no" "SPREADSHEETS/.template_defaults_verified.json" "$dst" >>"$LOG" 2>&1 || true
    done
fi
tail -n 500 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG" 2>/dev/null || true
