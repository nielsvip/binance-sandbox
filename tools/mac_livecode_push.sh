#!/bin/bash
# mac_livecode_push.sh — push latest TRADING CODE Mac -> S1 ~/binance (additive, never deletes).
# Runs on Mac (launchd com.niels.s1-livecode-push, every 15 min). Excludes ALL state: S1-local
# data/logs, position/account dirs, keys, venvs, sheets, NPZs. Writes data/livecode_sync.json stamp
# first so S1 always knows how fresh its code is. Mirrors tools/s1_livecode_pull.sh excludes.
set -u
REPO="/Users/niels/Documents/binance"
DST_DIR="/home/niels/binance/"
LOG="/Users/niels/logs/mac_livecode_push.log"
mkdir -p "$(dirname "$LOG")"
/usr/bin/python3 -c "import json,time; json.dump({'epoch':time.time(),'host':'macbook'}, open('$REPO/data/livecode_sync.json','w'))"
LOCALHOST_SHELL="ssh -p 2201 -i /Users/niels/.ssh/id_ed25519 -o BatchMode=yes -o ConnectTimeout=6 -o ControlMaster=no -o ControlPath=none"
PUBLIC_SHELL="ssh -i /Users/niels/.ssh/id_ed25519 -o BatchMode=yes -o ConnectTimeout=6 -o ControlMaster=no -o ControlPath=none"
EXCLUDES=(--include 'data/' --include 'data/livecode_sync.json' --exclude 'data/*' --exclude 'logs/' --exclude '*.log'
  --exclude '.venv/' --exclude '__pycache__/' --exclude '.git/' --exclude '.env*' --exclude '*secret*'
  --exclude '*.pem' --exclude '*.key' --exclude 'backups/' --exclude 'tests/' --exclude 'SPREADSHEETS/'
  --exclude '*.xlsx' --exclude '*.npz' --exclude '.agents/' --exclude '.muse/' --exclude '.codex/'
  --exclude 'node_modules/' --exclude '.nova/' --exclude '*.pyc' --exclude '.DS_Store'
  --exclude 'ang/' --exclude 'inf/' --exclude 'flz/' --exclude 'men/' --exclude 'fin/'
  --exclude 'tra/' --exclude 'trb/' --exclude 'trc/' --exclude 'klines_cache/' --exclude '*.tmp'
  --exclude 'agent_inbox_poller.py' --exclude 'vec_paths/mtf_dc_reject.py' --exclude 'vec_paths/live_entry_engine.py')
try_push() { # try_push <shell-or-EMPTY> <destination>
    local shell="$1" dest="$2" rc
    if [ -n "$shell" ]; then
        rsync -az --timeout=120 -e "$shell" "${EXCLUDES[@]}" "$REPO/" "$dest" >>"$LOG" 2>&1
    else
        rsync -az --timeout=120 "${EXCLUDES[@]}" "$REPO/" "$dest" >>"$LOG" 2>&1
    fi
    rc=$?
    return $rc
}
if try_push "$LOCALHOST_SHELL" "niels@localhost:$DST_DIR" \
    || try_push "$PUBLIC_SHELL" "niels@157.180.125.52:$DST_DIR" \
    || try_push "" "s1-int:$DST_DIR"; then
    echo "$(date -u +%FT%TZ) push OK" >>"$LOG"
else
    echo "$(date -u +%FT%TZ) push FAILED all transports" >>"$LOG"
fi
exit 0
