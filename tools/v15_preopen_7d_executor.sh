#!/bin/bash
# Worker-only FIN/TRC 7D vector lane. Called by cron; stage validity gates it.
set -u
cd "$HOME/binance-sandbox" || exit 2
[ -s data/vec_live_7d/validation.json ] || exit 0
PY=/home/niels/.conda/envs/binance_env_mac/bin/python
# Current dynamic books must arrive before load_sets checks side permission.
rsync -az --timeout=15 --include='symbols_men_*.json' --include='symbols_fin_*.json' --include='symbols_trb_*.json' --exclude='*' niels@10.0.0.3:binance-sandbox/ ./ >/dev/null 2>&1 || exit 2
host=$(hostname -s)
case "$host" in
  s2) shards="crypto_long crypto_short" ;;
  s5) shards="stock_long" ;;
  s6) shards="stock_short" ;;
  *) exit 2 ;;
esac
for shard in $shards; do
  VEC_LIVE_SCOPE=7d VEC_SHARD="$shard" "$PY" tools/vec_live_executor.py --once --no-poll --workers 3 || exit 2
done
