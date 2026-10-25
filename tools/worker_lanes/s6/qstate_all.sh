#!/bin/bash
# qstate_all.sh — recompute forward states for ALL given sides in seconds.
# usage: qstate_all.sh SIDES_FILE [PARALLEL]
# Splits sides into PARALLEL chunks (default 8), runs one forward_state per
# chunk concurrently, waits. Each side ~2s (NPZ load + vector eval).
SIDES=${1:?sides}; P=${2:-8}
SB=$HOME/binance-sandbox
PY=/home/niels/.conda/envs/binance_env_mac/bin/python
CH=/tmp/qstate_chunks
rm -rf "$CH"; mkdir -p "$CH"
split -n l/$P "$SIDES" $CH/c_ 2>/dev/null || return 1
for c in $CH/c_*; do
  [ -s "$c" ] || continue
  $PY -u "$SB/tools/v15_forward_state.py" $(cat "$c" | tr '\n' ' ') >> /tmp/npz_qstate.log 2>&1 &
done
wait
echo "$(date -u +%FT%TZ) qstate done $(wc -w < "$SIDES") sides" >> /tmp/npz_qstate.log
