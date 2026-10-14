#!/bin/bash
# klines_canonical_push — Mac -> S1 (+ s2) mirror of klines_canonical/ (USER 2026-10-14: boards and live read the SAME exchange bars).
# Spawned by tools/klines_canonical.py daemon after every cycle; mkdir-lock single-flight (macOS has no flock); the store is grow-only so a plain rsync is safe.
# Receivers never write klines_canonical/ themselves (S1 never fetches). --delay-updates: readers never see half-written files.
cd "$(dirname "$0")/.." || exit 1
LOCKD=/tmp/klines_canonical_push.lockdir
if ! mkdir "$LOCKD" 2>/dev/null; then
    pid=$(cat "$LOCKD/pid" 2>/dev/null)
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then exit 0; fi
    rm -rf "$LOCKD"; mkdir "$LOCKD" || exit 0
fi
echo $$ > "$LOCKD/pid"
trap 'rm -rf "$LOCKD"' EXIT
SRC="$PWD/klines_canonical/"
[ -d "$SRC" ] || exit 0
ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
push() {
    local host="$1" dst="$2"
    if rsync -az --delay-updates --timeout=60 --exclude='.*' -e "ssh -S none -o BatchMode=yes -o ConnectTimeout=10" "$SRC" "$host:$dst/" 2>&1 | tail -2; then
        echo "$(ts) push $host:$dst ok"
    else
        echo "$(ts) push $host:$dst FAILED"
    fi
}
push s1-pub binance-sandbox/klines_canonical
push 10.0.0.4 binance-sandbox/klines_canonical
