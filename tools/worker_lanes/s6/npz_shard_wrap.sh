#!/bin/bash
# shard refresh wrapper: lane shard + dispatch-queue syms, then exec refresh (args pass through).
export VEC_SHARD="${VEC_SHARD:-stock_short}"
q="${NPZ_QUEUE_FILE:-/tmp/npz_sides_s6.txt}"
if [ -f "$q" ]; then
  export VEC_NPZ_EXTRA_SYMS="$(sed -e 's/_SHORT$//' -e 's/_LONG$//' "$q" 2>/dev/null | paste -sd, -)"
fi
exec /home/niels/.conda/envs/binance_env_mac/bin/python /home/niels/binance-sandbox/tools/npz_bar_refresh.py "$@"
