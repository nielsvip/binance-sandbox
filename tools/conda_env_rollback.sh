#!/bin/bash
# conda_env_rollback.sh — USER 2026-10-17: "be careful have easy rollbacks as this has given me nightmares before".
#
# Restores a conda environment from the full filesystem snapshot taken before the package upgrade. This is a
# DIRECTORY SWAP, not a pip downgrade: it cannot fail on dependency resolution, cannot half-apply, and takes seconds.
# A `pip install` of an old freeze file is NOT a rollback — it can conflict, partially apply, or hang, which is
# exactly how an upgrade becomes an outage.
#
#   bash tools/conda_env_rollback.sh --list                 # show snapshots available on every host
#   bash tools/conda_env_rollback.sh --host s2 --snapshot binance_env_mac.snapshot_202610170220
#   bash tools/conda_env_rollback.sh --host mac --snapshot binance_env.snapshot_202610170220
#
# The live env is never deleted: it is moved aside to <env>.rolledback_<ts> so the upgraded state can be inspected.
set -u

SSH="ssh -o BatchMode=yes -o ConnectTimeout=20 -o StrictHostKeyChecking=accept-new"
HOST=""; SNAP=""; LIST=0
while [ $# -gt 0 ]; do
    case "$1" in
        --list) LIST=1; shift ;;
        --host) HOST="$2"; shift 2 ;;
        --snapshot) SNAP="$2"; shift 2 ;;
        *) echo "unknown arg: $1"; exit 2 ;;
    esac
done

envdir_for() { case "$1" in mac) echo /opt/anaconda3/envs ;; *) echo /home/niels/.conda/envs ;; esac; }

if [ "$LIST" = "1" ]; then
    echo "=== mac ==="; ls -d /opt/anaconda3/envs/*.snapshot_* 2>/dev/null | sed 's|^|  |' || echo "  none"
    for H in s1-pub s2; do
        echo "=== $H ==="
        $SSH "$H" 'ls -d /home/niels/.conda/envs/*.snapshot_* 2>/dev/null | sed "s|^|  |" || echo "  none"'
    done
    exit 0
fi

[ -n "$HOST" ] && [ -n "$SNAP" ] || { sed -n '2,16p' "$0"; exit 2; }
DIR=$(envdir_for "$HOST")
LIVE="${SNAP%%.snapshot_*}"
TS=$(date -u +%Y%m%d%H%M%S)

CMD='set -u
DIR="'"$DIR"'"; SNAP="'"$SNAP"'"; LIVE="'"$LIVE"'"; TS="'"$TS"'"
[ -d "$DIR/$SNAP" ] || { echo "REFUSING: snapshot $DIR/$SNAP not found"; exit 1; }
[ -x "$DIR/$SNAP/bin/python" ] || { echo "REFUSING: $DIR/$SNAP/bin/python missing or not executable — snapshot is incomplete"; exit 1; }
"$DIR/$SNAP/bin/python" -c "import sys" || { echo "REFUSING: snapshot interpreter does not run"; exit 1; }
if [ -d "$DIR/$LIVE" ]; then mv "$DIR/$LIVE" "$DIR/$LIVE.rolledback_$TS" || exit 1; fi
cp -a "$DIR/$SNAP" "$DIR/$LIVE" || { echo "RESTORE FAILED — putting the original back"; mv "$DIR/$LIVE.rolledback_$TS" "$DIR/$LIVE"; exit 1; }
echo "restored $DIR/$LIVE from $SNAP ; previous state kept at $DIR/$LIVE.rolledback_$TS"
"$DIR/$LIVE/bin/python" -V
"$DIR/$LIVE/bin/python" -c "import numpy,pandas;print(\"  numpy\",numpy.__version__,\"pandas\",pandas.__version__)"'

if [ "$HOST" = "mac" ]; then bash -c "$CMD"; else $SSH "$HOST" "$CMD"; fi
