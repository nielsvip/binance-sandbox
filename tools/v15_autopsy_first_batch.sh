#!/bin/bash
# v15_autopsy_first_batch — run tools/v15_autopsy_first.py over a queue file in N parallel streams and rsync every
# *_autopsy_base.json to S1 ~/v15_autopsy_first/ every 2 min, so tools/v15_fleet_scheduler.py (30D launch env
# V15_START_OVERRIDES=~/v15_autopsy_first/{ss}_autopsy_base.json) starts every board from an engine-verified positive base.
# USER 2026-10-08 FOCUS: 10x bh — never start a board from a dead/negative baseline.
#   bash tools/v15_autopsy_first_batch.sh <queue.txt> <streams> [workers_per_stream]
set -u
Q=$1; N=${2:-2}; W=${3:-4}; OUT=~/v15_autopsy_first; LOG=/tmp/v15_autopsy_first_batch.log
cd ~/binance-sandbox || exit 1; mkdir -p $OUT
log(){ echo "[$(date -u +%FT%TZ)] [af-batch] $*" >> $LOG; }
exec 9>/tmp/v15_autopsy_first_batch.lock
flock -n 9 || { log "another batch already running on this box, refusing to stack"; exit 0; }
TOTAL=$(grep -c . "$Q"); log "queue $Q: $TOTAL sym_sides, $N streams x $W workers"
for i in $(seq 0 $((N-1))); do
  awk -v n=$N -v i=$i 'NR%n==i' "$Q" | paste -sd, - > /tmp/af_stream_$i.txt
  setsid nohup bash -c "V12_NPZ_CACHE=6 nice -n 10 .venv/bin/python -u tools/v15_autopsy_first.py --symsides \$(cat /tmp/af_stream_$i.txt) --out $OUT --workers $W >> /tmp/v15_autopsy_first_stream_$i.log 2>&1" >/dev/null 2>&1 < /dev/null &
  log "stream $i started ($(tr ',' '\n' < /tmp/af_stream_$i.txt | wc -l) sym_sides)"
done
# sync loop: bases + summaries to S1 (the scheduler reads them at every launch)
setsid nohup bash -c "while pgrep -f 'v15_autopsy_first.py --symsides' >/dev/null; do rsync -az --update -e 'ssh -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=accept-new' $OUT/ niels@10.0.0.3:v15_autopsy_first/ 2>/dev/null; sleep 120; done; rsync -az --update -e 'ssh -o BatchMode=yes' $OUT/ niels@10.0.0.3:v15_autopsy_first/; echo done >> $LOG" >/dev/null 2>&1 < /dev/null &
log "sync loop started -> S1 ~/v15_autopsy_first/"
