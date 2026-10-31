#!/bin/bash
# Keep every backup location on the Mac and on s1/s2/s5/s6 drained onto the 5 TB drive (WD25), verified before removal.
#
# USER 2026-10-31 (verbatim): "we need ALL those tools to go to 5T directly wihout putting in danger mac integrity ever
# again (or any of the s1 2 5 6 servers".
#
# The writers that matter now write to the 5T drive themselves (board_name_truth_guard on the Mac, the h2h pre-apply
# backup) and refuse rather than fall back to a local disk. This job catches everything else that lands in a backups
# directory: anything >= 50 MB once it is 30 min old, everything else once it is 6 h old, so the last few hours of small
# per-edit backups stay local for a fast revert. Each file leaves its source only after size + sha256 match on the drive
# (tools/offload_to_5t.py). If the drive is unmounted, stalled or near its floor the run moves nothing and removes nothing.
#
# Restoring: everything is at /Volumes/WD25/binance_backups/{mac/<repo path>, servers/<host>/<home path>}.
set -u
BASE=/Users/niels/Documents/binance
PY=/opt/anaconda3/envs/binance_env/bin/python
LOG=/Users/niels/logs/offload_all_to_5t.log
cd "$BASE" || exit 2
say() { echo "[$(date -u +%FT%TZ)] $*" >> "$LOG" 2>/dev/null; }

if ! "$PY" -B tools/offload_target.py --subdir _health >/dev/null 2>&1; then
  say "SKIP: 5T drive unavailable ($("$PY" -B tools/offload_target.py --subdir _health --json 2>/dev/null)) — nothing moved, nothing removed"
  exit 0
fi

mac_free() { /bin/df -k /System/Volumes/Data 2>/dev/null | awk 'NR==2 {printf "%.1f GB", $4/1048576}'; }
say "start — Mac free $(mac_free)"

# Mac
for pass in "--min-mb 50 --settle-min 30" "--settle-min 360"; do
  # shellcheck disable=SC2086
  "$PY" -B -u tools/offload_to_5t.py local backups $pass >> "$LOG" 2>&1
done

# servers: every backups directory, plus the board guard's capped spool
for host in s1-pub s2 s5 s6; do
  for dir in binance-sandbox/backups binance/backups backups; do
    for pass in "--min-mb 50 --settle-min 30" "--settle-min 360"; do
      # shellcheck disable=SC2086
      "$PY" -B -u tools/offload_to_5t.py remote "$host:$dir" $pass >> "$LOG" 2>&1
    done
  done
done

say "done — Mac free $(mac_free)"
