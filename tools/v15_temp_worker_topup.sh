#!/bin/bash
# v15_temp_worker_topup — bring a worker cloned from the worker image up to S1's current state (USER 2026-10-08 "add 4").
# Runs ON S1:  bash tools/v15_temp_worker_topup.sh <name> <private_ip>     e.g.  s7 10.0.0.7
# INFRA law: workers are images of S1 — rsync from S1, never provision from scratch. --update everywhere: never overwrite
# a newer file. Then: hostname, pointer files for the current round, per-host crons (s6_365 -> sX_365), known_hosts.
set -u
NAME=$1; IP=$2; SSH="ssh -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new"
RS="rsync -az --update -e \"$SSH\""
log(){ echo "[$(date -u +%FT%TZ)] [topup $NAME] $*"; }
cd /home/niels/binance-sandbox || exit 1
ssh-keyscan -T 5 "$IP" >> ~/.ssh/known_hosts 2>/dev/null
until $SSH "niels@$IP" true 2>/dev/null; do log "waiting for ssh on $IP"; sleep 15; done
log "ssh up; hostname -> $NAME"
$SSH "niels@$IP" "sudo -n hostnamectl set-hostname $NAME 2>/dev/null; sudo -n sed -i 's/^127\.0\.1\.1.*/127.0.1.1 $NAME/' /etc/hosts 2>/dev/null; true"
# code + state top-up from S1 (whole tree, update-only, excluding bulk data that the image already carries and run dirs)
eval $RS --exclude 'backtest_v8/indicators/' --exclude 'backups/' --exclude 'logs/' --exclude 'data/reports/lifecycle_pilot/' --exclude 'SPREADSHEETS/V15_V16_CELL_BY_CELL*/' --exclude '*.log' --exclude '.git/' \
  /home/niels/binance-sandbox/ "niels@$IP:binance-sandbox/" && log "code/state synced"
# NPZ bars: crypto + stocks that changed since the image (update-only)
eval $RS /home/niels/binance-sandbox/backtest_v8/indicators/ "niels@$IP:binance-sandbox/backtest_v8/indicators/" && log "NPZ synced ($($SSH niels@$IP 'ls ~/binance-sandbox/backtest_v8/indicators/*.npz | wc -l') files)"
# round pointers = S1's
PD=$(cat ~/v15_current_progress_dir.txt); RD=$(cat ~/v15_defaults_round.txt)
$SSH "niels@$IP" "mkdir -p $PD && echo $PD > ~/v15_current_progress_dir.txt && echo $RD > ~/v15_defaults_round.txt"
# per-host crons: the image carries s6's (data/s6_365 push) -> this host's own dir; keep the rest
$SSH "niels@$IP" "crontab -l 2>/dev/null | sed 's#s6_365#${NAME}_365#g' | crontab -; mkdir -p ~/binance-sandbox/data/${NAME}_365; rm -f /tmp/sweep_*_30D.log"
# sanity
$SSH "niels@$IP" "cd ~/binance-sandbox && echo host=\$(hostname) v12=\$(md5sum v12_quick_engine.py|cut -c1-8) cfg=\$(md5sum config.py|cut -c1-8) twin=\$(md5sum vec_decisions/twin_exits_dead.py|cut -c1-8) catside=\$(md5sum data/cat_side_defaults_4.json|cut -c1-8) tpl=\$(md5sum SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_CRYPTO_LONG.xlsx|cut -c1-8) npz=\$(ls backtest_v8/indicators/*.npz|wc -l) && .venv/bin/python -c 'import v12_quick_engine as V; c=V.QuickConfig(); print(\"IMPORT-OK abl=\",c.ABLATION_DISABLE_QUICK_ENTRY,\"min_tfs=\",c.EXIT_VELOCITY_WT_MIN_TFS)'"
log "done"
