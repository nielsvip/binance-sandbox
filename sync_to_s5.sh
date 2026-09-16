#!/usr/bin/env bash
# sync_to_s5.sh — one-command sync of SPREADSHEETS templates + NPZ indicators to S5 server (10.0.0.6)
# Usage: ./sync_to_s5.sh [--templates-only] [--npz-only] [--dry-run]
# Requires: ssh via ~/.ssh/config Host s5 (ProxyJump gateway-internal)
set -euo pipefail
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
S5="s5"
REMOTE_BASE="~/binance-sandbox"
DRY=""
DO_TEMPLATES=1
DO_NPZ=1
for arg in "$@"; do
  case "$arg" in
    --templates-only) DO_NPZ=0 ;;
    --npz-only) DO_TEMPLATES=0 ;;
    --dry-run|-n) DRY="--dry-run" ;;
    -h|--help) echo "Usage: $0 [--templates-only] [--npz-only] [--dry-run]"; exit 0 ;;
  esac
done
echo "==> S5 target: $S5:$REMOTE_BASE  (via ssh s5)"
ssh "$S5" "mkdir -p $REMOTE_BASE/SPREADSHEETS $REMOTE_BASE/backtest_v8/indicators && chmod 775 $REMOTE_BASE/SPREADSHEETS $REMOTE_BASE/backtest_v8/indicators 2>/dev/null; echo ok mkdir"
if [[ $DO_TEMPLATES -eq 1 ]]; then
  echo "==> Syncing 5 TEMPLATE xlsx -> S5:SPREADSHEETS/"
  # fix read-only perms that caused 'Access denied' on overwrite (444 -> 644)
  ssh "$S5" "chmod 644 $REMOTE_BASE/SPREADSHEETS/TEMPLATE*.xlsx 2>/dev/null || true"
  for f in \
    "SPREADSHEETS/TEMPLATE.xlsx" \
    "SPREADSHEETS/TEMPLATE_STOCKS_LONG.xlsx" \
    "SPREADSHEETS/TEMPLATE_STOCKS_SHORT.xlsx" \
    "SPREADSHEETS/TEMPLATE_CRYPTO_LONG.xlsx" \
    "SPREADSHEETS/TEMPLATE_CRYPTO_SHORT.xlsx"; do
    if [[ ! -f "$REPO_DIR/$f" ]]; then echo "  skip missing $f"; continue; fi
    echo "  -> $f"
    rsync -avz --chmod=644 $DRY -e ssh "$REPO_DIR/$f" "$S5:$REMOTE_BASE/SPREADSHEETS/"
  done
  ssh "$S5" "chmod 644 $REMOTE_BASE/SPREADSHEETS/TEMPLATE*.xlsx 2>/dev/null; ls -lh $REMOTE_BASE/SPREADSHEETS/TEMPLATE*.xlsx | awk '{print \$1,\$5,\$9}'"
fi
if [[ $DO_NPZ -eq 1 ]]; then
  echo "==> Syncing NPZ indicators -> S5:backtest_v8/indicators/"
  # real .npz files only (skip symlinks); rsync handles 4 GB — use --update to skip up-to-date
  if [[ -d "$REPO_DIR/backtest_v8/indicators" ]]; then
    # count before
    LOCAL_N=$(find "$REPO_DIR/backtest_v8/indicators" -maxdepth 1 -type f -name "*.npz" | wc -l | tr -d ' ')
    echo "  local real npz: $LOCAL_N"
    # --copy-links would dereference symlinks to huge duplicates — use find -type f to send only real files
    NPZ_LIST=$(find "$REPO_DIR/backtest_v8/indicators" -maxdepth 1 -type f -name "*.npz" ! -name "*_S3*.npz" -print0 | tr '\0' ' ')
    if [[ -z "$NPZ_LIST" ]]; then echo "  no real npz to sync"; else
      # shellcheck disable=SC2086
      rsync -avz --chmod=644 $DRY -e ssh --update $NPZ_LIST "$S5:$REMOTE_BASE/backtest_v8/indicators/" 2>&1 | tail -n 50
    fi
    # also sync top-level MU.npz if present
    if [[ -f "$REPO_DIR/MU.npz" ]]; then
      echo "  -> MU.npz"
      rsync -avz --chmod=644 $DRY -e ssh "$REPO_DIR/MU.npz" "$S5:$REMOTE_BASE/"
    fi
    ssh "$S5" "chmod 644 $REMOTE_BASE/backtest_v8/indicators/*.npz 2>/dev/null; echo \"  s5 npz count: \$(ls $REMOTE_BASE/backtest_v8/indicators/*.npz 2>/dev/null | wc -l)\"; df -h / | tail -1"
  else
    echo "  no backtest_v8/indicators dir, skipping"
  fi
fi
# final perm fix — templates were historically 444 (read-only) causing Access Denied on overwrite
ssh "$S5" "chmod 644 $REMOTE_BASE/SPREADSHEETS/TEMPLATE*.xlsx $REMOTE_BASE/backtest_v8/indicators/*.npz 2>/dev/null; chmod 775 $REMOTE_BASE/SPREADSHEETS $REMOTE_BASE/backtest_v8/indicators 2>/dev/null; echo 'perms fixed 644/775'" >/dev/null || true
echo "==> Done. Verify: ssh s5 'ls -lh ~/binance-sandbox/SPREADSHEETS/TEMPLATE*.xlsx; ls ~/binance-sandbox/backtest_v8/indicators/*.npz | wc -l'"
