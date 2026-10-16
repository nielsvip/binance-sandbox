#!/bin/bash
# npz_refresh_batch — refresh a comma list of symbols' NPZ one at a time, with a private lock, and PRINT the tail each
# one ended on. Runs on the worker; exists as a file because the same loop written inline through ssh lost its
# quoting and silently refreshed nothing (2026-10-16: eight blank lines, no installs, boards then refused STALE).
#   bash tools/npz_refresh_batch.sh "CLS,WDAY" stocks [lockfile]
set -u
SYMS="${1:?comma list of symbols}"
MODE="${2:-crypto}"
LOCK="${3:-/tmp/npz_refresh_batch.lock}"
cd "$(dirname "$0")/.." || exit 1
PY=.venv/bin/python
[ -x "$PY" ] || PY=python3
for s in $(echo "$SYMS" | tr ',' ' '); do
  out=$("$PY" -u tools/npz_bar_refresh.py --symbols "$s" --mode "$MODE" --workers 1 --lock "$LOCK" 2>&1)
  st=$(printf '%s' "$out" | sed -n 's/.*"status": "\([A-Z_]*\)".*/\1/p' | tail -1)
  app=$(printf '%s' "$out" | sed -n 's/.*"appended": \([0-9]*\).*/\1/p' | tail -1)
  tail_iso=$("$PY" - "$s" <<'PY' 2>/dev/null
import numpy as np, sys, time, os
p = f"backtest_v8/indicators/{sys.argv[1]}.npz"
print(time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime(int(np.asarray(np.load(p, mmap_mode="r")["timestamps"]).astype("int64")[-1]))) if os.path.exists(p) else "MISSING")
PY
)
  echo "  npz $s status=${st:-NO_OUTPUT} appended=${app:-0} tail=${tail_iso:-?}"
  [ -z "${st:-}" ] && printf '%s\n' "$out" | tail -3 | sed 's/^/    | /'
done
exit 0
