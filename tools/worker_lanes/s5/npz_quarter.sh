#!/bin/bash
# npz_quarter.sh — quarter-hour priority sync for one cat_side owner.
# usage: npz_quarter.sh ASSIGN_FILE MODE SIDE
#   ASSIGN_FILE: /tmp/npz_assign_*.txt (symbols, one per line)
#   MODE: tradier|crypto   SIDE: LONG|SHORT
# Order: pull klines deltas -> incremental NPZ refresh (assigned only) ->
# forward-state recompute (assigned sides) -> push NPZs+states to S1.
# Boards keep running throughout (refresh is incremental + atomic).
SB=$HOME/binance-sandbox
PY=/home/niels/.conda/envs/binance_env_mac/bin/python
ASSIGN=${1:?assign}; MODE=${2:?mode}; SIDE=${3:?side}
if [ "$MODE" = "tradier" ]; then KD="klines_cache/tradier"; else KD="klines_canonical"; fi
[ "$MODE" = "tradier" ] && MODE=stocks  # USER 2026-10-25: refresher modes are both/crypto/stocks
LOG=/tmp/npz_quarter.log
echo "$(date -u +%FT%TZ) quarter start $MODE $SIDE" >> $LOG
LIST=/tmp/npz_q_$(basename "$ASSIGN" .txt).list
: > "$LIST"
for s in $(cat "$ASSIGN" 2>/dev/null); do
  ssh -q -o ConnectTimeout=6 niels@10.0.0.3 "ls $SB/$KD/${s}_*.json 2>/dev/null" 2>/dev/null | sed "s|$SB/$KD/||" >> "$LIST"
done
[ -s "$LIST" ] && rsync -az --timeout=120 --files-from="$LIST" "niels@10.0.0.3:$SB/$KD/" "$SB/$KD/" >> $LOG 2>&1
SYMS=$(paste -sd, "$ASSIGN" 2>/dev/null)
if [ -n "$SYMS" ]; then
  $PY -u "$SB/tools/npz_bar_refresh.py" --symbols "$SYMS" --mode "$MODE" >> $LOG 2>&1
  for s in $(cat "$ASSIGN" 2>/dev/null); do echo "${s}_${SIDE}"; done | tr '\n' ' ' > /tmp/npz_q_sides.txt
  tr ' ' '\n' < /tmp/npz_q_sides.txt | grep -v '^$' > /tmp/npz_q_sides.list
  PER_SYM_STORE_DB="$SB/data/hourly_reconfig/per_sym_forward_master.db" bash /tmp/qstate_all.sh /tmp/npz_q_sides.list 8 >> $LOG 2>&1
  for s in $(cat "$ASSIGN" 2>/dev/null); do
    [ -f "$SB/backtest_v8/indicators/$s.npz" ] && echo "backtest_v8/indicators/$s.npz"
  done | tee /tmp/npz_q_push.list | rsync -az --timeout=120 --files-from=- "$SB/" "niels@10.0.0.3:npz_inbox_q/" >> $LOG 2>&1
  if [ "$?" != "0" ]; then sleep 15; [ -s /tmp/npz_q_push.list ] && rsync -az --timeout=120 --files-from=/tmp/npz_q_push.list "$SB/" "niels@10.0.0.3:npz_inbox_q/" >> $LOG 2>&1; fi
  [ -s /tmp/npz_q_push.list ] && rsync -az --timeout=120 --files-from=/tmp/npz_q_push.list "$SB/" "niels@10.0.0.6:npz_inbox_q/" >> $LOG 2>&1
  # 2026-10-27: S1 is the ONLY crypto state producer (vector_direct_trader reads these). This push used no -u and
  # carried env-less "missing live settings" and 2-day-old crypto states over S1's fresh ones (64 + 6 held sides at
  # 22:10:51Z / 21:54:49Z). Crypto states are never pushed to S1; anything else only when newer (-u).
  rsync -az -u --timeout=60 --exclude '*USDT_*.json' --exclude '*USDC_*.json' --exclude '*USD1_*.json' --exclude '*USDS_*.json' --exclude '*BUSD_*.json' --exclude '*FDUSD_*.json' --exclude '*TUSD_*.json' --exclude '*DAI_*.json' "$SB/data/forward_state/"*.json "niels@10.0.0.3:binance-sandbox/data/forward_state/" >> $LOG 2>&1
fi
echo "$(date -u +%FT%TZ) quarter done $MODE $SIDE" >> $LOG
