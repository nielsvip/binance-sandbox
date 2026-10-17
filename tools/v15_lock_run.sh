#!/bin/bash
# v15_lock_run — portable non-blocking mutex, a drop-in for `flock -n <lock> <cmd...>` on macOS.
#
# 2026-10-17: the four v15_h2h_cycle crons were installed at 05:27 using `flock -n`. macOS ships no flock, so every
# single invocation died at `/bin/sh: flock: command not found` — 23 consecutive failures, nothing ever ran, and the
# cron log filled with the error while the pipeline silently produced nothing. That pipeline is the "re-measure every
# sym_side against the latest settings" machinery, i.e. exactly the work the boards depend on.
#
# Usage:  v15_lock_run.sh /tmp/some.lock <command> [args...]
# Exits 0 and runs nothing when the lock is held (same as flock -n losing the race, which cron treats as fine).
# The lock is a directory: mkdir is atomic on every POSIX filesystem, unlike `[ -f ] && touch`.
# A lock whose owner is gone is reaped, so a crashed run can never wedge the cron forever.
set -u
LOCK="${1:?usage: v15_lock_run.sh <lockpath> <cmd...>}"
shift
[ "$#" -gt 0 ] || { echo "[lock-run] no command given" >&2; exit 2; }
STALE_S="${V15_LOCK_STALE_S:-10800}"   # 3 h: longer than any h2h cycle (timeout 2400) by a wide margin

if ! mkdir "$LOCK" 2>/dev/null; then
    _owner="$(cat "$LOCK/pid" 2>/dev/null || true)"
    if [ -n "$_owner" ] && kill -0 "$_owner" 2>/dev/null; then
        exit 0                      # genuinely running — this is the flock -n "lost the race" case
    fi
    # No live owner. Only reap once it is also older than STALE_S, so two near-simultaneous starts
    # cannot both decide the other is dead.
    _age=$(( $(date +%s) - $(stat -f %m "$LOCK" 2>/dev/null || stat -c %Y "$LOCK" 2>/dev/null || date +%s) ))
    if [ "$_age" -lt "$STALE_S" ]; then
        exit 0
    fi
    echo "[lock-run] reaping stale lock $LOCK (owner '${_owner:-none}' gone, ${_age}s old)" >&2
    rm -rf "$LOCK"
    mkdir "$LOCK" 2>/dev/null || exit 0
fi
echo $$ > "$LOCK/pid" 2>/dev/null || true
trap 'rm -rf "$LOCK"' EXIT INT TERM
"$@"
