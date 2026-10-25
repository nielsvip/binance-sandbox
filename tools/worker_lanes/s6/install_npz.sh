#!/bin/bash
# usage: install_npz.sh SRCDIR [DESTDIR] — installs staged NPZs only for symbols
# with NO running board, never regresses tail. Safe to cron.
SRC=${1:?srcdir}; DST=${2:-$HOME/binance-sandbox/backtest_v8/indicators}
PY=/home/niels/.conda/envs/binance_env_mac/bin/python
inst=0; skip=0; regress=0
for f in $(find "$SRC" -name "*.npz" 2>/dev/null); do
  [ -e "$f" ] || continue
  s=$(basename "$f" .npz)
  if pgrep -f "v15_pilot.py --sym-side ${s} " >/dev/null; then skip=$((skip+1)); continue; fi
  if [ -f "$DST/$s.npz" ]; then
    verdict=$($PY - "$f" "$DST/$s.npz" <<'PYEOF' 2>/dev/null
import numpy as np, sys
try:
    a = np.load(sys.argv[1], mmap_mode='r'); b = np.load(sys.argv[2], mmap_mode='r')
    print('OK' if float(a['timestamps'][-1]) >= float(b['timestamps'][-1]) else 'REGRESS')
except Exception:
    print('ERR')
PYEOF
)
    if [ "$verdict" != "OK" ]; then regress=$((regress+1)); continue; fi
  fi
  mv -f "$f" "$DST/$s.npz" && inst=$((inst+1))
done
echo "installed=$inst skipped_running=$skip regress_blocked=$regress"
