#!/usr/bin/env bash
# sync_npz_between_servers.sh — server-to-server NPZ sync (S1 is source)
# Runs ON SERVERS only (not on Mac). Mac never holds NPZ (see npz_live_generator._is_s1_host).
# S1 (10.0.0.3 / niels) generates NPZ via ez/tradier_indicators.py (npz_live_generator, incremental per 15m close).
# S3 (10.0.0.5) / S5 (10.0.0.6) only calculate XLS sides and PULL missing NPZ from S1 on demand.
# No continuous generation on S3/S5.
#
# Usage (on S3/S5):
#   ./sync_npz_between_servers.sh --dry-run          # preview pull from S1
#   ./sync_npz_between_servers.sh                    # pull missing NPZ from S1
# Usage (on S1):
#   ./sync_npz_between_servers.sh --push --dry-run   # push S1 NPZ to S3/S5
#
# Cron (on S3/S5, every 5 min pull):
#   */5 * * * * /home/niels/binance-sandbox/tools/sync_npz_between_servers.sh >> /tmp/npz_sync.log 2>&1
# Cron (on S1, every 5 min push — optional, pull also works):
#   */5 * * * * /home/niels/binance-sandbox/tools/sync_npz_between_servers.sh --push >> /tmp/npz_sync.log 2>&1
#
# Mac is source of truth for scripts/templates only — see sync_to_s3.sh / sync_to_s5.sh (templates only).

set -euo pipefail
BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
NPZ_DIR="$BASE_DIR/backtest_v8/indicators"
DRY=""
MODE="auto"

for arg in "$@"; do
  case "$arg" in
    --dry-run|-n) DRY="--dry-run" ;;
    --from) MODE="pull" ;;
    --from=*) MODE="pull"; SRC="${arg#--from=}" ;;
    --push) MODE="push" ;;
    --pull) MODE="pull" ;;
    -h|--help) echo "Usage: $0 [--dry-run] [--push|--pull] [--from s1]"; exit 0 ;;
  esac
done

HOST=$(hostname -s 2>/dev/null || hostname 2>/dev/null || echo unknown)
if [[ "$(uname -s)" == "Darwin" ]]; then
  echo "REFUSE: Mac (Darwin) never holds NPZ — run this on servers only." >&2
  exit 1
fi

# Auto-detect: S1 pushes, S3/S5 pull from S1
MY_IPS=$(hostname -I 2>/dev/null || ip -4 addr show 2>/dev/null | grep -o "10\.0\.0\.[0-9]*" | tr "\n" " ")
if [[ "$MODE" == "auto" ]]; then
  if [[ "$MY_IPS" == *"10.0.0.3"* ]] || [[ "$HOST" == "niels" ]]; then
    MODE="push"
  else
    MODE="pull"
  fi
fi

if [[ ! -d "$NPZ_DIR" ]]; then
  echo "NPZ dir missing: $NPZ_DIR" >&2
  exit 1
fi

COUNT=$(find "$NPZ_DIR" -maxdepth 1 -type f -name "*.npz" 2>/dev/null | wc -l | tr -d ' ')
S1="10.0.0.3"

if [[ "$MODE" == "push" ]]; then
  # S1 pushes to S3/S5
  PEERS=("10.0.0.5" "10.0.0.6")
  echo "Host $HOST (S1): $COUNT NPZ in $NPZ_DIR — pushing to ${PEERS[*]}"
  for peer in "${PEERS[@]}"; do
    if [[ "$MY_IPS" == *"$peer"* ]]; then continue; fi
    echo "==> $peer"
    NPZ_REAL=$(find "$NPZ_DIR" -maxdepth 1 -type f -name "*.npz" -print0 | tr "\0" " ")
    if [[ -z "$NPZ_REAL" ]]; then echo "no real NPZ"; continue; fi
    # shellcheck disable=SC2086
    rsync -avz --chmod=644 $DRY -e "ssh -o StrictHostKeyChecking=accept-new" --update $NPZ_REAL "$peer:$NPZ_DIR/" 2>&1 | tail -n 20
    ssh -o StrictHostKeyChecking=accept-new "$peer" "chmod 644 $NPZ_DIR/*.npz 2>/dev/null; echo \"  $peer now \$(ls $NPZ_DIR/*.npz 2>/dev/null | wc -l) NPZ\"" 2>&1 | tail -n 5
  done
else
  # S3/S5 pull from S1
  echo "Host $HOST: $COUNT NPZ in $NPZ_DIR — pulling missing from S1 ($S1)"
  # Pull all NPZ from S1, only missing/newer (no delete)
  rsync -avz --chmod=644 $DRY -e "ssh -o StrictHostKeyChecking=accept-new" --update "$S1:$NPZ_DIR/"*.npz "$NPZ_DIR/" 2>&1 | tail -n 40
  # Also handle real files explicitly to avoid symlink glob issues
  # Fallback: rsync with find on S1 side via ssh
  if [[ -z "$DRY" ]]; then
    chmod 644 "$NPZ_DIR"/*.npz 2>/dev/null || true
  fi
  echo "  $HOST now $(ls "$NPZ_DIR"/*.npz 2>/dev/null | wc -l) NPZ (was $COUNT)"
fi
echo "Done. Mac still has 0 (correct). S3/S5 calculate XLS, S1 generates NPZ."
