#!/usr/bin/env bash
# sync_to_s3.sh — Mac is source of truth for scripts/templates only.
# S3 target: s3 (10.0.0.5) ~/binance-sandbox/SPREADSHEETS
# NPZ indicators are generated on servers by ez/tradier_indicators.py
# (npz_live_generator._is_s1_host==Linux) and synced server-to-server.
# Mac never holds or pushes NPZ — it only trades live.
# Usage: ./sync_to_s3.sh [--dry-run]
set -euo pipefail
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
S3="s3"
REMOTE_BASE="~/binance-sandbox"
DRY=""
for arg in "$@"; do
  case "$arg" in
    --dry-run|-n) DRY="--dry-run" ;;
    --templates-only) ;; # default, kept for compat
    --npz-only) echo "Mac does not hold NPZ (servers are source). NPZ sync is server-to-server only (see sync_npz_between_servers.sh on servers)." >&2; exit 1 ;;
    -h|--help) echo "Usage: $0 [--dry-run]  (templates only; NPZ is server-to-server)"; exit 0 ;;
    *) echo "unknown arg $arg" >&2; exit 1 ;;
  esac
done
echo "==> S3 target: $S3:$REMOTE_BASE  (via ssh s3) — templates only, Mac has no NPZ"
ssh "$S3" "mkdir -p $REMOTE_BASE/SPREADSHEETS && chmod 775 $REMOTE_BASE/SPREADSHEETS 2>/dev/null; echo ok mkdir"
echo "==> Syncing 5 TEMPLATE xlsx -> S3:SPREADSHEETS/"
ssh "$S3" "chmod 644 $REMOTE_BASE/SPREADSHEETS/TEMPLATE*.xlsx 2>/dev/null || true"
for f in \
  "SPREADSHEETS/TEMPLATE.xlsx" \
  "SPREADSHEETS/TEMPLATE_STOCKS_LONG.xlsx" \
  "SPREADSHEETS/TEMPLATE_STOCKS_SHORT.xlsx" \
  "SPREADSHEETS/TEMPLATE_CRYPTO_LONG.xlsx" \
  "SPREADSHEETS/TEMPLATE_CRYPTO_SHORT.xlsx"; do
  if [[ ! -f "$REPO_DIR/$f" ]]; then echo "  skip missing $f"; continue; fi
  echo "  -> $f"
  rsync -avz --chmod=644 $DRY -e ssh "$REPO_DIR/$f" "$S3:$REMOTE_BASE/SPREADSHEETS/"
done
ssh "$S3" "chmod 644 $REMOTE_BASE/SPREADSHEETS/TEMPLATE*.xlsx 2>/dev/null; ls -lh $REMOTE_BASE/SPREADSHEETS/TEMPLATE*.xlsx | awk '{print \$1,\$5,\$9}'"
ssh "$S3" "chmod 775 $REMOTE_BASE/SPREADSHEETS 2>/dev/null; echo 'perms fixed 644/775'" >/dev/null || true
echo "==> Done. Verify: ssh s3 'ls -lh ~/binance-sandbox/SPREADSHEETS/TEMPLATE*.xlsx'"
echo "    NPZ: not from Mac — servers refresh via ez/tradier_indicators.py and sync with sync_npz_between_servers.sh"
