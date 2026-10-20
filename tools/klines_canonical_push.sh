#!/bin/bash
# klines_canonical_push - mirror of klines_canonical/ to the receivers (USER 2026-10-14: boards and live read the SAME exchange bars).
# USER LAW 2026-10-20 ("all new calculations calculate up to the latest kline bar, max 15m old in the npz"):
#   1. FAST PATH (every call, seconds): tools/klines_tail_push.py ships only the changed TAILS (last 12 rows of every file whose last bar is newer
#      than what that receiver already has, ~100 KB) and tools/klines_tail_apply.py splices them into the receiver's store (grow-only, atomic).
#      Before: rsync -az of the whole 829-file store, serial per host, 5-7 min each - the closed bar reached S1/S2 10-20 min after the close.
#   2. SLOW PATH (at most once per KLINES_CANONICAL_FULL_EVERY_S = 3600 s, background, own lock, never delays the fast path): the whole-store rsync
#      with --update (a receiver file that is newer - e.g. already advanced by another source - is never overwritten by an older one).
# Single-flight (mkdir lock, macOS has no flock); a call arriving while a push runs sets .again and the running push does one more round.
# Receivers never fetch from Binance themselves (S1 is 418-banned); a receiver may ALSO be a source (S2 daemon) - both feed the same grow-only store.
# Env: KLINES_PUSH_TARGETS="host:remote_root ..." (default: s1-pub + 10.0.0.4 from the Mac), KLINES_PUSH_PY (python), KLINES_CANONICAL_PUSH_FULL=0 disables the slow path.
cd "$(dirname "$0")/.." || exit 1
LOCKD=${KLINES_PUSH_LOCKDIR:-/tmp/klines_canonical_push.lockdir}
if ! mkdir "$LOCKD" 2>/dev/null; then
    pid=$(cat "$LOCKD/pid" 2>/dev/null)
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then touch "$LOCKD.again"; exit 0; fi
    rm -rf "$LOCKD"; mkdir "$LOCKD" || exit 0
fi
echo $$ > "$LOCKD/pid"
trap 'rm -rf "$LOCKD"' EXIT
SRC="$PWD/klines_canonical/"
[ -d "$SRC" ] || exit 0
PY=${KLINES_PUSH_PY:-python3}
TARGETS=${KLINES_PUSH_TARGETS:-"s1-pub:binance-sandbox 10.0.0.4:binance-sandbox"}
ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
tail_push() {
    local args=() t
    for t in $TARGETS; do args+=(--to "$t"); done
    echo "$(ts) tail-push start"
    "$PY" tools/klines_tail_push.py "${args[@]}" 2>&1 | tail -4
    echo "$(ts) tail-push done"
}
full_push() {
    local FL=/tmp/klines_canonical_pushfull.lockdir STAMP=/tmp/klines_canonical_pushfull.stamp
    [ "${KLINES_CANONICAL_PUSH_FULL:-1}" = "0" ] && return 0
    if [ -f "$STAMP" ] && [ $(( $(date +%s) - $(stat -f %m "$STAMP" 2>/dev/null || stat -c %Y "$STAMP") )) -lt "${KLINES_CANONICAL_FULL_EVERY_S:-3600}" ]; then return 0; fi
    mkdir "$FL" 2>/dev/null || return 0
    (
        trap 'rm -rf "$FL"' EXIT
        for t in $TARGETS; do
            host="${t%%:*}"; root="${t#*:}"
            if rsync -az --update --delay-updates --timeout=60 --exclude='.*' -e "ssh -S none -o BatchMode=yes -o ConnectTimeout=10" "$SRC" "$host:$root/klines_canonical/" 2>&1 | tail -2; then
                echo "$(ts) full push $host:$root ok"
            else
                echo "$(ts) full push $host:$root FAILED"
            fi
        done
        touch "$STAMP"
    ) >> "${KLINES_PUSH_FULL_LOG:-data/klines_canonical_push_full.log}" 2>&1 &
}
while true; do
    rm -f "$LOCKD.again"
    tail_push
    [ -f "$LOCKD.again" ] || break   # a push was requested while this one ran (HTF pass) -> one more round
done
full_push
