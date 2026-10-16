#!/bin/bash
# USER 2026-10-16 ("did the servers stop? I see no new results?"): S1 hit 99% disk (3.7 GB free) and the fleet
# scheduler's disk guard idled every crypto launch. The hog was /tmp/vec_exact_raw — 66 orphaned 118 MB pickles
# (8.1 GB). live_twins/vec_exact._shared_raw_put prunes only the symbol it is writing, so a symbol that leaves
# the universe (or whose twin stops running) keeps its last bucket forever. This sweeps the whole directory by
# bucket age, which is what nothing was doing. Live code untouched: the cache is rebuilt per 15m bar on demand.
set -u
DIR="${VEC_EXACT_SHARED_DIR:-/tmp/vec_exact_raw}"
KEEP_BUCKETS="${VEC_EXACT_KEEP_BUCKETS:-2}"   # a bucket is one 15m bar; vec_exact reuses only the just-closed bar
[ -d "$DIR" ] || exit 0
CUR=$(( $(date -u +%s) / 900 ))
MIN=$(( CUR - KEEP_BUCKETS ))
n=0
bytes=0
for f in "$DIR"/*.pkl; do
  [ -e "$f" ] || continue
  b="${f##*_}"
  b="${b%.pkl}"
  case "$b" in ''|*[!0-9]*) continue ;; esac
  if [ "$b" -lt "$MIN" ]; then
    sz=$(stat -c %s "$f" 2>/dev/null || stat -f %z "$f" 2>/dev/null || echo 0)
    rm -f "$f" && n=$((n+1)) && bytes=$((bytes+sz))
  fi
done
for f in "$DIR"/*.tmp; do
  [ -e "$f" ] || continue
  if [ -n "$(find "$f" -mmin +60 2>/dev/null)" ]; then rm -f "$f" && n=$((n+1)); fi
done
if [ "$n" -gt 0 ]; then
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] vec_exact_raw_prune: removed $n file(s) ($((bytes/1048576)) MB), keep>=bucket $MIN, free=$(df -Pm / | awk 'NR==2{print $4}')MB"
fi
exit 0
