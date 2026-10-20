#!/bin/bash
# sync_fleet_state.sh — 2026-10-20 WP1 (V19 full-course round). S1 cron */2: mirror the small launch-state files every
# worker pilot reads at startup from S1 to the ephemeral workers, so a gate PASS (deployed to S1/S2 by the gate) reaches
# s5/s6 within 2 min even if the gate's own optional deploy to them missed. Backtest state only: no NPZ, no klines,
# no live files. --update: never overwrite a NEWER copy the gate already put on a worker. 10.0.0.4 (S2) gets the gate's
# own deploy; here it only receives files it LACKS (--ignore-existing), never an overwrite of S2's gate/bench state.
cd "$HOME/binance-sandbox" || exit 1
L=/tmp/sync_fleet_state.log
SSH="ssh -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=accept-new"
ITEMS="data/v15_gate_flags.json data/v15_gate_env.json data/learn/ data/brain/ SPREADSHEETS/TEMPLATE_FINAL_NORM/"
V18=$(ls tools/v18_data_* 2>/dev/null)
for ip in ${SYNC_FLEET_STATE_HOSTS:-10.0.0.5 10.0.0.6}; do
  $SSH niels@$ip "mkdir -p binance-sandbox/data/learn binance-sandbox/data/brain binance-sandbox/SPREADSHEETS/TEMPLATE_FINAL_NORM binance-sandbox/tools" >/dev/null 2>&1 || { echo "$(date -u +%FT%TZ) $ip UNREACHABLE" >> $L; continue; }
  rc=0
  for it in $ITEMS; do
    nice -n 10 rsync -au --timeout=60 -e "$SSH" "$it" "niels@$ip:binance-sandbox/$it" >/dev/null 2>&1 || rc=$?
  done
  [ -n "$V18" ] && { nice -n 10 rsync -au --timeout=60 -e "$SSH" $V18 "niels@$ip:binance-sandbox/tools/" >/dev/null 2>&1 || rc=$?; }
  echo "$(date -u +%FT%TZ) $ip rc=$rc env=$($SSH niels@$ip 'md5sum binance-sandbox/data/v15_gate_env.json 2>/dev/null' | cut -c1-8) s1env=$(md5sum data/v15_gate_env.json | cut -c1-8)" >> $L
done
for ip in 10.0.0.4; do
  for it in $ITEMS; do
    nice -n 10 rsync -a --ignore-existing --timeout=60 -e "$SSH" "$it" "niels@$ip:binance-sandbox/$it" >/dev/null 2>&1
  done
done
tail -n 2000 $L > $L.tmp 2>/dev/null && mv $L.tmp $L
